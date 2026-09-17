"""Background watcher for chart stop-loss and take-profit levels.

The levels are prices of the UNDERLYING FUTURE, so this reads the future out of
the in-memory book and, when a level is crossed, sells the OPTION position back
at market through the ordinary paper-order path.

It runs on its own cadence with its own database session, deliberately off the
tick path -- the same arrangement as OrderMatcher, and for the same reason: the
feed handler's job is to update the book in microseconds, not to open
transactions.

**Server-side on purpose.** A stop that lives in the browser dies with the tab.
This task keeps watching whether or not anyone has the chart open.
"""
import asyncio
from decimal import Decimal
from typing import Optional

from src import config_utils
from src.chart_trading.database.db_operations.chart_trade_repository import (
    ChartTradeRepository,
)
from src.database.session import session_scope
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.logging_config import get_logger
from src.orders.database.db_operations.order_repository import OrderRepository
from src.positions.database.db_operations.position_repository import PositionRepository

logger = get_logger("chart_trading.brackets")


class BracketMonitor:
    def __init__(self, book=None) -> None:
        self._book = book
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self.runs = 0
        self.triggers = 0
        self.last_error: Optional[str] = None

    @staticmethod
    def _enabled() -> bool:
        return config_utils.get_property_value_boolean("chart_trading.enabled", True)

    @staticmethod
    def _interval_seconds() -> float:
        return config_utils.get_property_value_int(
            "chart_trading.bracket_interval_ms", 250
        ) / 1000.0

    @property
    def book(self):
        if self._book is None:
            from src.market.services.feed_manager import get_feed_manager

            self._book = get_feed_manager().book
        return self._book

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        if not self._enabled():
            logger.info("Chart trading is disabled; bracket levels will not be watched")
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="bracket-monitor")
        logger.info(
            "Bracket monitor started (every %.0f ms)", self._interval_seconds() * 1000
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
            except Exception as exc:
                self.last_error = str(exc)
                logger.exception("Bracket monitor pass failed")

    async def run_once(self) -> int:
        """One pass. Returns the number of chart trades closed by a level."""
        self.runs += 1
        triggered = 0

        async with session_scope() as session:
            from src.chart_trading.services.chart_trading_service import (
                ChartTradingService,
            )

            from src.strategies.services.strategy_registry import (
                get_strategy_registry,
            )

            registry = get_strategy_registry()
            repository = ChartTradeRepository(session)
            armed = [
                trade
                for trade in await repository.list_open()
                # A disabled strategy's brackets are NOT watched. That is a real
                # consequence of switching one off -- a stop will not fire --
                # which is why the toggle warns about it first, and why the
                # levels are left in place rather than cleared.
                if registry.is_enabled(trade.strategy_key)
                and (
                    trade.stop_loss_level is not None
                    or trade.take_profit_level is not None
                )
            ]
            if not armed:
                return 0

            service = ChartTradingService(
                chart_trades=repository,
                instruments=InstrumentRepository(session),
                orders=OrderRepository(session),
                positions=PositionRepository(session),
                book=self.book,
            )

            for trade in armed:
                price = service.underlying_price(trade.underlying_security_id)
                if price is None:
                    # No tick yet. Never act on an unknown price.
                    continue
                reason = service.level_hit(trade, price)
                if reason is None:
                    continue

                level = (
                    trade.stop_loss_level
                    if reason == "STOP_LOSS"
                    else trade.take_profit_level
                )
                logger.info(
                    "Chart trade %s hit its %s: future %s crossed %s; closing %s",
                    trade.id, reason, price, level, trade.option_symbol,
                )
                await service.close(trade.id, reason=reason, triggered_level=Decimal(str(level)))
                triggered += 1

            if triggered:
                await session.commit()

        if triggered:
            self.triggers += triggered
        return triggered

    def status(self) -> dict:
        return {
            "enabled": self._enabled(),
            "intervalMs": int(self._interval_seconds() * 1000),
            "runs": self.runs,
            "triggered": self.triggers,
            "error": self.last_error,
        }


_monitor: Optional[BracketMonitor] = None


def get_bracket_monitor() -> BracketMonitor:
    global _monitor
    if _monitor is None:
        _monitor = BracketMonitor()
    return _monitor


async def shutdown_bracket_monitor() -> None:
    global _monitor
    if _monitor is not None:
        await _monitor.stop()
        _monitor = None
