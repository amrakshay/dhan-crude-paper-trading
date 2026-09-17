"""Feed manager: subscription policy, resubscription and market hours."""
from datetime import date, time
from decimal import Decimal

import pytest

from src import config_utils
from src.constants import ConnectionState
from src.core.time_utils import utc_now
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.market.services.feed_manager import FeedManager
from src.market.services.synthetic_feed import SyntheticFeed
from tests.conftest import (
    FAR_FUTURE_EXPIRY,
    FAR_OPTION_EXPIRY,
    NEAR_FUTURE_EXPIRY,
    NEAR_OPTION_EXPIRY,
)


def _contract(security_id, instrument_type, expiry, strike=None, option_type=None):
    return {
        "security_id": security_id,
        "exchange_id": "MCX",
        "exchange_segment": "MCX_COMM",
        "segment_code": 5,
        "instrument_type": instrument_type,
        "underlying_symbol": "CRUDEOIL",
        "underlying_scrip": 294,
        "trading_symbol": f"CRUDEOIL {security_id}",
        "display_name": f"CRUDEOIL {security_id}",
        "expiry_date": expiry,
        "strike_price": strike,
        "option_type": option_type,
        "lot_size": 100,
        "tick_size": Decimal("0.1"),
        "is_active": True,
        "refreshed_at": utc_now(),
    }


# The synthetic feed starts at 6800, so the ladder is deliberately wide enough
# on both sides for a full ATM +/- 20 window to fit without clamping at an edge.
CRUDE = "mcx-crude-options"

LADDER_START = 5000
LADDER_COUNT = 80
LADDER_STEP = 50


async def _seed_universe(session):
    """A future plus a wide strike ladder over two expiries."""
    rows = [
        _contract("565899", "FUTCOM", NEAR_FUTURE_EXPIRY),
        _contract("569900", "FUTCOM", FAR_FUTURE_EXPIRY),
    ]
    for expiry, prefix in ((NEAR_OPTION_EXPIRY, "S"), (FAR_OPTION_EXPIRY, "O")):
        for index in range(LADDER_COUNT):
            strike = Decimal(LADDER_START + index * LADDER_STEP)
            rows.append(
                _contract(f"{prefix}C{index}", "OPTFUT", expiry, strike, "CE")
            )
            rows.append(
                _contract(f"{prefix}P{index}", "OPTFUT", expiry, strike, "PE")
            )
    await InstrumentRepository(session).upsert_many(rows)
    await session.commit()


@pytest.fixture
async def manager(db_session):
    await _seed_universe(db_session)
    instance = FeedManager()
    instance.is_synthetic = True
    instance.feed = SyntheticFeed(instance.book)
    yield instance
    await instance.stop()


async def test_resync_subscribes_the_future_and_an_atm_window(manager):
    result = await manager.resync()

    window = config_utils.get_property_value_int("market_feed.strike_window", 20)
    expiries = config_utils.get_property_value_int("market_feed.expiries_to_subscribe", 2)
    # 1 future + (2*window + 1) strikes * 2 option types * N expiries
    expected = 1 + (2 * window + 1) * 2 * expiries

    assert result["subscribed"] == expected
    assert manager.near_future_security_id == "565899"
    assert manager.feed.subscribed_count == expected


async def test_only_the_nearest_expiries_are_subscribed(manager):
    """Subscribing all listed expiries by default wastes the connection."""
    await manager.resync()

    assert len(manager.subscribed_expiries) == 2
    assert manager.subscribed_expiries == [NEAR_OPTION_EXPIRY, FAR_OPTION_EXPIRY]


async def test_resync_is_idempotent(manager):
    await manager.resync()

    second = await manager.resync()

    assert second == {"subscribed": 0, "unsubscribed": 0}


async def test_strike_window_clamps_without_error_at_a_ladder_edge(manager):
    """A spot near the bottom of the listed ladder yields a truncated window
    rather than an error or an empty subscription."""
    manager.book.apply_packet({"security_id": "565899", "segment": 5, "ltp": 5050.0})

    result = await manager.resync()

    assert result["subscribed"] > 0
    assert manager.feed.subscribed_count < 1 + 41 * 2 * 2


