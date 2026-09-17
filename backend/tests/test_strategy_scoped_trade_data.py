"""Every trade says which strategy it belongs to, and history never moves.

The column is stored rather than derived (see the migration's docstring), so
these tests care about two things: that it is stamped from the CONTRACT at
placement, and that filtering by it never hides history -- including the
history of a strategy that has since been switched off.
"""
from datetime import date
from decimal import Decimal

import pytest

from src.core.time_utils import utc_now
from src.database.session import get_session_factory
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.orders.database.db_models.order_model import Order, OrderCharge, OrderFill
from src.market.services.feed_manager import get_feed_manager
from src.strategies.services.strategy_registry import get_strategy_registry
from tests.conftest import NEAR_OPTION_EXPIRY

CRUDE = "mcx-crude-options"
OTHER = "some-other-strategy"

BOOK_DEPTH = [
    (100, 50, 3, 2, 50.0, 51.0),
    (200, 80, 4, 3, 49.9, 51.1),
    (0, 0, 0, 0, 0.0, 0.0),
    (0, 0, 0, 0, 0.0, 0.0),
    (0, 0, 0, 0, 0.0, 0.0),
]


def _seed_book(security_id="576375"):
    book = get_feed_manager().book
    book.clear()
    book.register_instrument(
        security_id,
        {
            "tradingSymbol": "CRUDEOIL CALL", "optionType": "CE",
            "strikePrice": 6800.0, "lotSize": 100, "tickSize": 0.1,
        },
    )
    book.apply_packet(
        {"security_id": security_id, "segment": 5, "ltp": 50.5, "depth": BOOK_DEPTH}
    )
    return book


