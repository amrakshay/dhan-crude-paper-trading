"""One-click trading from the futures chart.

The thing under test is a translation: the user acts on FUTURES levels, the
book holds OPTIONS. Every test here is about that seam -- which contract a
click buys, how the two buttons net, and whether a level on the future closes
the option.
"""
from datetime import timedelta
from decimal import Decimal

import pytest

from src.core.time_utils import ist_today, utc_now
from src.database.session import get_session_factory
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.market.services.feed_manager import get_feed_manager

FUTURE_ID = "565899"
FUTURE_SYMBOL = "CRUDEOIL SEP FUT"

OPTION_EXPIRY = ist_today() + timedelta(days=7)
FUTURE_EXPIRY = ist_today() + timedelta(days=11)
STRIKES = [Decimal("6750"), Decimal("6800"), Decimal("6850")]

# The future sits at 6805, so 6800 is the ATM strike (nearest, ties go lower).
FUTURE_PRICE = 6805.0
ATM_STRIKE = Decimal("6800")

OPTION_DEPTH = [
    (200, 200, 3, 3, 118.0, 119.0),
    (300, 300, 4, 4, 117.9, 119.1),
    (400, 400, 5, 5, 117.8, 119.2),
    (0, 0, 0, 0, 0.0, 0.0),
    (0, 0, 0, 0, 0.0, 0.0),
]


def _option_id(strike: Decimal, option_type: str) -> str:
    return f"{int(strike)}{'1' if option_type == 'CE' else '2'}"


async def _seed_instruments():
    rows = [
        {
            "security_id": FUTURE_ID,
            "exchange_id": "MCX",
            "exchange_segment": "MCX_COMM",
            "segment_code": 5,
            "instrument_type": "FUTCOM",
            "underlying_symbol": "CRUDEOIL",
            "underlying_scrip": 294,
            "trading_symbol": FUTURE_SYMBOL,
            "display_name": FUTURE_SYMBOL,
            "expiry_date": FUTURE_EXPIRY,
            "strike_price": None,
            "option_type": None,
            "lot_size": 100,
            "tick_size": Decimal("1.0"),
            "is_active": True,
            "refreshed_at": utc_now(),
        }
    ]
    for strike in STRIKES:
        for option_type in ("CE", "PE"):
            symbol = f"CRUDEOIL {int(strike)} {'CALL' if option_type == 'CE' else 'PUT'}"
            rows.append(
                {
                    "security_id": _option_id(strike, option_type),
                    "exchange_id": "MCX",
                    "exchange_segment": "MCX_COMM",
                    "segment_code": 5,
                    "instrument_type": "OPTFUT",
                    "underlying_symbol": "CRUDEOIL",
                    "underlying_scrip": 294,
                    "trading_symbol": symbol,
                    "display_name": symbol,
                    "expiry_date": OPTION_EXPIRY,
                    "strike_price": strike,
                    "option_type": option_type,
                    "lot_size": 100,
                    "tick_size": Decimal("0.1"),
                    "is_active": True,
                    "refreshed_at": utc_now(),
                }
            )

    session = get_session_factory()()
    try:
        await InstrumentRepository(session).upsert_many(rows)
        await session.commit()
    finally:
        await session.close()


def _seed_book(future_price: float = FUTURE_PRICE):
    book = get_feed_manager().book
    book.clear()
    book.register_instrument(
        FUTURE_ID,
        {"tradingSymbol": FUTURE_SYMBOL, "lotSize": 100, "tickSize": 1.0},
    )
    book.apply_packet({"security_id": FUTURE_ID, "segment": 5, "ltp": future_price})
    for strike in STRIKES:
        for option_type in ("CE", "PE"):
            security_id = _option_id(strike, option_type)
            book.register_instrument(
                security_id,
                {
                    "tradingSymbol": f"CRUDEOIL {int(strike)} {option_type}",
                    "optionType": option_type,
                    "strikePrice": float(strike),
                    "lotSize": 100,
                    "tickSize": 0.1,
                },
            )
            book.apply_packet(
                {
                    "security_id": security_id,
                    "segment": 5,
                    "ltp": 118.5,
                    "depth": OPTION_DEPTH,
                }
            )
    return book


def _move_future(price: float):
    get_feed_manager().book.apply_packet(
        {"security_id": FUTURE_ID, "segment": 5, "ltp": price}
    )


@pytest.fixture
async def chart_client(auth_client):
    await _seed_instruments()
    _seed_book()
    yield auth_client
    get_feed_manager().book.clear()


async def _click(client, side: str):
    return await client.post(
        "/api/chart-trading/click", json={"securityId": FUTURE_ID, "side": side}
    )


# --- auth ------------------------------------------------------------------
async def test_chart_trading_endpoints_require_authentication(api_client):
    assert (
        await api_client.get(f"/api/chart-trading/state?securityId={FUTURE_ID}")
    ).status_code == 401
    assert (
        await api_client.post("/api/chart-trading/click", json={})
    ).status_code == 401


