"""Download and ingest Dhan's instrument master.

The master is a ~35 MB CSV that Dhan refreshes daily. Security IDs change every
expiry, so it is re-fetched at runtime rather than baked in.

Two fields in it cannot be taken at face value for MCX (both verified against
the live file on 2026-09-16):

  LOT_SIZE  -- reads 1.0 for *every* MCX row (158 FUTCOM, 15,844 OPTFUT) while
               NSE rows in the same file carry real values. Dhan does not
               publish MCX contract size here, so it comes from the strategy
               module's `contract_specs` instead.
  TICK_SIZE -- published in paise, not rupees (ALUMINIUM 5.0 = Rs 0.05,
               CARDAMOM 100.0 = Rs 1.00, CRUDEOIL options 10.0 = Rs 0.10).
               Divided by the strategy's `contract_specs.tick_size_divisor`.

Neither quirk generalises: NSE publishes lot size correctly and needs no tick
divisor, which is exactly why both live with the strategy rather than here.

**Which rows are wanted comes from the ENABLED strategy modules.** The file is
scanned once for all of them together -- it is 35 MB, and a pass per strategy
would be a pass per strategy. A disabled strategy contributes no filter, so
none of its contracts are ingested or refreshed.
"""
import csv
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import httpx

from src import config_utils
from src.core.time_utils import utc_now
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.logging_config import get_logger
from src.strategies.services.strategy_definition import (
    InstrumentSet,
    StrategyDefinition,
)
from src.strategies.services.strategy_registry import get_strategy_registry

logger = get_logger("instruments.master")

MASTER_FILENAME = "api-scrip-master-detailed.csv"

# Column names as published. Verified against the live header on 2026-09-16.
COL_EXCH_ID = "EXCH_ID"
COL_SECURITY_ID = "SECURITY_ID"
COL_INSTRUMENT = "INSTRUMENT"
COL_UNDERLYING_SECURITY_ID = "UNDERLYING_SECURITY_ID"
COL_UNDERLYING_SYMBOL = "UNDERLYING_SYMBOL"
COL_SYMBOL_NAME = "SYMBOL_NAME"
COL_DISPLAY_NAME = "DISPLAY_NAME"
COL_LOT_SIZE = "LOT_SIZE"
COL_EXPIRY = "SM_EXPIRY_DATE"
COL_STRIKE = "STRIKE_PRICE"
COL_OPTION_TYPE = "OPTION_TYPE"
COL_TICK_SIZE = "TICK_SIZE"
COL_SERIES = "SERIES"

REQUIRED_COLUMNS = {
    COL_EXCH_ID, COL_SECURITY_ID, COL_INSTRUMENT, COL_UNDERLYING_SYMBOL,
    COL_EXPIRY, COL_STRIKE, COL_OPTION_TYPE, COL_TICK_SIZE,
}

VALID_OPTION_TYPES = {"CE", "PE"}


class InstrumentMasterError(Exception):
    pass


@dataclass
class RefreshResult:
    downloaded: bool
    source_path: str
    rows_scanned: int = 0
    rows_matched: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    deactivated: int = 0
    expiries: List[date] = field(default_factory=list)
    duration_seconds: float = 0.0
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "downloaded": self.downloaded,
            "sourcePath": self.source_path,
            "rowsScanned": self.rows_scanned,
            "rowsMatched": self.rows_matched,
            "inserted": self.inserted,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "deactivated": self.deactivated,
            "expiries": [value.isoformat() for value in self.expiries],
            "durationSeconds": round(self.duration_seconds, 3),
            "warnings": self.warnings,
        }


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    text = value.strip()
    if not text or text.upper() in {"NA", "XX", "0"}:
        return None
    # Observed format is YYYY-MM-DD; the timestamped variant is accepted in
    # case Dhan changes it for some segments.
    for pattern in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _parse_decimal(value: Optional[str]) -> Optional[Decimal]:
    if value is None:
        return None
    text = value.strip()
    if not text or text.upper() in {"NA", "XX"}:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