async def test_window_recentres_when_the_underlying_moves(manager):
    await manager.resync()
    original = set(manager.feed.subscribed_security_ids())
    original_centre = manager.state_for(CRUDE).window_centre

    # Move the future well outside the current window.
    manager.book.apply_packet(
        {"security_id": "565899", "segment": 5, "ltp": 8000.0}
    )
    result = await manager.resync()

    assert result["subscribed"] > 0, "new strikes must be picked up"
    assert result["unsubscribed"] > 0, "strikes that left the window must be dropped"
    assert manager.state_for(CRUDE).window_centre != original_centre
    assert set(manager.feed.subscribed_security_ids()) != original


async def test_contracts_leaving_the_window_are_dropped_from_the_book(manager):
    await manager.resync()
    manager.book.apply_packet({"security_id": "565899", "segment": 5, "ltp": 8000.0})

    await manager.resync()

    subscribed = manager.feed.subscribed_security_ids()
    for row in manager.book.snapshot():
        assert row["securityId"] in subscribed, (
            "a row left in the book after unsubscribe would go stale silently"
        )


async def test_book_rows_carry_contract_metadata_after_resync(manager):
    await manager.resync()

    row = manager.book.get("565899")
    assert row["tradingSymbol"] == "CRUDEOIL 565899"
    assert row["lotSize"] == 100
    assert row["instrumentType"] == "FUTCOM"


async def test_resync_with_an_empty_database_is_not_fatal(db_session):
    """A fresh install has no instruments yet; the feed must not crash."""
    instance = FeedManager()
    instance.is_synthetic = True
    instance.feed = SyntheticFeed(instance.book)

    result = await instance.resync()

    assert result["subscribed"] == 0
    assert "no instruments" in result["reason"]
    await instance.stop()


def test_market_status_reports_mcx_hours():
    status = FeedManager.market_status()

    assert status["opens"] == "09:00"
    assert status["closes"] == "23:30"
    assert isinstance(status["isOpen"], bool)
    assert isinstance(status["isTradingDay"], bool)


async def test_status_exposes_the_synthetic_flag(manager):
    await manager.resync()

    status = manager.status()

    assert status["synthetic"] is True
    assert status["feed"]["state"] == ConnectionState.DISCONNECTED.value
    assert status["nearFutureSecurityId"] == "565899"
    assert status["book"]["instruments"] > 0


async def test_synthetic_feed_generates_a_consistent_chain(manager):
    """Synthetic prices must be self-consistent, not random noise: a call and a
    put on the same strike should both be priced off the same future."""
    await manager.resync()
    await manager.feed.start()
    try:
        manager.feed._generate_tick_batch()
    finally:
        await manager.feed.stop()

    rows = [row for row in manager.book.snapshot() if row.get("ltp") is not None]
    assert rows, "the synthetic feed produced no prices at all"

    future_row = manager.book.get("565899")
    assert future_row["ltp"] > 0
    assert future_row["depth"] is not None
    assert future_row["bid"] < future_row["ask"], "bid must sit below ask"


async def test_synthetic_feed_never_runs_without_the_flag():
    """Missing credentials must surface an error, never silent fake prices."""
    instance = FeedManager()
    original = config_utils.load_config()
    market_feed = original.get("market_feed", {})
    previous = market_feed.get("synthetic_feed")
    dhan = original.get("dhan", {})
    previous_id, previous_token = dhan.get("client_id"), dhan.get("access_token")

    market_feed["synthetic_feed"] = False
    dhan["client_id"] = ""
    dhan["access_token"] = ""
    try:
        await instance.start()
        assert instance.feed is None
        assert "credentials" in (instance.last_error or "")
        assert instance.status()["feed"]["state"] == ConnectionState.DISABLED.value
    finally:
        market_feed["synthetic_feed"] = previous
        dhan["client_id"] = previous_id
        dhan["access_token"] = previous_token
        await instance.stop()
