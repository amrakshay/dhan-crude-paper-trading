"""The exchange's per-order limits, which Dhan enforces (OPTIONS_TRADER_PLAN.md D6).

Fixture `fixtures/NSE_FO_contract_23112026.csv.gz` is cut from NSE's real F&O
contract file for 2026-11-23: three option rows (two NIFTY 01-Dec, one
RELIANCE 29-Dec) and one futures row, every column as published.

The order-path tests use the NIFTY contracts of test_options_margin (71001 /
71002, lot 65) with limits set directly, at NIFTY's real freeze quantity.
"""
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from src.core.time_utils import IST
from src.market.services.feed_manager import get_feed_manager
from src.options.services import contract_limits
from src.options.services.contract_limits import ContractLimits, set_contract_limits_for_tests
from src.options.services.risk_desk import RiskDesk
from src.positions.database.db_operations.position_repository import PositionRepository
from src.database.session import session_scope
from tests.test_options_margin import _basket, _portfolio, span_book  # noqa: F401

FIXTURE = Path(__file__).parent / "fixtures" / "NSE_FO_contract_23112026.csv.gz"
TODAY = date(2026, 11, 25)
PUBLISHED = date(2026, 11, 23)   # the file that sets 25-Nov's range (24-Nov was a holiday)
NIFTY_LIMIT = 1800   # NSE lists MaxTradQty 1801


# --- the file --------------------------------------------------------------------
def test_the_contract_file_gives_freeze_quantity_and_price_range_per_dhan_id():
    limits = contract_limits.parse(str(FIXTURE), date(2026, 11, 23))

    assert set(limits) == {"47810", "47941", "144259"}, "options only; the future is skipped"
    nifty = limits["47810"]   # NIFTY 01 DEC 25500 CALL, Dhan's id too
    assert nifty.max_order_quantity == NIFTY_LIMIT
    assert (nifty.min_price, nifty.max_price) == (Decimal("0.05"), Decimal("477.15"))
    assert limits["144259"].max_order_quantity == 20000   # RELIANCE: 20001 listed


def test_the_largest_order_is_a_whole_number_of_lots():
    class Contract:
        lot_size = 65

    assert contract_limits.largest_order(Contract(), ContractLimits(NIFTY_LIMIT, Decimal(0), Decimal(1), TODAY)) == 1755


# --- the order path ------------------------------------------------------------------
@pytest.fixture
def limits(span_book, monkeypatch):
    monkeypatch.setattr("src.core.time_utils.ist_now", lambda: datetime(2026, 11, 25, 11, 0, tzinfo=IST))
    set_contract_limits_for_tests(
        {
            "71001": ContractLimits(NIFTY_LIMIT, Decimal("150.00"), Decimal("400.00"), PUBLISHED),
            "71002": ContractLimits(NIFTY_LIMIT, Decimal("100.00"), Decimal("300.00"), PUBLISHED),
        },
        PUBLISHED,
    )
    yield span_book
    set_contract_limits_for_tests(None)


async def _order(client, portfolio_id, security_id, side, lots, order_type="MARKET", price=None, close=False):
    body = {"securityId": security_id, "side": side, "orderType": order_type, "lots": lots,
            "portfolioId": portfolio_id, "product": "CARRYFORWARD", "isCloseOrder": close}
    if price is not None:
        body["limitPrice"] = price
    return await client.post("/api/orders", json=body)


async def test_an_order_above_the_freeze_quantity_is_refused_and_says_how_to_split_it(limits):
    client = limits
    portfolio_id = await _portfolio(client, "5000000")

    refused = await _order(client, portfolio_id, "71002", "BUY", 28)    # 1,820
    allowed = await _order(client, portfolio_id, "71002", "BUY", 27)    # 1,755

    assert refused.status_code == 400
    assert "freeze limit of 1,800" in refused.json()["detail"] and "27 lots" in refused.json()["detail"]
    assert allowed.status_code == 201, allowed.text


async def test_a_limit_price_outside_todays_range_is_refused(limits):
    client = limits
    portfolio_id = await _portfolio(client, "1000000")

    too_high = await _order(client, portfolio_id, "71002", "BUY", 1, "LIMIT", 300.05)
    inside = await _order(client, portfolio_id, "71002", "BUY", 1, "LIMIT", 170.00)

    assert too_high.status_code == 400 and "price range 100.00-300.00" in too_high.json()["detail"]
    assert inside.status_code == 201, inside.text


