"""Keeping the daily-bar table current.

One job: after the close, top up every symbol a strategy needs so that the
indicator pass at 09:15 reads from the database instead of waiting five minutes
on Dhan's rate limiter.

Rules this follows, all of them load-bearing:

* **It fetches; it never accumulates.** Every bar comes whole from Dhan's
  `/charts/historical` endpoint through the same market-data client the price
  chart uses. Nothing here reads the feed, and nothing on the tick path knows
  this module exists (root `CLAUDE.md` section 4).
* **It paces itself.** `DhanChartsClient` throttles per security id, which does
  not throttle a loop over 500 different ones at all. The delay between symbols
  is this service's own, configured, and defaults to the 0.6 s the research
  project measured as safe.
* **A symbol that fails does not fail the run.** 499 refreshed symbols and one
  error is a reportable state; an exception that abandons the other 498 is not.
* **It never invents a bar.** No credentials means no bars and an error, the
  same as the price chart: there is no synthetic fallback on this path at all,
  because a fabricated close would flow straight into a trading decision.
* **The index's own dates are the trading calendar.** A date NSE published a
  bar for is a date NSE traded. That is why nothing here consults a holiday
  list, and why a run on a closed day writes nothing and says so.
"""
import asyncio
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

from src import config_utils
from src.core.time_utils import IST, ist_today
from src.daily_bars.database.db_models.daily_bar_model import SOURCE_DHAN
from src.daily_bars.database.db_operations.daily_bar_repository import (
    DailyBarRepository,
)
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.logging_config import get_logger
from src.market.services.dhan_charts_client import (
    Candle,
    ChartsError,
    DhanChartsClient,
)
from src.strategies.services.strategy_definition import StrategyDefinition

logger = get_logger("daily_bars.refresh")

# Dhan's historical endpoint wants an exclusive `toDate`, so a refresh asks for
# tomorrow to be sure of getting today.
ONE_DAY = timedelta(days=1)


class DailyBarRefreshError(Exception):
    pass


@dataclass
class SymbolRefresh:
    symbol: str
    exchange_segment: str
    security_id: Optional[str]
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    from_date: Optional[date] = None
    latest_before: Optional[date] = None
    latest_after: Optional[date] = None
    skipped_reason: Optional[str] = None
    error: Optional[str] = None


@dataclass
class RefreshResult:
    strategy_key: str
    as_of: date
    symbols: List[SymbolRefresh] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def refreshed(self) -> List[SymbolRefresh]:
        return [one for one in self.symbols if one.error is None and not one.skipped_reason]

    @property
    def failed(self) -> List[SymbolRefresh]:
        return [one for one in self.symbols if one.error is not None]

    @property
    def unresolved(self) -> List[SymbolRefresh]:
        """Symbols with no active row in the instrument master.

        Keyed on the MISSING SECURITY ID, not on `skipped_reason`: a symbol
        that was skipped as "already current" also carries a reason, and
        lumping the two together made a full refresh report "WELCORP has no
        active row in the instrument master" about a symbol that has one and
        was simply up to date. A report that names the wrong problem is worse
        than one that names none.
        """
        return [one for one in self.symbols if one.security_id is None]

    @property
    def already_current(self) -> List[SymbolRefresh]:
        """Skipped because the stored series already reaches `as_of`."""
        return [
            one
            for one in self.symbols
            if one.security_id is not None and one.skipped_reason
        ]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "strategyKey": self.strategy_key,
            "asOf": self.as_of.isoformat(),
            "symbolsConsidered": len(self.symbols),
            "symbolsRefreshed": len(self.refreshed),
            "symbolsFailed": len(self.failed),
            "symbolsUnresolved": len(self.unresolved),
            "symbolsAlreadyCurrent": len(self.already_current),
            "barsInserted": sum(one.inserted for one in self.symbols),
            "barsUpdated": sum(one.updated for one in self.symbols),
            "failures": [
                {"symbol": one.symbol, "error": one.error} for one in self.failed[:20]
            ],
            "unresolved": [one.symbol for one in self.unresolved[:20]],
            "durationSeconds": round(self.duration_seconds, 1),
        }


def candle_to_row(
    candle: Candle, symbol: str, exchange_segment: str, security_id: Optional[str]
) -> Optional[Dict[str, Any]]:
    """One Dhan candle as an upsertable row, or None if it is unusable.

    The candle's timestamp is epoch SECONDS and is resolved to a date **in
    IST**, not UTC: a session Dhan stamps 18:30Z is the following calendar day
    in UTC and would be filed one day late for the rest of the series' life.
    """
    try:
        bar_date = datetime.fromtimestamp(int(candle.time), IST).date()
    except (OSError, OverflowError, ValueError, TypeError):
        return None

    prices = (candle.open, candle.high, candle.low, candle.close)
    if any(value is None or value <= 0 for value in prices):
        return None
    if candle.high < candle.low:
        return None

    # A session the exchange did not hold: all four prices identical with zero
    # volume. Live Dhan has produced none of these -- zero across 1,108,462
    # rows on 2026-09-18 -- but the same shape is what the research project's
    # spliced panel carries for 395 equities on an NSE holiday, and every
    # lookback in this strategy counts ROWS rather than days. The guard is here
    # as well as in the importer because this is where a future vendor change
    # would arrive. See `bar_import_service.is_phantom_bar`.
    if (
        candle.volume == 0
        and candle.open == candle.high == candle.low == candle.close
    ):
        return None

    return {
        "symbol": symbol,
        "exchange_segment": exchange_segment,
        "security_id": security_id,
        "bar_date": bar_date,
        "open": Decimal(str(candle.open)),
        "high": Decimal(str(candle.high)),
        "low": Decimal(str(candle.low)),
        "close": Decimal(str(candle.close)),
        "volume": int(candle.volume) if candle.volume is not None else None,
        "source": SOURCE_DHAN,
    }


