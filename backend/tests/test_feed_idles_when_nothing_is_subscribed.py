"""The feed holds no upstream connection when there is nothing to carry.

**The bug this pins.** Dhan closes a WebSocket that connects and never
subscribes -- within the same second, with no close frame and no DISCONNECT
packet. `_run_forever` treated that as a lost connection and reconnected for
ever: on 2026-09-19 it ran 237 times in an afternoon, taking and losing one of
Dhan's five per-user slots every thirty seconds, all night, carrying no data
because there was nothing to carry.

An empty subscription set is the ORDINARY state now, not a fault. A
`positions` strategy holds nothing most of the time and a `universe` strategy
is outside its window for most of the day, so the client idles instead.

**And the other half.** With that guard alone, a universe strategy's window
would never bring the feed back up: the resync loop only ever resynced an
OPTION-CHAIN strategy, watching a front future drift away from a strike
window. A universe strategy's trigger is THE TIME, and nothing was watching a
clock on the feed's behalf -- BTST's window would have opened at 14:45 with
nobody noticing. The two changes only work together, which is why they are
tested together.
"""
import asyncio
from datetime import time
from decimal import Decimal

import pytest

from src.constants import ConnectionState
from src.core.time_utils import utc_now
from src.market.services.dhan_feed_client import DhanFeedClient
from src.market.services.feed_manager import FeedManager
from src.market.services.market_book import MarketBook
from src.strategies.services.strategy_definition import SubscriptionPolicy
from src.strategies.services.strategy_registry import get_strategy_registry

BTST = "nse-btst-overnight"


@pytest.fixture
def client():
    return DhanFeedClient(MarketBook())


def _shrink_backoff(monkeypatch):
    """Make the reconnect backoff negligible, so a test measures behaviour.

    The real default is one second, doubling to thirty. A test that waited it
    out would be slow and, worse, would be asserting the sleep rather than the
    state machine.
    """
    from src import config_utils

    real = config_utils.get_property_value_float

    def _fake(key, default=0.0):
        if key.startswith("market_feed.reconnect_backoff"):
            return 0.01
        return real(key, default)

    monkeypatch.setattr(
        "src.market.services.dhan_feed_client.config_utils.get_property_value_float",
        _fake,
    )


# --- the idle guard ---------------------------------------------------------


def test_a_client_with_nothing_subscribed_wants_no_connection(client):
    """The predicate the whole guard turns on."""
    assert client._wants_a_connection() is False  # noqa: SLF001


def test_a_client_wants_a_connection_for_a_PENDING_subscription(client):
    """Both halves of the predicate matter.

    `_subscribed` is what a reconnect would restore; `_pending_subscribe` is
    what arrived while the socket was down. Checking only the first would leave
    a client that went idle unable to ever come back, because `subscribe()`
    queues into the second while disconnected.
    """
    client._pending_subscribe.append(("NSE_EQ", "1234"))  # noqa: SLF001
    assert client._wants_a_connection() is True  # noqa: SLF001


async def test_it_goes_IDLE_rather_than_connecting_and_never_calls_dhan(
    client, monkeypatch
):
    """The actual fix: no socket is opened at all.

    `_connect_and_consume` is replaced with something that fails the test if it
    is ever reached -- the assertion is that Dhan is not called, not merely
    that the state looks right.
    """
    async def _must_not_connect():
        raise AssertionError(
            "the feed opened an upstream connection with nothing to subscribe"
        )

    monkeypatch.setattr(client, "_connect_and_consume", _must_not_connect)
    monkeypatch.setattr(
        "src.market.services.dhan_feed_client.IDLE_RECHECK_SECONDS", 0.01
    )

    task = asyncio.create_task(client._run_forever())  # noqa: SLF001
    await asyncio.sleep(0.08)
    client._stopping = True  # noqa: SLF001
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert client.state == ConnectionState.IDLE
    assert "no strategy has an instrument" in (client.state_detail or "")


async def test_idling_does_not_count_against_the_reconnect_budget(
    client, monkeypatch
):
    """An idle pass is not a reconnect.

    `reconnect_count` is on the health page and drives the backoff. Counting
    every idle recheck would report a feed churning through hundreds of
    reconnects while it sat quietly doing exactly the right thing.
    """
    async def _must_not_connect():
        raise AssertionError("should not connect")

    monkeypatch.setattr(client, "_connect_and_consume", _must_not_connect)
    monkeypatch.setattr(
        "src.market.services.dhan_feed_client.IDLE_RECHECK_SECONDS", 0.01
    )

    task = asyncio.create_task(client._run_forever())  # noqa: SLF001
    await asyncio.sleep(0.08)
    client._stopping = True  # noqa: SLF001
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert client.reconnect_count == 0


