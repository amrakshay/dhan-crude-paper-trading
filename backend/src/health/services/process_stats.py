"""What this process knows about itself.

Two things live here: the moment the application started (nothing recorded it
before -- `main.py`'s lifespan calls `mark_started()`), and the OS-level
resource figures that need `psutil`.

**On `psutil` being a dependency.** This repository has been deliberate about
every dependency it has, and the alternative was `resource.getrusage()`, which
gives peak RSS and CPU *time* and nothing else portably -- no current RSS, no
CPU percentage, no open file descriptors. A resources card built on that would
have been a placeholder. `psutil` ships pure wheels for macOS/arm64 and Linux,
so it adds no build step, and it is imported lazily here: if it is ever missing
the health page says the figures are unavailable rather than failing.
"""
import os
import platform
import sys
import time
from typing import Any, Dict, Optional

from src.logging_config import get_logger

logger = get_logger("health.process")

_started_at_ms: Optional[int] = None
_started_monotonic: Optional[float] = None

# psutil computes CPU percent as the delta since the previous call on the same
# object, so the instance is kept: the first reading is meaningless (it reports
# 0.0) and every one after it covers the interval since the last poll.
_process: Any = None
_cpu_primed = False
_psutil_error: Optional[str] = None


def mark_started() -> None:
    """Record the process start time. Called once, from the lifespan."""
    global _started_at_ms, _started_monotonic
    if _started_at_ms is not None:
        return
    _started_at_ms = int(time.time() * 1000)
    _started_monotonic = time.monotonic()


def started_at_ms() -> Optional[int]:
    return _started_at_ms


def uptime_seconds() -> Optional[float]:
    """Seconds since the lifespan started, or None if it never ran.

    Measured on the monotonic clock, so a system clock adjustment cannot make
    the uptime jump or go backwards.
    """
    if _started_monotonic is None:
        return None
    return time.monotonic() - _started_monotonic


def _get_process() -> Any:
    global _process, _psutil_error
    if _process is not None or _psutil_error is not None:
        return _process
    try:
        import psutil  # noqa: PLC0415 - optional, resolved once and cached

        _process = psutil.Process(os.getpid())
    except Exception as exc:  # pragma: no cover - only when psutil is absent
        _psutil_error = str(exc)
        logger.warning(
            "psutil is unavailable (%s); the health page will not show memory, "
            "CPU or file-descriptor figures",
            exc,
        )
    return _process


def resources() -> Dict[str, Any]:
    """RSS, CPU, file descriptors and OS threads, or a stated unavailability.

    The OS thread count is reported for completeness and labelled as such: this
    application has no thread pool, and the threads that exist belong to the
    interpreter and libc rather than to anything an operator can act on. The
    concurrency that matters here is the named asyncio tasks -- see
    `task_inspector`.
    """
    global _cpu_primed
    process = _get_process()
    if process is None:
        return {
            "available": False,
            "unavailableReason": _psutil_error or "psutil is not installed",
            "rssBytes": None,
            "vmsBytes": None,
            "cpuPercent": None,
            "openFileDescriptors": None,
            "osThreads": None,
        }

    payload: Dict[str, Any] = {"available": True, "unavailableReason": None}
    try:
        memory = process.memory_info()
        payload["rssBytes"] = int(memory.rss)
        payload["vmsBytes"] = int(memory.vms)
    except Exception:
        payload["rssBytes"] = None
        payload["vmsBytes"] = None

    try:
        cpu = process.cpu_percent()
        # The first call has no previous sample to difference against, so its
        # 0.0 is an artefact rather than a measurement. Say so instead.
        payload["cpuPercent"] = None if not _cpu_primed else float(cpu)
        _cpu_primed = True
    except Exception:
        payload["cpuPercent"] = None

    try:
        payload["openFileDescriptors"] = int(process.num_fds())
    except Exception:  # pragma: no cover - not available on every platform
        payload["openFileDescriptors"] = None

    try:
        payload["osThreads"] = int(process.num_threads())
    except Exception:  # pragma: no cover
        payload["osThreads"] = None

    return payload


def interpreter() -> Dict[str, Any]:
    return {
        "pid": os.getpid(),
        "pythonVersion": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
    }