@pytest.mark.parametrize("file_date", [TODAY, date(2026, 11, 18)], ids=["dated-today-sets-tomorrow", "too-old"])
async def test_a_file_that_does_not_set_todays_range_is_not_applied(limits, file_date):
    client = limits
    portfolio_id = await _portfolio(client, "1000000")
    set_contract_limits_for_tests(
        {"71002": ContractLimits(NIFTY_LIMIT, Decimal("100.00"), Decimal("300.00"), file_date)}, file_date,
    )

    response = await _order(client, portfolio_id, "71002", "BUY", 1, "LIMIT", 350.00)

    assert response.status_code == 201, response.text


def test_the_file_published_in_the_evening_sets_the_next_sessions_range():
    assert contract_limits.sets_range_for(date(2026, 11, 23), date(2026, 11, 25))       # over a holiday
    assert contract_limits.sets_range_for(date(2026, 11, 20), date(2026, 11, 23))       # over a weekend
    assert not contract_limits.sets_range_for(date(2026, 11, 25), date(2026, 11, 25))   # describes tomorrow


async def test_a_range_the_market_has_already_traded_through_is_not_applied(limits):
    """NSE widens an option's range intraday; a last price outside the file's
    range means it has, and refusing there would be stricter than NSE."""
    client = limits
    portfolio_id = await _portfolio(client, "1000000")
    get_feed_manager().book.apply_packet({"security_id": "71002", "segment": 2, "ltp": 320.0})

    response = await _order(client, portfolio_id, "71002", "BUY", 1, "LIMIT", 330.00)

    assert response.status_code == 201, response.text


async def test_the_basket_preview_names_the_refusal_before_place(limits):
    client = limits
    portfolio_id = await _portfolio(client, "5000000")
    body = _basket(portfolio_id, [("71002", "BUY")])
    body["legs"][0]["lots"] = 28

    preview = (await client.post("/api/options-trader/basket/preview", json=body)).json()

    assert "freeze limit" in preview["legs"][0]["refusal"]


async def test_a_risk_desk_exit_above_the_freeze_quantity_goes_as_several_orders(limits):
    client = limits
    portfolio_id = await _portfolio(client, "5000000")
    set_contract_limits_for_tests(None)   # open 40 lots before the limit exists
    for _ in range(2):
        assert (await _order(client, portfolio_id, "71002", "BUY", 20)).status_code == 201
    set_contract_limits_for_tests(
        {"71002": ContractLimits(NIFTY_LIMIT, Decimal("0.05"), Decimal("999.00"), PUBLISHED)}, PUBLISHED
    )

    from src.options.services.basket_service import BasketService

    async with session_scope() as session:
        rows = await BasketService(session).exit_matching(portfolio_id, lambda _p, _i: True, reason="test exit")
        await session.commit()

    assert [row["quantity"] for row in rows] == [1755, 845]
    assert all(row["outcome"] == "FILLED" for row in rows)
    async with session_scope() as session:
        open_ = [p for p in await PositionRepository(session).list_open(portfolio_id=portfolio_id) if int(p.net_quantity or 0)]
    assert open_ == []


async def test_without_a_contract_file_nothing_is_refused(span_book, monkeypatch):
    monkeypatch.setattr("src.core.time_utils.ist_now", lambda: datetime(2026, 11, 25, 11, 0, tzinfo=IST))
    set_contract_limits_for_tests(None)
    client = span_book
    portfolio_id = await _portfolio(client, "5000000")

    assert (await _order(client, portfolio_id, "71002", "BUY", 28)).status_code == 201


def test_the_risk_desk_still_imports():   # guards the shared exit path's imports
    assert RiskDesk is not None


# --- resting limit baskets (Phase 5) ----------------------------------------------------
async def test_a_resting_limit_basket_is_listed_and_its_resting_legs_can_be_cancelled(span_book, monkeypatch):
    monkeypatch.setattr("src.core.time_utils.ist_now", lambda: datetime(2026, 11, 25, 11, 0, tzinfo=IST))
    client = span_book
    portfolio_id = await _portfolio(client, "1000000")
    body = _basket(portfolio_id, [("71002", "BUY"), ("71001", "SELL")])
    body["legs"][0].update({"orderType": "LIMIT", "limitPrice": 150.00})   # below the 178.5 ask: rests
    placed = (await client.post("/api/options-trader/basket", json=body)).json()
    assert placed["status"] == "RESTING", placed

    listed = (await client.get("/api/options-trader/baskets", params={"portfolioId": portfolio_id})).json()
    assert listed["baskets"][0]["id"] == placed["id"]
    assert {leg["outcome"] for leg in listed["baskets"][0]["legs"]} == {"OPEN", "FILLED"}

    cancelled = (await client.post(f"/api/options-trader/basket/{placed['id']}/cancel")).json()
    outcomes = {leg["securityId"]: leg["outcome"] for leg in cancelled["legs"]}
    assert outcomes == {"71002": "CANCELLED", "71001": "FILLED"}, "the filled short is not unwound"
    assert cancelled["status"] == "PARTIAL", "re-derived from the orders, not the placement"


