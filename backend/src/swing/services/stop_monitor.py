"""Server-side watcher for the rotation's chandelier stops.

A sibling of `chart_trading/bracket_monitor.py`, NOT a second shape bolted onto
it. That one watches a FUTURE and closes an OPTION from a `chart_trades` row;
this one watches a stock and closes the same stock from a `swing_stops` row.
The rules they share are the ones that matter, and they are kept identical
deliberately:

* its own asyncio task, its own database session, **off the tick path**;
* it never acts on a `None` price;
* a disabled strategy's stops are not watched -- a real consequence of
  switching one off, which the toggle warns about;
* exits go through `submit_paper_order` like everything else, so they cross the
  spread and can partially fill. No privileged fill path.

Two rules that are this monitor's own.

**It only acts while the market is open.** Between the close and the next open
the book holds the session's last prices, and a stop evaluated against one of
those would fire on a price nobody can trade at -- every evening, on the same
stale tick. `market_clock` answers that from the strategy's own hours.

**The Closing Auction Session.** Live since 3 August 2026: for F&O-eligible
names, continuous cash trading ends at 15:15 and a call auction runs to 15:35.
An exit sent in that window would land in an auction this simulator has no
model of, so the trigger is RECORDED and the order waits for the next session's
open. The backtest predates the CAS entirely and assumes a continuous market to
the close; letting the exit fill here anyway would be flattering it silently.
"""
import asyncio
from decimal import Decimal
from typing import Any, Dict, Optional

from src import config_utils
from src.constants import OrderSide, OrderType
from src.database.session import session_scope
from src.logging_config import get_logger
from src.strategies.services import market_clock
from src.swing.database.db_models.swing_stop_model import SwingStop
from src.swing.database.db_operations.swing_stop_repository import SwingStopRepository

logger = get_logger("swing.stop_monitor")

TASK_NAME = "swing-stop-monitor"


