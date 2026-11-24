"""How option positions END (Phase 4), on Dhan's rule.

Index options (NIFTY 01-Dec, the real-file contracts of test_options_margin)
are cash-settled at intrinsic against the official close. Stock options
(a RELIANCE 29-Dec contract seeded here) are physically settled: no fresh
position on expiry day, 100% of contract value blocked from 10:00 for an
in-the-money obligation to BUY shares, an obligation that cannot be met
squared off, and -- after the close -- the option closed at zero and the
shares traded at the strike.

The closing price comes from a fake provider: the real one asks Dhan's daily
bars, and settlement must WAIT when it has nothing.
"""
from datetime import date, datetime
from decimal import Decimal

import pytest

from src.core.time_utils import IST, utc_now
from src.database.session import get_session_factory, session_scope
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.market.services.feed_manager import get_feed_manager
from src.options.services.risk_desk import RiskDesk
from src.orders.database.db_operations.order_repository import OrderRepository  # noqa: F401
from src.positions.database.db_operations.position_repository import PositionRepository
from tests.test_options_margin import _basket, _portfolio, span_book  # noqa: F401

DEC1 = date(2026, 12, 1)
DEC28 = date(2026, 12, 28)
DEC29 = date(2026, 12, 29)
RELIANCE_CASH = "2885"
RELIANCE_CALL = "72001"   # 1350 CE, 29-Dec, lot 500
RELIANCE_PUT = "72002"    # 1400 PE, 29-Dec, lot 500
RELIANCE_LOT = 500


def at(day: date, hhmm: str) -> datetime:
    hour, minute = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


@pytest.fixture
def clock(monkeypatch):
    """Set the order path's clock; starts mid-session on 25-Nov."""
    state = {"now": datetime(2026, 11, 25, 11, 0, tzinfo=IST)}
    monkeypatch.setattr("src.core.time_utils.ist_now", lambda: state["now"])

    def set_to(moment: datetime):
        state["now"] = moment

    return set_to


def closes(table):
    """A fake closing-price provider: {(symbol, day): close}."""
    calls = []

    async def provider(symbol, kind, day, scrip):
        calls.append((symbol, kind, day, scrip))
        return table.get((symbol, day))

    provider.calls = calls
    return provider


async def _positions(portfolio_id, include_closed=False):
    async with session_scope() as session:
        rows = await PositionRepository(session).list_all(include_closed=include_closed)
    return {row.security_id: row for row in rows if int(row.portfolio_id) == portfolio_id}


async def _open_ids(portfolio_id):
    return {sid for sid, row in (await _positions(portfolio_id)).items() if int(row.net_quantity or 0)}


# --- stock contracts ----------------------------------------------------------
async def _seed_reliance():
    session = get_session_factory()()
    try:
        common = {"exchange_id": "NSE", "underlying_symbol": "RELIANCE", "is_active": True,
                  "refreshed_at": utc_now()}
        await InstrumentRepository(session).upsert_many(
            [
                {**common, "security_id": RELIANCE_CASH, "exchange_segment": "NSE_EQ", "segment_code": 1,
                 "instrument_type": "EQUITY", "trading_symbol": "RELIANCE", "display_name": "Reliance Industries",
                 "lot_size": 1, "tick_size": Decimal("0.10"), "expiry_date": None, "strike_price": None,
                 "option_type": None},
                {**common, "security_id": RELIANCE_CALL, "exchange_segment": "NSE_FNO", "segment_code": 2,
                 "instrument_type": "OPTSTK", "underlying_scrip": 2885,
                 "trading_symbol": "RELIANCE 29 DEC 1350 CALL", "display_name": "RELIANCE 29 DEC 1350 CALL",
                 "expiry_date": DEC29, "strike_price": Decimal(1350), "option_type": "CE",
                 "lot_size": RELIANCE_LOT, "tick_size": Decimal("0.05")},
                {**common, "security_id": RELIANCE_PUT, "exchange_segment": "NSE_FNO", "segment_code": 2,
                 "instrument_type": "OPTSTK", "underlying_scrip": 2885,
                 "trading_symbol": "RELIANCE 29 DEC 1400 PUT", "display_name": "RELIANCE 29 DEC 1400 PUT",
                 "expiry_date": DEC29, "strike_price": Decimal(1400), "option_type": "PE",
                 "lot_size": RELIANCE_LOT, "tick_size": Decimal("0.05")},
            ]
        )
        await session.commit()
    finally:
        await session.close()


def _quote(security_id, price, segment=2):
    depth = [(50000, 50000, 9, 9, price - 0.5, price + 0.5)] + [(0, 0, 0, 0, 0.0, 0.0)] * 4
    book = get_feed_manager().book
    book.register_instrument(security_id, {"tradingSymbol": security_id, "lotSize": 1, "tickSize": 0.05})
    book.apply_packet({"security_id": security_id, "segment": segment, "ltp": price, "depth": depth})


