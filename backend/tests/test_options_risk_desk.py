"""The options risk desk (Phase 3): what a broker's RMS does without asking.

Driven through `RiskDesk.run_once(now=...)` with a fixed IST clock, on the same
real-file SPAN fixture and contracts as tests/test_options_margin.py. The
task itself is switched off in tests (conftest), so nothing here races it.
"""
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from src.connections.database.db_models.alert_model import Alert
from src.core.time_utils import IST
from src.database.session import session_scope
from src.market.services.feed_manager import get_feed_manager
from src.options.database.db_operations.risk_action_repository import RiskActionRepository
from src.options.services.risk_desk import RiskDesk
from src.positions.database.db_operations.position_repository import PositionRepository
from tests.test_options_margin import _basket, _portfolio, span_book  # noqa: F401

WEDNESDAY = datetime(2026, 11, 25, tzinfo=IST)


def at(hhmm: str) -> datetime:
    hour, minute = map(int, hhmm.split(":"))
    return WEDNESDAY.replace(hour=hour, minute=minute)


@pytest.fixture(autouse=True)
def mid_session_clock(monkeypatch):
    """Orders in these tests are placed at 11:00 IST, whatever the wall clock
    says -- the Intraday entry cut-off reads the time, and a suite that passed
    at night and failed at 15:24 would be worse than none."""
    monkeypatch.setattr("src.core.time_utils.ist_now", lambda: at("11:00"))


async def _open(client, portfolio_id, legs, product):
    response = await client.post("/api/options-trader/basket", json=_basket(portfolio_id, legs, product=product))
    assert response.json()["status"] == "COMPLETE", response.text


async def _open_positions(portfolio_id):
    async with session_scope() as session:
        return {
            position.security_id: position
            for position in await PositionRepository(session).list_open(portfolio_id=portfolio_id)
            if int(position.net_quantity or 0)
        }


def _mark(security_id, ltp):
    get_feed_manager().book.apply_packet({"security_id": security_id, "segment": 2, "ltp": ltp})


# --- intraday square-off -------------------------------------------------------
async def test_nothing_is_squared_off_before_1525(span_book):
    client = span_book
    portfolio_id = await _portfolio(client, "1000000")
    await _open(client, portfolio_id, [("71002", "BUY"), ("71001", "SELL")], "INTRADAY")

    assert await RiskDesk().run_once(now=at("15:24")) == []
    assert len(await _open_positions(portfolio_id)) == 2


async def test_at_1525_intraday_positions_are_closed_shorts_first(span_book):
    client = span_book
    portfolio_id = await _portfolio(client, "1000000")
    await _open(client, portfolio_id, [("71002", "BUY"), ("71001", "SELL")], "INTRADAY")

    actions = await RiskDesk().run_once(now=at("15:25"))

    assert [action["rule"] for action in actions] == ["INTRADAY_SQUARE_OFF"]
    assert [order["side"] for order in actions[0]["orders"]] == ["BUY", "SELL"], "the short is covered first"
    assert actions[0]["outcome"] == "DONE"
    assert await _open_positions(portfolio_id) == {}


async def test_carry_forward_positions_are_left_alone_at_1525(span_book):
    client = span_book
    portfolio_id = await _portfolio(client, "1000000")
    await _open(client, portfolio_id, [("71002", "BUY"), ("71001", "SELL")], "CARRYFORWARD")

    assert await RiskDesk().run_once(now=at("15:26")) == []
    assert len(await _open_positions(portfolio_id)) == 2


async def test_a_second_pass_does_nothing_more(span_book):
    client = span_book
    portfolio_id = await _portfolio(client, "1000000")
    await _open(client, portfolio_id, [("71002", "BUY")], "INTRADAY")
    desk = RiskDesk()

    first = await desk.run_once(now=at("15:25"))
    second = await desk.run_once(now=at("15:26"))

    assert len(first) == 1 and second == []


async def test_the_desk_does_nothing_while_the_market_is_shut(span_book):
    client = span_book
    portfolio_id = await _portfolio(client, "1000000")
    await _open(client, portfolio_id, [("71002", "BUY")], "INTRADAY")

    assert await RiskDesk().run_once(now=at("16:00")) == []
    assert await RiskDesk().run_once(now=datetime(2026, 11, 28, 15, 25, tzinfo=IST)) == []   # Saturday