def _report(
    on_progress: Optional[Callable[[int, int, str], None]],
    done: int,
    total: int,
    symbol: str,
) -> None:
    """Call a progress callback without ever letting it end the run.

    A refresh that died because something watching it raised would be a far
    worse bug than a progress bar that stopped moving, so this swallows
    everything and logs at DEBUG. Nothing here is on the tick path; this loop
    already sleeps 0.6 s per symbol.
    """
    if on_progress is None:
        return
    try:
        on_progress(done, total, symbol)
    except Exception:  # noqa: BLE001 - see the docstring
        logger.debug("A daily-bar progress callback raised; ignoring it", exc_info=True)


class DailyBarRefreshService:
    """Tops up `daily_bars` for everything a strategy reads."""

    def __init__(
        self,
        repository: DailyBarRepository,
        instruments: InstrumentRepository,
        client: Optional[DhanChartsClient] = None,
    ):
        self.repository = repository
        self.instruments = instruments
        self.client = client or DhanChartsClient()

    # --- configuration -----------------------------------------------------
    @staticmethod
    def _request_delay() -> float:
        """Seconds between symbols.

        `DhanChartsClient` throttles per security id, so a loop over 500 ids
        gets no throttling at all from it. 0.6 s is the interval the research
        project's own downloader used against this endpoint without being rate
        limited; a 500-symbol pull therefore takes about five minutes, which is
        comfortable overnight and impossible just before the open.
        """
        return config_utils.get_property_value_float("daily_bars.request_delay_seconds", 0.6)

    @staticmethod
    def _bootstrap_days() -> int:
        """How far back to go for a symbol with no stored history.

        The strategy needs 260 sessions of warm-up (specification section 6);
        this is deliberately far more, so that importing the panel later, or
        adding a symbol mid-life, does not leave a name permanently short of
        history and therefore permanently unrankable.
        """
        return config_utils.get_property_value_int("daily_bars.bootstrap_days", 3650)

    # --- targets -----------------------------------------------------------
    async def targets_for(
        self, strategy: StrategyDefinition
    ) -> Tuple[List[Tuple[str, str, str, str]], List[str]]:
        """(symbol, segment, security_id, dhan_instrument) for everything read.

        Resolved from the instrument master, not from the universe file: the
        file's own security ids are documented as advisory and four of them
        already disagree with the master.

        The reference instruments (the regime index) are appended from the
        strategy definition, because they are deliberately NOT rows in the
        instruments table -- Dhan's ids are unique per segment and the index's
        would collide with a stock's.
        """
        targets: List[Tuple[str, str, str, str]] = []
        unresolved: List[str] = []

        for instrument_set in strategy.instrument_sets:
            symbols = (
                strategy.universe.symbols
                if instrument_set.from_universe and strategy.universe
                else instrument_set.symbols
            )
            rows = await self.instruments.list_by_symbols(
                sorted(symbols), instrument_set.exchange_segment
            )
            by_symbol = {row.underlying_symbol: row for row in rows if row.is_active}
            for symbol in sorted(symbols):
                row = by_symbol.get(symbol)
                if row is None:
                    unresolved.append(symbol)
                    continue
                targets.append(
                    (
                        symbol,
                        instrument_set.exchange_segment,
                        row.security_id,
                        instrument_set.instrument_type,
                    )
                )

        for reference in strategy.reference_instruments.values():
            targets.append(
                (
                    reference.symbol,
                    reference.exchange_segment,
                    reference.security_id,
                    reference.instrument,
                )
            )

        return targets, unresolved

    # --- the refresh -------------------------------------------------------
    async def refresh_strategy(
        self,
        strategy: StrategyDefinition,
        as_of: Optional[date] = None,
        only_symbols: Optional[List[str]] = None,
        force_full: bool = False,
        commit_each_symbol: bool = True,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> RefreshResult:
        """Top every symbol up to `as_of` (default: today in IST).

        `commit_each_symbol` for the same two reasons the importer does it
        (`bar_import_service.import_symbols`), and a third that is specific to
        this path:

          * a failure part-way through leaves the symbols already done;
          * the session's identity map stays flat across five hundred symbols;
          * and **it does not hold the write lock for twelve minutes.** On
            SQLite one long transaction blocks every other writer: the swing
            stop monitor polls every second, and during the first observed
            full refresh it logged "database is locked" once a second for the
            whole run. Committing per symbol turns that into five hundred
            short locks that nothing notices.

        Pass False only when the caller owns the transaction and knows the
        batch is small.

        `on_progress(done, total, symbol)` is called after each symbol, for
        anything that wants to SHOW the run rather than wait for it. Five
        hundred symbols at the rate limiter's 0.6 s is twelve minutes, which is
        long enough that "it is doing something" and "it has hung" look
        identical without it. It is synchronous, must be cheap, and cannot
        break the run: it is called inside a try/except that swallows
        everything, because a reporting callback that killed a refresh would be
        a far worse bug than a stalled progress bar.
        """
        started = time.perf_counter()
        as_of = as_of or ist_today()
        result = RefreshResult(strategy_key=strategy.key, as_of=as_of)

        if not self.client.has_credentials():
            raise DailyBarRefreshError(
                "Dhan market-data credentials are not configured, so no daily "
                "bars can be fetched. Nothing is generated in their place: a "
                "fabricated close would go straight into a trading decision."
            )

        targets, unresolved = await self.targets_for(strategy)
        for symbol in unresolved:
            result.symbols.append(
                SymbolRefresh(
                    symbol=symbol,
                    exchange_segment=strategy.exchange_segment,
                    security_id=None,
                    skipped_reason="no active row in the instrument master",
                )
            )

        if only_symbols is not None:
            wanted = {one.upper() for one in only_symbols}
            targets = [one for one in targets if one[0] in wanted]

        latest_by_segment: Dict[str, Dict[str, date]] = {}
        for segment in {segment for _, segment, _, _ in targets}:
            symbols = [symbol for symbol, seg, _, _ in targets if seg == segment]
            latest_by_segment[segment] = await self.repository.latest_dates(
                segment, symbols
            )

        delay = self._request_delay()
        bootstrap = timedelta(days=self._bootstrap_days())

        total = len(targets)
        for index, (symbol, segment, security_id, instrument) in enumerate(targets):
            # Reported BEFORE the work, so the name on screen is the one being
            # fetched rather than the one just finished -- which is what makes
            # a stalled run point at the symbol that stalled it.
            _report(on_progress, index, total, symbol)
            entry = SymbolRefresh(
                symbol=symbol, exchange_segment=segment, security_id=security_id
            )
            result.symbols.append(entry)

            latest = None if force_full else latest_by_segment.get(segment, {}).get(symbol)
            entry.latest_before = latest
            if latest is not None and latest >= as_of:
                entry.skipped_reason = "already current"
                continue

            # Re-request the last stored session as well as the new ones. Dhan
            # restates a series after a corporate action, and the upsert
            # updates rather than duplicates, so the cost is one extra bar and
            # the benefit is that an adjustment is picked up.
            from_date = (latest - ONE_DAY) if latest is not None else (as_of - bootstrap)
            entry.from_date = from_date

            if index > 0 and delay > 0:
                await asyncio.sleep(delay)

            try:
                candles = await self.client.fetch_daily(
                    security_id=security_id,
                    exchange_segment=segment,
                    instrument=instrument,
                    from_date=datetime.combine(from_date, datetime.min.time()),
                    # `toDate` is exclusive.
                    to_date=datetime.combine(as_of + ONE_DAY, datetime.min.time()),
                )
            except ChartsError as error:
                entry.error = str(error)
                logger.warning("Daily bars for %s failed: %s", symbol, error)
                continue
            except Exception as error:  # noqa: BLE001 - one symbol must not end the run
                entry.error = f"{type(error).__name__}: {error}"
                logger.warning(
                    "Daily bars for %s failed unexpectedly: %s", symbol, error
                )
                continue

            rows = [
                row
                for row in (
                    candle_to_row(candle, symbol, segment, security_id)
                    for candle in candles
                )
                if row is not None and row["bar_date"] <= as_of
            ]
            entry.fetched = len(rows)
            if rows:
                counts = await self.repository.upsert_many(rows)
                entry.inserted = counts["inserted"]
                entry.updated = counts["updated"]
                entry.latest_after = max(row["bar_date"] for row in rows)
            else:
                entry.latest_after = latest

            if commit_each_symbol:
                await self.repository.session.commit()
                self.repository.session.expunge_all()

        _report(on_progress, total, total, "")
        result.duration_seconds = time.perf_counter() - started
        logger.info(
            "Daily bar refresh for %s as of %s: %s refreshed, %s failed, "
            "%s unresolved, %s bars inserted in %.1fs",
            strategy.key, as_of.isoformat(), len(result.refreshed),
            len(result.failed), len(result.unresolved),
            sum(one.inserted for one in result.symbols), result.duration_seconds,
        )
        if result.failed:
            logger.warning(
                "Daily bar refresh: %s symbol(s) failed: %s",
                len(result.failed),
                ", ".join(f"{one.symbol} ({one.error})" for one in result.failed[:5]),
            )
        return result
