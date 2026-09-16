"""Background matcher for resting limit orders.

Market orders resolve at placement time. Limit orders rest until the book moves
through them, so something has to keep looking -- that is this task.

It runs on its own cadence with its own database session, deliberately off the
tick path: the feed handler's job is to update the in-memory book in
microseconds, not to open transactions. The matcher reads that book, so it sees
the latest state without ever blocking the writer.
"""
import asyncio
from typing import Optional

from src import config_utils
from src.database.session import session_scope
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.logging_config import get_logger
from src.orders.database.db_operations.order_repository import OrderRepository
from src.orders.services.order_service import OrderService
from src.positions.database.db_operations.position_repository import PositionRepository

logger = get_logger("orders.matcher")


class OrderMatcher:
    def __init__(self, book=None) -> None:
        self._book = book
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self.runs = 0
        self.fills = 0
        self.last_error: Optional[str] = None

    @staticmethod
    def _enabled() -> bool:
        return config_utils.get_property_value_boolean("trading.matcher_enabled", True)

    @staticmethod
    def _interval_seconds() -> float:
        return config_utils.get_property_value_int("trading.matcher_interval_ms", 250) / 1000.0

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
            logger.info("Order matcher is disabled; resting limit orders will not fill")
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="order-matcher")
        logger.info("Order matcher started (every %.0f ms)", self._interval_seconds() * 1000)

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
                logger.exception("Order matcher pass failed")

    async def run_once(self) -> int:
        """One matching pass. Returns the number of orders that filled."""
        self.runs += 1
        async with session_scope() as session:
            service = OrderService(
                order_repository=OrderRepository(session),
                instrument_repository=InstrumentRepository(session),
                position_repository=PositionRepository(session),
                book=self.book,
            )
            filled = await service.try_fill_open_orders()
        if filled:
            self.fills += filled
            logger.info("Matcher filled %s resting order(s)", filled)
        return filled

    def status(self) -> dict:
        return {
            "enabled": self._enabled(),
            "intervalMs": int(self._interval_seconds() * 1000),
            "runs": self.runs,
            "ordersFilled": self.fills,
            "error": self.last_error,
        }


_matcher: Optional[OrderMatcher] = None


def get_order_matcher() -> OrderMatcher:
    global _matcher
    if _matcher is None:
        _matcher = OrderMatcher()
    return _matcher


async def shutdown_order_matcher() -> None:
    global _matcher
    if _matcher is not None:
        await _matcher.stop()
        _matcher = None