# --- found in the live session of 2026-11-25 ------------------------------------------
def _depth(bid, ask):
    return [(5000, 5000, 9, 9, bid, ask)] + [(0, 0, 0, 0, 0.0, 0.0)] * 4


async def _balance(client, portfolio_id):
    return (await client.get(f"/api/portfolios/{portfolio_id}")).json()["balance"]


async def test_a_resting_short_blocks_its_margin_and_cancelling_it_releases_it(span_book, monkeypatch):
    monkeypatch.setattr("src.core.time_utils.ist_now", lambda: datetime(2026, 11, 25, 11, 0, tzinfo=IST))
    client = span_book
    portfolio_id = await _portfolio(client, "1000000")
    before = await _balance(client, portfolio_id)

    resting = await _order(client, portfolio_id, "71001", "SELL", 1, "LIMIT", 300.00)   # ask 264: rests
    assert resting.json()["status"] == "OPEN", resting.text
    during = await _balance(client, portfolio_id)
    cancelled = await client.post(f"/api/orders/{resting.json()['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    after = await _balance(client, portfolio_id)

    assert Decimal(during["blockedMargin"]) > Decimal("100000"), "a naked NIFTY short blocks well over 1L"
    assert Decimal(during["available"]) < Decimal(before["available"])
    assert Decimal(after["blockedMargin"]) == Decimal(before["blockedMargin"])


async def test_a_resting_buy_blocks_its_premium_at_the_limit_price(span_book, monkeypatch):
    monkeypatch.setattr("src.core.time_utils.ist_now", lambda: datetime(2026, 11, 25, 11, 0, tzinfo=IST))
    client = span_book
    portfolio_id = await _portfolio(client, "1000000")

    await _order(client, portfolio_id, "71002", "BUY", 1, "LIMIT", 150.00)   # ask 178.5: rests

    assert Decimal((await _balance(client, portfolio_id))["blockedMargin"]) == Decimal("9750.00")   # 150 x 65


async def test_a_resting_order_is_not_refused_at_its_fill_for_its_own_reservation(span_book, monkeypatch):
    """With its reservation counted, a short that needed nearly all the cash
    would otherwise pay for itself twice at the fill and be rejected."""
    monkeypatch.setattr("src.core.time_utils.ist_now", lambda: datetime(2026, 11, 25, 11, 0, tzinfo=IST))
    client = span_book
    portfolio_id = await _portfolio(client, "200000")
    placed = await _order(client, portfolio_id, "71001", "SELL", 1, "LIMIT", 270.00)
    assert placed.json()["status"] == "OPEN", placed.text
    get_feed_manager().book.apply_packet({"security_id": "71001", "segment": 2, "ltp": 271.0, "depth": _depth(271.0, 272.0)})

    from src.orders.services.order_service import OrderService  # noqa: F401
    from src.options.services.basket_service import BasketService

    async with session_scope() as session:
        await BasketService(session)._order_service().try_fill_open_orders()
        await session.commit()

    order = (await client.get(f"/api/orders/{placed.json()['id']}")).json()
    assert order["status"] == "FILLED", order


async def test_a_basket_the_trader_withdrew_reads_cancelled_not_rejected():
    from src.options.services.basket_service import _basket_status

    assert _basket_status(["CANCELLED", "CANCELLED"]) == "CANCELLED"
    assert _basket_status(["REJECTED", "REJECTED"]) == "REJECTED"
    assert _basket_status(["FILLED", "CANCELLED"]) == "PARTIAL"


def test_a_range_the_market_has_moved_out_of_is_scaled_not_dropped():
    """Live 2026-11-25: NIFTY 26300 CE, base 28.75 and range 0.05-70.15 on the
    23-Nov file, traded at 110 after a 2.3% gap-up."""
    class Book:
        def get(self, _sid):
            return {"ltp": 110.0}

    class Contract:
        security_id, trading_symbol, lot_size = "47918", "NIFTY 01 DEC 26300 CALL", 65

    set_contract_limits_for_tests(
        {"47918": ContractLimits(NIFTY_LIMIT, Decimal("0.05"), Decimal("70.15"), PUBLISHED, Decimal("28.75"))}, PUBLISHED
    )
    try:
        low, high, note = contract_limits.current_range(contract_limits.get_contract_limits().get("47918"), Book(), "47918")
        assert low == Decimal("0.20") and high == Decimal("268.40") and "scaled" in note
        assert "outside today's NSE price range" in contract_limits.refusal(Contract(), 65, "LIMIT", 5000, TODAY, book=Book())
        assert contract_limits.refusal(Contract(), 65, "LIMIT", 115, TODAY, book=Book()) is None
    finally:
        set_contract_limits_for_tests(None)