class SwingStopMonitor:
    """Watches every active chandelier stop and exits the ones that are hit."""

    def __init__(self, book=None) -> None:
        self._book = book
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self.runs = 0
        self.triggers = 0
        self.exits_placed = 0
        self.deferred_to_auction = 0
        self.last_error: Optional[str] = None
        # What the LAST PASS saw, for the Live tab's status strip. Counted
        # while the pass is walking rows it has already read, never measured
        # separately and never on the tick path.
        #
        # None is not zero anywhere here: "no pass has run" and "the monitor
        # ran and is watching nothing" are different states and the page has to
        # be able to say which.
        self.last_pass_at: Optional[str] = None
        self.watching: Optional[int] = None
        self.unprotected: Optional[int] = None
        self.unmarked: Optional[int] = None
        self.nearest_symbol: Optional[str] = None
        self.nearest_percent: Optional[float] = None

    # --- configuration -----------------------------------------------------
    @staticmethod
    def _interval_seconds() -> float:
        """Slower than the bracket monitor's 250 ms, and deliberately so.

        A chandelier stop sits 9-14% below the highest close. Nothing about it
        needs sub-second resolution, and a cash-equity book of ten names is not
        worth four database sessions a second.
        """
        return config_utils.get_property_value_int(
            "swing.stop_interval_ms", 1000
        ) / 1000.0

    @staticmethod
    def _enabled() -> bool:
        return config_utils.get_property_value_boolean("swing.stops_enabled", True)

    @property
    def book(self):
        if self._book is None:
            from src.market.services.feed_manager import get_feed_manager

            self._book = get_feed_manager().book
        return self._book

    # --- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        if not self._enabled():
            logger.info(
                "Swing trailing stops are disabled (swing.stops_enabled=false); "
                "no chandelier stop will fire."
            )
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name=TASK_NAME)
        logger.info(
            "Swing stop monitor started (every %.0f ms)",
            self._interval_seconds() * 1000,
        )

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None

    async def _run(self) -> None:
        interval = self._interval_seconds()
        while not self._stopping:
            await asyncio.sleep(interval)
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                logger.exception("Swing stop monitor pass failed")

    # --- one pass ----------------------------------------------------------
    async def run_once(self) -> int:
        """Returns the number of exits PLACED, not the number of triggers.

        The two come apart inside the closing auction, which is exactly the
        case this monitor has to be honest about.
        """
        self.runs += 1
        placed = 0
        triggered_now = 0

        async with session_scope() as session:
            from src.strategies.services.strategy_registry import get_strategy_registry

            registry = get_strategy_registry()
            repository = SwingStopRepository(session)

            # A stop that fired but could not be exited yet -- the auction
            # case, or a restart between the trigger and the order. Placed
            # first, because it has been waiting longer than anything about to
            # trigger now.
            for stop in await repository.list_triggered():
                if not registry.is_enabled(stop.strategy_key):
                    continue
                if await self._place_exit(session, repository, stop, resumed=True):
                    placed += 1

            watching = 0
            unprotected = 0
            unmarked = 0
            nearest_symbol: Optional[str] = None
            nearest_percent: Optional[float] = None

            for stop in await repository.list_active():
                if not registry.is_enabled(stop.strategy_key):
                    # Not watched while the strategy is off. The level stays in
                    # place and resumes when it comes back on; clearing it
                    # would destroy the position's protection permanently.
                    continue
                definition = registry.get(stop.strategy_key)
                if definition is None:
                    continue

                if stop.stop_price is None:
                    # Entered on a session with no usable ATR14, so P14 could
                    # not be applied and there is no level to compare against.
                    # Counted and reported rather than skipped silently: the
                    # position is genuinely unprotected until the next nightly
                    # ratchet sets its first stop.
                    unprotected += 1
                    continue

                watching += 1
                if not market_clock.is_market_open(definition):
                    continue

                price = self._mark(stop.security_id)
                if price is None:
                    # Never act on an unknown price.
                    unmarked += 1
                    continue

                # How close the nearest position is to its level, computed off
                # the mark this pass has already read. None when nothing can be
                # measured -- a book with no marks does not have a nearest stop
                # of 0%.
                if price > 0:
                    percent = float(
                        (price - Decimal(str(stop.stop_price))) / price * 100
                    )
                    if nearest_percent is None or percent < nearest_percent:
                        nearest_percent = round(percent, 2)
                        nearest_symbol = stop.symbol

                if not self._is_hit(stop, price):
                    continue

                await self._mark_triggered(session, repository, stop, price)
                self.triggers += 1
                triggered_now += 1
                if await self._place_exit(session, repository, stop):
                    placed += 1

            from src.core.time_utils import ist_now

            self.last_pass_at = ist_now().isoformat()
            self.watching = watching
            self.unprotected = unprotected
            self.unmarked = unmarked
            self.nearest_symbol = nearest_symbol
            self.nearest_percent = nearest_percent

            if placed or triggered_now or self.deferred_to_auction:
                # A trigger is worth committing even when no order followed:
                # the record of WHEN a stop fired is what the auction case and
                # the disarmed case exist to preserve.
                await session.commit()

        self.exits_placed += placed
        return placed

    # --- helpers -----------------------------------------------------------
    def _mark(self, security_id: str) -> Optional[Decimal]:
        """LTP, else the mid of the touch. None means no mark, never zero."""
        row = self.book.get(str(security_id))
        if not row:
            return None
        if row.get("ltp"):
            return Decimal(str(row["ltp"]))
        bid, ask = row.get("bid"), row.get("ask")
        if bid and ask:
            return (Decimal(str(bid)) + Decimal(str(ask))) / 2
        return None

    @staticmethod
    def _is_hit(stop: SwingStop, price: Optional[Decimal]) -> bool:
        from src.swing.services.stop_service import StopService

        return StopService.is_hit(stop, price)

    async def _mark_triggered(
        self, session, repository: SwingStopRepository, stop: SwingStop, price: Decimal
    ) -> None:
        from src.core.time_utils import utc_now
        from src.swing.database.db_models.swing_stop_model import (
            EXIT_TRAIL,
            STOP_TRIGGERED,
        )

        stop.status = STOP_TRIGGERED
        stop.exit_kind = EXIT_TRAIL
        stop.triggered_at = utc_now()
        stop.trigger_price = Decimal(str(price)).quantize(Decimal("0.0001"))
        await session.flush()
        logger.warning(
            "Chandelier stop HIT for %s: %s crossed %s (entry %s, highest close "
            "%s). Exiting at market.",
            stop.symbol, stop.trigger_price, stop.stop_price, stop.entry_price,
            stop.highest_close,
        )

    async def _place_exit(
        self,
        session,
        repository: SwingStopRepository,
        stop: SwingStop,
        resumed: bool = False,
    ) -> bool:
        """Sell the whole holding at market, unless it could not fill honestly."""
        from src.strategies.services.strategy_registry import get_strategy_registry

        registry = get_strategy_registry()
        definition = registry.get(stop.strategy_key)
        if definition is None:
            return False

        if not registry.is_armed(stop.strategy_key):
            # Disarmed. The trigger is recorded and stays recorded; what is
            # withheld is the ORDER. An operator watching an unarmed strategy
            # sees exactly which stop fired and when.
            if not resumed:
                stop.note = (
                    "Stop triggered while the strategy was NOT ARMED, so no exit "
                    "order was placed. Arm it on Strategies & Features, or close "
                    "the position by hand."
                )[:500]
                await session.flush()
            return False

        fno_eligible = await self._is_fno_eligible(session, stop.security_id)
        if not market_clock.can_execute_continuously(definition, fno_eligible):
            note = self._deferral_note(definition, fno_eligible)
            if stop.note != note:
                stop.note = note
                await session.flush()
                self.deferred_to_auction += 1
                logger.warning("%s: %s", stop.symbol, note)
            return False

        from src.instruments.database.db_operations.instrument_repository import (
            InstrumentRepository,
        )
        from src.orders.database.db_operations.order_repository import OrderRepository
        from src.orders.services.order_service import (
            OrderService,
            OrderValidationError,
        )
        from src.positions.database.db_operations.position_repository import (
            PositionRepository,
        )

        quantity = await self._open_quantity(session, stop)
        if quantity <= 0:
            # The position went away by another route -- a manual close, or a
            # rotation exit that beat the stop to it. Nothing to sell.
            from src.core.time_utils import utc_now
            from src.swing.database.db_models.swing_stop_model import STOP_CLOSED

            stop.status = STOP_CLOSED
            stop.closed_at = utc_now()
            stop.note = (
                "Stop triggered, but the position was already closed by "
                "something else. No exit order was placed."
            )
            await session.flush()
            return False

        reason = (
            f"Trailing stop hit: {stop.trigger_price} crossed {stop.stop_price} "
            f"(entry {stop.entry_price}, highest close since entry "
            f"{stop.highest_close}, {stop.atr_multiple} x ATR14)."
        )
        try:
            order = await OrderService(
                OrderRepository(session),
                InstrumentRepository(session),
                PositionRepository(session),
                book=self._book,
            ).submit_paper_order(
                security_id=stop.security_id,
                side=OrderSide.SELL.value,
                order_type=OrderType.MARKET.value,
                lots=int(quantity),
                is_close_order=True,
                portfolio_id=stop.portfolio_id,
                reason=reason,
            )
        except OrderValidationError as error:
            stop.note = f"Exit refused: {error}"[:500]
            await session.flush()
            logger.warning("Swing stop exit for %s refused: %s", stop.symbol, error)
            return False

        from src.core.time_utils import utc_now

        stop.exit_order_id = order.id
        stop.closed_at = utc_now()
        stop.note = reason[:500]
        await session.flush()
        logger.info(
            "Swing stop exit placed for %s: order %s, %s share(s)%s",
            stop.symbol, order.client_order_id, quantity,
            " (resumed from an earlier trigger)" if resumed else "",
        )
        return True

    @staticmethod
    def _deferral_note(definition, fno_eligible: bool) -> str:
        if market_clock.in_closing_auction(definition, fno_eligible):
            auction = definition.market_hours.closing_auction
            return (
                f"Stop triggered at {auction.continuous_close.strftime('%H:%M')} "
                f"or later on an F&O-eligible name. Continuous cash trading has "
                f"ended and the Closing Auction Session runs to "
                f"{auction.auction_close.strftime('%H:%M')}; an exit sent now "
                f"would land in an auction this simulator does not model. The "
                f"exit waits for the next session's open."
            )[:500]
        return (
            "Stop triggered outside continuous trading hours. The exit waits "
            "for the next session's open rather than filling against a stale "
            "book."
        )

    @staticmethod
    async def _is_fno_eligible(session, security_id: str) -> bool:
        from src.instruments.database.db_operations.instrument_repository import (
            InstrumentRepository,
        )

        instrument = await InstrumentRepository(session).get_by_security_id(
            str(security_id)
        )
        return bool(instrument is not None and instrument.fno_eligible)

    @staticmethod
    async def _open_quantity(session, stop: SwingStop) -> int:
        """What is actually held, not what the stop was opened for.

        A partial exit earlier in the session, or a partially filled entry,
        leaves a different quantity from the one recorded at entry. Selling the
        recorded number would over-sell into a short, which this strategy must
        never hold.
        """
        from src.positions.database.db_operations.position_repository import (
            PositionRepository,
        )

        position = await PositionRepository(session).get_open_for_security(
            stop.portfolio_id, stop.security_id
        )
        if position is None:
            return 0
        return max(int(position.net_quantity or 0), 0)

    # --- reporting ---------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self._enabled(),
            "intervalMs": int(self._interval_seconds() * 1000),
            "runs": self.runs,
            "triggered": self.triggers,
            "exitsPlaced": self.exits_placed,
            "deferredToAuction": self.deferred_to_auction,
            # Everything below is what the LAST pass saw. `running` is whether
            # the task is alive, which is not the same fact as `enabled` and is
            # not the same fact as "watching nothing" -- the Live tab renders
            # the three differently on purpose.
            "running": self._task is not None and not self._task.done(),
            "lastPassAtIst": self.last_pass_at,
            "watching": self.watching,
            "unprotected": self.unprotected,
            "unmarked": self.unmarked,
            "nearestSymbol": self.nearest_symbol,
            "nearestPercent": self.nearest_percent,
            "error": self.last_error,
        }


_monitor: Optional[SwingStopMonitor] = None


def get_swing_stop_monitor() -> SwingStopMonitor:
    global _monitor
    if _monitor is None:
        _monitor = SwingStopMonitor()
    return _monitor


async def shutdown_swing_stop_monitor() -> None:
    global _monitor
    if _monitor is not None:
        await _monitor.stop()
        _monitor = None
