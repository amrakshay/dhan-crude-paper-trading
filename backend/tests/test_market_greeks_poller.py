"""Greeks poller: merge semantics, isolation from the tick path, synthetic mode."""
from datetime import date, timedelta

import pytest

from src.core.time_utils import ist_today
from src.market.services.dhan_option_chain_client import OptionChainSnapshot, OptionLeg
from src.market.services.greeks_poller import GreeksPoller
from src.market.services.market_book import MarketBook


class _StubFeedManager:
    def __init__(self, expiries, near_future_security_id="565899"):
        self.subscribed_expiries = expiries
        self.near_future_security_id = near_future_security_id


def _snapshot(expiry="2026-09-17"):
    snapshot = OptionChainSnapshot(underlying_last_price=6805.0, expiry=expiry)
    snapshot.strikes = {
        6800.0: {
            "CE": OptionLeg(
                security_id="576375", last_price=51.8, oi=12400, previous_oi=11900,
                volume=3400, implied_volatility=34.9, delta=0.5196, theta=-12.4,
                gamma=0.0032, vega=1.41, top_bid_price=51.6, top_ask_price=52.0,
            ),
            "PE": OptionLeg(
                security_id="576528", last_price=47.6, oi=9800, previous_oi=10300,
                volume=2900, implied_volatility=35.11, delta=-0.4803, theta=-11.8,
                gamma=0.0032, vega=1.41,
            ),
        }
    }
    return snapshot


@pytest.fixture
def book():
    return MarketBook()


async def test_greeks_merge_onto_existing_rows(book, monkeypatch):
    book.apply_packet({"security_id": "576375", "segment": 5, "ltp": 51.8, "volume": 3400})
    poller = GreeksPoller(book, _StubFeedManager([date(2026, 9, 17)]))

    async def fake_fetch(*args, **kwargs):
        return _snapshot()

    monkeypatch.setattr(poller.client, "fetch_option_chain", fake_fetch)
    merged = await poller._poll_expiry(date(2026, 9, 17))

    row = book.get("576375")
    assert merged == 2
    assert row["ltp"] == 51.8, "the tick-sourced price must survive the merge"
    assert row["iv"] == pytest.approx(34.9)
    assert row["delta"] == pytest.approx(0.5196)
    assert row["oiChange"] == 500
    assert row["greeksSource"] == "dhan"


async def test_greeks_never_overwrite_a_live_field_with_null(book, monkeypatch):
    """A chain response missing a value must not blank what the feed provided."""
    book.apply_packet({"security_id": "576375", "segment": 5, "ltp": 51.8, "oi": 12400})
    poller = GreeksPoller(book, _StubFeedManager([date(2026, 9, 17)]))

    sparse = OptionChainSnapshot(underlying_last_price=6805.0)
    sparse.strikes = {6800.0: {"CE": OptionLeg(security_id="576375", delta=0.52)}}

    async def fake_fetch(*args, **kwargs):
        return sparse

    monkeypatch.setattr(poller.client, "fetch_option_chain", fake_fetch)
    await poller._poll_expiry(date(2026, 9, 17))

    row = book.get("576375")
    assert row["delta"] == pytest.approx(0.52)
    assert row["ltp"] == 51.8
    assert row["oi"] == 12400
    assert "iv" not in row or row["iv"] is not None


async def test_legs_without_a_security_id_are_skipped(book, monkeypatch):
    poller = GreeksPoller(book, _StubFeedManager([date(2026, 9, 17)]))
    snapshot = OptionChainSnapshot()
    snapshot.strikes = {6800.0: {"CE": OptionLeg(security_id=None, delta=0.5)}}

    async def fake_fetch(*args, **kwargs):
        return snapshot

    monkeypatch.setattr(poller.client, "fetch_option_chain", fake_fetch)
    merged = await poller._poll_expiry(date(2026, 9, 17))

    assert merged == 0
    assert book.size == 0


async def test_a_failing_expiry_does_not_stop_the_others(book, monkeypatch):
    """Expiries are polled concurrently; one failure must not lose the rest."""
    expiries = [date(2026, 9, 17), date(2026, 10, 15)]
    poller = GreeksPoller(book, _StubFeedManager(expiries))

    async def fake_fetch(scrip, segment, expiry, **kwargs):
        if expiry == "2026-09-17":
            raise RuntimeError("upstream blew up")
        return _snapshot(expiry)

    monkeypatch.setattr(poller.client, "fetch_option_chain", fake_fetch)
    merged = await poller.poll_once()

    assert merged == 2, "the healthy expiry still merged"
    assert "upstream blew up" in (poller.last_error or "")


