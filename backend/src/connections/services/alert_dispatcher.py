"""Draining the outbox: the only thing in this application that sends a message.

Own task, own session per pass, off the tick path, registered in
`TASK_DESCRIPTIONS` **and** `expected_task_names`, with a `status()` the health
surfaces read -- the shape `OrderMatcher` set and every background component
here follows.

Two jobs, both cheap:

  1. **Turn queued ERROR records into outbox rows.** `src/alert_sink.py` is a
     logging handler and cannot await, so it queues in memory; this is what
     moves the queue into the database, where the de-duplication decides
     whether each one is worth a message.
  2. **Deliver PENDING rows, oldest first.** An outbox delivers in the order
     things happened, and a row that fails is retried a bounded number of times
     before it is marked FAILED -- retrying for ever would turn one bad message
     into a permanent blockage behind it.

**Nothing here raises out of a pass.** An alerting system that can take the
process down is worse than no alerting system, and every failure it has is a
failure it must not itself alert about (see `alert_sink.EXCLUDED_LOGGER_PREFIXES`).
"""
import asyncio
from typing import Any, Dict, Optional

from src import alert_sink, config_utils
from src.core.time_utils import utc_now
from src.logging_config import get_logger

logger = get_logger("connections.dispatcher")

# Fast enough that a fill reaches a phone while the trader still cares, slow
# enough to be free. Nothing on this path touches the tick path.
DEFAULT_INTERVAL_SECONDS = 5.0

# How many times a row is retried before it is given up on. A message that has
# failed five times is failing for a reason a retry will not fix, and leaving
# it PENDING for ever blocks everything behind it.
MAX_ATTEMPTS = 5

# How many to send per pass. Telegram's flood control is real; a burst that
# empties a hundred-row backlog at once is how you meet it.
BATCH_SIZE = 5


class AlertDispatcher:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self.passes = 0
        self.ingested = 0
        self.delivered = 0
        self.failed = 0
        self.suppressed = 0
        self.last_pass_at = None
        self.last_error: Optional[str] = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @staticmethod
    def _interval() -> float:
        return float(
            config_utils.get_property_value_int(
                "connections.alert_dispatch_interval_seconds",
                int(DEFAULT_INTERVAL_SECONDS),
            )
        )

    @staticmethod
    def _enabled() -> bool:
        return config_utils.get_property_value_boolean("connections.alerts_enabled", True)

    async def start(self) -> None:
        if self.running:
            return
        if not self._enabled():
            logger.info("Alert delivery is switched off; not starting the dispatcher")
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="alert-dispatcher")
        logger.info(
            "Alert dispatcher started (every %.0f s, %s per pass)",
            self._interval(), BATCH_SIZE,
        )

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _run(self) -> None:
        while not self._stopping:
            try:
                await self.run_once()
                self.passes += 1
                self.last_pass_at = utc_now()
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - a bad pass must not end the task
                self.last_error = f"{type(error).__name__}: {error}"
                logger.warning(
                    "Alert dispatch pass failed: %s", type(error).__name__, exc_info=True
                )
            try:
                await asyncio.sleep(self._interval())
            except asyncio.CancelledError:
                raise

    async def run_once(self) -> Dict[str, int]:
        """One ingest-then-deliver pass. Exposed so a test can drive it."""
        from src.database.session import session_scope

        result = {"ingested": 0, "delivered": 0, "failed": 0, "suppressed": 0}
        async with session_scope() as session:
            result["ingested"] = await self._ingest_log_records(session)
            await session.commit()
        async with session_scope() as session:
            sent, failed, suppressed = await self._deliver(session)
            result["delivered"] = sent
            result["failed"] = failed
            result["suppressed"] = suppressed
            await session.commit()
        return result

    async def _ingest_log_records(self, session) -> int:
        """Move the sink's in-memory queue into the outbox."""
        from src.connections.services.alert_service import AlertService

        handler = alert_sink.get_handler()
        if handler is None:
            return 0
        records = handler.drain()
        if not records:
            return 0

        service = AlertService(session)
        written = 0
        for record in records:
            row = await service.record_error(
                logger_name=record.get("logger", "unknown"),
                level=record.get("level", "ERROR"),
                message=record.get("message", ""),
            )
            if row is not None:
                written += 1
        self.ingested += written
        return written

    async def _deliver(self, session) -> tuple:
        from src.connections.database.db_operations.alert_repository import (
            AlertRepository,
        )
        from src.connections.services.telegram_client import TelegramError
        from src.connections.services.telegram_service import TelegramService

        repository = AlertRepository(session)
        pending = await repository.pending(limit=BATCH_SIZE)
        if not pending:
            return 0, 0, 0

        service = TelegramService(session)
        config = await service.config()
        sent = failed = suppressed = 0

        if not config.can_send:
            # A fact is worth RECORDING even when nothing is configured to
            # carry it. The row is closed with the reason rather than left
            # PENDING for ever behind a connection that may never exist.
            reason = (
                "The Telegram connection is switched off."
                if config.connection_id and not config.enabled
                else "No Telegram bot token and channel are configured."
            )
            for alert in pending:
                await repository.mark_suppressed(alert, reason)
                suppressed += 1
            self.suppressed += suppressed
            return 0, 0, suppressed

        for alert in pending:
            text = self.render(alert)
            try:
                await service.send(text)
            except TelegramError as error:
                give_up = (not error.transient) or int(alert.attempts or 0) + 1 >= MAX_ATTEMPTS
                await repository.mark_attempt_failed(alert, error.message, give_up)
                failed += 1
                if error.retry_after:
                    # Telegram asked for a pause. Honour it rather than
                    # discovering flood control again on the next row.
                    break
                continue
            await repository.mark_sent(alert)
            sent += 1

        self.delivered += sent
        self.failed += failed
        return sent, failed, suppressed

    @staticmethod
    def render(alert) -> str:
        """One alert as the text that goes to the channel.

        The suppressed count is ON the message, because a collapsed flood that
        arrives as a single line looks like a single event.
        """
        lines = [alert.title, "", alert.body]
        if int(alert.suppressed_count or 0) > 0:
            lines += [
                "",
                f"(this has now happened {int(alert.suppressed_count) + 1} times; "
                f"repeats within the de-duplication window are collapsed)",
            ]
        return "\n".join(lines)

    def status(self) -> Dict[str, Any]:
        sink = alert_sink.get_handler()
        return {
            "running": self.running,
            "enabled": self._enabled(),
            "intervalSeconds": self._interval(),
            "passes": self.passes,
            "ingested": self.ingested,
            "delivered": self.delivered,
            "failed": self.failed,
            "suppressed": self.suppressed,
            "lastPassAt": self.last_pass_at.isoformat() if self.last_pass_at else None,
            "lastError": self.last_error,
            "sink": sink.stats() if sink is not None else None,
        }


_dispatcher: Optional[AlertDispatcher] = None


def get_alert_dispatcher() -> AlertDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = AlertDispatcher()
    return _dispatcher


async def shutdown_alert_dispatcher() -> None:
    global _dispatcher
    if _dispatcher is not None:
        await _dispatcher.stop()
        _dispatcher = None
