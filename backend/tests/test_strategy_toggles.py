"""Off has to mean something.

A disabled strategy must stop COSTING: no instruments on the shared feed
connection, no greeks poll against the Dhan token, no chart fetches, no
background work, and its live pages gone. What it must NOT stop is history:
its past orders, its P&L and its totals are exactly what they were.

Resting orders and armed brackets are frozen rather than cancelled, and the
toggle warns about that before it is flipped.
"""
from decimal import Decimal

import pytest

from src.core.time_utils import utc_now
from src.database.session import get_session_factory, session_scope
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.market.services.feed_manager import FeedManager, get_feed_manager
from src.market.services.synthetic_feed import SyntheticFeed
from src.strategies.services.strategy_registry import get_strategy_registry
from tests.conftest import NEAR_OPTION_EXPIRY

CRUDE = "mcx-crude-options"
SECURITY_ID = "576375"
SYMBOL = "CRUDEOIL 6800 CALL"

BOOK_DEPTH = [
    (500, 500, 5, 5, 50.0, 51.0),
    (0, 0, 0, 0, 0.0, 0.0),
    (0, 0, 0, 0, 0.0, 0.0),
    (0, 0, 0, 0, 0.0, 0.0),
    (0, 0, 0, 0, 0.0, 0.0),
]


@pytest.fixture(autouse=True)
def restore_state():
    """Every test here flips a global; none of them may leak it."""
    registry = get_strategy_registry()
    strategies = registry.strategy_states()
    capabilities = registry.capability_states()
    yield
    registry.apply_state(strategies, capabilities)


async def _seed_instrument():
    session = get_session_factory()()
    try:
        await InstrumentRepository(session).upsert_many(
            [
                {
                    "security_id": SECURITY_ID,
                    "exchange_id": "MCX", "exchange_segment": "MCX_COMM",
                    "segment_code": 5, "instrument_type": "OPTFUT",
                    "underlying_symbol": "CRUDEOIL", "underlying_scrip": 294,
                    "trading_symbol": SYMBOL, "display_name": SYMBOL,
                    "expiry_date": NEAR_OPTION_EXPIRY,
                    "strike_price": Decimal("6800"), "option_type": "CE",
                    "lot_size": 100, "tick_size": Decimal("0.1"),
                    "is_active": True, "refreshed_at": utc_now(),
                }
            ]
        )
        await session.commit()
    finally:
        await session.close()


def _seed_book():
    book = get_feed_manager().book
    book.clear()
    book.register_instrument(
        SECURITY_ID,
        {
            "tradingSymbol": SYMBOL, "optionType": "CE", "strikePrice": 6800.0,
            "lotSize": 100, "tickSize": 0.1, "strategyKey": CRUDE,
        },
    )
    book.apply_packet(
        {"security_id": SECURITY_ID, "segment": 5, "ltp": 50.5, "depth": BOOK_DEPTH}
    )


@pytest.fixture
async def ready(auth_client):
    await _seed_instrument()
    _seed_book()
    yield auth_client
    get_feed_manager().book.clear()