async def test_subscribing_wakes_an_idle_client_immediately(client, monkeypatch):
    """A universe window opening at 14:45 must reach the feed at 14:45.

    `subscribe()` sets the event rather than leaving it to the recheck, so the
    wait is not the mechanism -- it is only the backstop for a path that adds
    to the set without going through `subscribe()`.
    """
    connected = asyncio.Event()

    async def _connect():
        connected.set()
        await asyncio.sleep(10)

    monkeypatch.setattr(client, "_connect_and_consume", _connect)
    # Deliberately LONG, so passing can only mean the event woke it.
    monkeypatch.setattr(
        "src.market.services.dhan_feed_client.IDLE_RECHECK_SECONDS", 30.0
    )

    task = asyncio.create_task(client._run_forever())  # noqa: SLF001
    await asyncio.sleep(0.02)
    assert client.state == ConnectionState.IDLE

    await client.subscribe([("NSE_EQ", "1234")])
    await asyncio.wait_for(connected.wait(), timeout=1.0)

    client._stopping = True  # noqa: SLF001
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_losing_the_socket_after_the_last_unsubscribe_is_not_an_ERROR(
    client, monkeypatch
):
    """Unsubscribing the last instrument makes Dhan drop the socket.

    That is the expected consequence of unsubscribing, not a connection
    problem, so it must not be reported as one -- and must not spend the
    reconnect budget. Without this the ordinary "market closed, everything
    unsubscribed" transition logs a WARNING that reads like a fault.
    """
    async def _drop():
        raise ConnectionError("no close frame received or sent")

    monkeypatch.setattr(client, "_connect_and_consume", _drop)
    monkeypatch.setattr(
        "src.market.services.dhan_feed_client.IDLE_RECHECK_SECONDS", 0.01
    )
    # The reconnect backoff is a second by default, which would make this test
    # about the clock rather than about the behaviour.
    _shrink_backoff(monkeypatch)
    client._pending_subscribe.append(("NSE_EQ", "1234"))  # noqa: SLF001

    task = asyncio.create_task(client._run_forever())  # noqa: SLF001
    await asyncio.sleep(0.02)
    # Everything goes away while it is "connected".
    client._pending_subscribe.clear()  # noqa: SLF001
    client._subscribed.clear()  # noqa: SLF001
    await asyncio.sleep(0.15)

    client._stopping = True  # noqa: SLF001
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert client.state == ConnectionState.IDLE
    assert client.state != ConnectionState.RECONNECTING


def test_IDLE_is_its_own_state_and_not_an_alias_for_DISCONNECTED():
    """Three states where two would lie.

    DISCONNECTED is a failure and IDLE is not, so they must not be the same
    value -- the header indicator colours one red and the other grey, and the
    system health page reports one as a problem and the other as normal.
    """
    assert ConnectionState.IDLE.value == "IDLE"
    assert ConnectionState.IDLE != ConnectionState.DISCONNECTED
    assert ConnectionState.IDLE != ConnectionState.DISABLED


def test_the_system_health_page_does_not_call_an_idle_feed_a_problem():
    """Off is not broken.

    A permanent entry on the problems list for a system working exactly as
    configured is how a problems list stops being read.
    """
    from src.health.services.health_service import summarise

    idle = summarise(
        {
            "upstreamFeed": {
                "hasUpstreamConnection": True,
                "state": ConnectionState.IDLE.value,
            },
            "tasks": {},
        }
    )["problems"]
    assert not any("Upstream feed" in one for one in idle)

    # A genuine drop is still reported.
    dropped = summarise(
        {
            "upstreamFeed": {
                "hasUpstreamConnection": True,
                "state": ConnectionState.RECONNECTING.value,
            },
            "tasks": {},
        }
    )["problems"]
    assert any("Upstream feed is RECONNECTING" in one for one in dropped)


# --- the universe window ----------------------------------------------------


def test_a_universe_window_transition_is_detected_and_is_EDGE_triggered():
    """The other half, without which the guard would leave BTST dark for ever.

    Edge-triggered: the window opening costs ONE resync rather than one every
    five seconds for the fifty minutes it stays open.
    """
    registry = get_strategy_registry()
    definition = registry.require(BTST)
    assert definition.subscription.kind == SubscriptionPolicy.UNIVERSE

    manager = FeedManager()
    try:
        registry.set_enabled(BTST, True)

        # First pass inside the window: a transition from "unknown".
        manager._window_open.clear()  # noqa: SLF001
        import src.market.services.feed_manager as module

        inside = definition.subscription.window_opens_at
        assert inside is not None

        class _Clock:
            def __init__(self, at):
                self._at = at

            def time(self):
                return self._at

        def _at(moment):
            return lambda: _Clock(moment)

        module_time = "src.core.time_utils.ist_now"

        import unittest.mock as mock

        with mock.patch(module_time, _at(time(15, 0))):
            assert manager._universe_window_changed() == BTST  # noqa: SLF001
            # SECOND pass, same window: no transition, so no resync.
            assert manager._universe_window_changed() is None  # noqa: SLF001

        # It closes again, which is also a transition -- the universe has to
        # come OFF the feed, or the cost this window exists to bound is paid
        # all day anyway.
        with mock.patch(module_time, _at(time(19, 0))):
            assert manager._universe_window_changed() == BTST  # noqa: SLF001
            assert manager._universe_window_changed() is None  # noqa: SLF001
    finally:
        manager._window_open.clear()  # noqa: SLF001


def test_a_positions_strategy_never_triggers_a_window_resync():
    """The rotation has no window, and must not acquire one by accident.

    It is resynced by the events that change what it HOLDS -- a fill, a toggle,
    a rebalance pinning names -- and adding a clock to that would be a resync
    every five seconds for a strategy whose target set had not moved.
    """
    registry = get_strategy_registry()
    manager = FeedManager()
    try:
        registry.set_enabled(BTST, False)
        registry.set_enabled("nse-swing-momentum", True)
        manager._window_open.clear()  # noqa: SLF001
        assert manager._universe_window_changed() is None  # noqa: SLF001
    finally:
        manager._window_open.clear()  # noqa: SLF001
