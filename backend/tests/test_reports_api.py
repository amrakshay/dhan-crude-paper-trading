"""Report endpoints and CSV export."""
import csv
import io
from datetime import date
from decimal import Decimal

from src.core.time_utils import utc_now
from src.database.session import get_session_factory
from src.orders.database.db_models.order_model import Order, OrderCharge, OrderFill

# Every row in this suite belongs to the one configured strategy module.
STRATEGY = "mcx-crude-options"


async def _seed_round_trip():
    session = get_session_factory()()
    try:
        for side, price in (("BUY", "50"), ("SELL", "60")):
            when = utc_now()
            order = Order(
                strategy_key=STRATEGY,
                client_order_id=f"rt-{side}",
                security_id="576375",
                trading_symbol="CRUDEOIL 17 SEP 6800 CALL",
                expiry_date=date(2026, 9, 17),
                strike_price=Decimal("6800"),
                option_type="CE",
                lot_size=100,
                side=side,
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
                    brokerage=Decimal("20.00"), ctt=Decimal("2.50"),
                    exchange_transaction_charge=Decimal("2.09"),
                    sebi_turnover_fee=Decimal("0.01"), stamp_duty=Decimal("0.15"),
                    gst=Decimal("3.98"), total_charges=Decimal("28.73"),
                    rates_version="2026-09-16",
                )
            )
        await session.commit()
    finally:
        await session.close()


async def test_report_endpoints_require_authentication(api_client):
    assert (await api_client.get("/api/reports/pnl")).status_code == 401
    assert (await api_client.get("/api/reports/pnl/export.csv")).status_code == 401


async def test_pnl_endpoint_returns_a_full_report(auth_client):
    await _seed_round_trip()

    response = await auth_client.get("/api/reports/pnl")
    body = response.json()

    assert response.status_code == 200
    assert Decimal(body["realisedGross"]) == Decimal("1000.00")
    assert Decimal(body["totalCharges"]) == Decimal("57.46")
    assert Decimal(body["realisedNet"]) == Decimal("942.54")
    assert body["tradeCount"] == 1
    assert len(body["byDay"]) == 1
    assert len(body["equityCurve"]) == 1


async def test_pnl_report_is_empty_before_any_trading(auth_client):
    response = await auth_client.get("/api/reports/pnl")
    body = response.json()

    assert Decimal(body["realisedGross"]) == 0
    assert body["equityCurve"] == []
    assert body["tradeCount"] == 0


async def test_pnl_csv_export(auth_client):
    await _seed_round_trip()

    response = await auth_client.get("/api/reports/pnl/export.csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]

    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 1
    assert rows[0]["trading_symbol"] == "CRUDEOIL 17 SEP 6800 CALL"
    assert Decimal(rows[0]["gross_pnl"]) == Decimal("1000.00")
    assert rows[0]["option_type"] == "CE"


async def test_orders_csv_export_carries_every_charge_component(auth_client):
    await _seed_round_trip()

    response = await auth_client.get("/api/reports/orders/export.csv")
    rows = list(csv.DictReader(io.StringIO(response.text)))

    assert response.status_code == 200
    assert len(rows) == 2
    for column in (
        "brokerage", "ctt", "exchange_transaction_charge",
        "sebi_turnover_fee", "stamp_duty", "gst", "total_charges", "rates_version",
    ):
        assert column in rows[0], f"{column} missing from the orders export"
    assert rows[0]["rates_version"] == "2026-09-16"


async def test_csv_export_is_empty_but_valid_with_no_trades(auth_client):
    response = await auth_client.get("/api/reports/pnl/export.csv")
    rows = list(csv.DictReader(io.StringIO(response.text)))

    assert response.status_code == 200
    assert rows == []