# --- the feed ---------------------------------------------------------------
async def test_a_disabled_strategy_contributes_no_subscription_targets(db_session):
    """The concrete cost: instruments on the one shared connection."""
    from src.instruments.database.db_operations.instrument_repository import (
        InstrumentRepository as Repo,
    )

    rows = [
        {
            "security_id": "565899", "exchange_id": "MCX",
            "exchange_segment": "MCX_COMM", "segment_code": 5,
            "instrument_type": "FUTCOM", "underlying_symbol": "CRUDEOIL",
            "underlying_scrip": 294, "trading_symbol": "CRUDEOIL FUT",
            "display_name": "CRUDEOIL FUT", "expiry_date": NEAR_OPTION_EXPIRY,
            "strike_price": None, "option_type": None, "lot_size": 100,
            "tick_size": Decimal("1.0"), "is_active": True,
            "refreshed_at": utc_now(),
        },
    ]
    for index in range(6):
        strike = Decimal(6700 + index * 50)
        for option_type, prefix in (("CE", "C"), ("PE", "P")):
            rows.append(
                {
                    "security_id": f"{prefix}{index}", "exchange_id": "MCX",
                    "exchange_segment": "MCX_COMM", "segment_code": 5,
                    "instrument_type": "OPTFUT", "underlying_symbol": "CRUDEOIL",
                    "underlying_scrip": 294,
                    "trading_symbol": f"CRUDEOIL {strike} {option_type}",
                    "display_name": f"CRUDEOIL {strike} {option_type}",
                    "expiry_date": NEAR_OPTION_EXPIRY, "strike_price": strike,
                    "option_type": option_type, "lot_size": 100,
                    "tick_size": Decimal("0.1"), "is_active": True,
                    "refreshed_at": utc_now(),
                }
            )
    await Repo(db_session).upsert_many(rows)
    await db_session.commit()

    manager = FeedManager()
    manager.is_synthetic = True
    manager.feed = SyntheticFeed(manager.book)
    try:
        registry = get_strategy_registry()

        enabled_targets, _meta = await manager._resolve_targets()  # noqa: SLF001
        assert len(enabled_targets) > 0

        registry.set_enabled(CRUDE, False)
        disabled_targets, disabled_meta = await manager._resolve_targets()  # noqa: SLF001

        assert disabled_targets == []
        assert disabled_meta == {}
        assert manager.strategy_state == {}, "its per-strategy state goes too"
    finally:
        await manager.stop()


async def test_disabling_every_strategy_unsubscribes_the_feed(db_session):
    """Freeing the shared connection is the whole point of switching one off.

    The empty target set used to mean only one thing -- the instrument master
    has not been ingested -- and resync protected the live subscription by
    doing nothing. It now means two things, and telling them apart is what
    makes "off" actually free anything.
    """
    from src.instruments.database.db_operations.instrument_repository import (
        InstrumentRepository as Repo,
    )

    rows = [
        {
            "security_id": "565899", "exchange_id": "MCX",
            "exchange_segment": "MCX_COMM", "segment_code": 5,
            "instrument_type": "FUTCOM", "underlying_symbol": "CRUDEOIL",
            "underlying_scrip": 294, "trading_symbol": "CRUDEOIL FUT",
            "display_name": "CRUDEOIL FUT", "expiry_date": NEAR_OPTION_EXPIRY,
            "strike_price": None, "option_type": None, "lot_size": 100,
            "tick_size": Decimal("1.0"), "is_active": True,
            "refreshed_at": utc_now(),
        },
    ]
    for index in range(4):
        strike = Decimal(6700 + index * 50)
        rows.append(
            {
                "security_id": f"C{index}", "exchange_id": "MCX",
                "exchange_segment": "MCX_COMM", "segment_code": 5,
                "instrument_type": "OPTFUT", "underlying_symbol": "CRUDEOIL",
                "underlying_scrip": 294,
                "trading_symbol": f"CRUDEOIL {strike} CE",
                "display_name": f"CRUDEOIL {strike} CE",
                "expiry_date": NEAR_OPTION_EXPIRY, "strike_price": strike,
                "option_type": "CE", "lot_size": 100,
                "tick_size": Decimal("0.1"), "is_active": True,
                "refreshed_at": utc_now(),
            }
        )
    await Repo(db_session).upsert_many(rows)
    await db_session.commit()

    manager = FeedManager()
    manager.is_synthetic = True
    manager.feed = SyntheticFeed(manager.book)
    try:
        first = await manager.resync()
        assert first["subscribed"] > 0
        assert manager.feed.subscribed_count > 0

        get_strategy_registry().set_enabled(CRUDE, False)
        after = await manager.resync()

        assert after["unsubscribed"] > 0
        assert after["reason"] == "no strategy is enabled"
        assert manager.feed.subscribed_count == 0, (
            "an instrument nobody is looking at still costs bandwidth and still "
            "counts against the one connection"
        )
    finally:
        await manager.stop()


async def test_a_disabled_strategy_is_not_polled_for_greeks(ready):
    """No outbound Dhan REST call, which is the point -- it is token spend."""
    poller = get_feed_manager().greeks_poller
    registry = get_strategy_registry()

    assert any(strategy.key == CRUDE for strategy in poller._strategies())  # noqa: SLF001

    registry.set_enabled(CRUDE, False)
    assert poller._strategies() == []  # noqa: SLF001
    assert poller._enabled() is False  # noqa: SLF001