@pytest.fixture
async def reliance(span_book, clock):
    await _seed_reliance()
    _quote(RELIANCE_CASH, 1400.0, segment=1)   # spot: the 1350 call is 50 in the money
    _quote(RELIANCE_CALL, 60.0)
    _quote(RELIANCE_PUT, 30.0)
    return span_book


async def _order(client, portfolio_id, security_id, side, lots=1):
    return await client.post(
        "/api/orders",
        json={"securityId": security_id, "side": side, "orderType": "MARKET", "lots": lots,
              "portfolioId": portfolio_id, "product": "CARRYFORWARD"},
    )


# --- expiry day: no fresh stock-option position ----------------------------------
async def test_a_fresh_stock_option_position_is_refused_on_expiry_day_but_a_close_is_not(reliance, clock):
    client = reliance
    portfolio_id = await _portfolio(client, "2000000")
    clock(at(DEC28, "11:00"))
    opened = await _order(client, portfolio_id, RELIANCE_CALL, "BUY")
    assert opened.status_code == 201, opened.text

    clock(at(DEC29, "09:30"))
    fresh = await _order(client, portfolio_id, RELIANCE_PUT, "BUY")
    close = await _order(client, portfolio_id, RELIANCE_CALL, "SELL")

    assert fresh.status_code == 400 and "expiry day" in fresh.json()["detail"]
    assert close.status_code == 201, close.text


async def test_an_index_option_may_still_be_opened_on_its_expiry_day(span_book, clock):
    client = span_book
    portfolio_id = await _portfolio(client, "1000000")
    clock(at(DEC1, "11:00"))

    response = await _order(client, portfolio_id, "71002", "BUY")

    assert response.status_code == 201, response.text


# --- expiry day: delivery margin from 10:00 --------------------------------------
async def test_from_ten_an_itm_long_call_blocks_its_full_contract_value(reliance, clock):
    client = reliance
    portfolio_id = await _portfolio(client, "2000000")
    clock(at(DEC28, "11:00"))
    assert (await _order(client, portfolio_id, RELIANCE_CALL, "BUY")).status_code == 201

    clock(at(DEC29, "09:59"))
    before = (await client.get(f"/api/portfolios/{portfolio_id}")).json()["balance"]
    clock(at(DEC29, "10:00"))
    after = (await client.get(f"/api/portfolios/{portfolio_id}")).json()["balance"]

    added = Decimal(after["blockedMargin"]) - Decimal(before["blockedMargin"])
    assert added == pytest.approx(Decimal(1350 * RELIANCE_LOT), abs=Decimal("1"))


async def test_an_otm_contract_blocks_nothing_for_delivery(reliance, clock):
    client = reliance
    portfolio_id = await _portfolio(client, "2000000")
    clock(at(DEC28, "11:00"))
    assert (await _order(client, portfolio_id, RELIANCE_PUT, "SELL")).status_code == 201
    # A short 1400 put with spot at 1400 is at the money, not in it.

    clock(at(DEC29, "09:59"))
    before = (await client.get(f"/api/portfolios/{portfolio_id}")).json()["balance"]
    clock(at(DEC29, "11:00"))
    after = (await client.get(f"/api/portfolios/{portfolio_id}")).json()["balance"]

    assert Decimal(after["blockedMargin"]) == pytest.approx(Decimal(before["blockedMargin"]), abs=Decimal("1"))


# --- expiry day: an obligation that cannot be met --------------------------------
async def test_an_itm_short_call_with_no_shares_is_squared_off_from_ten(reliance, clock):
    client = reliance
    portfolio_id = await _portfolio(client, "2000000")
    clock(at(DEC28, "11:00"))
    assert (await _order(client, portfolio_id, RELIANCE_CALL, "SELL")).status_code == 201
    clock(at(DEC29, "09:59"))
    assert await RiskDesk().run_once(now=at(DEC29, "09:59")) == []

    clock(at(DEC29, "10:00"))
    actions = await RiskDesk().run_once(now=at(DEC29, "10:00"))

    assert [action["rule"] for action in actions] == ["PHYSICAL_DELIVERY"]
    assert "no shares to deliver" in actions[0]["detail"]
    assert RELIANCE_CALL not in await _open_ids(portfolio_id)


