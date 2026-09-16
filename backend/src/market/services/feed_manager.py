"""Process-wide market data orchestration.

Owns the one upstream connection, the one in-memory book and the one
broadcaster, and decides which contracts are worth subscribing to.

Subscription policy: the near-month future, plus ATM +/- N strikes (config
`market_feed.strike_window`, default 20) across the nearest `expiries_to_subscribe`
option expiries. The full 446-contract chain would fit inside a single
connection, but subscribing to all three listed expiries by default wastes
bandwidth on a series nobody is looking at. The window re-centres itself as the
underlying moves.
"""
import asyncio
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

from src import config_utils
from src.constants import ConnectionState
from src.core.time_utils import ist_now, parse_hhmm
from src.database.session import session_scope
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.instruments.services.chain_service import ChainService
from src.logging_config import get_logger
from src.market.services.broadcaster import Broadcaster
from src.market.services.dhan_feed_client import DhanFeedClient
from src.market.services.greeks_poller import GreeksPoller
from src.market.services.market_book import MarketBook, now_ms
from src.market.services.synthetic_feed import SyntheticFeed

logger = get_logger("market.manager")


class FeedManager:
    """The single orchestrator. Use `get_feed_manager()`, never construct twice."""

    def __init__(self) -> None:
        self.book = MarketBook()
        self.broadcaster = Broadcaster(self.book)
        self.feed = None
        self.greeks_poller = GreeksPoller(self.book, self)
        self.is_synthetic = False

        self._started = False
        self._resync_task: Optional[asyncio.Task] = None
        self._stopping = False

        self.near_future_security_id: Optional[str] = None
        self.subscribed_expiries: List[Any] = []
        self._window_centre: Optional[Decimal] = None
        self._strike_step: Optional[Decimal] = None
        self._contract_meta: Dict[str, Dict[str, Any]] = {}
        self.last_resync_ms: Optional[int] = None
        self.last_error: Optional[str] = None

    # --- configuration -----------------------------------------------------
    @staticmethod
    def _enabled() -> bool:
        return config_utils.get_property_value_boolean("market_feed.enabled", True)

    @staticmethod
    def _synthetic_enabled() -> bool:
        return config_utils.get_property_value_boolean("market_feed.synthetic_feed", False)

    @staticmethod
    def _strike_window() -> int:
        return config_utils.get_property_value_int("market_feed.strike_window", 20)

    @staticmethod
    def _expiry_count() -> int:
        return config_utils.get_property_value_int("market_feed.expiries_to_subscribe", 2)

    @staticmethod
    def _resubscribe_move_strikes() -> int:
        return config_utils.get_property_value_int("market_feed.resubscribe_move_strikes", 3)

    @staticmethod
    def _exchange_segment() -> str:
        return config_utils.get_property_value("underlying.exchange_segment", "MCX_COMM")

    # --- market hours ------------------------------------------------------
    @classmethod
    def market_status(cls) -> Dict[str, Any]:
        """Whether MCX should be open right now (09:00-23:30 IST, Mon-Fri).

        Informational only: it never gates the feed. If MCX runs late during US
        DST the feed still delivers, and the UI shows "outside market hours"
        rather than pretending the book is stale for a reason it is not.
        """
        now = ist_now()
        open_time = parse_hhmm(config_utils.get_property_value("market_hours.open", "09:00"))
        close_time = parse_hhmm(config_utils.get_property_value("market_hours.close", "23:30"))
        trading_days = config_utils.get_property_value_list("market_hours.trading_days", [0, 1, 2, 3, 4])
        trading_days = {int(day) for day in trading_days}

        is_trading_day = now.weekday() in trading_days
        is_open = is_trading_day and open_time <= now.time() <= close_time
        return {
            "isOpen": is_open,
            "isTradingDay": is_trading_day,
            "nowIst": now.isoformat(),
            "opens": open_time.strftime("%H:%M"),
            "closes": close_time.strftime("%H:%M"),
        }

    # --- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stopping = False

        if not self._enabled():
            logger.warning("Market feed is disabled (market_feed.enabled=false)")
            return

        if self._synthetic_enabled():
            self.is_synthetic = True
            self.feed = SyntheticFeed(self.book, on_state_change=self._on_state_change)
        elif DhanFeedClient.has_credentials():
            self.is_synthetic = False
            self.feed = DhanFeedClient(self.book, on_state_change=self._on_state_change)
        else:
            # Never silently invent prices. If the operator wanted fake data
            # they would have set the flag.
            self.last_error = (
                "No Dhan market-data credentials (DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN) "
                "and DHAN_SYNTHETIC_FEED is false. The feed is not running."
            )
            logger.error(self.last_error)
            self.broadcaster.set_status_provider(self.status)
            await self.broadcaster.start()
            return

        self.broadcaster.set_status_provider(self.status)
        await self.broadcaster.start()
        await self.feed.start()
        await self.resync()
        # Separate task on its own cadence; it must never sit on the tick path.
        await self.greeks_poller.start(is_synthetic=self.is_synthetic)

        self._resync_task = asyncio.create_task(self._resync_loop(), name="feed-resync")

    async def stop(self) -> None:
        self._stopping = True
        if self._resync_task is not None and not self._resync_task.done():
            self._resync_task.cancel()
            try:
                await self._resync_task
            except (asyncio.CancelledError, Exception):
                pass
        self._resync_task = None

        await self.greeks_poller.stop()
        if self.feed is not None:
            await self.feed.stop()
        await self.broadcaster.stop()
        self._started = False

    def _on_state_change(self, state: ConnectionState, detail: Optional[str]) -> None:
        # Fire and forget: the feed's state callback must never await.
        try:
            asyncio.get_running_loop().create_task(
                self.broadcaster.send_status(self.status())
            )
        except RuntimeError:
            pass

    # --- subscription management ------------------------------------------
    async def _resolve_targets(self) -> Tuple[List[Tuple[str, str]], Dict[str, Dict[str, Any]]]:
        """Which contracts should be subscribed right now."""
        segment = self._exchange_segment()
        window = self._strike_window()
        expiry_count = self._expiry_count()

        targets: List[Tuple[str, str]] = []
        meta: Dict[str, Dict[str, Any]] = {}

        async with session_scope() as session:
            repository = InstrumentRepository(session)
            chain_service = ChainService(repository)

            near_future = await chain_service.get_near_future()
            if near_future is None:
                logger.warning(
                    "No CRUDEOIL futures contract in the database. Refresh the "
                    "instrument master (POST /api/instruments/refresh)."
                )
                return [], {}

            self.near_future_security_id = near_future.security_id
            targets.append((segment, near_future.security_id))
            meta[near_future.security_id] = self._meta_for(near_future)

            # The future's own LTP is what centres the strike window.
            future_row = self.book.get(near_future.security_id)
            spot = None
            if future_row and future_row.get("ltp"):
                spot = Decimal(str(future_row["ltp"]))
            elif isinstance(self.feed, SyntheticFeed):
                # Before the first tick there is no LTP. In synthetic mode the
                # generator's own price is known, so use it rather than
                # defaulting to the middle of a 2850-13950 ladder and then
                # resubscribing a moment later.
                spot = Decimal(str(self.feed.reference_price))

            expiries = await chain_service.nearest_option_expiries(expiry_count)
            self.subscribed_expiries = expiries

            for expiry in expiries:
                strikes = await chain_service.list_strikes(expiry)
                if self._strike_step is None:
                    self._strike_step = chain_service.infer_strike_step(strikes)

                contracts = await chain_service.get_strike_window(expiry, spot, window)
                for contract in contracts:
                    targets.append((segment, contract.security_id))
                    meta[contract.security_id] = self._meta_for(contract)

                if spot is not None:
                    self._window_centre = chain_service.resolve_atm_strike(strikes, spot)

        return targets, meta

    @staticmethod
    def _meta_for(instrument) -> Dict[str, Any]:
        """Static contract details attached to a book row."""
        return {
            "securityId": instrument.security_id,
            "tradingSymbol": instrument.trading_symbol,
            "instrumentType": instrument.instrument_type,
            "optionType": instrument.option_type,
            "strikePrice": float(instrument.strike_price) if instrument.strike_price else None,
            "expiryDate": instrument.expiry_date.isoformat() if instrument.expiry_date else None,
            "lotSize": instrument.lot_size,
            "tickSize": float(instrument.tick_size) if instrument.tick_size else None,
        }

    async def resync(self) -> Dict[str, Any]:
        """Recompute the target set and reconcile subscriptions against it."""
        if self.feed is None:
            return {"subscribed": 0, "unsubscribed": 0, "reason": "feed not running"}

        targets, meta = await self._resolve_targets()
        if not targets:
            return {"subscribed": 0, "unsubscribed": 0, "reason": "no instruments available"}

        self._contract_meta = meta
        self.book.register_many(meta)
        if isinstance(self.feed, SyntheticFeed):
            # The synthetic generator needs strikes and expiries to price against.
            self.feed.set_contracts(meta)

        current = self.feed.subscribed_security_ids()
        wanted = {security_id for _segment, security_id in targets}

        to_add = [(segment, sid) for segment, sid in targets if sid not in current]
        to_remove = [
            (self._exchange_segment(), security_id)
            for security_id in current
            if security_id not in wanted
        ]

        removed = await self.feed.unsubscribe(to_remove) if to_remove else 0
        added = await self.feed.subscribe(to_add) if to_add else 0
        if to_remove:
            self.book.forget(security_id for _segment, security_id in to_remove)

        self.last_resync_ms = now_ms()
        if added or removed:
            logger.info(
                "Feed resync: +%s -%s (now %s instruments, centre=%s)",
                added, removed, self.feed.subscribed_count, self._window_centre,
            )
        return {"subscribed": added, "unsubscribed": removed}

    async def _resync_loop(self) -> None:
        """Re-centre the strike window as the underlying moves.

        Only resubscribes once the move is material (default 3 strikes), so a
        price oscillating around a strike boundary does not churn the
        subscription on every tick.
        """
        while not self._stopping:
            await asyncio.sleep(5)
            try:
                if self.feed is None or self.near_future_security_id is None:
                    continue

                future_row = self.book.get(self.near_future_security_id)
                if not future_row or not future_row.get("ltp"):
                    continue
                if self._window_centre is None or self._strike_step is None:
                    await self.resync()
                    continue

                spot = Decimal(str(future_row["ltp"]))
                drift_strikes = abs(spot - self._window_centre) / self._strike_step
                if drift_strikes >= self._resubscribe_move_strikes():
                    logger.info(
                        "Underlying moved %.1f strikes from the window centre; resyncing",
                        float(drift_strikes),
                    )
                    await self.resync()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Feed resync loop error")

    # --- status ------------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        feed_status = (
            self.feed.status()
            if self.feed is not None
            else {"state": ConnectionState.DISABLED.value, "detail": self.last_error}
        )
        return {
            "feed": feed_status,
            "greeks": self.greeks_poller.status(),
            "synthetic": self.is_synthetic,
            "book": self.book.stats(),
            "fanout": self.broadcaster.stats(),
            "market": self.market_status(),
            "nearFutureSecurityId": self.near_future_security_id,
            "subscribedExpiries": [
                expiry.isoformat() for expiry in self.subscribed_expiries
            ],
            "strikeWindow": self._strike_window(),
            "windowCentre": float(self._window_centre) if self._window_centre else None,
            "lastResyncMs": self.last_resync_ms,
            "error": self.last_error,
        }


_manager: Optional[FeedManager] = None


def get_feed_manager() -> FeedManager:
    """The one FeedManager for this process."""
    global _manager
    if _manager is None:
        _manager = FeedManager()
    return _manager


async def shutdown_feed_manager() -> None:
    global _manager
    if _manager is not None:
        await _manager.stop()
        _manager = None
