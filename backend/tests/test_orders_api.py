"""Order and position API: placement, preview, lifecycle and safety."""
from datetime import date
from decimal import Decimal

import pytest

from src.core.time_utils import utc_now
from src.database.session import get_session_factory
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.market.services.feed_manager import get_feed_manager

SECURITY_ID = "576375"
SYMBOL = "CRUDEOIL 17 SEP 6800 CALL"

BOOK_DEPTH = [
    (100, 50, 3, 2, 50.0, 51.0),
    (200, 80, 4, 3, 49.9, 51.1),
    (300, 120, 5, 4, 49.8, 51.2),
    (0, 0, 0, 0, 0.0, 0.0),
    (0, 0, 0, 0, 0.0, 0.0),
]


async def _seed_instrument():
    session = get_session_factory()()
    try:
        await InstrumentRepository(session).upsert_many(
            [
                {
                    "security_id": SECURITY_ID,
                    "exchange_id": "MCX",
                    "exchange_segment": "MCX_COMM",
                    "segment_code": 5,
                    "instrument_type": "OPTFUT",
                    "underlying_symbol": "CRUDEOIL",
                    "underlying_scrip": 294,
                    "trading_symbol": SYMBOL,
                    "display_name": SYMBOL,
                    "expiry_date": date(2026, 9, 17),
                    "strike_price": Decimal("6800"),
                    "option_type": "CE",
                    "lot_size": 100,
                    "tick_size": Decimal("0.1"),
                    "is_active": True,
                    "refreshed_at": utc_now(),
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
            "expiryDate": "2026-09-17", "lotSize": 100, "tickSize": 0.1,
        },
    )
    book.apply_packet(
        {"security_id": SECURITY_ID, "segment": 5, "ltp": 50.5, "depth": BOOK_DEPTH}
    )
    return book


@pytest.fixture
async def ready_client(auth_client):
    await _seed_instrument()
    _seed_book()
    yield auth_client
    get_feed_manager().book.clear()


# --- auth ------------------------------------------------------------------
async def test_order_endpoints_require_authentication(api_client):
    assert (await api_client.get("/api/orders")).status_code == 401
    assert (await api_client.post("/api/orders", json={})).status_code == 401
    assert (await api_client.get("/api/positions")).status_code == 401


# --- preview ---------------------------------------------------------------
async def test_preview_does_not_create_an_order(ready_client):
    response = await ready_client.post(
        "/api/orders/preview",
        json={"securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET", "lots": 1},
    )

    assert response.status_code == 200
    listed = await ready_client.get("/api/orders")
    assert listed.json()["total"] == 0, "a preview must never place anything"


async def test_preview_shows_charges_and_net_debit_before_confirming(ready_client):
    response = await ready_client.post(
        "/api/orders/preview",
        json={"securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET", "lots": 1},
    )
    body = response.json()

    assert body["charges"] is not None
    assert Decimal(body["charges"]["total"]) > 0
    assert Decimal(body["netAmount"]) < 0, "a buy is a debit"
    assert body["estimatedFillQuantity"] == 100


async def test_preview_warns_when_an_order_would_only_partially_fill(ready_client):
    response = await ready_client.post(
        "/api/orders/preview",
        json={"securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET", "lots": 10},
    )
    body = response.json()

    assert body["wouldPartiallyFill"] is True
    assert body["estimatedFillQuantity"] < body["quantity"]


async def test_preview_says_when_a_limit_would_rest(ready_client):
    response = await ready_client.post(
        "/api/orders/preview",
        json={
            "securityId": SECURITY_ID, "side": "BUY",
            "orderType": "LIMIT", "lots": 1, "limitPrice": "10.0",
        },
    )
    body = response.json()

    assert body["wouldRest"] is True
    assert body["estimatedFillQuantity"] == 100


# --- placement -------------------------------------------------------------
async def test_market_buy_fills_at_the_ask_not_the_ltp(ready_client):
    response = await ready_client.post(
        "/api/orders",
        json={"securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET", "lots": 1},
    )
    body = response.json()

    assert response.status_code == 201
    assert body["status"] == "FILLED"
    assert Decimal(body["averageFillPrice"]) > Decimal("50.5"), "LTP was 50.5"


async def test_a_filled_order_records_events_fills_and_charges(ready_client):
    response = await ready_client.post(
        "/api/orders",
        json={"securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET", "lots": 1},
    )
    body = response.json()

    assert [event["eventType"] for event in body["events"]][0] == "PLACED"
    assert body["events"][-1]["eventType"] == "FILLED"
    assert len(body["fills"]) >= 1
    assert body["charges"]["totalCharges"] is not None
    # Millisecond precision is a stated requirement for order history.
    assert "." in body["events"][0]["eventAt"]


async def test_a_sell_pays_ctt_and_no_stamp_duty(ready_client):
    response = await ready_client.post(
        "/api/orders",
        json={"securityId": SECURITY_ID, "side": "SELL", "orderType": "MARKET", "lots": 1},
    )
    charges = response.json()["charges"]

    assert Decimal(charges["ctt"]) > 0
    assert Decimal(charges["stampDuty"]) == 0


async def test_a_buy_pays_stamp_duty_and_no_ctt(ready_client):
    response = await ready_client.post(
        "/api/orders",
        json={"securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET", "lots": 1},
    )
    charges = response.json()["charges"]

    assert Decimal(charges["ctt"]) == 0
    assert Decimal(charges["stampDuty"]) > 0


async def test_an_order_beyond_the_visible_book_partially_fills(ready_client):
    response = await ready_client.post(
        "/api/orders",
        json={"securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET", "lots": 10},
    )
    body = response.json()

    assert body["status"] == "PARTIALLY_FILLED"
    assert body["filledQuantity"] == 250, "50 + 80 + 120 displayed"
    assert body["filledQuantity"] < body["quantity"]


async def test_a_passive_limit_order_rests(ready_client):
    response = await ready_client.post(
        "/api/orders",
        json={
            "securityId": SECURITY_ID, "side": "BUY",
            "orderType": "LIMIT", "lots": 1, "limitPrice": "10.0",
        },
    )
    body = response.json()

    assert body["status"] == "OPEN"
    assert body["filledQuantity"] == 0


async def test_a_marketable_limit_fills_within_its_limit(ready_client):
    response = await ready_client.post(
        "/api/orders",
        json={
            "securityId": SECURITY_ID, "side": "BUY",
            "orderType": "LIMIT", "lots": 1, "limitPrice": "55.0",
        },
    )
    body = response.json()

    assert body["status"] == "FILLED"
    assert Decimal(body["averageFillPrice"]) <= Decimal("55.0")


async def test_an_order_with_no_book_is_rejected_not_filled(auth_client):
    """No liquidity must reject, never invent a price."""
    await _seed_instrument()
    get_feed_manager().book.clear()

    response = await auth_client.post(
        "/api/orders",
        json={"securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET", "lots": 1},
    )
    body = response.json()

    assert body["status"] == "REJECTED"
    assert body["filledQuantity"] == 0
    assert body["rejectionReason"]


# --- validation ------------------------------------------------------------
async def test_unknown_security_is_rejected(ready_client):
    response = await ready_client.post(
        "/api/orders",
        json={"securityId": "does-not-exist", "side": "BUY", "orderType": "MARKET", "lots": 1},
    )
    assert response.status_code == 400


async def test_a_limit_order_without_a_price_is_rejected(ready_client):
    response = await ready_client.post(
        "/api/orders",
        json={"securityId": SECURITY_ID, "side": "BUY", "orderType": "LIMIT", "lots": 1},
    )
    assert response.status_code == 400


async def test_excessive_lots_are_rejected(ready_client):
    response = await ready_client.post(
        "/api/orders",
        json={"securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET", "lots": 100000},
    )
    assert response.status_code == 400


# --- cancel ----------------------------------------------------------------
async def test_a_resting_order_can_be_cancelled(ready_client):
    placed = await ready_client.post(
        "/api/orders",
        json={
            "securityId": SECURITY_ID, "side": "BUY",
            "orderType": "LIMIT", "lots": 1, "limitPrice": "10.0",
        },
    )
    order_id = placed.json()["id"]

    response = await ready_client.post(f"/api/orders/{order_id}/cancel")
    body = response.json()

    assert body["status"] == "CANCELLED"
    assert body["events"][-1]["eventType"] == "CANCELLED", (
        "the response must carry the transition that just happened"
    )


async def test_a_filled_order_cannot_be_cancelled(ready_client):
    placed = await ready_client.post(
        "/api/orders",
        json={"securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET", "lots": 1},
    )
    order_id = placed.json()["id"]

    response = await ready_client.post(f"/api/orders/{order_id}/cancel")
    assert response.status_code == 400


# --- positions -------------------------------------------------------------
async def test_a_fill_creates_a_position_with_a_live_mark(ready_client):
    await ready_client.post(
        "/api/orders",
        json={"securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET", "lots": 1},
    )

    body = (await ready_client.get("/api/positions")).json()

    assert len(body["positions"]) == 1
    position = body["positions"][0]
    assert position["netQuantity"] == 100
    assert position["hasMark"] is True
    assert position["unrealizedPnl"] is not None


async def test_a_position_can_be_partially_closed(ready_client):
    await ready_client.post(
        "/api/orders",
        json={"securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET", "lots": 2},
    )
    position_id = (await ready_client.get("/api/positions")).json()["positions"][0]["id"]

    response = await ready_client.post(
        f"/api/positions/{position_id}/close", json={"lots": 1, "orderType": "MARKET"}
    )
    body = response.json()

    assert response.status_code == 200
    assert body["side"] == "SELL", "closing a long sells"
    assert body["isCloseOrder"] is True

    after = (await ready_client.get("/api/positions")).json()["positions"][0]
    assert after["netQuantity"] == 100


async def test_closing_more_than_is_open_is_rejected(ready_client):
    await ready_client.post(
        "/api/orders",
        json={"securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET", "lots": 1},
    )
    position_id = (await ready_client.get("/api/positions")).json()["positions"][0]["id"]

    response = await ready_client.post(
        f"/api/positions/{position_id}/close", json={"lots": 99, "orderType": "MARKET"}
    )
    assert response.status_code == 400


async def test_position_summary_aggregates_pnl_and_charges(ready_client):
    await ready_client.post(
        "/api/orders",
        json={"securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET", "lots": 1},
    )

    summary = (await ready_client.get("/api/positions")).json()["summary"]

    assert summary["openPositions"] == 1
    assert Decimal(summary["totalCharges"]) > 0
    assert summary["totalUnrealizedPnl"] is not None
    assert summary["netPnl"] is not None


async def test_a_sub_lot_residual_position_can_still_be_closed(ready_client):
    """A market order can partially fill below one lot when the visible book
    runs out. That residual must not become impossible to close."""
    # 10 lots against a book holding only 250 barrels -> a 250-barrel position.
    await ready_client.post(
        "/api/orders",
        json={"securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET", "lots": 10},
    )
    position = (await ready_client.get("/api/positions")).json()["positions"][0]
    assert position["netQuantity"] % 100 != 0, "fixture should leave a sub-lot residual"

    response = await ready_client.post(
        f"/api/positions/{position['id']}/close", json={"orderType": "MARKET"}
    )

    assert response.status_code == 200
    assert response.json()["quantity"] == position["netQuantity"], (
        "closing with no lot count must close the exact open quantity"
    )


async def test_closing_without_lots_closes_the_whole_position(ready_client):
    await ready_client.post(
        "/api/orders",
        json={"securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET", "lots": 1},
    )
    position = (await ready_client.get("/api/positions")).json()["positions"][0]

    await ready_client.post(
        f"/api/positions/{position['id']}/close", json={"orderType": "MARKET"}
    )

    after = (await ready_client.get("/api/positions?includeClosed=true")).json()["positions"]
    assert all(p["netQuantity"] == 0 for p in after)