async def test_an_itm_short_call_covered_by_shares_is_left_to_deliver(reliance, clock):
    client = reliance
    portfolio_id = await _portfolio(client, "3000000")
    clock(at(DEC28, "11:00"))
    assert (await _order(client, portfolio_id, RELIANCE_CALL, "SELL")).status_code == 201
    async with session_scope() as session:
        cash = await InstrumentRepository(session).get_by_security_id(RELIANCE_CASH)
        from src.options.services.basket_service import BasketService

        await BasketService(session)._order_service().record_off_market_fill(
            instrument=cash, portfolio_id=portfolio_id, strategy_key="nse-options-trader", side="BUY",
            quantity=RELIANCE_LOT, price=Decimal("1390"), reason="shares held for the covered call",
            is_close_order=False, order_type="DELIVERY",
        )
        await session.commit()

    clock(at(DEC29, "10:00"))
    assert await RiskDesk().run_once(now=at(DEC29, "10:00")) == []
    assert RELIANCE_CALL in await _open_ids(portfolio_id)


# --- after the close: index options are cash-settled -------------------------------
async def test_index_options_settle_at_intrinsic_and_otm_ones_expire_at_zero(span_book):
    client = span_book
    portfolio_id = await _portfolio(client, "1000000")
    response = await client.post(
        "/api/options-trader/basket", json=_basket(portfolio_id, [("71001", "BUY"), ("71002", "BUY")])
    )
    assert response.json()["status"] == "COMPLETE", response.text
    provider = closes({("NIFTY", DEC1): 25500.0})

    actions = await RiskDesk().run_once(now=at(DEC1, "15:45"), price_provider=provider)

    assert [action["rule"] for action in actions] == ["EXPIRY_SETTLEMENT"]
    assert actions[0]["outcome"] == "DONE"
    assert provider.calls == [("NIFTY", "OPTIDX", DEC1, 13)], "asked once, for the IDX_I id"
    rows = await _positions(portfolio_id, include_closed=True)
    itm, otm = rows["71001"], rows["71002"]
    assert int(itm.net_quantity) == 0 and int(otm.net_quantity) == 0
    # Bought at ~264 and ~178.5; settled at 150 (25500 - 25350) and at 0.
    assert float(itm.realized_pnl) == pytest.approx((150.0 - float(itm.average_price or 264)) * 65, abs=65)
    assert float(itm.sell_value) == pytest.approx(150.0 * 65)
    assert float(otm.sell_value) == 0.0
    # The exercised long pays the exercise fee, GST and STT on exercise; the
    # worthless one pays nothing more than it did to buy.
    assert float(itm.total_charges) > float(otm.total_charges)


async def test_nothing_settles_before_settle_after(span_book):
    client = span_book
    portfolio_id = await _portfolio(client, "1000000")
    await client.post("/api/options-trader/basket", json=_basket(portfolio_id, [("71001", "BUY")]))
    provider = closes({("NIFTY", DEC1): 25500.0})

    assert await RiskDesk().run_once(now=at(DEC1, "15:40"), price_provider=provider) == []
    assert provider.calls == []


async def test_with_no_official_close_settlement_waits_and_says_so_once(span_book):
    client = span_book
    portfolio_id = await _portfolio(client, "1000000")
    await client.post("/api/options-trader/basket", json=_basket(portfolio_id, [("71001", "BUY")]))
    desk = RiskDesk()

    first = await desk.run_once(now=at(DEC1, "15:45"), price_provider=closes({}))
    second = await desk.run_once(now=at(DEC1, "15:50"), price_provider=closes({}))

    assert [action["outcome"] for action in first] == ["FAILED"]
    assert "waiting for the official close" in first[0]["detail"]
    assert second == [], "the wait is reported once a day, not every five seconds"
    assert "71001" in await _open_ids(portfolio_id)

    # The bar arrives later -- or after a restart the next morning.
    later = await desk.run_once(now=at(date(2026, 12, 2), "08:00"), price_provider=closes({("NIFTY", DEC1): 25300.0}))
    assert [action["outcome"] for action in later] == ["DONE"]
    assert "71001" not in await _open_ids(portfolio_id)


async def test_a_provider_error_is_waiting_not_a_guess(span_book):
    client = span_book
    portfolio_id = await _portfolio(client, "1000000")
    await client.post("/api/options-trader/basket", json=_basket(portfolio_id, [("71001", "BUY")]))

    async def broken(*_args):
        raise TimeoutError("dhan slow")

    actions = await RiskDesk().run_once(now=at(DEC1, "15:45"), price_provider=broken)

    assert [action["outcome"] for action in actions] == ["FAILED"]
    assert "71001" in await _open_ids(portfolio_id)