# --- which contract a click buys -------------------------------------------
async def test_a_buy_click_buys_the_nearest_atm_call(chart_client):
    response = await _click(chart_client, "BUY")

    assert response.status_code == 201, response.text
    trade = response.json()["state"]["trade"]
    assert trade["optionType"] == "CE"
    assert Decimal(trade["strikePrice"]) == ATM_STRIKE
    assert trade["chartSide"] == "BUY"
    assert trade["quantity"] == 100, "one lot of CRUDEOIL is 100 barrels"


async def test_a_sell_click_buys_the_nearest_atm_put_it_never_writes_one(chart_client):
    """A Sell is a LONG PUT. The worst case must stay the premium paid."""
    response = await _click(chart_client, "SELL")

    trade = response.json()["state"]["trade"]
    assert trade["optionType"] == "PE"
    assert trade["chartSide"] == "SELL"
    assert trade["quantity"] > 0, "a long position, not a short one"

    orders = (await chart_client.get("/api/orders")).json()["orders"]
    assert [order["side"] for order in orders] == ["BUY"]


async def test_the_atm_strike_follows_the_future_not_the_option_chain(chart_client):
    """The levels being traded are the future's, so the strike tracks it."""
    _move_future(6845.0)

    trade = (await _click(chart_client, "BUY")).json()["state"]["trade"]

    assert Decimal(trade["strikePrice"]) == Decimal("6850")


# --- netting ---------------------------------------------------------------
async def test_the_opposite_click_closes_an_open_trade_rather_than_stacking(chart_client):
    await _click(chart_client, "BUY")

    response = await _click(chart_client, "SELL")

    assert response.json()["action"] == "CLOSED"
    assert response.json()["state"]["trade"] is None, "the click should leave us flat"


async def test_clicking_sell_twice_closes_the_call_then_opens_a_put(chart_client):
    await _click(chart_client, "BUY")
    await _click(chart_client, "SELL")

    response = await _click(chart_client, "SELL")

    trade = response.json()["state"]["trade"]
    assert response.json()["action"] == "OPENED"
    assert trade["optionType"] == "PE"


async def test_clicking_the_same_side_twice_is_refused(chart_client):
    await _click(chart_client, "BUY")

    response = await _click(chart_client, "BUY")

    assert response.status_code == 400
    assert "already open" in response.json()["detail"]


async def test_a_call_and_a_put_are_never_held_at_once(chart_client):
    """The netting rule exists so one click cannot leave an accidental straddle."""
    await _click(chart_client, "BUY")
    await _click(chart_client, "SELL")    # closes
    await _click(chart_client, "SELL")    # opens the put

    positions = (await chart_client.get("/api/positions")).json()["positions"]
    open_types = {
        position["optionType"] for position in positions if position["netQuantity"] != 0
    }
    assert open_types == {"PE"}


# --- the cost is on screen before the click --------------------------------
async def test_the_preview_shows_both_sides_with_charges_and_net_debit(chart_client):
    response = await chart_client.get(f"/api/chart-trading/preview?securityId={FUTURE_ID}")

    assert response.status_code == 200
    body = response.json()
    for side in ("buy", "sell"):
        leg = body[side]
        assert leg["error"] is None, leg
        assert Decimal(leg["estimatedCharges"]) > 0
        assert Decimal(leg["netAmount"]) < 0, "buying an option is a debit"
    assert body["buy"]["optionType"] == "CE"
    assert body["sell"]["optionType"] == "PE"


async def test_the_preview_places_nothing(chart_client):
    await chart_client.get(f"/api/chart-trading/preview?securityId={FUTURE_ID}")

    assert (await chart_client.get("/api/orders")).json()["total"] == 0


# --- bracket levels --------------------------------------------------------
async def test_levels_start_unset(chart_client):
    trade = (await _click(chart_client, "BUY")).json()["state"]["trade"]

    assert trade["stopLossLevel"] is None
    assert trade["takeProfitLevel"] is None


async def test_dragging_levels_in_arms_them(chart_client):
    trade_id = (await _click(chart_client, "BUY")).json()["state"]["trade"]["id"]

    response = await chart_client.patch(
        f"/api/chart-trading/trades/{trade_id}/levels",
        json={"stopLoss": 6780, "takeProfit": 6850},
    )

    trade = response.json()["trade"]
    assert Decimal(trade["stopLossLevel"]) == Decimal("6780")
    assert Decimal(trade["takeProfitLevel"]) == Decimal("6850")


async def test_a_stop_on_the_wrong_side_of_the_market_is_refused(chart_client):
    """Armed above the price on a Buy, it would fire the instant it was set --
    which reads as the chart closing the trade by itself."""
    trade_id = (await _click(chart_client, "BUY")).json()["state"]["trade"]["id"]

    response = await chart_client.patch(
        f"/api/chart-trading/trades/{trade_id}/levels", json={"stopLoss": 6900}
    )

    assert response.status_code == 400
    assert "below the future" in response.json()["detail"]