async def test_poll_once_with_no_subscribed_expiries_is_a_noop(book):
    poller = GreeksPoller(book, _StubFeedManager([]))
    assert await poller.poll_once() == 0


async def test_underlying_price_is_recorded(book, monkeypatch):
    poller = GreeksPoller(book, _StubFeedManager([date(2026, 9, 17)]))

    async def fake_fetch(*args, **kwargs):
        return _snapshot()

    monkeypatch.setattr(poller.client, "fetch_option_chain", fake_fetch)
    await poller._poll_expiry(date(2026, 9, 17))

    assert poller.underlying_last_price == 6805.0


# --- synthetic mode --------------------------------------------------------
def _seed_synthetic_chain(book, expiry):
    book.register_instrument("565899", {"instrumentType": "FUTCOM"})
    book.apply_packet({"security_id": "565899", "segment": 5, "ltp": 6805.0})
    for option_type, price in (("CE", 51.8), ("PE", 47.6)):
        security_id = f"X{option_type}"
        book.register_instrument(
            security_id,
            {
                "optionType": option_type,
                "strikePrice": 6800.0,
                "expiryDate": expiry.isoformat(),
                "lotSize": 100,
            },
        )
        book.apply_packet({"security_id": security_id, "segment": 5, "ltp": price, "oi": 1000})


async def test_synthetic_greeks_are_computed_and_flagged(book):
    expiry = ist_today() + timedelta(days=1)
    _seed_synthetic_chain(book, expiry)
    poller = GreeksPoller(book, _StubFeedManager([expiry]))
    poller.is_synthetic = True

    merged = poller._merge_synthetic_greeks([expiry])

    assert merged == 2
    call = book.get("XCE")
    assert call["greeksSource"] == "synthetic", (
        "synthetic greeks must be labelled so they are never mistaken for real ones"
    )
    assert call["delta"] > 0
    assert book.get("XPE")["delta"] < 0


async def test_synthetic_greeks_respect_put_call_parity(book):
    """Both legs must price off the same future, or the chain is incoherent."""
    expiry = ist_today() + timedelta(days=1)
    _seed_synthetic_chain(book, expiry)
    poller = GreeksPoller(book, _StubFeedManager([expiry]))
    poller.is_synthetic = True

    poller._merge_synthetic_greeks([expiry])

    delta_call = book.get("XCE")["delta"]
    delta_put = book.get("XPE")["delta"]
    # Black-76: delta_C - delta_P = exp(-rT), which is ~1 for a 1-day option.
    assert delta_call - delta_put == pytest.approx(1.0, abs=0.01)


async def test_synthetic_greeks_need_an_underlying_price(book):
    """Without a future price there is nothing to price options off."""
    expiry = ist_today() + timedelta(days=1)
    poller = GreeksPoller(book, _StubFeedManager([expiry]))
    poller.is_synthetic = True

    assert poller._merge_synthetic_greeks([expiry]) == 0


async def test_synthetic_greeks_ignore_unsubscribed_expiries(book):
    expiry = ist_today() + timedelta(days=1)
    other = ist_today() + timedelta(days=40)
    _seed_synthetic_chain(book, expiry)
    poller = GreeksPoller(book, _StubFeedManager([other]))
    poller.is_synthetic = True

    assert poller._merge_synthetic_greeks([other]) == 0


async def test_poller_does_not_start_without_credentials_or_synthetic_flag(book):
    """No credentials must mean no greeks -- never silently invented ones."""
    from src import config_utils

    config = config_utils.load_config()
    dhan = config.get("dhan", {})
    previous_id, previous_token = dhan.get("client_id"), dhan.get("access_token")
    dhan["client_id"] = ""
    dhan["access_token"] = ""
    try:
        poller = GreeksPoller(book, _StubFeedManager([date(2026, 9, 17)]))
        await poller.start(is_synthetic=False)
        assert poller._task is None
        assert "No Dhan credentials" in (poller.last_error or "")
    finally:
        dhan["client_id"] = previous_id
        dhan["access_token"] = previous_token
        await poller.stop()


async def test_status_reports_cadence_and_counters(book):
    poller = GreeksPoller(book, _StubFeedManager([date(2026, 9, 17)]))
    status = poller.status()

    assert status["intervalSeconds"] == 3.0
    assert status["polls"] == 0
    assert "client" in status
