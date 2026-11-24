"""NSE SPAN + exposure margin, baskets and the hedge-break (Phase 2).

The fixture `tests/fixtures/span_nifty_20261123.spn` is cut from NSE
Clearing's real file for 2026-11-23 -- NIFTY's settlement price, its
calendar-spread definition, and twenty option contracts (five strikes, calls
and puts, two expiries) exactly as published. Every number asserted here is
either read from it or follows from SPAN's arithmetic on it.
"""
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from src.core.time_utils import utc_now
from src.database.session import get_session_factory, session_scope
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.market.services.feed_manager import get_feed_manager
from src.options.services import span_file, span_margin as sm
from src.options.services.span_store import set_span_data_for_tests
from src.positions.database.db_operations.position_repository import PositionRepository

FIXTURE = Path(__file__).parent / "fixtures" / "span_nifty_20261123.spn"
CARD = Path(__file__).resolve().parent.parent / "conf" / "margins" / "nse-span.yaml"
FILE_DATE = date(2026, 11, 23)
DEC1 = date(2026, 12, 1)
DEC29 = date(2026, 12, 29)
TODAY = date(2026, 11, 24)
SPOT = 25349.65
LOT = 65
KEY = "nse-options-trader"


@pytest.fixture(scope="module")
def data():
    return span_file.parse(str(FIXTURE), FILE_DATE)


@pytest.fixture(scope="module")
def card():
    return sm.MarginCard(yaml.safe_load(CARD.read_text(encoding="utf-8")))


def leg(right, strike, quantity, expiry=DEC1, spot=SPOT):
    return sm.Leg("NIFTY", sm.KIND_INDEX, right, expiry, strike, quantity, spot=spot)


# --- the parser ---------------------------------------------------------------
def test_the_parser_reads_the_exchanges_own_numbers(data):
    assert data.underlying_price["NIFTY"] == 25349.65
    entry = data.entry("NIFTY", "C", DEC1, 25350)
    assert entry.price == 263.55
    assert entry.delta == 0.5125
    # Scenario 11: price up a full scan range -- the worst case for a short call.
    assert entry.array[10] == -2117.82
    assert len(entry.array) == 16
    assert data.som_rate["NIFTY"] == 0.0
    spread = data.spreads["NIFTY"][0]
    assert spread.rate == 450.0 and [one[0] for one in spread.legs] == ["20261201", "20261229"]


def test_a_strike_is_found_however_it_is_spelled(data):
    assert data.entry("NIFTY", "C", DEC1, Decimal("25350.0000")) is not None
    assert data.entry("NIFTY", "C", DEC1, 25350.0) is not None


# --- the arithmetic -----------------------------------------------------------
def test_a_naked_short_pays_worst_scenario_plus_two_percent_exposure(data, card):
    margin = sm.compute([leg("C", 25350, -LOT)], data, card, TODAY)

    u = margin.underlyings["NIFTY"]
    assert u.scan_risk == pytest.approx(LOT * 2117.82)
    assert u.exposure == pytest.approx(SPOT * LOT * 0.02)
    assert margin.total == pytest.approx(LOT * 2117.82 + SPOT * LOT * 0.02)
    assert margin.source == sm.SOURCE_SPAN


def test_a_long_option_blocks_nothing_beyond_its_premium(data, card):
    margin = sm.compute([leg("C", 25350, LOT)], data, card, TODAY)

    assert margin.exposure == 0
    assert margin.blocked == pytest.approx(0.0, abs=0.01)


def test_a_short_keeps_its_premium_unspendable(data, card):
    """Dhan: option receivable is not counted against margin."""
    margin = sm.compute([leg("C", 25350, -LOT)], data, card, TODAY)

    assert margin.blocked == pytest.approx(margin.total + LOT * 263.55)


def test_a_credit_spread_margins_far_below_the_naked_short(data, card):
    naked = sm.compute([leg("C", 25350, -LOT)], data, card, TODAY)
    spread = sm.compute([leg("C", 25350, -LOT), leg("C", 25550, LOT)], data, card, TODAY)

    assert spread.underlyings["NIFTY"].scan_risk < naked.underlyings["NIFTY"].scan_risk / 5
    # The hedged short still pays exposure margin on its notional.
    assert spread.exposure == pytest.approx(naked.exposure)
    # SPAN on a defined-risk spread is at most its width.
    assert spread.span <= 200 * LOT


def test_a_strangle_nets_its_two_sides_inside_one_scenario(data, card):
    """Only one side can lose in any one scenario, and in the rally that hurts
    the short call the short put GAINS -- so SPAN on the strangle is below the
    sum of the two, and can be below the worse side alone (it is, on this file:
    117,790 against the call's 124,290)."""
    call = sm.compute([leg("C", 25700, -LOT)], data, card, TODAY)
    put = sm.compute([leg("P", 25000, -LOT)], data, card, TODAY)
    both = sm.compute([leg("C", 25700, -LOT), leg("P", 25000, -LOT)], data, card, TODAY)

    assert both.span < call.span + put.span
    assert both.span < max(call.span, put.span)
    assert both.exposure == pytest.approx(call.exposure + put.exposure)