async def test_an_action_is_journalled_and_alerted_against_the_owning_strategy(span_book):
    client = span_book
    portfolio_id = await _portfolio(client, "1000000")
    await _open(client, portfolio_id, [("71002", "BUY")], "INTRADAY")

    await RiskDesk().run_once(now=at("15:25"))

    async with session_scope() as session:
        rows = await RiskActionRepository(session).recent(portfolio_id)
        alerts = (await session.execute(select(Alert).where(Alert.dedupe_key.like("health|options-rms|%")))).scalars().all()
    assert rows[0].rule == "INTRADAY_SQUARE_OFF" and rows[0].strategy_keys == "nse-options-trader"
    assert alerts and alerts[0].strategy_key == "nse-options-trader"

    page = (await client.get("/api/options-trader/rms", params={"portfolioId": portfolio_id})).json()
    assert page["actions"][0]["rule"] == "INTRADAY_SQUARE_OFF"
    assert page["rules"]["intradaySquareOffAt"] == "15:25"


# --- the 2-minute entry cut-off ------------------------------------------------
async def test_no_new_intraday_position_in_the_two_minutes_before_square_off(span_book, monkeypatch):
    client = span_book
    portfolio_id = await _portfolio(client, "1000000")
    monkeypatch.setattr("src.core.time_utils.ist_now", lambda: at("15:23"))   # overrides 11:00

    refused = await client.post(
        "/api/orders",
        json={"securityId": "71002", "side": "BUY", "orderType": "MARKET", "lots": 1,
              "portfolioId": portfolio_id, "product": "INTRADAY"},
    )
    allowed = await client.post(
        "/api/orders",
        json={"securityId": "71002", "side": "BUY", "orderType": "MARKET", "lots": 1,
              "portfolioId": portfolio_id, "product": "CARRYFORWARD"},
    )

    assert refused.status_code == 400 and "15:23" in refused.json()["detail"]
    assert allowed.status_code == 201, allowed.text


# --- MTM loss ------------------------------------------------------------------
async def test_an_mtm_loss_of_80_percent_of_margin_closes_the_option_book(span_book):
    client = span_book
    portfolio_id = await _portfolio(client, "1000000")
    await _open(client, portfolio_id, [("71001", "SELL")], "CARRYFORWARD")
    # The short call was sold near 263; at 3,000 it has lost far more than 80%
    # of the margin it blocks. Depth at the new price so the exit can fill.
    get_feed_manager().book.apply_packet(
        {"security_id": "71001", "segment": 2, "ltp": 3000.0,
         "depth": [(5000, 5000, 9, 9, 2999.0, 3001.0)] + [(0, 0, 0, 0, 0.0, 0.0)] * 4}
    )

    actions = await RiskDesk().run_once(now=at("11:00"))

    assert [action["rule"] for action in actions] == ["MTM_LOSS"]
    assert await _open_positions(portfolio_id) == {}


async def test_a_loss_below_80_percent_is_left_alone(span_book):
    client = span_book
    portfolio_id = await _portfolio(client, "1000000")
    await _open(client, portfolio_id, [("71001", "SELL")], "CARRYFORWARD")
    _mark("71001", 300.0)   # a small loss

    assert await RiskDesk().run_once(now=at("11:00")) == []


async def test_an_unmarked_position_never_triggers_the_mtm_rule(span_book):
    client = span_book
    portfolio_id = await _portfolio(client, "1000000")
    await _open(client, portfolio_id, [("71001", "SELL")], "CARRYFORWARD")
    get_feed_manager().book.forget(["71001"])

    assert await RiskDesk().run_once(now=at("11:00")) == []
    assert len(await _open_positions(portfolio_id)) == 1


# --- margin shortfall ----------------------------------------------------------
async def test_a_new_span_file_that_raises_margin_past_the_cash_cuts_the_largest_consumer(span_book):
    """The real cause of a shortfall: the exchange widens its scenarios
    overnight and the same book now blocks more than the portfolio holds."""
    from dataclasses import replace

    from src.options.services import span_file
    from src.options.services.span_store import get_span_store, set_span_data_for_tests

    client = span_book
    portfolio_id = await _portfolio(client, "250000")
    await _open(client, portfolio_id, [("71001", "SELL")], "CARRYFORWARD")
    available = Decimal((await client.get(f"/api/portfolios/{portfolio_id}")).json()["balance"]["available"])
    withdrawn = await client.post(
        f"/api/portfolios/{portfolio_id}/withdraw", json={"amount": str(available - Decimal("1000"))}
    )
    assert withdrawn.status_code == 200, withdrawn.text

    data = get_span_store().current()
    wider = span_file.SpanData(
        business_date=data.business_date, created=data.created,
        contracts={key: replace(entry, array=tuple(1.2 * a for a in entry.array)) for key, entry in data.contracts.items()},
        spreads=data.spreads, som_rate=data.som_rate, underlying_price=data.underlying_price,
    )
    set_span_data_for_tests(wider)

    actions = await RiskDesk().run_once(now=at("09:20"))

    assert [action["rule"] for action in actions] == ["MARGIN_SHORTFALL"]
    assert "NIFTY" in actions[0]["detail"]
    assert await _open_positions(portfolio_id) == {}
