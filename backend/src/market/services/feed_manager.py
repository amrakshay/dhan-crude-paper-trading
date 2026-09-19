"""Process-wide market data orchestration.

Owns the one upstream connection, the one in-memory book and the one
broadcaster, and decides which contracts are worth subscribing to.

**One connection, many strategies.** Dhan allows five concurrent connections
per user and two of them mean a desynchronised book, so every ENABLED strategy
module contributes its targets to the single `DhanFeedClient` rather than
opening one of its own (root CLAUDE.md section 4). A disabled strategy
contributes nothing: no instruments on the wire, no greeks poll, no share of
the connection.

Subscription policy is per strategy: the near-month future, plus ATM +/- N
strikes (`subscription.strike_window`) across the nearest
`subscription.expiries_to_subscribe` option expiries. The full 446-contract
chain would fit inside a single connection, but subscribing to all three listed
expiries by default wastes bandwidth on a series nobody is looking at. Each
strategy's window re-centres itself as its own underlying moves, which is why
the centre and the strike step are per strategy rather than manager-wide.
"""
import asyncio
from dataclasses import dataclass, field
from datetime import date
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
from src.strategies.services.strategy_definition import (
    StrategyDefinition,
    SubscriptionPolicy,
)
from src.strategies.services.strategy_registry import get_strategy_registry

logger = get_logger("market.manager")


