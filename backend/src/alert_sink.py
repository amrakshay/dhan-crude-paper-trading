"""The ERROR-level log sink that feeds the alert outbox.

**It hooks the logging system, not every call site.** `src/log_buffer.py`
already proved the shape: a handler sees every record, stores text already
formatted through `RedactingFormatter`, and never raises out of `emit()`. This
is a second handler with the same three properties and a higher floor.

It lives at `src/` rather than inside `src/connections/` for the same reason
`log_buffer` does: `configure_logging()` installs it, and `configure_logging()`
runs before the application has bootstrapped. Anything it imports is imported
at that moment, so it imports `logging`, `threading` and `collections` and
nothing else. The database half is `src/connections/services/alert_service.py`,
which imports the normalisation from HERE rather than the other way round.

**ERROR and above, not WARNING.** WARNING is where the log stops being a
narrative and starts being a complaint -- which is the right floor for a page
somebody is reading. It is far too chatty for a phone.

**The alerting machinery is excluded from its own sink.** A Telegram delivery
failure that logs an ERROR that raises an alert that fails to deliver is a loop
that ends in Telegram's flood control, with the one message that mattered stuck
behind it. `EXCLUDED_LOGGER_PREFIXES` is that exclusion, and
`tests/test_alerts.py` asserts it directly rather than trusting the comment.

**Nothing is written to the database here.** `emit()` is synchronous and may be
called from the middle of anything, including the order path. It appends to a
bounded in-memory deque; `alert_dispatcher` drains it into the outbox. A deque
that overflows counts what it dropped rather than blocking the caller -- the
situation in which it overflows IS a flood, and the count is the honest report
of one.
"""
import logging
import re
import threading
from collections import deque
from typing import Any, Deque, Dict, List, Optional

# Loggers whose records never become alerts. The prefix matches the `dcpt.`
# namespace `get_logger` builds, so `get_logger("connections.alerts")` is
# `dcpt.connections.alerts` and is covered.
EXCLUDED_LOGGER_PREFIXES = ("dcpt.connections",)

# Deliberately higher than log_buffer's WARNING.
MINIMUM_LEVEL = logging.ERROR

# A flood is bounded here rather than in the database. 200 is far more than a
# healthy process produces in a dispatcher interval, and far less than the
# 3,500 requests the nightly bug once made in two hours.
DEFAULT_CAPACITY = 200

# Digits, hex blobs and addresses vary between two occurrences of the same
# problem; whatever is left is what the problem IS. Normalising matters because
# an alert body carries its own occurrence count, and keying on the raw text
# would give every repeat a new key and defeat the de-duplication entirely.
_NORMALISE_PATTERNS = (
    (re.compile(r"0x[0-9a-fA-F]+"), "#"),
    (re.compile(r"\b[0-9a-fA-F]{8,}\b"), "#"),
    (re.compile(r"\d+"), "#"),
    (re.compile(r"\s+"), " "),
)


def normalise_for_dedupe(logger_name: str, message: str) -> str:
    """The de-duplication key: the logger plus the SHAPE of the message."""
    text = message or ""
    for pattern, replacement in _NORMALISE_PATTERNS:
        text = pattern.sub(replacement, text)
    return f"{logger_name}|{text.strip()[:150]}"


def is_excluded(logger_name: str) -> bool:
    return any(
        logger_name == prefix or logger_name.startswith(prefix + ".")
        for prefix in EXCLUDED_LOGGER_PREFIXES
    )


class AlertSinkHandler(logging.Handler):
    """Queues ERROR+ records for the alert dispatcher to pick up."""

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        super().__init__(level=MINIMUM_LEVEL)
        from src import log_redaction

        self.capacity = capacity
        self._pending: Deque[Dict[str, Any]] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self.seen = 0
        self.dropped = 0
        self.excluded = 0
        # The same formatter log_buffer uses, for the same reason: what leaves
        # this handler is read by an HTTP endpoint and sent to a phone, so it
        # must already be scrubbed by the layer that protects app.log.
        self.setFormatter(log_redaction.RedactingFormatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        # A handler that raises breaks the call site it is observing, so this
        # swallows everything -- including a record whose args do not
        # interpolate.
        try:
            if is_excluded(record.name):
                self.excluded += 1
                return
            message = self.format(record)
        except Exception:  # pragma: no cover - defensive
            message = f"<unformattable record from {record.name}>"

        entry = {
            "timestampMs": int(record.created * 1000),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }
        with self._lock:
            self.seen += 1
            if len(self._pending) == self._pending.maxlen:
                self.dropped += 1
            self._pending.append(entry)

    def drain(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Take the queued records. Oldest first -- an outbox keeps order."""
        with self._lock:
            if limit is None or limit >= len(self._pending):
                taken = list(self._pending)
                self._pending.clear()
            else:
                taken = [self._pending.popleft() for _ in range(limit)]
        return taken

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "seen": self.seen,
                "queued": len(self._pending),
                "dropped": self.dropped,
                "excluded": self.excluded,
                "capacity": self.capacity,
            }

    def clear(self) -> None:
        with self._lock:
            self._pending.clear()
            self.seen = 0
            self.dropped = 0
            self.excluded = 0


_handler: Optional[AlertSinkHandler] = None
_INSTALL_LOCK = threading.Lock()


def install(logger: logging.Logger, capacity: int = DEFAULT_CAPACITY) -> AlertSinkHandler:
    """Attach the sink to `logger`, at most once per process."""
    global _handler
    with _INSTALL_LOCK:
        if _handler is None:
            _handler = AlertSinkHandler(capacity)
        if _handler not in logger.handlers:
            logger.addHandler(_handler)
        return _handler


def get_handler() -> Optional[AlertSinkHandler]:
    return _handler


def reset_for_tests() -> None:
    """Detach and forget the sink. Mirrors log_buffer.reset_for_tests()."""
    global _handler
    with _INSTALL_LOCK:
        _handler = None
