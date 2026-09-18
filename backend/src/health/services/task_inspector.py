"""The live asyncio task table -- this application's answer to "threads".

There is no thread pool here and no worker pool. `server.py` forces
`workers=1`, because more uvicorn workers would mean more upstream Dhan
connections and a desynchronised book (root CLAUDE.md section 4). Reporting a
thread count would be reporting an implementation detail of asyncio and libc,
not anything an operator can act on.

What this process actually runs concurrently is a fixed set of **named** asyncio
tasks. Every one of them is created with `name=`, which is what makes this
table close to free: `asyncio.all_tasks()` plus `Task.get_name()`.

The table is cross-checked against what *should* be running, because the
failure this exists to catch is a task that died silently. A dead
`greeks-poller` does not announce itself -- the greeks simply stop updating and
look merely stale.
"""
import asyncio
from typing import Any, Dict, List, Optional, Set

from src import config_utils

# Every long-lived named task in the process, with what each one is for. The
# feed is either dhan-feed (+ its watchdog) or synthetic-feed, never both.
TASK_DESCRIPTIONS = {
    "dhan-feed": "Upstream Dhan market-data WebSocket: connect, subscribe, read frames",
    "dhan-feed-watchdog": "Reconnects the upstream feed when it goes quiet past the drop cliff",
    "synthetic-feed": "Locally generated prices; opens no upstream connection",
    "broadcaster": "Coalesced fan-out from the shared book to every browser tab",
    "greeks-poller": "Option chain REST poll; the only source of greeks",
    "feed-resync": "Re-centres the subscribed strike window as the underlying moves",
    "order-matcher": "Fills resting limit orders against the book",
    "bracket-monitor": "Watches chart stop-loss and take-profit levels server-side",
    "swing-stop-monitor": (
        "Watches the rotation's chandelier trailing stops and exits the ones "
        "that are hit"
    ),
    "swing-scheduler": (
        "Runs the rotation's nightly decision and its rebalance on an IST clock"
    ),
}


def _running_task_names() -> Dict[str, int]:
    """Named, not-yet-finished tasks on this loop, counted by name.

    `asyncio.all_tasks()` returns only pending tasks, so a task that has
    finished is absent rather than present-and-done. That is exactly the signal
    wanted: absent from here while expected is what "it died" looks like.
    """
    counts: Dict[str, int] = {}
    try:
        tasks = asyncio.all_tasks()
    except RuntimeError:  # pragma: no cover - no running loop
        return counts
    for task in tasks:
        if task.done():
            continue
        name = task.get_name()
        counts[name] = counts.get(name, 0) + 1
    return counts


def _is_known_task(name: str) -> bool:
    """Whether a task name belongs to this application's fixed set.

    Membership of TASK_DESCRIPTIONS is the test, rather than "does not look
    like `Task-N`". Starlette names its per-request tasks after the coroutine
    (`...BaseHTTPMiddleware.__call__.<locals>.coro`), and the WebSocket route's
    send/receive loops are unnamed, so a shape-based filter let framework
    plumbing into the table as though it were an unexpected application task.
    """
    return name in TASK_DESCRIPTIONS


def _transient_task_count(running: Dict[str, int]) -> int:
    """Tasks that are not part of the fixed set: request handlers, socket loops.

    Counted rather than listed. Naming them would mean printing
    `...call_next.<locals>.coro` at an operator, which tells them nothing --
    but the count moving with traffic is real information.
    """
    return sum(count for name, count in running.items() if not _is_known_task(name))


def _greeks_expected() -> bool:
    from src.strategies.services.strategy_definition import CAPABILITY_GREEKS
    from src.strategies.services.strategy_registry import get_strategy_registry

    return get_strategy_registry().capability_active_anywhere(CAPABILITY_GREEKS)


def _chart_trading_expected() -> bool:
    from src.strategies.services.strategy_definition import CAPABILITY_CHART_TRADING
    from src.strategies.services.strategy_registry import get_strategy_registry

    return get_strategy_registry().capability_active_anywhere(CAPABILITY_CHART_TRADING)


def _automation_expected() -> bool:
    """Is any AUTOMATED strategy running?

    Both swing tasks are started once and keep running; what makes them
    expected is a strategy that decides on a schedule being enabled. Switching
    the last one off stops them, and the page must not then report two missing
    tasks -- a false problem on the page whose whole job is to surface real
    ones.
    """
    from src.strategies.services.strategy_registry import get_strategy_registry

    registry = get_strategy_registry()
    return any(
        registry.is_enabled(definition.key)
        for definition in registry.automated()
    )