async def test_turning_the_greeks_capability_off_stops_the_poll_for_everyone(ready):
    poller = get_feed_manager().greeks_poller
    registry = get_strategy_registry()

    registry.set_capability_enabled("greeks", False)

    assert poller._strategies() == []  # noqa: SLF001


# --- the endpoints ----------------------------------------------------------
async def test_a_disabled_strategy_refuses_new_orders_with_a_reason(ready):
    client = ready
    get_strategy_registry().set_enabled(CRUDE, False)

    response = await client.post(
        "/api/orders",
        json={"securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET", "lots": 1},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "switched off" in detail
    assert "history is unchanged" in detail


async def test_a_disabled_strategys_chart_is_not_fetched(ready):
    client = ready
    get_strategy_registry().set_enabled(CRUDE, False)

    response = await client.get(f"/api/market/candles?securityId={SECURITY_ID}")

    # 409, not 404 or 502: the request is well formed and the upstream is fine.
    assert response.status_code == 409
    assert "switched off" in response.json()["detail"]


async def test_turning_the_price_chart_off_refuses_candles_for_every_strategy(ready):
    client = ready
    get_strategy_registry().set_capability_enabled("price-chart", False)

    response = await client.get(f"/api/market/candles?securityId={SECURITY_ID}")

    assert response.status_code == 409
    assert "price chart is switched off" in response.json()["detail"]


async def test_chart_trading_refuses_when_its_capability_is_off(ready):
    client = ready
    get_strategy_registry().set_capability_enabled("chart-trading", False)

    response = await client.post(
        "/api/chart-trading/click",
        json={"securityId": "565899", "side": "BUY"},
    )

    assert response.status_code == 400
    assert "switched off" in response.json()["detail"]


# --- the pages --------------------------------------------------------------
async def test_a_disabled_strategys_live_pages_disappear_from_the_session(ready):
    client = ready
    registry = get_strategy_registry()

    before = (await client.get("/api/auth/me")).json()["pages"]
    assert "/live" in before and "/chain" in before

    registry.set_enabled(CRUDE, False)
    after = (await client.get("/api/auth/me")).json()["pages"]

    assert "/live" not in after
    assert "/chain" not in after
    # History and administration are untouched.
    assert "/orders" in after
    assert "/positions" in after
    assert "/settings" in after


async def test_history_pages_survive_every_strategy_being_switched_off(ready):
    """Decision 3, on the sidebar.

    Reports and Trade Notes show HISTORY. Switching the last strategy off is
    exactly when someone wants to look at what it did, so those pages follow
    their own capability and nothing else. Only the LIVE pages go.
    """
    client = ready
    get_strategy_registry().set_enabled(CRUDE, False)

    pages = (await client.get("/api/auth/me")).json()["pages"]

    assert "/live" not in pages, "nothing live to show"
    assert "/chain" not in pages, "a live quote screen with no quotes"
    assert "/reports" in pages
    assert "/notes" in pages
    assert "/orders" in pages
    assert "/positions" in pages


async def test_turning_the_reports_capability_off_hides_only_that_page(ready):
    client = ready
    get_strategy_registry().set_capability_enabled("pnl-reports", False)

    pages = (await client.get("/api/auth/me")).json()["pages"]

    assert "/reports" not in pages
    assert "/notes" in pages
    assert "/live" in pages


# --- what off must NOT do ---------------------------------------------------
async def test_the_history_of_a_disabled_strategy_is_still_readable(ready):
    client = ready
    placed = await client.post(
        "/api/orders",
        json={"securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET", "lots": 1},
    )
    assert placed.status_code == 201

    before_orders = (await client.get("/api/orders")).json()
    before_report = (await client.get("/api/reports/pnl")).json()

    get_strategy_registry().set_enabled(CRUDE, False)

    after_orders = (await client.get("/api/orders")).json()
    after_report = (await client.get("/api/reports/pnl")).json()

    assert after_orders["total"] == before_orders["total"] == 1
    assert after_report["totalCharges"] == before_report["totalCharges"]
    assert after_report["realisedNet"] == before_report["realisedNet"]


async def test_an_open_position_can_still_be_closed_after_its_strategy_is_off(ready):
    """Off must not trap a trader in a position it opened."""
    client = ready
    await client.post(
        "/api/orders",
        json={"securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET", "lots": 1},
    )
    positions = (await client.get("/api/positions")).json()["positions"]

    get_strategy_registry().set_enabled(CRUDE, False)

    response = await client.post(
        f"/api/positions/{positions[0]['id']}/close", json={"orderType": "MARKET"}
    )

    assert response.status_code == 200, response.text


async def test_the_health_page_does_not_report_a_missing_task_when_greeks_are_off(
    ready,
):
    """A false problem on the page whose job is to surface real ones."""
    from src.health.services.task_inspector import expected_task_names

    get_strategy_registry().set_capability_enabled("greeks", False)

    expected = expected_task_names(is_synthetic=True, feed_running=True)

    assert "greeks-poller" not in expected


async def test_the_health_page_reports_which_strategies_are_off(ready):
    client = ready
    get_strategy_registry().set_enabled(CRUDE, False)

    features = (await client.get("/api/healthcheck/system")).json()["features"]

    assert CRUDE in features["disabledStrategies"]
    assert features["strategies"][0]["enabled"] is False


# --- warnings ---------------------------------------------------------------
async def test_disabling_with_open_positions_warns_before_it_happens(ready):
    client = ready
    await client.post(
        "/api/orders",
        json={"securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET", "lots": 1},
    )

    warnings = (
        await client.get(f"/api/strategies/{CRUDE}/disable-warnings")
    ).json()["warnings"]

    assert any("stop being marked" in warning for warning in warnings)


async def test_disabling_with_resting_orders_says_they_are_frozen_not_cancelled(ready):
    client = ready
    await client.post(
        "/api/orders",
        json={
            "securityId": SECURITY_ID, "side": "BUY", "orderType": "LIMIT",
            "lots": 1, "limitPrice": "10.0",
        },
    )

    warnings = (
        await client.get(f"/api/strategies/{CRUDE}/disable-warnings")
    ).json()["warnings"]

    resting = [warning for warning in warnings if "resting order" in warning]
    assert resting, warnings
    assert "Nothing is cancelled" in resting[0]


async def test_the_toggle_endpoint_is_admin_only(api_client):
    from tests.conftest import SEED_ADMIN_EMAIL, SEED_ADMIN_PASSWORD, login_as

    await login_as(api_client, SEED_ADMIN_EMAIL, SEED_ADMIN_PASSWORD)
    await api_client.post(
        "/api/users",
        json={
            "email": "plain@abc.com", "firstName": "Plain", "lastName": "User",
            "password": "plain-user-password", "role": "ROLE_USER",
        },
    )
    await login_as(api_client, "plain@abc.com", "plain-user-password")

    # Reading is fine: other pages need the labels.
    assert (await api_client.get("/api/strategies")).status_code == 200
    assert (
        await api_client.put(
            f"/api/strategies/{CRUDE}/enabled", json={"enabled": False}
        )
    ).status_code == 403


# --- toggling through the API applies it live --------------------------------
async def test_toggling_through_the_api_takes_effect_immediately(ready):
    client = ready
    registry = get_strategy_registry()

    response = await client.put(
        f"/api/strategies/{CRUDE}/enabled", json={"enabled": False}
    )

    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is False
    # Not "on the next restart": the registry the running process reads is
    # already updated.
    assert registry.is_enabled(CRUDE) is False

    back_on = await client.put(
        f"/api/strategies/{CRUDE}/enabled", json={"enabled": True}
    )
    assert back_on.json()["enabled"] is True
    assert registry.is_enabled(CRUDE) is True


async def test_the_stored_state_survives_a_reload(ready):
    """The toggle is a row, so a restart does not undo it."""
    from src.strategies.services.strategy_state_service import StrategyStateService

    client = ready
    await client.put(f"/api/strategies/{CRUDE}/enabled", json={"enabled": False})

    # Simulate a restart: defaults come back, then the stored state is applied.
    registry = get_strategy_registry()
    registry.apply_state({CRUDE: True}, {})
    assert registry.is_enabled(CRUDE) is True

    async with session_scope() as session:
        await StrategyStateService(session).apply_stored_state()

    assert registry.is_enabled(CRUDE) is False