def test_a_calendar_spread_pays_the_files_spread_charge(data, card):
    margin = sm.compute(
        [leg("C", 25350, -LOT), leg("C", 25350, LOT, expiry=DEC29)], data, card, TODAY
    )
    u = margin.underlyings["NIFTY"]
    near = data.entry("NIFTY", "C", DEC1, 25350).delta
    far = data.entry("NIFTY", "C", DEC29, 25350).delta
    matched = min(abs(-LOT * near), abs(LOT * far))
    assert u.spread_charge == pytest.approx(matched * 450.0)


def test_on_expiry_day_the_expiring_leg_loses_its_calendar_benefit_and_pays_2pct(data, card):
    legs = [leg("C", 25350, -LOT), leg("C", 25350, LOT, expiry=DEC29)]
    before = sm.compute(legs, data, card, TODAY)
    on_expiry = sm.compute(legs, data, card, DEC1)

    u = on_expiry.underlyings["NIFTY"]
    assert u.spread_charge == 0, "no spread is formed with the expiring leg"
    assert u.expiry_day_addon == pytest.approx(SPOT * LOT * 0.02)
    assert u.scan_risk > before.underlyings["NIFTY"].scan_risk


def test_a_deep_otm_index_short_pays_three_percent_exposure(card):
    deep = sm.Leg("NIFTY", sm.KIND_INDEX, "C", DEC1, 28000, -LOT, spot=SPOT)
    margin = sm.compute([deep], None, card, TODAY)

    assert margin.exposure == pytest.approx(SPOT * LOT * 0.03)


def test_without_a_span_file_the_fallback_is_used_and_says_so(card):
    margin = sm.compute([leg("C", 25350, -LOT)], None, card, TODAY)

    assert margin.source == sm.SOURCE_FALLBACK
    assert margin.underlyings["NIFTY"].fallback_legs
    assert margin.total > SPOT * LOT * 0.02, "a fallback still blocks real money"


# --- through the application: balances, baskets, hedge-break ------------------
STRIKES = {25350: "71001", 25550: "71002"}
DEPTHS = {
    "71001": [(5000, 5000, 9, 9, 263.0, 264.0)] + [(0, 0, 0, 0, 0.0, 0.0)] * 4,
    "71002": [(5000, 5000, 9, 9, 177.5, 178.5)] + [(0, 0, 0, 0, 0.0, 0.0)] * 4,
}


async def _seed_contracts():
    session = get_session_factory()()
    try:
        await InstrumentRepository(session).upsert_many(
            [
                {
                    "security_id": security_id,
                    "exchange_id": "NSE", "exchange_segment": "NSE_FNO", "segment_code": 2,
                    "instrument_type": "OPTIDX", "underlying_symbol": "NIFTY",
                    "underlying_scrip": 26000,
                    "trading_symbol": f"NIFTY 01 DEC {strike} CALL",
                    "display_name": f"NIFTY 01 DEC {strike} CALL",
                    "expiry_date": DEC1, "strike_price": Decimal(strike), "option_type": "CE",
                    "lot_size": LOT, "tick_size": Decimal("0.05"),
                    "is_active": True, "refreshed_at": utc_now(),
                }
                for strike, security_id in STRIKES.items()
            ]
        )
        await session.commit()
    finally:
        await session.close()


def _seed_book():
    book = get_feed_manager().book
    for strike, security_id in STRIKES.items():
        book.register_instrument(
            security_id,
            {"tradingSymbol": f"NIFTY {strike} CE", "optionType": "CE",
             "strikePrice": float(strike), "lotSize": LOT, "tickSize": 0.05},
        )
        book.apply_packet(
            {"security_id": security_id, "segment": 2, "ltp": DEPTHS[security_id][0][4],
             "depth": DEPTHS[security_id]}
        )


@pytest.fixture
async def span_book(auth_client):
    set_span_data_for_tests(span_file.parse(str(FIXTURE), FILE_DATE))
    from src.options.services.option_margin_service import remember_spot

    remember_spot("NIFTY", SPOT)
    await _seed_contracts()
    _seed_book()
    yield auth_client
    set_span_data_for_tests(None)
    get_feed_manager().book.clear()


