"""The inactivity watchdog, and the one case where silence proves nothing.

The watchdog forces a reconnect when a connected feed goes quiet, because a
socket that is open but dead is indistinguishable from a quiet market in the
UI. That is right whenever the client has asked for data.

It is wrong when it has not. Dhan's protocol pings are handled inside the
websockets library and never reach the message loop, so the only thing that
updates `last_message_ms` is a data frame for a subscribed instrument. With an
empty subscription set the timer drains on a perfectly healthy socket.

Observed on 2026-09-18: nine forced reconnects in six minutes, after MCX crude
was switched off and the swing rotation -- which subscribes only what it holds,
and held nothing -- was switched on.
"""
import asyncio

import pytest

from src.constants import ConnectionState
from src.market.services.dhan_feed_client import DhanFeedClient
from src.market.services.market_book import MarketBook, now_ms


class _FakeSocket:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


def _quiet_client():
    """A connected client whose last message is far outside the window."""
    client = DhanFeedClient(book=MarketBook())
    client._websocket = _FakeSocket()          # noqa: SLF001 - test double
    client.state = ConnectionState.CONNECTED
    client.last_message_ms = now_ms() - 120_000
    return client


async def _run_one_watchdog_pass(client):
    """Let the watchdog take exactly one look, then stop it."""
    task = asyncio.create_task(client._watchdog())  # noqa: SLF001
    await asyncio.sleep(5.2)
    client._stopping = True                        # noqa: SLF001
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_a_quiet_feed_with_subscriptions_is_reconnected():
    """The behaviour the watchdog exists for, unchanged."""
    client = _quiet_client()
    client._subscribed = {("MCX_COMM", "565899")}  # noqa: SLF001

    await _run_one_watchdog_pass(client)

    assert client._websocket.closed is True, "a dead socket must be dropped"


async def test_a_quiet_feed_with_nothing_subscribed_is_left_alone():
    """Nothing was asked for, so nothing arriving proves nothing.

    Without this the watchdog reconnects every 45 seconds for ever, burning
    one of Dhan's five connection slots on a loop and turning the dead-feed
    signal into constant noise.
    """
    client = _quiet_client()
    client._subscribed = set()  # noqa: SLF001

    await _run_one_watchdog_pass(client)

    assert client._websocket.closed is False


def test_the_health_card_withholds_headroom_when_nothing_is_subscribed():
    """Same honesty rule as the synthetic card, in the other direction.

    A countdown that drains to zero on a healthy socket is worse than no
    countdown: it reads as an imminent failure that is not coming.
    """
    from src.health.services import health_service

    class _Manager:
        is_synthetic = False
        last_error = None

    status = {
        "state": ConnectionState.CONNECTED.value,
        "lastMessageAgeMs": 39_000,
        "subscribed": 0,
    }
    card = health_service._upstream_feed_health(_Manager(), status)  # noqa: SLF001

    assert card["inactivityHeadroomMs"] is None
    assert card["inactivityTimeoutSeconds"] is None
    assert "nothing is subscribed" in card["upstreamNote"]


def test_the_health_card_reports_headroom_once_something_is_subscribed():
    from src.health.services import health_service

    class _Manager:
        is_synthetic = False
        last_error = None

    status = {
        "state": ConnectionState.CONNECTED.value,
        "lastMessageAgeMs": 39_000,
        "subscribed": 165,
    }
    card = health_service._upstream_feed_health(_Manager(), status)  # noqa: SLF001

    assert card["inactivityHeadroomMs"] == pytest.approx(1000.0)
    assert card["upstreamNote"] is None
