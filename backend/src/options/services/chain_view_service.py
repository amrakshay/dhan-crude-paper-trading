"""The option chain a PERSON is looking at, for any NSE underlying.

Three sources, kept apart on purpose:

* **Contracts** come from the `instruments` table -- strikes, security ids,
  lot and tick sizes, exactly what an order will be placed against.
* **Greeks, IV, OI and the underlying's price** come from Dhan's option chain
  REST endpoint, fetched on demand for the one (underlying, expiry) on screen
  and cached for Dhan's own 3-second limit. Nothing polls a chain nobody is
  looking at.
* **Live prices and depth** come from the ONE shared feed (root CLAUDE.md
  section 4). The strike window around ATM is PINNED to it while the chain is
  open, through the same `pin_instruments` a rebalance uses, and released when
  nobody has asked for `view_ttl_seconds`.

The index id quirk lives here: Dhan's chain endpoint wants the IDX_I security
id of an index (NIFTY = 13), not the UNDERLYING_SECURITY_ID printed on the
option rows (26000), which it refuses with "813 Invalid SecurityId" --
verified live 2026-11-24. Stocks use their own NSE_EQ security id.
"""
import asyncio
import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from src.core.time_utils import ist_today
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.logging_config import get_logger
from src.strategies.services.strategy_definition import StrategyDefinition

logger = get_logger("options.chain")

SEGMENT_FNO = "NSE_FNO"
SEGMENT_EQ = "NSE_EQ"
SEGMENT_INDEX = "IDX_I"

KIND_INDEX = "INDEX"
KIND_STOCK = "STOCK"

# Dhan: one unique request per 3 seconds per (underlying, expiry).
SNAPSHOT_MAX_AGE_SECONDS = 3.0

DEFAULT_STRIKE_WINDOW = 15
DEFAULT_VIEW_TTL_SECONDS = 90


class ChainViewError(Exception):
    """Something the person asked for that cannot be shown. Carries why."""


@dataclass(frozen=True)
class Underlying:
    symbol: str
    kind: str                       # INDEX / STOCK
    lot_size: int
    chain_scrip: Optional[int]      # what /optionchain wants
    chain_segment: str

    def as_dict(self) -> Dict[str, Any]:
        return {"symbol": self.symbol, "kind": self.kind, "lotSize": self.lot_size}


# --- shared state -------------------------------------------------------------
# One chain client and one snapshot cache per PROCESS, so the 3-second limit is
# honoured across every browser tab and every module. Built lazily.
_client = None
_snapshots: Dict[Tuple[int, str, str], Tuple[float, Any]] = {}
_snapshot_locks: Dict[Tuple[int, str, str], asyncio.Lock] = {}
# strategy key -> (pinned security ids, monotonic time of the last view)
_views: Dict[str, Tuple[frozenset, float]] = {}


def _chain_client():
    global _client
    if _client is None:
        from src.market.services.dhan_option_chain_client import DhanOptionChainClient

        _client = DhanOptionChainClient()
    return _client


def reset_state_for_tests() -> None:
    """Forget cached snapshots and views. Tests only."""
    global _client
    _client = None
    _snapshots.clear()
    _snapshot_locks.clear()
    _views.clear()