class InstrumentMasterService:
    """Fetches the master CSV and upserts the universe every strategy wants."""

    def __init__(
        self,
        repository: InstrumentRepository,
        strategies: Optional[List[StrategyDefinition]] = None,
    ):
        self.repository = repository
        self._strategies = strategies

    def strategies(self) -> List[StrategyDefinition]:
        """The strategies whose contracts this service ingests.

        Defaults to the RUNNING ones: a disabled strategy must not have rows
        fetched or refreshed for it. An explicit list (tests, a targeted
        refresh) overrides that.
        """
        if self._strategies is not None:
            return list(self._strategies)
        registry = get_strategy_registry()
        return registry.enabled() or []

    # --- configuration -----------------------------------------------------
    @staticmethod
    def _master_url() -> str:
        return config_utils.get_property_value(
            "dhan.instrument_master_url",
            "https://images.dhan.co/api-data/api-scrip-master-detailed.csv",
        )

    @staticmethod
    def _cache_dir() -> str:
        return config_utils.get_property_value(
            "dhan.instrument_master_cache_dir", "./data/instrument_master"
        )

    @classmethod
    def _cache_path(cls) -> str:
        return os.path.join(cls._cache_dir(), MASTER_FILENAME)

    @staticmethod
    def _max_age_seconds() -> float:
        return config_utils.get_property_value_int(
            "dhan.instrument_master_max_age_hours", 12
        ) * 3600

    @staticmethod
    def _configured_lot_size(strategy: StrategyDefinition) -> Optional[int]:
        return strategy.lot_size()

    @staticmethod
    def _tick_divisor(strategy: StrategyDefinition) -> Decimal:
        return Decimal(str(strategy.tick_size_divisor or 1))

    # --- download ----------------------------------------------------------
    def is_cache_fresh(self) -> bool:
        path = self._cache_path()
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            return False
        return (time.time() - os.path.getmtime(path)) < self._max_age_seconds()

    async def download(self, force: bool = False) -> bool:
        """Fetch the master to the cache. Returns True if it was downloaded.

        The CSV is public and needs no credentials, so this works whether or
        not Dhan market-data credentials are configured.
        """
        if not force and self.is_cache_fresh():
            logger.info("Instrument master cache is fresh; skipping download")
            return False

        url = self._master_url()
        cache_dir = self._cache_dir()
        os.makedirs(cache_dir, exist_ok=True)
        timeout = config_utils.get_property_value_int("dhan.http_timeout_seconds", 30)

        logger.info("Downloading instrument master from %s", url)
        started = time.perf_counter()
        total_bytes = 0

        # Stream to a temp file in the same directory, then atomically replace,
        # so a failed download can never leave a truncated master in place.
        handle, temp_path = tempfile.mkstemp(dir=cache_dir, suffix=".partial")
        try:
            with os.fdopen(handle, "wb") as sink:
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                    async with client.stream("GET", url) as response:
                        response.raise_for_status()
                        async for chunk in response.aiter_bytes(chunk_size=1 << 20):
                            sink.write(chunk)
                            total_bytes += len(chunk)

            if total_bytes < 1_000_000:
                raise InstrumentMasterError(
                    f"Instrument master download looks truncated ({total_bytes} bytes)"
                )

            os.replace(temp_path, self._cache_path())
        except Exception:
            logger.exception(
                "Instrument master download failed after %s bytes from %s",
                total_bytes, url,
            )
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

        logger.info(
            "Instrument master downloaded: %.1f MB in %.1fs",
            total_bytes / (1 << 20),
            time.perf_counter() - started,
        )
        return True

    # --- parse + ingest ----------------------------------------------------
    def parse(self, path: str) -> tuple[List[Dict[str, Any]], int, List[str]]:
        """Read the CSV once and return the rows every wanted strategy claims.

        One pass for all strategies together. The file is 35 MB; a pass per
        strategy would cost a full read per strategy for no benefit, since the
        filters are disjoint (a row belongs to at most one strategy, by its
        exchange, instrument type and underlying).

        A strategy claims rows through its `instrument_sets`. A derivatives
        module on one underlying claims two sets of one symbol each; a rotation
        claims one set of five hundred. The lookup is the same dictionary
        either way, so the cost of the second case is 500 dictionary entries
        rather than a second pass.
        """
        strategies = self.strategies()
        if not strategies:
            raise InstrumentMasterError(
                "No strategy module is enabled, so there is no instrument "
                "universe to ingest. Enable one on the Strategies & Features "
                "page."
            )

        # (EXCH_ID, INSTRUMENT, UNDERLYING_SYMBOL) -> who claims those rows.
        claims: Dict[tuple, tuple] = {}
        # (EXCH_ID, UNDERLYING_SYMBOL) -> strategy key, so two strategies
        # cannot own the same name through different instrument types.
        symbol_owner: Dict[tuple, str] = {}
        needs_series = False
        claimed_symbols: Dict[str, set] = {}

        for strategy in strategies:
            claimed_symbols[strategy.key] = set()
            for instrument_set in strategy.instrument_sets:
                if instrument_set.series:
                    needs_series = True
                if instrument_set.from_universe:
                    if strategy.universe is None:
                        raise InstrumentMasterError(
                            f"Strategy {strategy.key!r} claims its universe but "
                            f"declares none."
                        )
                    symbols = strategy.universe.symbols
                else:
                    symbols = instrument_set.symbols
                for symbol in symbols:
                    owner = symbol_owner.get((strategy.exchange_id, symbol))
                    if owner is not None and owner != strategy.key:
                        raise InstrumentMasterError(
                            f"Strategies {owner!r} and {strategy.key!r} both claim "
                            f"{strategy.exchange_id} {symbol}; a contract may "
                            f"belong to only one strategy."
                        )
                    symbol_owner[(strategy.exchange_id, symbol)] = strategy.key
                    claimed_symbols[strategy.key].add(symbol)
                    claims[
                        (strategy.exchange_id, instrument_set.instrument_type, symbol)
                    ] = (strategy, instrument_set)

                # Only a set that does NOT trust the master's lot size needs one
                # configured. For NSE EQUITY the master is right and demanding
                # 500 contract_specs entries would be busywork; for MCX it is
                # wrong and the configured value is load-bearing.
                if not instrument_set.trusts_master_lot_size():
                    if not self._configured_lot_size(strategy):
                        raise InstrumentMasterError(
                            f"No contract_specs.{strategy.symbol}.lot_size configured "
                            f"for strategy {strategy.key!r}. Dhan's master reports "
                            f"LOT_SIZE=1 for every MCX contract, so the real lot size "
                            f"must be supplied in conf/strategies/{strategy.key}.yaml "
                            f"(or the instrument set must declare "
                            f"lot_size_source: master)."
                        )

        warnings: List[str] = []
        rows: List[Dict[str, Any]] = []
        scanned = 0
        refreshed_at = utc_now()
        suspicious_lot_sizes: Dict[str, int] = {}
        # security_id -> the segment it was first seen in. Dhan's ids are
        # unique per SEGMENT, not globally (id 13 is NIFTY in IDX_I and ABB in
        # NSE_EQ), while `instruments.security_id` carries a global UNIQUE
        # constraint and MarketBook keys its rows by id alone. No collision
        # exists between the segments ingested today -- checked 2026-09-18
        # across MCX CRUDEOIL's 1,240 ids and the Nifty 500's 499 -- but that
        # is luck rather than a guarantee, so it is checked rather than assumed.
        seen_security_ids: Dict[str, str] = {}
        matched_symbols: Dict[str, set] = {key: set() for key in claimed_symbols}

        with open(path, "r", encoding="utf-8", errors="replace", newline="") as handle:
            reader = csv.DictReader(handle)
            required = set(REQUIRED_COLUMNS)
            if needs_series:
                required.add(COL_SERIES)
            missing = required - set(reader.fieldnames or [])
            if missing:
                raise InstrumentMasterError(
                    f"Instrument master is missing expected columns: {sorted(missing)}. "
                    f"Dhan may have changed the schema; got {reader.fieldnames}"
                )

            for record in reader:
                scanned += 1
                underlying_symbol = record.get(COL_UNDERLYING_SYMBOL, "").strip()
                claim = claims.get(
                    (
                        record.get(COL_EXCH_ID, "").strip(),
                        record.get(COL_INSTRUMENT, "").strip(),
                        underlying_symbol,
                    )
                )
                if claim is None:
                    continue
                strategy, instrument_set = claim
                instrument = instrument_set.instrument_type

                if not instrument_set.accepts_series(record.get(COL_SERIES)):
                    continue

                security_id = record.get(COL_SECURITY_ID, "").strip()
                if not security_id:
                    continue

                segment = instrument_set.exchange_segment
                previous_segment = seen_security_ids.get(security_id)
                if previous_segment is not None and previous_segment != segment:
                    raise InstrumentMasterError(
                        f"Security id {security_id} appears in both "
                        f"{previous_segment} and {segment}. Dhan's ids are unique "
                        f"per segment, but `instruments.security_id` is unique "
                        f"globally and the market book is keyed by id alone, so "
                        f"ingesting both would silently merge two instruments' "
                        f"prices. Narrow one strategy's instrument sets."
                    )
                seen_security_ids[security_id] = segment
                matched_symbols[strategy.key].add(underlying_symbol)

                tick_divisor = self._tick_divisor(strategy)

                option_type = (record.get(COL_OPTION_TYPE) or "").strip().upper()
                if option_type not in VALID_OPTION_TYPES:
                    option_type = None

                strike = _parse_decimal(record.get(COL_STRIKE))
                # Futures rows carry a placeholder strike (0 or -0.01).
                if option_type is None or (strike is not None and strike <= 0):
                    strike = None if option_type is None else strike
                if option_type is not None and (strike is None or strike <= 0):
                    warnings.append(f"Skipped option {security_id}: unusable strike")
                    continue

                master_lot_size = _parse_decimal(record.get(COL_LOT_SIZE))
                if instrument_set.trusts_master_lot_size():
                    # The master is the source of truth for this set. A row
                    # with no usable lot size is skipped rather than guessed.
                    if master_lot_size is None or master_lot_size < 1:
                        warnings.append(
                            f"Skipped {underlying_symbol} {security_id}: "
                            f"LOT_SIZE={record.get(COL_LOT_SIZE)!r} is unusable and "
                            f"this instrument set trusts the master's lot size"
                        )
                        continue
                    lot_size = int(master_lot_size)
                else:
                    configured_lot_size = self._configured_lot_size(strategy)
                    if master_lot_size is not None and master_lot_size > 1:
                        # If Dhan ever starts publishing a real MCX lot size, use it
                        # and say so rather than silently preferring the config.
                        lot_size = int(master_lot_size)
                        warnings.append(
                            f"Master now publishes LOT_SIZE={lot_size} for {security_id}; "
                            f"using it in preference to the configured {configured_lot_size}"
                        )
                    else:
                        lot_size = configured_lot_size
                        suspicious_lot_sizes[underlying_symbol] = (
                            suspicious_lot_sizes.get(underlying_symbol, 0) + 1
                        )

                tick_size = _parse_decimal(record.get(COL_TICK_SIZE))
                if tick_size is not None and tick_size > 0:
                    tick_size = tick_size / tick_divisor
                else:
                    tick_size = None

                underlying_scrip = _parse_decimal(record.get(COL_UNDERLYING_SECURITY_ID))

                display_name = (record.get(COL_DISPLAY_NAME) or "").strip()
                if (
                    instrument_set.trading_symbol_source
                    == InstrumentSet.TRADING_SYMBOL_FROM_UNDERLYING
                ):
                    # The ticker. For a cash equity DISPLAY_NAME is the
                    # company's long name, which is not what a trader types.
                    trading_symbol = underlying_symbol
                else:
                    # DISPLAY_NAME is the descriptive, per-contract name
                    # ("CRUDEOIL 17 SEP 8350 CALL"); SYMBOL_NAME is just the
                    # underlying ("CRUDEOIL") and is the fallback only.
                    trading_symbol = (
                        display_name or (record.get(COL_SYMBOL_NAME) or "").strip()
                    )

                rows.append(
                    {
                        "security_id": security_id,
                        "exchange_id": strategy.exchange_id,
                        "exchange_segment": segment,
                        "segment_code": instrument_set.exchange_segment_code,
                        "instrument_type": instrument,
                        "underlying_symbol": underlying_symbol,
                        "underlying_scrip": int(underlying_scrip) if underlying_scrip else None,
                        "trading_symbol": trading_symbol[:128],
                        "display_name": display_name[:160],
                        "expiry_date": _parse_date(record.get(COL_EXPIRY)),
                        "strike_price": strike,
                        "option_type": option_type,
                        "lot_size": lot_size,
                        "tick_size": tick_size,
                        "is_active": True,
                        "refreshed_at": refreshed_at,
                    }
                )

        for symbol, count in sorted(suspicious_lot_sizes.items()):
            strategy = next(
                one
                for one in strategies
                if symbol in claimed_symbols.get(one.key, set())
            )
            configured = self._configured_lot_size(strategy)
            warnings.append(
                f"{count} {symbol} rows had LOT_SIZE<=1 in the master (expected "
                f"for MCX); used contract_specs.{symbol}.lot_size={configured}"
            )
            # Expected for MCX today, but load-bearing: if this substitution is
            # ever wrong, every turnover, charge and P&L figure is wrong with it.
            logger.warning(
                "%s %s rows published LOT_SIZE<=1; substituted the configured "
                "contract_specs.%s.lot_size=%s from strategy %s. Expected for "
                "MCX -- see CLAUDE.md section 5.",
                count, symbol, symbol, configured, strategy.key,
            )

        # A universe that quietly shrinks still produces a ranking -- just a
        # different one. Unresolved symbols are reported, never dropped in
        # silence.
        for strategy in strategies:
            unresolved = sorted(claimed_symbols[strategy.key] - matched_symbols[strategy.key])
            if not unresolved:
                continue
            shown = ", ".join(unresolved[:10])
            more = "" if len(unresolved) <= 10 else f" (+{len(unresolved) - 10} more)"
            warnings.append(
                f"{len(unresolved)} of {len(claimed_symbols[strategy.key])} symbols "
                f"claimed by {strategy.key!r} have no matching row in the instrument "
                f"master: {shown}{more}"
            )
            logger.warning(
                "Strategy %s: %s of %s claimed symbols did not resolve against the "
                "instrument master: %s%s",
                strategy.key, len(unresolved), len(claimed_symbols[strategy.key]),
                shown, more,
            )

        # The universe file's own security ids are not used to trade, but a
        # divergence from the master is worth saying out loud.
        by_symbol: Dict[str, str] = {}
        for row in rows:
            by_symbol.setdefault(row["underlying_symbol"], row["security_id"])
        for strategy in strategies:
            if strategy.universe is None:
                continue
            diverged = [
                f"{symbol} (file {declared}, master {by_symbol[symbol]})"
                for symbol, declared in sorted(
                    strategy.universe.declared_security_ids.items()
                )
                if symbol in by_symbol and by_symbol[symbol] != declared
            ]
            if diverged:
                warnings.append(
                    f"{len(diverged)} universe security ids differ from the master "
                    f"(the master is used): {', '.join(diverged[:10])}"
                )
                logger.warning(
                    "Universe %s: %s declared security ids differ from the "
                    "instrument master; the master is authoritative: %s",
                    strategy.universe.name, len(diverged), ", ".join(diverged[:10]),
                )

        if not rows:
            raise InstrumentMasterError(
                f"No {'/'.join(sorted(one.symbol for one in strategies))} "
                f"contracts found in the instrument master. "
                f"Scanned {scanned} rows."
            )

        return rows, scanned, warnings

    async def refresh(self, force: bool = False) -> RefreshResult:
        """Download if needed, parse, and upsert. Idempotent."""
        started = time.perf_counter()
        downloaded = await self.download(force=force)
        path = self._cache_path()
        if not os.path.exists(path):
            raise InstrumentMasterError(f"Instrument master not found at {path}")

        rows, scanned, warnings = self.parse(path)
        counts = await self.repository.upsert_many(rows)

        # Deactivation is per underlying: a strategy that is off contributed no
        # rows, and must not have its contracts deactivated as a side effect of
        # another strategy's refresh.
        deactivated = 0
        for strategy in self.strategies():
            symbols = strategy.symbols()
            deactivated += await self.repository.deactivate_missing_for_symbols(
                sorted(symbols),
                [
                    row["security_id"]
                    for row in rows
                    if row["underlying_symbol"] in symbols
                ],
            )

        expiries = sorted({row["expiry_date"] for row in rows if row["expiry_date"]})

        result = RefreshResult(
            downloaded=downloaded,
            source_path=path,
            rows_scanned=scanned,
            rows_matched=len(rows),
            inserted=counts["inserted"],
            updated=counts["updated"],
            unchanged=counts["unchanged"],
            deactivated=deactivated,
            expiries=expiries,
            duration_seconds=time.perf_counter() - started,
            warnings=warnings,
        )
        logger.info(
            "Instrument master refresh: matched=%s inserted=%s updated=%s "
            "unchanged=%s deactivated=%s in %.2fs",
            result.rows_matched, result.inserted, result.updated,
            result.unchanged, result.deactivated, result.duration_seconds,
        )
        logger.debug(
            "Refresh detail: scanned=%s rows downloaded=%s source=%s expiries=%s",
            result.rows_scanned, result.downloaded, result.source_path,
            [expiry.isoformat() for expiry in expiries],
        )
        for warning in warnings:
            logger.warning("Instrument master: %s", warning)
        return result