async def _portfolio(client, balance: str, name: str = "Spread Book") -> int:
    response = await client.post(
        "/api/portfolios", json={"name": name, "strategies": [KEY], "openingBalance": balance}
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


def _basket(portfolio_id, legs, product="CARRYFORWARD"):
    return {
        "portfolioId": portfolio_id,
        "product": product,
        "legs": [{"securityId": sid, "side": side, "lots": 1} for sid, side in legs],
    }


async def test_a_naked_short_that_cannot_be_carried_is_refused(span_book):
    client = span_book
    portfolio_id = await _portfolio(client, "60000")

    response = await client.post(
        "/api/orders",
        json={"securityId": "71001", "side": "SELL", "orderType": "MARKET", "lots": 1,
              "portfolioId": portfolio_id, "product": "CARRYFORWARD"},
    )

    assert response.status_code == 400
    assert "Insufficient funds" in response.json()["detail"]


async def test_the_same_short_inside_a_spread_fits_because_the_hedge_fills_first(span_book):
    client = span_book
    portfolio_id = await _portfolio(client, "60000")

    # Given SELL first on purpose: the basket still sends the BUY first.
    response = await client.post(
        "/api/options-trader/basket",
        json=_basket(portfolio_id, [("71001", "SELL"), ("71002", "BUY")]),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert [(leg["sequence"], leg["side"]) for leg in body["legs"]] == [(1, "BUY"), (2, "SELL")]
    assert [leg["outcome"] for leg in body["legs"]] == ["FILLED", "FILLED"]
    assert body["status"] == "COMPLETE"


async def test_a_refused_leg_does_not_stop_the_others(span_book):
    client = span_book
    portfolio_id = await _portfolio(client, "15000")

    response = await client.post(
        "/api/options-trader/basket",
        json=_basket(portfolio_id, [("71002", "BUY"), ("71001", "SELL")]),
    )

    body = response.json()
    assert body["legs"][0]["outcome"] == "FILLED"
    assert body["legs"][1]["outcome"] == "REJECTED"
    assert "Insufficient funds" in body["legs"][1]["reason"]
    assert body["status"] == "PARTIAL"


async def test_the_preview_shows_the_hedge_benefit(span_book):
    client = span_book
    portfolio_id = await _portfolio(client, "1000000")

    response = await client.post(
        "/api/options-trader/basket/preview",
        json=_basket(portfolio_id, [("71001", "SELL"), ("71002", "BUY")]),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "span"
    assert body["overallMargin"] > body["finalMargin"] > 0
    assert body["hedgeBenefit"] == pytest.approx(body["overallMargin"] - body["finalMargin"], abs=0.02)
    assert body["sentOrder"] == ["71002", "71001"]


async def test_the_balance_blocks_the_portfolios_span_margin(span_book):
    client = span_book
    portfolio_id = await _portfolio(client, "1000000")
    await client.post("/api/options-trader/basket", json=_basket(portfolio_id, [("71002", "BUY"), ("71001", "SELL")]))

    balance = (await client.get(f"/api/portfolios/{portfolio_id}")).json()["balance"]
    positions = (await client.get("/api/options-trader/positions", params={"portfolioId": portfolio_id})).json()

    assert Decimal(balance["blockedMargin"]) == Decimal(str(positions["margin"]["blocked"])).quantize(Decimal("0.01"))
    group = positions["groups"][0]
    assert group["underlying"] == "NIFTY" and group["hedged"] is True


async def test_exiting_the_hedge_first_is_a_hedge_break_and_exit_all_is_not(span_book):
    client = span_book
    portfolio_id = await _portfolio(client, "60000")
    placed = await client.post(
        "/api/options-trader/basket", json=_basket(portfolio_id, [("71002", "BUY"), ("71001", "SELL")])
    )
    assert placed.json()["status"] == "COMPLETE"

    async with session_scope() as session:
        long_leg = await PositionRepository(session).get_open_for_security(portfolio_id, "71002")
    refused = await client.post(f"/api/positions/{long_leg.id}/close", json={})
    assert refused.status_code == 400
    assert "Hedge Break Alert" in refused.json()["detail"]

    exited = await client.post(
        "/api/options-trader/exit-group", json={"portfolioId": portfolio_id, "underlying": "NIFTY"}
    )
    orders = exited.json()["orders"]
    assert [order["side"] for order in orders] == ["BUY", "SELL"], "the short is covered first"
    assert all(order["outcome"] == "FILLED" for order in orders)


async def test_closing_a_naked_long_is_never_refused(span_book):
    client = span_book
    portfolio_id = await _portfolio(client, "20000")
    bought = await client.post(
        "/api/orders",
        json={"securityId": "71002", "side": "BUY", "orderType": "MARKET", "lots": 1,
              "portfolioId": portfolio_id, "product": "CARRYFORWARD"},
    )
    assert bought.status_code == 201, bought.text
    async with session_scope() as session:
        position = await PositionRepository(session).get_open_for_security(portfolio_id, "71002")

    response = await client.post(f"/api/positions/{position.id}/close", json={})

    assert response.status_code in (200, 201), response.text