async def _seed_instrument(security_id="576375", symbol="CRUDEOIL CALL"):
    session = get_session_factory()()
    try:
        await InstrumentRepository(session).upsert_many(
            [
                {
                    "security_id": security_id,
                    "exchange_id": "MCX",
                    "exchange_segment": "MCX_COMM",
                    "segment_code": 5,
                    "instrument_type": "OPTFUT",
                    "underlying_symbol": "CRUDEOIL",
                    "underlying_scrip": 294,
                    "trading_symbol": symbol,
                    "display_name": symbol,
                    "expiry_date": NEAR_OPTION_EXPIRY,
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


async def _seed_order(strategy_key: str, security_id="576375", price="50"):
    """A filled order with its fill and charges, written directly."""
    session = get_session_factory()()
    try:
        when = utc_now()
        order = Order(
            strategy_key=strategy_key,
            client_order_id=f"{strategy_key}-{security_id}-{when.timestamp()}",
            security_id=security_id,
            trading_symbol=f"{strategy_key} CONTRACT",
            expiry_date=date(2026, 9, 17),
            strike_price=Decimal("6800"),
            option_type="CE",
            lot_size=100,
            side="BUY",
            order_type="MARKET",
            lots=1,
            quantity=100,
            status="FILLED",
            filled_quantity=100,
            average_fill_price=Decimal(price),
            placed_at=when,
            last_event_at=when,
        )
        session.add(order)
        await session.flush()
        session.add(
            OrderFill(
                order_id=order.id, fill_at=when,
                price=Decimal(price), quantity=100,
            )
        )
        session.add(
            OrderCharge(
                order_id=order.id,
                turnover=Decimal(price) * 100,
                brokerage=Decimal("20.00"),
                ctt=Decimal("0"), exchange_transaction_charge=Decimal("0"),
                sebi_turnover_fee=Decimal("0"), stamp_duty=Decimal("0"),
                gst=Decimal("0"), total_charges=Decimal("20.00"),
                rates_version="test",
            )
        )
        await session.commit()
        return order.id
    finally:
        await session.close()


# --- stamping --------------------------------------------------------------
async def test_an_order_is_stamped_with_the_strategy_that_owns_its_contract(
    auth_client,
):
    await _seed_instrument()
    _seed_book()

    response = await auth_client.post(
        "/api/orders",
        json={"securityId": "576375", "side": "BUY", "orderType": "MARKET", "lots": 1},
    )

    assert response.status_code == 201, response.text
    assert response.json()["strategyKey"] == CRUDE


async def test_a_contract_belonging_to_no_strategy_cannot_be_traded(auth_client):
    """Not a 500 and not a silent default -- a specific, actionable refusal."""
    session = get_session_factory()()
    try:
        await InstrumentRepository(session).upsert_many(
            [
                {
                    "security_id": "999999",
                    "exchange_id": "NSE",
                    "exchange_segment": "NSE_FNO",
                    "segment_code": 2,
                    "instrument_type": "OPTIDX",
                    "underlying_symbol": "NIFTY",
                    "underlying_scrip": 13,
                    "trading_symbol": "NIFTY 24000 CALL",
                    "display_name": "NIFTY 24000 CALL",
                    "expiry_date": NEAR_OPTION_EXPIRY,
                    "strike_price": Decimal("24000"),
                    "option_type": "CE",
                    "lot_size": 65,
                    "tick_size": Decimal("0.05"),
                    "is_active": True,
                    "refreshed_at": utc_now(),
                }
            ]
        )
        await session.commit()
    finally:
        await session.close()

    response = await auth_client.post(
        "/api/orders",
        json={"securityId": "999999", "side": "BUY", "orderType": "MARKET", "lots": 1},
    )

    assert response.status_code == 400
    assert "strategy module" in response.json()["detail"]


# --- filtering -------------------------------------------------------------
async def test_order_history_can_be_filtered_by_strategy(auth_client):
    await _seed_order(CRUDE, security_id="111")
    await _seed_order(OTHER, security_id="222")

    everything = await auth_client.get("/api/orders")
    crude_only = await auth_client.get(f"/api/orders?strategyKey={CRUDE}")

    assert everything.json()["total"] == 2
    assert crude_only.json()["total"] == 1
    assert crude_only.json()["orders"][0]["securityId"] == "111"


async def test_reports_can_be_sliced_by_strategy(auth_client):
    await _seed_order(CRUDE, security_id="111")
    await _seed_order(OTHER, security_id="222")

    everything = await auth_client.get("/api/reports/pnl")
    crude_only = await auth_client.get(f"/api/reports/pnl?strategyKey={CRUDE}")

    assert Decimal(everything.json()["totalCharges"]) == Decimal("40.00")
    assert Decimal(crude_only.json()["totalCharges"]) == Decimal("20.00")


# --- the toggle must not move a number -------------------------------------
async def test_a_disabled_strategys_history_is_still_reported_with_the_same_totals(
    auth_client,
):
    """Decision 3: past P&L must not silently change when a toggle moves."""
    await _seed_order(CRUDE, security_id="111")
    await _seed_order(CRUDE, security_id="222", price="60")

    before = (await auth_client.get("/api/reports/pnl")).json()
    orders_before = (await auth_client.get("/api/orders")).json()["total"]

    registry = get_strategy_registry()
    try:
        registry.set_enabled(CRUDE, False)

        after = (await auth_client.get("/api/reports/pnl")).json()
        orders_after = (await auth_client.get("/api/orders")).json()["total"]
    finally:
        registry.set_enabled(CRUDE, True)

    assert after["totalCharges"] == before["totalCharges"]
    assert after["realisedGross"] == before["realisedGross"]
    assert after["realisedNet"] == before["realisedNet"]
    assert orders_after == orders_before == 2


async def test_positions_report_whether_their_strategy_is_still_running(auth_client):
    await _seed_instrument()
    _seed_book()
    await auth_client.post(
        "/api/orders",
        json={"securityId": "576375", "side": "BUY", "orderType": "MARKET", "lots": 1},
    )

    running = (await auth_client.get("/api/positions")).json()["positions"]
    assert running, "the order should have opened a position"
    assert running[0]["strategyKey"] == CRUDE
    assert running[0]["strategyEnabled"] is True

    registry = get_strategy_registry()
    try:
        registry.set_enabled(CRUDE, False)
        stopped = (await auth_client.get("/api/positions")).json()["positions"]
    finally:
        registry.set_enabled(CRUDE, True)

    # Still there, still its own row -- but flagged, so the UI can say the mark
    # has stopped moving instead of showing a stale price as if it were live.
    assert len(stopped) == len(running)
    assert stopped[0]["strategyEnabled"] is False
