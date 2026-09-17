"""P&L reporting: replay correctness, gross vs net, aggregation, equity curve."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from src.core.time_utils import utc_now
from src.orders.database.db_models.order_model import Order, OrderCharge, OrderFill
from src.orders.database.db_operations.order_repository import OrderRepository
from src.reports.services.pnl_service import PnlService

# Every row in this suite belongs to the one configured strategy module.
STRATEGY = "mcx-crude-options"

SECURITY = "576375"
SYMBOL = "CRUDEOIL 17 SEP 6800 CALL"


class _StubBook:
    def __init__(self, rows=None):
        self._rows = rows or {}

    def get(self, security_id):
        return self._rows.get(str(security_id))



def _breakdown(**amounts):
    """A stored charge breakdown, in the shape the engine writes.

    The line items ARE the breakdown now, so a fixture that sets only
    total_charges would leave a report with nothing to break out.
    """
    import json

    return json.dumps(
        [
            {"name": name, "label": name.replace("_", " ").capitalize(),
             "amount": str(amount), "rawAmount": str(amount),
             "rate": None, "base": None, "formula": "", "note": ""}
            for name, amount in amounts.items()
        ]
    )

async def _order(session, side, quantity, price, when=None, security_id=SECURITY,
                 symbol=SYMBOL, strike="6800", option_type="CE", charges="20.00"):
    """Create a filled order with its fill and charges rows."""
    when = when or utc_now()
    order = Order(
        strategy_key=STRATEGY,
        client_order_id=f"{side}-{quantity}-{when.timestamp()}-{security_id}",
        security_id=security_id,
        trading_symbol=symbol,
        expiry_date=date(2026, 9, 17),
        strike_price=Decimal(strike),
        option_type=option_type,
        lot_size=100,
        side=side,
        order_type="MARKET",
        lots=max(1, quantity // 100),
        quantity=quantity,
        status="FILLED",
        filled_quantity=quantity,
        average_fill_price=Decimal(str(price)),
        placed_at=when,
        last_event_at=when,
    )
    session.add(order)
    await session.flush()

    session.add(
        OrderFill(
            order_id=order.id, fill_at=when,
            price=Decimal(str(price)), quantity=quantity,
        )
    )
    session.add(
        OrderCharge(
            order_id=order.id,
            turnover=Decimal(str(price)) * quantity,
            total_charges=Decimal(charges),
            rates_version="test",
            breakdown_json=_breakdown(
                brokerage=Decimal("20.00"),
                ctt=Decimal("0"),
                exchange_transaction_charge=Decimal("0"),
                sebi_turnover_fee=Decimal("0"),
                stamp_duty=Decimal("0"),
                gst=Decimal("0"),
            ),
        )
    )
    await session.flush()
    return order


@pytest.fixture
def service_factory(db_session):
    def _make(book=None):
        return PnlService(OrderRepository(db_session), book=book or _StubBook())
    return _make


# --- replay ----------------------------------------------------------------
async def test_a_round_trip_realises_the_price_difference(db_session, service_factory):
    await _order(db_session, "BUY", 100, "50")
    await _order(db_session, "SELL", 100, "60")

    report = await service_factory().build_report()

    assert report.realised_gross == Decimal("1000.00"), "(60 - 50) * 100"
    assert report.trade_count == 1
    assert report.win_count == 1


async def test_an_open_position_realises_nothing(db_session, service_factory):
    await _order(db_session, "BUY", 100, "50")

    report = await service_factory().build_report()

    assert report.realised_gross == Decimal("0.00")
    assert report.trade_count == 0


async def test_a_partial_close_realises_only_the_closed_quantity(db_session, service_factory):
    await _order(db_session, "BUY", 200, "50")
    await _order(db_session, "SELL", 100, "60")

    report = await service_factory().build_report()

    assert report.realised_gross == Decimal("1000.00")
    assert report.events[0].quantity == 100


async def test_realisation_uses_the_weighted_average_entry(db_session, service_factory):
    await _order(db_session, "BUY", 100, "50")
    await _order(db_session, "BUY", 100, "60")
    await _order(db_session, "SELL", 200, "70")

    report = await service_factory().build_report()

    # Average entry 55; (70 - 55) * 200 = 3000
    assert report.realised_gross == Decimal("3000.00")
    assert report.events[0].entry_price == Decimal("55.00")


async def test_a_short_round_trip_realises_correctly(db_session, service_factory):
    await _order(db_session, "SELL", 100, "60")
    await _order(db_session, "BUY", 100, "50")

    report = await service_factory().build_report()

    assert report.realised_gross == Decimal("1000.00"), "shorted high, covered low"


async def test_a_losing_trade_is_counted_as_a_loss(db_session, service_factory):
    await _order(db_session, "BUY", 100, "60")
    await _order(db_session, "SELL", 100, "50")

    report = await service_factory().build_report()

    assert report.realised_gross == Decimal("-1000.00")
    assert report.loss_count == 1
    assert report.win_count == 0


async def test_positions_in_different_contracts_do_not_net_against_each_other(
    db_session, service_factory
):
    await _order(db_session, "BUY", 100, "50", security_id="AAA", symbol="A")
    await _order(db_session, "SELL", 100, "40", security_id="BBB", symbol="B")

    report = await service_factory().build_report()

    assert report.realised_gross == Decimal("0.00"), "two opens, no closes"
    assert report.trade_count == 0


# --- gross vs net ----------------------------------------------------------
async def test_net_is_gross_minus_charges(db_session, service_factory):
    await _order(db_session, "BUY", 100, "50", charges="28.84")
    await _order(db_session, "SELL", 100, "60", charges="33.54")

    report = await service_factory().build_report()

    assert report.realised_gross == Decimal("1000.00")
    assert report.total_charges == Decimal("62.38")
    assert report.realised_net == Decimal("937.62")


async def test_charges_are_broken_out_by_component(db_session, service_factory):
    await _order(db_session, "BUY", 100, "50")

    report = await service_factory().build_report()

    # The names come from the rate card the order was charged under, not from
    # a list this codebase keeps.
    assert set(report.charge_components) == {
        "brokerage", "ctt", "exchange_transaction_charge",
        "sebi_turnover_fee", "stamp_duty", "gst",
    }
    assert report.charge_components["brokerage"] == Decimal("20.00")


async def test_charges_are_counted_even_with_no_realised_trades(db_session, service_factory):
    """Opening a position costs money before anything is realised."""
    await _order(db_session, "BUY", 100, "50", charges="28.84")

    report = await service_factory().build_report()

    assert report.realised_gross == Decimal("0.00")
    assert report.total_charges == Decimal("28.84")
    assert report.realised_net == Decimal("-28.84")


# --- unrealised ------------------------------------------------------------
async def test_unrealised_uses_the_live_mark(db_session, service_factory):
    await _order(db_session, "BUY", 100, "50")
    book = _StubBook({SECURITY: {"ltp": 55.0}})

    report = await service_factory(book).build_report()

    assert report.unrealised == Decimal("500.00")


async def test_unrealised_is_none_when_nothing_is_marked(db_session, service_factory):
    """None, not zero -- unknown is not the same as flat."""
    await _order(db_session, "BUY", 100, "50")

    report = await service_factory(_StubBook()).build_report()

    assert report.unrealised is None
    assert report.net_including_unrealised is None
    assert report.open_positions_without_marks == 1


async def test_a_closed_book_has_no_unrealised(db_session, service_factory):
    await _order(db_session, "BUY", 100, "50")
    await _order(db_session, "SELL", 100, "60")
    book = _StubBook({SECURITY: {"ltp": 99.0}})

    report = await service_factory(book).build_report()

    assert report.unrealised is None, "nothing is open to mark"


# --- aggregation -----------------------------------------------------------
async def test_grouping_by_expiry_and_strike(db_session, service_factory):
    await _order(db_session, "BUY", 100, "50", strike="6800", option_type="CE")
    await _order(db_session, "SELL", 100, "60", strike="6800", option_type="CE")
    await _order(
        db_session, "BUY", 100, "30",
        security_id="OTHER", symbol="PUT", strike="6900", option_type="PE",
    )
    await _order(
        db_session, "SELL", 100, "20",
        security_id="OTHER", symbol="PUT", strike="6900", option_type="PE",
    )

    report = await service_factory().build_report()

    by_strike = {row["key"]: row["grossPnl"] for row in report.by_strike}
    assert by_strike["6800 CE"] == Decimal("1000.00")
    assert by_strike["6900 PE"] == Decimal("-1000.00")

    assert len(report.by_expiry) == 1
    assert report.by_expiry[0]["key"] == "2026-09-17"


async def test_grouping_by_day_uses_ist_calendar_days(db_session, service_factory):
    yesterday = utc_now() - timedelta(days=1)
    await _order(db_session, "BUY", 100, "50", when=yesterday)
    await _order(db_session, "SELL", 100, "60", when=yesterday)
    await _order(db_session, "BUY", 100, "50")
    await _order(db_session, "SELL", 100, "55")

    report = await service_factory().build_report()

    assert len(report.by_day) == 2
    assert sum(row["trades"] for row in report.by_day) == 2


# --- equity curve ----------------------------------------------------------
async def test_equity_curve_accumulates_gross_and_net(db_session, service_factory):
    yesterday = utc_now() - timedelta(days=1)
    await _order(db_session, "BUY", 100, "50", when=yesterday, charges="20.00")
    await _order(db_session, "SELL", 100, "60", when=yesterday, charges="20.00")
    await _order(db_session, "BUY", 100, "50", charges="20.00")
    await _order(db_session, "SELL", 100, "55", charges="20.00")

    report = await service_factory().build_report()
    curve = report.equity_curve

    assert len(curve) == 2
    assert curve[0]["cumulativeGross"] == Decimal("1000.00")
    assert curve[0]["cumulativeNet"] == Decimal("960.00")     # minus 40 charges
    assert curve[1]["cumulativeGross"] == Decimal("1500.00")
    assert curve[1]["cumulativeNet"] == Decimal("1420.00")    # minus 80 total


async def test_equity_curve_shows_charges_on_days_with_no_realisation(
    db_session, service_factory
):
    """A day that only opened positions still costs money; the net line must
    step down on it."""
    await _order(db_session, "BUY", 100, "50", charges="28.84")

    report = await service_factory().build_report()

    assert len(report.equity_curve) == 1
    point = report.equity_curve[0]
    assert point["grossPnl"] == Decimal("0.00")
    assert point["charges"] == Decimal("28.84")
    assert point["cumulativeNet"] == Decimal("-28.84")


async def test_an_empty_book_produces_an_empty_report(db_session, service_factory):
    report = await service_factory().build_report()

    assert report.realised_gross == Decimal("0.00")
    assert report.equity_curve == []
    assert report.by_day == []
    assert report.trade_count == 0


async def test_report_agrees_with_the_position_book_on_realised_pnl(db_session):
    """The replay and the live position book must apply identical rules.

    Both compute realised P&L from a weighted-average entry, so they must
    quantise prices at the same precision. Rounding the replay's running average
    to 2dp instead of 4dp silently skews every multi-fill round trip.
    """
    from src.positions.database.db_operations.position_repository import (
        PositionRepository,
    )
    from src.positions.services.position_service import PositionService

    # A buy that filled across three book levels, then a single-fill close.
    prices = [Decimal("48.10"), Decimal("48.17"), Decimal("48.25")]
    quantities = [30, 40, 30]

    position_service = PositionService(PositionRepository(db_session))
    for price, quantity in zip(prices, quantities):
        await position_service.apply_fill(
            strategy_key=STRATEGY,
            security_id=SECURITY, trading_symbol=SYMBOL, side="BUY",
            quantity=quantity, price=price, lot_size=100,
            expiry_date=date(2026, 9, 17), strike_price=Decimal("6800"),
            option_type="CE",
        )
        await _order(db_session, "BUY", quantity, price)

    exit_price = Decimal("46.5840")
    position, position_realised = await position_service.apply_fill(
        strategy_key=STRATEGY,
        security_id=SECURITY, trading_symbol=SYMBOL, side="SELL",
        quantity=100, price=exit_price, lot_size=100,
        expiry_date=date(2026, 9, 17), strike_price=Decimal("6800"),
        option_type="CE",
    )
    await _order(db_session, "SELL", 100, exit_price)

    report = await PnlService(OrderRepository(db_session), book=_StubBook()).build_report()

    assert report.realised_gross == position_realised, (
        f"report says {report.realised_gross}, position book says {position_realised}"
    )
    assert report.realised_gross == position.realized_pnl
