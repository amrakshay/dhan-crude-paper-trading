"""An in-memory ring buffer of recent WARNING+ log records.

Every component in this process carries a single `last_error` string. That is
enough to explain *why a component is unhappy right now*, and useless for "what
went wrong in the last hour" -- there is no count, no ordering and no history.
This module is that history, for the system health page to read.

Three properties are deliberate:

- **Nothing is written to disk and nothing is persisted.** The buffer dies with
  the process, which is the proportionate answer here: `app.log` already
  survives restarts, and the root CLAUDE.md is explicit that this database does
  not accumulate time series. The health page says the buffer is
  process-scoped rather than implying otherwise.
- **Records are stored FORMATTED, through `RedactingFormatter`.** The buffer is
  read by an HTTP endpoint, so the text that leaves it must already be scrubbed
  by the same layer that protects the log file. Storing the raw record and
  formatting on read would put the redaction on the caller.
- **The handler is installed from `configure_logging()`, never at a call
  site**, which is the rule in backend/CLAUDE.md section 8.

WARNING is the floor because that is where the log stops being a narrative and
starts being a complaint. Nothing here is on the tick path: the tick path does
not log at all.
"""
import logging
import threading
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from src import log_redaction

# Enough to cover a bad morning without being a memory concern: a record is a
# few hundred bytes, so the whole buffer is well under a megabyte.
DEFAULT_CAPACITY = 250

# Below this nothing is worth keeping: INFO is the running commentary.
MINIMUM_LEVEL = logging.WARNING


class RecentProblemsHandler(logging.Handler):
    """Keeps the last N formatted WARNING+ records in memory."""

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        super().__init__(level=MINIMUM_LEVEL)
        self.capacity = capacity
        self._records: Deque[Dict[str, Any]] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        # Counts every record ever seen, not just the ones still in the buffer,
        # so "12 warnings, oldest 8 shown" is answerable after it wraps.
        self._counts: Dict[str, int] = {}
        self.setFormatter(log_redaction.RedactingFormatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        # A handler that raises would break the call site it is observing, so
        # this swallows everything -- including a record whose args do not
        # interpolate.
        try:
            message = self.format(record)
        except Exception:  # pragma: no cover - defensive
            message = f"<unformattable record from {record.name}>"

        entry = {
            "timestampMs": int(record.created * 1000),
            "level": record.levelname,
            "logger": record.name,
            # The traceback, when there is one, is already inside `message`
            # because RedactingFormatter renders and scrubs exc_info.
            "message": message,
        }
        with self._lock:
            self._records.append(entry)
            self._counts[record.levelname] = self._counts.get(record.levelname, 0) + 1

    def recent(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """The buffered records, newest first."""
        with self._lock:
            entries = list(self._records)
        entries.reverse()
        return entries[:limit] if limit else entries

    def counts(self) -> Dict[str, int]:
        """Total records seen per level, including ones the buffer has dropped."""
        with self._lock:
            return dict(self._counts)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._counts.clear()


_handler: Optional[RecentProblemsHandler] = None
_INSTALL_LOCK = threading.Lock()


def install(logger: logging.Logger, capacity: int = DEFAULT_CAPACITY) -> RecentProblemsHandler:
    """Attach the buffer to `logger`, at most once per process."""
    global _handler
    with _INSTALL_LOCK:
        if _handler is None:
            _handler = RecentProblemsHandler(capacity)
        if _handler not in logger.handlers:
            logger.addHandler(_handler)
        return _handler


def get_handler() -> Optional[RecentProblemsHandler]:
    """The installed buffer, or None if logging was never configured."""
    return _handler


def reset_for_tests() -> None:
    """Detach and forget the buffer. Mirrors reset_logging_for_tests()."""
    global _handler
    with _INSTALL_LOCK:
        _handler = None