class ChainViewService:
    def __init__(self, session, strategy: StrategyDefinition, feed_manager=None, client=None):
        self.repository = InstrumentRepository(session)
        self.strategy = strategy
        self._feed_manager = feed_manager
        self._client = client
        section = strategy.module_section("options")
        self._index_ids: Dict[str, int] = {
            str(symbol).upper(): int(scrip)
            for symbol, scrip in (section.get("index_underlyings") or {}).items()
        }
        self.strike_window = int(section.get("strike_window") or DEFAULT_STRIKE_WINDOW)
        self.view_ttl_seconds = float(
            section.get("view_ttl_seconds") or DEFAULT_VIEW_TTL_SECONDS
        )

    @property
    def feed_manager(self):
        if self._feed_manager is None:
            from src.market.services.feed_manager import get_feed_manager

            self._feed_manager = get_feed_manager()
        return self._feed_manager

    @property
    def client(self):
        return self._client or _chain_client()

    # --- what can be traded -------------------------------------------------
    def _stock_symbols(self) -> List[str]:
        return sorted(self.strategy.universe.symbols) if self.strategy.universe else []

    async def underlyings(self) -> List[Underlying]:
        """Indices first (in the YAML's order), then stocks A-Z.

        Only names with ACTIVE option rows: an instrument refresh that has not
        run yet, or a name that left F&O, is absent rather than offered empty.
        """
        wanted = list(self._index_ids) + self._stock_symbols()
        lots = await self.repository.option_underlyings(wanted, SEGMENT_FNO)
        found: List[Underlying] = []
        for symbol in wanted:
            if symbol not in lots:
                continue
            found.append(await self._describe(symbol, lots[symbol]))
        return found

    async def underlying(self, symbol: str) -> Underlying:
        symbol = str(symbol or "").strip().upper()
        if symbol not in self._index_ids and symbol not in set(self._stock_symbols()):
            raise ChainViewError(
                f"{symbol or 'That'} is not an NSE index or F&O stock this "
                f"strategy trades."
            )
        lots = await self.repository.option_underlyings([symbol], SEGMENT_FNO)
        if symbol not in lots:
            raise ChainViewError(
                f"No active {symbol} option contracts. Refresh the instrument "
                f"master (Strategies & Features, or POST /api/instruments/refresh)."
            )
        return await self._describe(symbol, lots[symbol])

    async def _describe(self, symbol: str, lot_size: int) -> Underlying:
        if symbol in self._index_ids:
            return Underlying(symbol, KIND_INDEX, lot_size, self._index_ids[symbol], SEGMENT_INDEX)
        cash = await self.repository.find_cash_row(symbol, SEGMENT_EQ)
        scrip = int(cash.security_id) if cash is not None else None
        return Underlying(symbol, KIND_STOCK, lot_size, scrip, SEGMENT_EQ)

    async def expiries(self, symbol: str) -> List[date]:
        underlying = await self.underlying(symbol)
        instrument_type = "OPTIDX" if underlying.kind == KIND_INDEX else "OPTSTK"
        return await self.repository.list_expiries(
            underlying.symbol, instrument_type, on_or_after=ist_today()
        )

    # --- the chain ----------------------------------------------------------
    async def chain(self, symbol: str, expiry: Optional[date] = None, pin: bool = True) -> Dict[str, Any]:
        underlying = await self.underlying(symbol)
        expiries = await self.expiries(underlying.symbol)
        if not expiries:
            raise ChainViewError(f"No {underlying.symbol} expiries from today on.")
        if expiry is None:
            expiry = expiries[0]
        elif expiry not in expiries:
            raise ChainViewError(
                f"{underlying.symbol} has no expiry on {expiry.isoformat()}."
            )

        contracts = await self.repository.list_chain(underlying.symbol, expiry)
        contracts = [one for one in contracts if one.is_active]
        snapshot, snapshot_note = await self._snapshot(underlying, expiry)

        spot_row = None
        spot_security_id = None
        if underlying.kind == KIND_STOCK and underlying.chain_scrip is not None:
            spot_security_id = str(underlying.chain_scrip)
            spot_row = self.feed_manager.book.get(spot_security_id)
        spot = None
        spot_source = None
        if spot_row and spot_row.get("ltp"):
            spot, spot_source = float(spot_row["ltp"]), "feed"
        elif snapshot is not None and snapshot.underlying_last_price:
            spot, spot_source = float(snapshot.underlying_last_price), "option-chain"

        if spot is not None:
            # The margin engine's freshest price for an index, which has no
            # row on the feed (src/options/services/option_margin_service.py).
            from src.options.services.option_margin_service import remember_spot

            remember_spot(underlying.symbol, spot)

        strikes = sorted({Decimal(str(one.strike_price)) for one in contracts})
        atm = _nearest(strikes, spot) if spot is not None else None
        window = _window(strikes, atm, self.strike_window)

        legs_by_id: Dict[str, Any] = {}
        if snapshot is not None:
            for _strike, _option_type, leg in snapshot.legs():
                if leg.security_id:
                    legs_by_id[str(leg.security_id)] = leg

        rows: Dict[Decimal, Dict[str, Any]] = {}
        pinned: List[str] = []
        for contract in contracts:
            strike = Decimal(str(contract.strike_price))
            row = rows.setdefault(strike, {"strike": float(strike), "inWindow": strike in window})
            side = "call" if contract.option_type == "CE" else "put"
            row[side] = self._contract_view(contract, legs_by_id.get(str(contract.security_id)))
            if strike in window:
                pinned.append(str(contract.security_id))

        if pin:
            ids = set(pinned)
            if spot_security_id:
                ids.add(spot_security_id)
            await self._pin(ids)
            if snapshot is not None:
                self._merge_greeks(snapshot, ids)

        return {
            "underlying": underlying.as_dict(),
            "expiry": expiry.isoformat(),
            "expiries": [one.isoformat() for one in expiries],
            "spot": spot,
            "spotSource": spot_source,
            "spotSecurityId": spot_security_id,
            "atmStrike": float(atm) if atm is not None else None,
            "strikeWindow": self.strike_window,
            "rows": [rows[strike] for strike in sorted(rows)],
            "snapshotNote": snapshot_note,
            "synthetic": bool(getattr(self.feed_manager, "is_synthetic", False)),
        }

    def _contract_view(self, contract, leg) -> Dict[str, Any]:
        live = self.feed_manager.book.get(str(contract.security_id)) or {}
        view = {
            "securityId": str(contract.security_id),
            "tradingSymbol": contract.trading_symbol,
            "lotSize": int(contract.lot_size),
            "tickSize": float(contract.tick_size) if contract.tick_size else None,
            "ltp": live.get("ltp"),
            "bid": live.get("bid"),
            "ask": live.get("ask"),
            "oi": live.get("oi"),
        }
        if leg is not None:
            view.update(
                {
                    "chainLtp": leg.last_price,
                    "chainBid": leg.top_bid_price or None,
                    "chainAsk": leg.top_ask_price or None,
                    "chainOi": leg.oi,
                    "oiChange": leg.oi_change,
                    "volume": leg.volume,
                    "iv": leg.implied_volatility or None,
                    "delta": leg.delta,
                    "theta": leg.theta,
                    "gamma": leg.gamma,
                    "vega": leg.vega,
                    "prevClose": leg.previous_close_price,
                }
            )
        return view

    async def _snapshot(self, underlying: Underlying, expiry: date):
        """(snapshot or None, why none). Never raises: a chain without greeks
        is still a chain an order can be placed from."""
        if getattr(self.feed_manager, "is_synthetic", False):
            return None, "Synthetic feed: no option-chain data is fetched."
        if underlying.chain_scrip is None:
            return None, f"No NSE cash row for {underlying.symbol}; refresh the instrument master."
        if not self.client.has_credentials():
            return None, "No Dhan credentials: greeks, IV and OI are unavailable."
        key = (underlying.chain_scrip, underlying.chain_segment, expiry.isoformat())
        cached = _snapshots.get(key)
        if cached and time.monotonic() - cached[0] < SNAPSHOT_MAX_AGE_SECONDS:
            return cached[1], None
        lock = _snapshot_locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = _snapshots.get(key)
            if cached and time.monotonic() - cached[0] < SNAPSHOT_MAX_AGE_SECONDS:
                return cached[1], None
            try:
                snapshot = await self.client.fetch_option_chain(
                    underlying.chain_scrip, underlying.chain_segment, expiry.isoformat()
                )
            except Exception as error:  # noqa: BLE001 - reported, not fatal
                logger.warning(
                    "Option chain for %s %s unavailable: %s",
                    underlying.symbol, expiry, type(error).__name__,
                )
                stale = _snapshots.get(key)
                if stale:
                    return stale[1], "Showing the last option-chain data; the latest fetch failed."
                return None, "Dhan's option chain could not be fetched just now."
            _snapshots[key] = (time.monotonic(), snapshot)
            return snapshot, None

    def _merge_greeks(self, snapshot, ids) -> None:
        book = self.feed_manager.book
        for _strike, _type, leg in snapshot.legs():
            if not leg.security_id or str(leg.security_id) not in ids:
                continue
            payload = {
                "iv": leg.implied_volatility or None,
                "delta": leg.delta,
                "theta": leg.theta,
                "gamma": leg.gamma,
                "vega": leg.vega,
                "oiChange": leg.oi_change,
                "prevClose": leg.previous_close_price,
                "greeksSource": "dhan",
            }
            book.merge_greeks(
                str(leg.security_id),
                {key: value for key, value in payload.items() if value is not None},
            )

    # --- feed pinning -------------------------------------------------------
    async def _pin(self, ids) -> None:
        """Pin this chain's window to the feed; resync only when it changed."""
        key = self.strategy.key
        wanted = frozenset(ids)
        previous = _views.get(key)
        _views[key] = (wanted, time.monotonic())
        if previous is not None and previous[0] == wanted:
            return
        self.feed_manager.pin_instruments(key, set(wanted))
        await self._resync()
        self._schedule_release(key)

    async def release(self) -> None:
        """The page closed: unpin now rather than waiting for the TTL."""
        key = self.strategy.key
        if _views.pop(key, None) is None:
            return
        self.feed_manager.pin_instruments(key, set())
        await self._resync()

    async def _resync(self) -> None:
        try:
            await self.feed_manager.resync()
        except Exception:  # noqa: BLE001 - a failed resync must not fail the page
            logger.exception("Feed resync after an options chain pin failed")

    def _schedule_release(self, key: str) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        ttl = self.view_ttl_seconds
        manager = self.feed_manager

        def _check() -> None:
            current = _views.get(key)
            if current is None:
                return
            age = time.monotonic() - current[1]
            if age < ttl:
                loop.call_later(ttl - age + 1, _check)
                return
            _views.pop(key, None)
            manager.pin_instruments(key, set())
            logger.info("Options chain for %s not viewed for %.0fs; unpinned", key, age)
            loop.create_task(manager.resync())

        loop.call_later(ttl + 1, _check)


def _nearest(strikes: List[Decimal], spot: float) -> Optional[Decimal]:
    if not strikes:
        return None
    target = Decimal(str(spot))
    return min(strikes, key=lambda strike: (abs(strike - target), strike))


def _window(strikes: List[Decimal], atm: Optional[Decimal], width: int) -> set:
    """ATM +/- `width` strikes. With no spot, the middle of the ladder."""
    if not strikes:
        return set()
    index = strikes.index(atm) if atm in strikes else len(strikes) // 2
    return set(strikes[max(0, index - width): index + width + 1])
