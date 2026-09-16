"""Fan-out: coalescing, per-client filtering and slow-client handling."""
import asyncio

import pytest

from src.market.services.broadcaster import Broadcaster
from src.market.services.market_book import MarketBook


class _FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(text)


def _packet(security_id, ltp=6800.0, depth=None):
    fields = {"security_id": security_id, "segment": 5, "ltp": ltp}
    if depth is not None:
        fields["depth"] = depth
    return fields


@pytest.fixture
def book():
    return MarketBook()


@pytest.fixture
def broadcaster(book):
    return Broadcaster(book)


def test_client_registration_and_removal(broadcaster):
    client = broadcaster.register(_FakeWebSocket(), "c1")
    assert broadcaster.client_count == 1

    broadcaster.unregister(client)
    assert broadcaster.client_count == 0


def test_one_upstream_book_serves_many_clients(broadcaster, book):
    """The whole point of the fan-out: N tabs, one book, one upstream."""
    clients = [broadcaster.register(_FakeWebSocket(), f"c{i}") for i in range(5)]
    for client in clients:
        client.needs_snapshot = False

    book.apply_packet(_packet("565899", 6801.0))
    broadcaster._flush()

    for client in clients:
        payload = client.queue.get_nowait()
        assert payload["type"] == "update"
        assert payload["rows"][0]["ltp"] == 6801.0


def test_client_filter_limits_what_it_receives(broadcaster, book):
    """The live-price page watches one instrument, not the whole chain."""
    client = broadcaster.register(_FakeWebSocket(), "c1")
    client.configure(["565899"], include_depth=True)
    client.needs_snapshot = False

    book.apply_packet(_packet("565899"))
    book.apply_packet(_packet("576266"))
    broadcaster._flush()

    payload = client.queue.get_nowait()
    assert [row["securityId"] for row in payload["rows"]] == ["565899"]


def test_unfiltered_client_receives_everything(broadcaster, book):
    client = broadcaster.register(_FakeWebSocket(), "c1")
    client.needs_snapshot = False

    book.apply_packet(_packet("565899"))
    book.apply_packet(_packet("576266"))
    broadcaster._flush()

    payload = client.queue.get_nowait()
    assert len(payload["rows"]) == 2


def test_depth_is_dropped_for_clients_that_do_not_render_it(broadcaster, book):
    depth = [(1, 2, 3, 4, 6799.0, 6801.0)] * 5
    client = broadcaster.register(_FakeWebSocket(), "c1")
    client.configure(None, include_depth=False)
    client.needs_snapshot = False

    book.apply_packet(_packet("565899", depth=depth))
    broadcaster._flush()

    row = client.queue.get_nowait()["rows"][0]
    assert "depth" not in row
    assert row["bid"] == 6799.0, "best bid/ask survive; only the ladder is dropped"


def test_dropping_depth_does_not_mutate_the_shared_book_row(broadcaster, book):
    """Two clients share one row object; projection must not be destructive."""
    depth = [(1, 2, 3, 4, 6799.0, 6801.0)] * 5
    book.apply_packet(_packet("565899", depth=depth))

    lite = broadcaster.register(_FakeWebSocket(), "lite")
    lite.configure(None, include_depth=False)
    lite.needs_snapshot = False
    full = broadcaster.register(_FakeWebSocket(), "full")
    full.needs_snapshot = False

    book.apply_packet(_packet("565899", depth=depth))
    broadcaster._flush()

    lite.queue.get_nowait()
    full_row = full.queue.get_nowait()["rows"][0]
    assert full_row["depth"] is not None
    assert book.get("565899")["depth"] is not None


def test_first_flush_sends_a_snapshot(broadcaster, book):
    book.apply_packet(_packet("565899"))
    client = broadcaster.register(_FakeWebSocket(), "c1")

    broadcaster._flush()

    payload = client.queue.get_nowait()
    assert payload["type"] == "snapshot"
    assert len(payload["rows"]) == 1


def test_slow_client_is_resynced_rather_than_backed_up(broadcaster, book):
    """A stale queue would show old prices as though they were current."""
    client = broadcaster.register(_FakeWebSocket(), "slow")
    client.needs_snapshot = False
    # Fill its queue without ever draining it.
    for _ in range(client.queue.maxsize):
        client.queue.put_nowait({"type": "update", "rows": []})

    book.apply_packet(_packet("565899"))
    broadcaster._flush()

    assert client.dropped == 1
    assert client.needs_snapshot is True, "must resync, not deliver a stale backlog"
    assert client.queue.qsize() == 0, "the backlog is discarded"


def test_a_slow_client_does_not_starve_a_healthy_one(broadcaster, book):
    slow = broadcaster.register(_FakeWebSocket(), "slow")
    slow.needs_snapshot = False
    for _ in range(slow.queue.maxsize):
        slow.queue.put_nowait({"type": "update", "rows": []})

    healthy = broadcaster.register(_FakeWebSocket(), "healthy")
    healthy.needs_snapshot = False

    book.apply_packet(_packet("565899", 6802.0))
    broadcaster._flush()

    payload = healthy.queue.get_nowait()
    assert payload["rows"][0]["ltp"] == 6802.0


def test_heartbeat_is_sent_even_with_no_changes(broadcaster, book):
    """The UI ages its last-tick indicator off these; silence must not look
    the same as a dead pipe."""
    client = broadcaster.register(_FakeWebSocket(), "c1")
    client.needs_snapshot = False

    broadcaster._flush()

    payload = client.queue.get_nowait()
    assert payload["type"] == "update"
    assert payload["rows"] == []


def test_status_provider_is_attached_to_payloads(broadcaster, book):
    broadcaster.set_status_provider(lambda: {"state": "SYNTHETIC"})
    client = broadcaster.register(_FakeWebSocket(), "c1")
    client.needs_snapshot = False

    broadcaster._flush()

    assert client.queue.get_nowait()["status"] == {"state": "SYNTHETIC"}