# --- after the close: stock options are physically settled --------------------------
async def test_an_itm_long_stock_call_closes_at_zero_and_buys_the_shares_at_the_strike(reliance, clock):
    client = reliance
    portfolio_id = await _portfolio(client, "2000000")
    clock(at(DEC28, "11:00"))
    assert (await _order(client, portfolio_id, RELIANCE_CALL, "BUY")).status_code == 201

    actions = await RiskDesk().run_once(
        now=at(DEC29, "15:45"), price_provider=closes({("RELIANCE", DEC29): 1410.0})
    )

    assert [action["rule"] for action in actions] == ["EXPIRY_SETTLEMENT"]
    rows = await _positions(portfolio_id, include_closed=True)
    option, shares = rows[RELIANCE_CALL], rows[RELIANCE_CASH]
    assert int(option.net_quantity) == 0 and float(option.sell_value) == 0.0, "the option closes at ZERO"
    assert int(shares.net_quantity) == RELIANCE_LOT
    assert float(shares.average_price) == pytest.approx(1350.0), "shares bought at the strike"
    # Exercise fee, GST, STT on exercise and the 0.10% delivery charge.
    delivery_charge = 1350 * RELIANCE_LOT * 0.001
    assert float(option.total_charges) > delivery_charge
    assert shares.strategy_key == "nse-options-trader"


async def test_an_itm_short_stock_call_with_shares_held_delivers_them(reliance, clock):
    client = reliance
    portfolio_id = await _portfolio(client, "3000000")
    clock(at(DEC28, "11:00"))
    assert (await _order(client, portfolio_id, RELIANCE_CALL, "SELL")).status_code == 201
    async with session_scope() as session:
        cash = await InstrumentRepository(session).get_by_security_id(RELIANCE_CASH)
        from src.options.services.basket_service import BasketService

        await BasketService(session)._order_service().record_off_market_fill(
            instrument=cash, portfolio_id=portfolio_id, strategy_key="nse-options-trader", side="BUY",
            quantity=RELIANCE_LOT, price=Decimal("1390"), reason="shares held for the covered call",
            is_close_order=False, order_type="DELIVERY",
        )
        await session.commit()

    await RiskDesk().run_once(now=at(DEC29, "15:45"), price_provider=closes({("RELIANCE", DEC29): 1410.0}))

    rows = await _positions(portfolio_id, include_closed=True)
    assert int(rows[RELIANCE_CALL].net_quantity) == 0
    assert int(rows[RELIANCE_CASH].net_quantity) == 0, "the shares went out at the strike"
    assert float(rows[RELIANCE_CASH].realized_pnl) == pytest.approx((1350 - 1390) * RELIANCE_LOT)


async def test_a_short_stock_call_with_nothing_to_deliver_is_cash_settled_and_labelled(reliance, clock):
    """Normally the risk desk closes it at 10:00; this is the case it could
    not (the desk was down all day) -- settlement still has to happen."""
    client = reliance
    portfolio_id = await _portfolio(client, "2000000")
    clock(at(DEC28, "11:00"))
    assert (await _order(client, portfolio_id, RELIANCE_CALL, "SELL")).status_code == 201

    actions = await RiskDesk().run_once(
        now=at(date(2026, 12, 30), "08:00"), price_provider=closes({("RELIANCE", DEC29): 1410.0})
    )

    settled = [order for order in actions[0]["orders"] if order["securityId"] == RELIANCE_CALL]
    assert settled[0]["price"] == pytest.approx(60.0)
    assert "NO SHARES TO DELIVER" in settled[0]["reason"]
    rows = await _positions(portfolio_id, include_closed=True)
    assert RELIANCE_CASH not in rows or int(rows[RELIANCE_CASH].net_quantity) == 0


async def test_an_otm_stock_option_expires_worthless_with_no_delivery(reliance, clock):
    client = reliance
    portfolio_id = await _portfolio(client, "2000000")
    clock(at(DEC28, "11:00"))
    assert (await _order(client, portfolio_id, RELIANCE_PUT, "BUY")).status_code == 201

    await RiskDesk().run_once(now=at(DEC29, "15:45"), price_provider=closes({("RELIANCE", DEC29): 1410.0}))

    rows = await _positions(portfolio_id, include_closed=True)
    assert int(rows[RELIANCE_PUT].net_quantity) == 0 and float(rows[RELIANCE_PUT].sell_value) == 0.0
    assert RELIANCE_CASH not in rows


async def test_the_page_lists_upcoming_expiries_and_what_each_will_do(reliance, clock):
    client = reliance
    portfolio_id = await _portfolio(client, "2000000")
    clock(at(DEC28, "11:00"))
    assert (await _order(client, portfolio_id, RELIANCE_CALL, "SELL")).status_code == 201

    page = (await client.get("/api/options-trader/rms", params={"portfolioId": portfolio_id})).json()

    [row] = page["expiries"]
    assert row["tradingSymbol"] == "RELIANCE 29 DEC 1350 CALL" and row["daysLeft"] == 1
    assert row["settlement"] == "PHYSICAL" and row["inTheMoney"] is True
    assert row["obligation"] == "SELL_SHARES" and row["sharesHeld"] == 0
    assert page["rules"]["deliveryMarginFrom"] == "10:00" and page["rules"]["settleAfter"] == "15:45"
