"""Logging for a process that runs for weeks: quiet console, complete files.

  console          one line per 5-minute cycle, trades of the PLAN book, list
                   changes, warnings -- each distinct warning at most once per
                   15 minutes. Anything logged with extra={"console": True}.
  logs/run.log     INFO and up from this experiment and the app modules it
                   uses; rotated daily, 30 days kept.
  logs/debug.log   everything, DEBUG included (every stop move, every fetch);
                   rotated at 20 MB, 5 kept.

The app's own logging (`dcpt`) is DETACHED here: its handlers are removed and
its records flow into the files above. That matters beyond tidiness -- the
app installs an alert sink on that logger, and an ERROR raised inside this
experiment must not become a Telegram alert from the trading application.
"""
from __future__ import annotations

import logging
import logging.handlers
import re
import sys
import time
from pathlib import Path

FMT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


class ConsoleFilter(logging.Filter):
    def __init__(self, repeat_after_s: float = 900.0) -> None:
        super().__init__()
        self.repeat_after_s = repeat_after_s
        self._seen: dict[str, float] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "console", False):
            return True
        if record.levelno < logging.WARNING:
            return False
        key = record.name + ":" + re.sub(r"\d+(\.\d+)?", "#", record.getMessage())
        now = time.monotonic()
        if now - self._seen.get(key, -1e9) < self.repeat_after_s:
            return False
        self._seen[key] = now
        return True


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        stamp = time.strftime("%H:%M:%S", time.localtime(record.created))
        tag = "" if record.levelno < logging.WARNING else f"{record.levelname}: "
        return f"[{stamp}] {tag}{record.getMessage()}"


def setup(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    app = logging.getLogger("dcpt")
    for h in list(app.handlers):
        app.removeHandler(h)
    app.propagate = True
    app.setLevel(logging.INFO)

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(logging.DEBUG)

    run = logging.handlers.TimedRotatingFileHandler(log_dir / "run.log", when="midnight", backupCount=30)
    run.setLevel(logging.INFO)
    run.setFormatter(logging.Formatter(FMT))
    # The app says these once per 5-minute cycle (the token is re-read every
    # cycle). They stay in debug.log; run.log keeps only their warnings.
    run.addFilter(lambda r: r.levelno >= logging.WARNING
                  or not r.name.startswith(("dcpt.settings", "dcpt.database")))
    debug = logging.handlers.RotatingFileHandler(log_dir / "debug.log", maxBytes=20_000_000, backupCount=5)
    debug.setLevel(logging.DEBUG)
    debug.setFormatter(logging.Formatter(FMT))
    console = logging.StreamHandler(sys.stdout)
    console.addFilter(ConsoleFilter())
    console.setFormatter(ConsoleFormatter())
    for h in (run, debug, console):
        root.addHandler(h)
    for noisy in ("httpx", "httpcore", "websockets", "asyncio", "aiosqlite", "playwright"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