def feed_flags() -> Dict[str, bool]:
    """`is_synthetic` and `feed_running`, read off the live feed manager.

    Both callers of `inspect()` need the same two booleans derived the same
    way, and "running" is subtler than it looks: a feed in DISABLED, and one
    that has never reported a state at all, are both not-running, while every
    other connection state is. Deriving that twice is how the system health
    page and the strategy health tab would start disagreeing about whether a
    task should be alive.
    """
    from src.constants import ConnectionState
    from src.market.services.feed_manager import get_feed_manager

    manager = get_feed_manager()
    state = (manager.status().get("feed") or {}).get("state")
    return {
        "is_synthetic": bool(manager.is_synthetic),
        "feed_running": state not in (None, ConnectionState.DISABLED.value),
    }


def expected_task_names(*, is_synthetic: bool, feed_running: bool) -> Set[str]:
    """Which named tasks should be alive, given the current configuration."""
    expected: Set[str] = set()

    if not config_utils.get_property_value_boolean("market_feed.enabled", True):
        # The feed is off entirely; the broadcaster still runs so tabs get a
        # status message saying so.
        expected.add("broadcaster")
    elif feed_running:
        expected.update({"broadcaster", "feed-resync"})
        if is_synthetic:
            expected.add("synthetic-feed")
        else:
            expected.update({"dhan-feed", "dhan-feed-watchdog"})
        # The greeks poller runs only while some RUNNING strategy wants
        # greeks. Switching the last one off stops it, and the health page must
        # not then report it as a missing task -- a false problem on the page
        # whose whole job is to surface real ones.
        if _greeks_expected():
            expected.add("greeks-poller")
    else:
        # Credentials missing and synthetic off: start() bails after starting
        # the broadcaster, deliberately, rather than inventing prices.
        expected.add("broadcaster")

    if config_utils.get_property_value_boolean("trading.matcher_enabled", True):
        expected.add("order-matcher")
    if _chart_trading_expected():
        expected.add("bracket-monitor")

    if _automation_expected():
        # The rotation's two background tasks. Each has its own config switch
        # as well, so an operator can stop one without switching the strategy
        # off (which would also stop the marks).
        if config_utils.get_property_value_boolean("swing.stops_enabled", True):
            expected.add("swing-stop-monitor")
        if config_utils.get_property_value_boolean("swing.scheduler_enabled", True):
            expected.add("swing-scheduler")

    return expected


def inspect(
    *,
    is_synthetic: bool,
    feed_running: bool,
    counters: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """The task table, with each row judged against what should be running.

    `counters` maps a task name to whatever its owning component already
    tracks (interval, run count, last error). Those live on the components, not
    on the tasks; this only joins them onto the rows.
    """
    counters = counters or {}
    running = _running_task_names()
    expected = expected_task_names(is_synthetic=is_synthetic, feed_running=feed_running)

    named_running = {
        name: count for name, count in running.items() if _is_known_task(name)
    }

    rows: List[Dict[str, Any]] = []
    for name in sorted(set(named_running) | expected):
        is_running = name in named_running
        is_expected = name in expected
        if is_running and is_expected:
            state = "running"
        elif is_running:
            # Running but not expected: a leftover from a previous
            # configuration, e.g. dhan-feed still up after switching to
            # synthetic. Worth surfacing, not necessarily broken.
            state = "unexpected"
        else:
            state = "missing"

        row: Dict[str, Any] = {
            "name": name,
            "state": state,
            "expected": is_expected,
            "instances": named_running.get(name, 0),
            "description": TASK_DESCRIPTIONS.get(name, "Not a task this build knows about"),
        }
        row.update(counters.get(name, {}))
        rows.append(row)

    missing = [row["name"] for row in rows if row["state"] == "missing"]
    unexpected = [row["name"] for row in rows if row["state"] == "unexpected"]
    duplicated = [row["name"] for row in rows if row["instances"] > 1]

    return {
        "tasks": rows,
        "expectedCount": len(expected),
        "runningCount": len(named_running),
        "missing": missing,
        "unexpected": unexpected,
        "duplicated": duplicated,
        # Per-request and per-WebSocket tasks, which come and go.
        "transientCount": _transient_task_count(running),
        "healthy": not missing and not duplicated,
    }