@dataclass
class StrategyFeedState:
    """What the feed currently holds for one strategy.

    These were scalars on the FeedManager when there was exactly one
    underlying. They are per strategy because two strategies have two front
    futures, two strike ladders and two window centres, and collapsing them
    would re-centre one strategy's window on another's price.
    """

    key: str
    near_future_security_id: Optional[str] = None
    subscribed_expiries: List[date] = field(default_factory=list)
    window_centre: Optional[Decimal] = None
    strike_step: Optional[Decimal] = None
    instrument_count: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "strategyKey": self.key,
            "nearFutureSecurityId": self.near_future_security_id,
            "subscribedExpiries": [
                expiry.isoformat() for expiry in self.subscribed_expiries
            ],
            "windowCentre": float(self.window_centre) if self.window_centre else None,
            "strikeStep": float(self.strike_step) if self.strike_step else None,
            "instrumentCount": self.instrument_count,
        }


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

        self.strategy_state: Dict[str, StrategyFeedState] = {}
        # Per strategy, security ids kept subscribed on top of whatever it
        # holds. See pin_instruments().
        self._pinned: Dict[str, Set[str]] = {}
        # Per universe strategy, whether its subscription window was open on
        # the last pass. The resync loop is EDGE-triggered off this, so a
        # window opening costs one resync rather than one every five seconds
        # for as long as it stays open.
        self._window_open: Dict[str, bool] = {}
        self._contract_meta: Dict[str, Dict[str, Any]] = {}
        self.last_resync_ms: Optional[int] = None
        self.last_error: Optional[str] = None

    # --- per-strategy state ------------------------------------------------
    def state_for(self, strategy_key: str) -> StrategyFeedState:
        """The feed's state for one strategy, created on first use."""
        state = self.strategy_state.get(strategy_key)
        if state is None:
            state = StrategyFeedState(key=strategy_key)
            self.strategy_state[strategy_key] = state
        return state

    def _primary_state(self) -> StrategyFeedState:
        """State for the first running strategy.

        Single-strategy callers -- the status payload, the synthetic greeks --
        still ask for "the" front future. With one module running that is
        unambiguous; with several they should be asking per strategy, which is
        what `state_for` is for.
        """
        for strategy in get_strategy_registry().enabled():
            state = self.strategy_state.get(strategy.key)
            if state is not None:
                return state
        return StrategyFeedState(key="")

    @property
    def near_future_security_id(self) -> Optional[str]:
        return self._primary_state().near_future_security_id

    @property
    def subscribed_expiries(self) -> List[date]:
        return list(self._primary_state().subscribed_expiries)

    # --- configuration -----------------------------------------------------
    @staticmethod
    def _enabled() -> bool:
        return config_utils.get_property_value_boolean("market_feed.enabled", True)

    @staticmethod
    def _synthetic_enabled() -> bool:
        return config_utils.get_property_value_boolean("market_feed.synthetic_feed", False)

    @staticmethod
    def _strategies() -> List[StrategyDefinition]:
        """Every running strategy. A disabled one contributes nothing at all."""
        return get_strategy_registry().enabled()

    # --- market hours ------------------------------------------------------
    @classmethod
    def market_status(cls, strategy: Optional[StrategyDefinition] = None) -> Dict[str, Any]:
        """Whether the strategy's exchange should be open right now.

        MCX runs 09:00-23:30 IST Mon-Fri; NSE runs 09:15-15:30, which is why
        the hours belong to the strategy module and not to this file.

        Informational only: it never gates the feed. If MCX runs late during US
        DST the feed still delivers, and the UI shows "outside market hours"
        rather than pretending the book is stale for a reason it is not.
        """
        registry = get_strategy_registry()
        if strategy is None:
            running = registry.enabled()
            strategy = running[0] if running else registry.default()

        hours = strategy.market_hours
        now = ist_now()
        trading_days = {int(day) for day in hours.trading_days}

        is_trading_day = now.weekday() in trading_days
        is_open = is_trading_day and hours.open <= now.time() <= hours.close
        return {
            "isOpen": is_open,
            "isTradingDay": is_trading_day,
            "nowIst": now.isoformat(),
            "opens": hours.open.strftime("%H:%M"),
            "closes": hours.close.strftime("%H:%M"),
            "strategyKey": strategy.key,
        }

    @classmethod
    def market_status_by_strategy(cls) -> Dict[str, Dict[str, Any]]:
        """Market hours for every running strategy, keyed by strategy."""
        return {
            strategy.key: cls.market_status(strategy)
            for strategy in get_strategy_registry().enabled()
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
            logger.info(
                "Starting the SYNTHETIC feed -- prices are generated locally and "
                "are not market data (market_feed.synthetic_feed=true)"
            )
        elif DhanFeedClient.has_credentials():
            self.is_synthetic = False
            self.feed = DhanFeedClient(self.book, on_state_change=self._on_state_change)
            logger.info(
                "Starting the live Dhan feed: mode=%s strategies=%s",
                config_utils.get_property_value("market_feed.mode", "FULL"),
                [strategy.key for strategy in self._strategies()],
            )
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
        await self.broadcaster.start()   # no-op if already running
        await self.feed.start()
        await self.resync()
        # Separate task on its own cadence; it must never sit on the tick path.
        await self.greeks_poller.start(is_synthetic=self.is_synthetic)

        self._resync_task = asyncio.create_task(self._resync_loop(), name="feed-resync")
        logger.info(
            "Market feed started: synthetic=%s instruments=%s near_future=%s",
            self.is_synthetic,
            self.feed.subscribed_count,
            self.near_future_security_id,
        )

    async def stop(self) -> None:
        logger.info("Stopping the market feed")
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
        logger.info("Market feed stopped")

    async def reconfigure(self) -> Dict[str, Any]:
        """Rebuild the feed after a settings change, keeping browser clients.

        The broadcaster and its registered WebSocket clients are deliberately
        NOT recreated -- replacing the FeedManager wholesale would silently
        strand every open browser tab on a dead broadcaster. Only the upstream
        client, the book and the greeks poller are torn down and rebuilt.
        """
        logger.info("Reconfiguring the market feed after a settings change")

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
            self.feed = None

        # Prices from the previous mode must not linger: a synthetic price left
        # in the book after switching to live data would be indistinguishable
        # from a real one.
        self.book.clear()
        # Cached candle history was fetched under the previous credentials and
        # the previous synthetic setting, so it is discarded for the same
        # reason the book is: a synthetic bar must not survive into live mode.
        from src.market.services.candle_service import get_candle_service

        get_candle_service().invalidate()
        # Every strategy's window centre and strike step are dropped with the
        # book they were derived from.
        self.strategy_state = {}
        self._contract_meta = {}
        self.last_error = None
        self._started = False
        self._stopping = False

        await self.start()
        return self.status()

    def _on_state_change(self, state: ConnectionState, detail: Optional[str]) -> None:
        # The feed client logs the transition itself in _set_state; this only
        # has to push it out to the browsers.
        # Fire and forget: the feed's state callback must never await.
        try:
            asyncio.get_running_loop().create_task(
                self.broadcaster.send_status(self.status())
            )
        except RuntimeError:
            pass

    # --- subscription management ------------------------------------------
    async def _resolve_targets(self) -> Tuple[List[Tuple[str, str]], Dict[str, Dict[str, Any]]]:
        """Which contracts should be subscribed right now, across strategies.

        A loop over the running strategies, each contributing to the ONE
        connection. A strategy that is off appears nowhere in the result, which
        is what makes "off" free rather than merely hidden.
        """
        targets: List[Tuple[str, str]] = []
        meta: Dict[str, Dict[str, Any]] = {}
        strategies = self._strategies()

        if not strategies:
            logger.warning(
                "No strategy module is enabled; nothing will be subscribed. "
                "Enable one on the Strategies & Features page."
            )
            self.strategy_state = {}
            return [], {}

        async with session_scope() as session:
            repository = InstrumentRepository(session)
            for strategy in strategies:
                strategy_targets, strategy_meta = await self._resolve_strategy_targets(
                    strategy, repository
                )
                targets.extend(strategy_targets)
                meta.update(strategy_meta)

        # A strategy switched off since the last resync keeps no state: its
        # window centre and front future are gone with its subscription.
        for key in list(self.strategy_state):
            if key not in {strategy.key for strategy in strategies}:
                self.strategy_state.pop(key, None)

        logger.debug(
            "Subscription target set: %s contracts across %s strateg%s",
            len(targets), len(strategies), "y" if len(strategies) == 1 else "ies",
        )
        return targets, meta

    async def _resolve_strategy_targets(
        self, strategy: StrategyDefinition, repository: InstrumentRepository
    ) -> Tuple[List[Tuple[str, str]], Dict[str, Dict[str, Any]]]:
        """One strategy's contribution to the single upstream connection."""
        if strategy.subscription.kind == SubscriptionPolicy.UNIVERSE:
            return await self._resolve_universe_targets(strategy, repository)
        if strategy.subscription.kind == SubscriptionPolicy.POSITIONS:
            return await self._resolve_position_targets(strategy, repository)
        return await self._resolve_option_chain_targets(strategy, repository)

    async def _resolve_universe_targets(
        self, strategy: StrategyDefinition, repository: InstrumentRepository
    ) -> Tuple[List[Tuple[str, str]], Dict[str, Dict[str, Any]]]:
        """Every tradable name the strategy claims -- but only in its window.

        THIS INVERTS `_resolve_position_targets`' REASONING, and both are right
        about their own strategy. A rotation decides overnight on daily bars
        fetched over REST, so subscribing its universe would spend the one
        connection's budget on instruments nobody reads. BTST decides at 15:20
        on the session's own running high, low and cumulative volume for every
        candidate at once -- for which the feed is not an optimisation, it is
        the only mechanism that fits in the window. The specification's own
        section 8 measures the alternative: REST-polling ~480 symbols at Dhan's
        0.6 s rate limit takes about five minutes.

        **Outside the window this behaves exactly like `positions`**, which is
        what keeps the cost bounded: the universe joins the feed in the
        afternoon and leaves again after the scan, and the rest of the day the
        strategy subscribes only what it holds. Dhan's packet carries the
        session AGGREGATE rather than a delta, so a subscription opened at
        14:45 should still report the whole session -- a claim that is
        UNVERIFIED against a live feed and that
        `scripts/verify_feed_session_fields.py` exists to settle. If it turns
        out to be false, the fix is to widen the window in the YAML, not to
        change this.

        The window is evaluated per resync rather than scheduled, so nothing
        new runs on a clock: the feed already resyncs, and this simply answers
        differently depending on the time.
        """
        from src.core.time_utils import ist_now

        state = self.state_for(strategy.key)
        held, meta = await self._resolve_position_targets(strategy, repository)

        if not strategy.subscription.window_is_open(ist_now().time()):
            logger.debug(
                "Universe window closed for %s; subscribing %s held/pinned "
                "instrument(s) only.",
                strategy.key, len(held),
            )
            return held, meta

        if strategy.universe is None:
            logger.warning(
                "Strategy %s declares a universe subscription and no universe; "
                "subscribing what it holds instead.",
                strategy.key,
            )
            return held, meta

        targets: List[Tuple[str, str]] = list(held)
        seen = {security_id for _, security_id in held}
        instruments = await repository.list_by_symbols(
            sorted(strategy.universe.symbols), strategy.exchange_segment
        )
        for instrument in instruments:
            if not instrument.is_active:
                continue
            security_id = str(instrument.security_id)
            if security_id in seen:
                continue
            seen.add(security_id)
            targets.append((instrument.exchange_segment, security_id))
            meta[security_id] = self._meta_for(instrument, strategy)

        state.near_future_security_id = None
        state.subscribed_expiries = []
        state.instrument_count = len(targets)
        logger.info(
            "Universe window open for %s: %s instrument(s) subscribed (%s "
            "held or pinned). The window is %s-%s IST.",
            strategy.key, len(targets), len(held),
            strategy.subscription.window_opens_at,
            strategy.subscription.window_closes_at,
        )
        return targets, meta

    async def _resolve_position_targets(
        self, strategy: StrategyDefinition, repository: InstrumentRepository
    ) -> Tuple[List[Tuple[str, str]], Dict[str, Dict[str, Any]]]:
        """What a cash strategy needs live: what it HOLDS, and what it is about
        to trade.

        A rotation takes its decisions from daily bars fetched over REST, so
        the live feed is needed only to price the book and to fill an order.
        Subscribing the whole universe would spend the one connection's budget
        on hundreds of instruments nothing reads.

        `pin_instruments()` is how a rebalance adds the handful of candidates
        it is about to buy, a few minutes ahead of placing the orders -- the
        fill simulator needs a depth book for a name BEFORE the order, not
        after it.
        """
        state = self.state_for(strategy.key)
        targets: List[Tuple[str, str]] = []
        meta: Dict[str, Dict[str, Any]] = {}

        # Imported here rather than at module scope: src.positions imports the
        # orders package, which reads the strategy registry (backend CLAUDE.md
        # section 1).
        from src.positions.database.db_operations.position_repository import (
            PositionRepository,
        )

        positions = await PositionRepository(repository.session).list_all(
            include_closed=False, strategy_key=strategy.key
        )
        wanted = {position.security_id for position in positions}
        wanted.update(self._pinned.get(strategy.key, set()))

        for instrument in await repository.get_many_by_security_ids(sorted(wanted)):
            if not instrument.is_active:
                continue
            targets.append((instrument.exchange_segment, instrument.security_id))
            meta[instrument.security_id] = self._meta_for(instrument, strategy)

        state.near_future_security_id = None
        state.subscribed_expiries = []
        state.instrument_count = len(targets)
        logger.debug(
            "Resolved %s live instruments for %s (%s held, %s pinned)",
            len(targets), strategy.key, len(positions),
            len(self._pinned.get(strategy.key, set())),
        )
        return targets, meta

    def pin_instruments(self, strategy_key: str, security_ids: Set[str]) -> None:
        """Keep these subscribed for a `positions` strategy until unpinned.

        Used by a rebalance to warm the book for names it is about to buy.
        Pinning does not itself subscribe anything -- call `resync()` after.
        """
        if security_ids:
            self._pinned[str(strategy_key)] = set(str(one) for one in security_ids)
        else:
            self._pinned.pop(str(strategy_key), None)

    def pinned_instruments(self, strategy_key: str) -> Set[str]:
        return set(self._pinned.get(str(strategy_key), set()))

    async def _resolve_option_chain_targets(
        self, strategy: StrategyDefinition, repository: InstrumentRepository
    ) -> Tuple[List[Tuple[str, str]], Dict[str, Dict[str, Any]]]:
        """One strategy's contribution: its front future plus its ATM window."""
        segment = strategy.exchange_segment
        window = strategy.subscription.strike_window
        expiry_count = strategy.subscription.expiries_to_subscribe
        state = self.state_for(strategy.key)

        targets: List[Tuple[str, str]] = []
        meta: Dict[str, Dict[str, Any]] = {}

        chain_service = ChainService(repository, strategy)

        near_future = await chain_service.get_near_future()
        if near_future is None:
            logger.warning(
                "No %s futures contract in the database for strategy %s. Refresh "
                "the instrument master (POST /api/instruments/refresh).",
                strategy.symbol, strategy.key,
            )
            state.near_future_security_id = None
            state.instrument_count = 0
            return [], {}

        state.near_future_security_id = near_future.security_id
        targets.append((segment, near_future.security_id))
        meta[near_future.security_id] = self._meta_for(near_future, strategy)

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
        state.subscribed_expiries = expiries

        for expiry in expiries:
            strikes = await chain_service.list_strikes(expiry)
            if state.strike_step is None:
                state.strike_step = chain_service.infer_strike_step(strikes)

            contracts = await chain_service.get_strike_window(expiry, spot, window)
            for contract in contracts:
                targets.append((segment, contract.security_id))
                meta[contract.security_id] = self._meta_for(contract, strategy)

            if spot is not None:
                state.window_centre = chain_service.resolve_atm_strike(strikes, spot)
            logger.debug(
                "Resolved %s contracts for %s expiry %s (strikes=%s step=%s spot=%s)",
                len(contracts), strategy.key, expiry, len(strikes),
                state.strike_step, spot,
            )

        state.instrument_count = len(targets)
        return targets, meta

    @staticmethod
    def _meta_for(
        instrument, strategy: Optional[StrategyDefinition] = None
    ) -> Dict[str, Any]:
        """Static contract details attached to a book row.

        The strategy key travels with the contract metadata so a consumer can
        tell which module a row belongs to WITHOUT a lookup per packet. This is
        resolved once, when the subscription is built.
        """
        return {
            "securityId": instrument.security_id,
            "strategyKey": strategy.key if strategy else None,
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
        if not targets and self._strategies():
            # Strategies ARE running but resolved nothing, which means the
            # instrument master has not been ingested. Unsubscribing everything
            # here would empty a book that is only temporarily unresolvable, so
            # the live subscription is left alone.
            logger.warning(
                "Feed resync found no instruments to subscribe to; the book will "
                "stay empty until the instrument master is refreshed"
            )
            return {"subscribed": 0, "unsubscribed": 0, "reason": "no instruments available"}

        if not targets:
            # NOTHING is running. This is the case where unsubscribing
            # everything is exactly right: freeing the instruments on the shared
            # connection is the whole point of switching a strategy off, and a
            # subscription nobody is looking at still costs bandwidth and still
            # counts against the connection.
            current = self.feed.subscribed_security_ids()
            removed = 0
            if current:
                to_remove = [
                    (self._segment_for_subscribed(security_id), security_id)
                    for security_id in current
                ]
                removed = await self.feed.unsubscribe(to_remove)
                self.book.forget(security_id for _segment, security_id in to_remove)
            self._contract_meta = {}
            self.last_resync_ms = now_ms()
            logger.info(
                "No strategy is enabled; unsubscribed %s instrument(s). The feed "
                "connection stays open and the browsers stay connected.",
                removed,
            )
            return {
                "subscribed": 0,
                "unsubscribed": removed,
                "reason": "no strategy is enabled",
            }

        self._contract_meta = meta
        self.book.register_many(meta)
        if isinstance(self.feed, SyntheticFeed):
            # The synthetic generator needs strikes and expiries to price against.
            self.feed.set_contracts(meta)

        current = self.feed.subscribed_security_ids()
        wanted = {security_id for _segment, security_id in targets}

        to_add = [(segment, sid) for segment, sid in targets if sid not in current]
        # Unsubscribing needs the segment the contract was subscribed UNDER,
        # which with more than one strategy is no longer a single value. The
        # feed client remembers it; falling back to the first running
        # strategy's segment keeps single-strategy behaviour identical.
        to_remove = [
            (self._segment_for_subscribed(security_id), security_id)
            for security_id in current
            if security_id not in wanted
        ]

        logger.debug(
            "Subscription diff: current=%s wanted=%s add=%s remove=%s",
            len(current), len(wanted),
            [security_id for _segment, security_id in to_add],
            [security_id for _segment, security_id in to_remove],
        )

        removed = await self.feed.unsubscribe(to_remove) if to_remove else 0
        added = await self.feed.subscribe(to_add) if to_add else 0
        if to_remove:
            self.book.forget(security_id for _segment, security_id in to_remove)

        self.last_resync_ms = now_ms()
        if added or removed:
            logger.info(
                "Feed resync: +%s -%s (now %s instruments, centre=%s)",
                added, removed, self.feed.subscribed_count,
                {
                    key: float(state.window_centre) if state.window_centre else None
                    for key, state in self.strategy_state.items()
                },
            )
        return {"subscribed": added, "unsubscribed": removed}

    def _segment_for_subscribed(self, security_id: str) -> str:
        """The exchange segment a currently-subscribed contract belongs to."""
        meta = self._contract_meta.get(security_id) or self.book.get(security_id) or {}
        key = meta.get("strategyKey")
        if key:
            strategy = get_strategy_registry().get(key)
            if strategy is not None:
                return strategy.exchange_segment
        running = self._strategies()
        if running:
            return running[0].exchange_segment
        return get_strategy_registry().default().exchange_segment

    def _universe_window_changed(self) -> Optional[str]:
        """Has a universe strategy's subscription window just opened or shut?

        Returns the strategy key that moved, or None.

        **Without this a universe subscription never happens at all.** The loop
        below only ever resyncs an OPTION-CHAIN strategy, because it is
        watching a front future drift away from a strike window -- a
        `positions` strategy has no such centre and is resynced by the events
        that change what it holds (a fill, a toggle, a rebalance pinning
        names). A `universe` strategy is neither: what changes is THE TIME, and
        nothing else in this application was watching a clock on the feed's
        behalf. BTST's window would have opened at 14:45 with nobody noticing.

        Edge-triggered, not level-triggered: it resyncs on the TRANSITION, so
        the window opening costs one resync rather than one every five seconds
        for the fifty minutes it is open.
        """
        from src.core.time_utils import ist_now

        now = ist_now().time()
        for strategy in self._strategies():
            if strategy.subscription.kind != SubscriptionPolicy.UNIVERSE:
                continue
            open_now = strategy.subscription.window_is_open(now)
            if self._window_open.get(strategy.key) != open_now:
                self._window_open[strategy.key] = open_now
                return strategy.key
        return None

    async def _resync_loop(self) -> None:
        """Re-centre each strategy's strike window as its underlying moves, and
        open or shut a universe strategy's subscription window on time.

        Only resubscribes once a move is material (default 3 strikes), so a
        price oscillating around a strike boundary does not churn the
        subscription on every tick. One strategy drifting is enough to trigger
        a resync, which recomputes every running strategy's targets -- the diff
        against the live subscription makes that cheap.
        """
        while not self._stopping:
            await asyncio.sleep(5)
            try:
                if self.feed is None:
                    continue

                moved = self._universe_window_changed()
                if moved is not None:
                    logger.info(
                        "%s's subscription window %s; resyncing.",
                        moved,
                        "opened" if self._window_open.get(moved) else "closed",
                    )
                    await self.resync()
                    continue

                for strategy in self._strategies():
                    state = self.strategy_state.get(strategy.key)
                    if state is None or state.near_future_security_id is None:
                        continue

                    future_row = self.book.get(state.near_future_security_id)
                    if not future_row or not future_row.get("ltp"):
                        continue
                    if state.window_centre is None or state.strike_step is None:
                        await self.resync()
                        break

                    spot = Decimal(str(future_row["ltp"]))
                    drift_strikes = abs(spot - state.window_centre) / state.strike_step
                    if drift_strikes >= strategy.subscription.resubscribe_move_strikes:
                        logger.info(
                            "%s moved %.1f strikes from its window centre; resyncing",
                            strategy.key, float(drift_strikes),
                        )
                        await self.resync()
                        break
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Feed resync loop error")

    # --- status ------------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        primary = self._primary_state()
        running = self._strategies()
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
            "marketByStrategy": self.market_status_by_strategy(),
            # The single-strategy fields stay, resolved against the first
            # running strategy, so every existing client keeps working. A
            # multi-strategy client reads `strategies` instead.
            "nearFutureSecurityId": primary.near_future_security_id,
            "subscribedExpiries": [
                expiry.isoformat() for expiry in primary.subscribed_expiries
            ],
            "strikeWindow": (
                running[0].subscription.strike_window if running else None
            ),
            "windowCentre": (
                float(primary.window_centre) if primary.window_centre else None
            ),
            "strategies": [
                self.strategy_state[strategy.key].as_dict()
                for strategy in running
                if strategy.key in self.strategy_state
            ],
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