async def test_a_target_on_the_wrong_side_of_the_market_is_refused(chart_client):
    trade_id = (await _click(chart_client, "SELL")).json()["state"]["trade"]["id"]

    response = await chart_client.patch(
        f"/api/chart-trading/trades/{trade_id}/levels", json={"takeProfit": 6900}
    )

    assert response.status_code == 400


async def test_a_level_can_be_cleared(chart_client):
    trade_id = (await _click(chart_client, "BUY")).json()["state"]["trade"]["id"]
    await chart_client.patch(
        f"/api/chart-trading/trades/{trade_id}/levels", json={"stopLoss": 6780}
    )

    response = await chart_client.patch(
        f"/api/chart-trading/trades/{trade_id}/levels", json={"clearStopLoss": True}
    )

    assert response.json()["trade"]["stopLossLevel"] is None


# --- the monitor: a level on the FUTURE closes the OPTION ------------------
async def _run_monitor():
    from src.chart_trading.services.bracket_monitor import BracketMonitor

    return await BracketMonitor(book=get_feed_manager().book).run_once()


async def test_an_unarmed_trade_is_never_touched_by_the_monitor(chart_client):
    await _click(chart_client, "BUY")
    _move_future(6000.0)

    assert await _run_monitor() == 0
    state = (await chart_client.get(f"/api/chart-trading/state?securityId={FUTURE_ID}")).json()
    assert state["trade"] is not None


async def test_the_stop_closes_the_option_when_the_future_crosses_it(chart_client):
    trade_id = (await _click(chart_client, "BUY")).json()["state"]["trade"]["id"]
    await chart_client.patch(
        f"/api/chart-trading/trades/{trade_id}/levels", json={"stopLoss": 6780}
    )

    _move_future(6779.0)
    assert await _run_monitor() == 1

    state = (await chart_client.get(f"/api/chart-trading/state?securityId={FUTURE_ID}")).json()
    assert state["trade"] is None, "the chart trade should be flat"
    positions = (await chart_client.get("/api/positions")).json()["positions"]
    assert all(position["netQuantity"] == 0 for position in positions)


async def test_the_target_closes_the_option_when_the_future_crosses_it(chart_client):
    trade_id = (await _click(chart_client, "BUY")).json()["state"]["trade"]["id"]
    await chart_client.patch(
        f"/api/chart-trading/trades/{trade_id}/levels", json={"takeProfit": 6850}
    )

    _move_future(6851.0)

    assert await _run_monitor() == 1


async def test_a_stop_does_not_fire_while_the_future_is_short_of_it(chart_client):
    trade_id = (await _click(chart_client, "BUY")).json()["state"]["trade"]["id"]
    await chart_client.patch(
        f"/api/chart-trading/trades/{trade_id}/levels", json={"stopLoss": 6780}
    )

    _move_future(6781.0)

    assert await _run_monitor() == 0


async def test_a_sell_trade_stops_out_when_the_future_RISES(chart_client):
    """A long put loses as the future rises, so its stop sits above the market."""
    trade_id = (await _click(chart_client, "SELL")).json()["state"]["trade"]["id"]
    await chart_client.patch(
        f"/api/chart-trading/trades/{trade_id}/levels", json={"stopLoss": 6830}
    )

    _move_future(6831.0)

    assert await _run_monitor() == 1


async def test_the_monitor_never_acts_on_an_unknown_price(chart_client):
    """No tick means no level check -- never trade against a made-up price."""
    trade_id = (await _click(chart_client, "BUY")).json()["state"]["trade"]["id"]
    await chart_client.patch(
        f"/api/chart-trading/trades/{trade_id}/levels", json={"stopLoss": 6780}
    )
    get_feed_manager().book.clear()

    assert await _run_monitor() == 0


# --- live P&L --------------------------------------------------------------
async def test_the_state_reports_pnl_net_of_charges(chart_client):
    await _click(chart_client, "BUY")

    state = (await chart_client.get(f"/api/chart-trading/state?securityId={FUTURE_ID}")).json()

    trade = state["trade"]
    assert Decimal(trade["charges"]) > 0
    expected = (
        Decimal(trade["realizedPnl"])
        + Decimal(trade["unrealizedPnl"])
        - Decimal(trade["charges"])
    )
    assert Decimal(trade["netPnl"]) == expected
    assert Decimal(trade["netPnl"]) < 0, "straight after a market buy we are down the spread and charges"


async def test_the_state_is_flat_when_nothing_is_open(chart_client):
    state = (await chart_client.get(f"/api/chart-trading/state?securityId={FUTURE_ID}")).json()

    assert state["trade"] is None
    assert Decimal(state["underlyingPrice"]) == Decimal(str(FUTURE_PRICE))


async def test_closing_by_hand_flattens_the_chart_trade(chart_client):
    trade_id = (await _click(chart_client, "BUY")).json()["state"]["trade"]["id"]

    response = await chart_client.post(f"/api/chart-trading/trades/{trade_id}/close")

    assert response.status_code == 200
    assert response.json()["trade"] is None
