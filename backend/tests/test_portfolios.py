"""Portfolios: separate books, derived balances, and rules enforced on data.

The first test in this file is the one that matters most. Two portfolios
holding the same contract must stay two position rows; if the lookup that finds
"the" open position ever loses the portfolio from its key, they silently net
into one and both books are wrong with no error anywhere.
"""
from decimal import Decimal

import pytest

from src.core.time_utils import utc_now
from src.database.session import get_session_factory, session_scope
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.market.services.feed_manager import get_feed_manager
from src.portfolios.database.db_operations.portfolio_repository import (
    PortfolioRepository,
)
from src.portfolios.services.portfolio_service import PortfolioService
from src.positions.database.db_operations.position_repository import PositionRepository
from tests.conftest import NEAR_OPTION_EXPIRY, TEST_PORTFOLIO_NAME

SECURITY_ID = "576375"
SYMBOL = "CRUDEOIL 6800 CALL"
FUTURE_ID = "565899"

BOOK_DEPTH = [
    (500, 500, 5, 5, 50.0, 51.0),
    (500, 500, 5, 5, 49.9, 51.1),
    (500, 500, 5, 5, 49.8, 51.2),
    (0, 0, 0, 0, 0.0, 0.0),
    (0, 0, 0, 0, 0.0, 0.0),
]


async def _seed_instruments():
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
                },
                {
                    "security_id": FUTURE_ID,
                    "exchange_id": "MCX", "exchange_segment": "MCX_COMM",
                    "segment_code": 5, "instrument_type": "FUTCOM",
                    "underlying_symbol": "CRUDEOIL", "underlying_scrip": 294,
                    "trading_symbol": "CRUDEOIL FUT", "display_name": "CRUDEOIL FUT",
                    "expiry_date": NEAR_OPTION_EXPIRY,
                    "strike_price": None, "option_type": None,
                    "lot_size": 100, "tick_size": Decimal("1.0"),
                    "is_active": True, "refreshed_at": utc_now(),
                },
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
            "lotSize": 100, "tickSize": 0.1,
            "expiryDate": NEAR_OPTION_EXPIRY.isoformat(),
        },
    )
    book.apply_packet(
        {"security_id": SECURITY_ID, "segment": 5, "ltp": 50.5, "depth": BOOK_DEPTH}
    )
    book.register_instrument(
        FUTURE_ID,
        {
            "tradingSymbol": "CRUDEOIL FUT", "instrumentType": "FUTCOM",
            "lotSize": 100, "tickSize": 1.0,
        },
    )
    book.apply_packet(
        {"security_id": FUTURE_ID, "segment": 5, "ltp": 6800.0, "depth": BOOK_DEPTH}
    )
    return book


async def _make_portfolio(name: str, opening: str = "1000000") -> int:
    async with session_scope() as session:
        portfolio = await PortfolioService(session).create(
            name=name,
            strategy_keys=["mcx-crude-options"],
            opening_balance=Decimal(opening),
        )
        portfolio_id = portfolio.id
        await session.commit()
    return portfolio_id


@pytest.fixture
async def ready(auth_client):
    await _seed_instruments()
    _seed_book()
    async with session_scope() as session:
        first = await PortfolioRepository(session).get_by_name(TEST_PORTFOLIO_NAME)
        first_id = first.id
    second_id = await _make_portfolio("Experiment-A")
    yield auth_client, first_id, second_id
    get_feed_manager().book.clear()


async def _buy(client, portfolio_id: int, lots: int = 1):
    return await client.post(
        "/api/orders",
        json={
            "securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET",
            "lots": lots, "portfolioId": portfolio_id,
        },
    )


# --- the books must not mix ------------------------------------------------
async def test_two_portfolios_holding_the_same_contract_keep_two_positions(ready):
    """The silent one. A shared lookup would net these into a single row."""
    client, first, second = ready

    assert (await _buy(client, first)).status_code == 201
    assert (await _buy(client, second)).status_code == 201

    async with session_scope() as session:
        rows = await PositionRepository(session).list_all()

    same_contract = [row for row in rows if row.security_id == SECURITY_ID]
    assert len(same_contract) == 2, "one row per portfolio, not one row shared"
    assert {row.portfolio_id for row in same_contract} == {first, second}
    for row in same_contract:
        assert int(row.net_quantity) == 100, "each book holds its own lot"


async def test_a_position_lookup_in_one_portfolio_cannot_see_another(ready):
    client, first, second = ready
    await _buy(client, first)

    async with session_scope() as session:
        repository = PositionRepository(session)
        mine = await repository.get_open_for_security(first, SECURITY_ID)
        theirs = await repository.get_open_for_security(second, SECURITY_ID)

    assert mine is not None
    assert theirs is None


async def test_selling_in_one_portfolio_does_not_close_the_other(ready):
    client, first, second = ready
    await _buy(client, first)
    await _buy(client, second)

    sell = await client.post(
        "/api/orders",
        json={
            "securityId": SECURITY_ID, "side": "SELL", "orderType": "MARKET",
            "lots": 1, "portfolioId": first,
        },
    )
    assert sell.status_code == 201

    async with session_scope() as session:
        repository = PositionRepository(session)
        closed = await repository.get_open_for_security(first, SECURITY_ID)
        untouched = await repository.get_open_for_security(second, SECURITY_ID)

    assert closed is None, "the first portfolio is flat"
    assert untouched is not None and int(untouched.net_quantity) == 100


async def test_positions_can_be_listed_per_portfolio(ready):
    client, first, second = ready
    await _buy(client, first)
    await _buy(client, second)

    everything = (await client.get("/api/positions")).json()["positions"]
    just_first = (
        await client.get(f"/api/positions?portfolioId={first}")
    ).json()["positions"]

    assert len(everything) == 2
    assert len(just_first) == 1
    assert just_first[0]["portfolioId"] == first


# --- a chart trade belongs to one portfolio --------------------------------
async def test_a_chart_trade_in_one_portfolio_does_not_close_another(ready):
    client, first, second = ready

    opened_first = await client.post(
        "/api/chart-trading/click",
        json={"securityId": FUTURE_ID, "side": "BUY", "portfolioId": first},
    )
    opened_second = await client.post(
        "/api/chart-trading/click",
        json={"securityId": FUTURE_ID, "side": "BUY", "portfolioId": second},
    )

    assert opened_first.status_code == 201, opened_first.text
    assert opened_second.status_code == 201, opened_second.text
    assert opened_first.json()["action"] == "OPENED"
    # Not "CLOSED": the second click is in a different book, so it opens rather
    # than netting against the first.
    assert opened_second.json()["action"] == "OPENED"

    # The opposite click closes only its own portfolio's trade.
    closed = await client.post(
        "/api/chart-trading/click",
        json={"securityId": FUTURE_ID, "side": "SELL", "portfolioId": first},
    )
    assert closed.json()["action"] == "CLOSED"

    still_open = await client.get(
        f"/api/chart-trading/state?securityId={FUTURE_ID}&portfolioId={second}"
    )
    assert still_open.json()["trade"] is not None


# --- the ledger and the four numbers ---------------------------------------
async def test_cash_is_the_sum_of_the_ledger_and_a_buy_debits_it(ready):
    client, first, _second = ready

    before = (await client.get(f"/api/portfolios/{first}")).json()["balance"]
    order = (await _buy(client, first)).json()
    after = (await client.get(f"/api/portfolios/{first}")).json()["balance"]

    premium = Decimal(order["averageFillPrice"]) * int(order["filledQuantity"])
    charges = Decimal(order["charges"]["totalCharges"])
    expected = (Decimal(before["cash"]) - premium - charges).quantize(Decimal("0.01"))

    assert Decimal(after["cash"]) == expected


async def test_a_multi_fill_order_is_charged_once_not_once_per_fill(ready):
    """Charges are recomputed on cumulative quantity, so the ledger records the
    DELTA. Posting the full figure on each fill would charge twice."""
    client, first, _second = ready
    # 10 lots walks several depth levels, producing several fills.
    order = (await _buy(client, first, lots=10)).json()
    assert len(order["fills"]) > 1, "this test needs a multi-fill order"

    ledger = (await client.get(f"/api/portfolios/{first}/ledger")).json()
    charge_entries = [
        entry for entry in ledger["entries"] if entry["entryType"] == "CHARGES"
    ]
    charged = sum(-Decimal(entry["amount"]) for entry in charge_entries)

    assert charged == Decimal(order["charges"]["totalCharges"])


async def test_deposits_and_withdrawals_move_cash(ready):
    client, first, _second = ready

    await client.post(f"/api/portfolios/{first}/deposit", json={"amount": "5000"})
    after_deposit = (await client.get(f"/api/portfolios/{first}")).json()["balance"]
    await client.post(f"/api/portfolios/{first}/withdraw", json={"amount": "2000"})
    after_withdrawal = (await client.get(f"/api/portfolios/{first}")).json()["balance"]

    assert Decimal(after_deposit["cash"]) - Decimal(after_withdrawal["cash"]) == Decimal(
        "2000"
    )


async def test_a_withdrawal_cannot_take_available_below_zero(ready):
    client, first, _second = ready
    balance = (await client.get(f"/api/portfolios/{first}")).json()["balance"]
    too_much = Decimal(balance["available"]) + Decimal("1")

    response = await client.post(
        f"/api/portfolios/{first}/withdraw", json={"amount": str(too_much)}
    )

    assert response.status_code == 400
    assert "available" in response.json()["detail"]

    unchanged = (await client.get(f"/api/portfolios/{first}")).json()["balance"]
    assert unchanged["cash"] == balance["cash"]


async def test_the_margin_estimate_is_always_labelled_an_estimate(ready):
    client, first, _second = ready
    balance = (await client.get(f"/api/portfolios/{first}")).json()["balance"]

    assert balance["marginIsEstimate"] is True


async def test_a_short_position_blocks_margin_that_cannot_be_withdrawn(ready):
    client, first, _second = ready

    # Sell to open: a short option.
    sold = await client.post(
        "/api/orders",
        json={
            "securityId": SECURITY_ID, "side": "SELL", "orderType": "MARKET",
            "lots": 1, "portfolioId": first,
        },
    )
    assert sold.status_code == 201

    balance = (await client.get(f"/api/portfolios/{first}")).json()["balance"]

    assert Decimal(balance["blockedMargin"]) > 0, "a short ties money up"
    assert Decimal(balance["available"]) == Decimal(balance["cash"]) - Decimal(
        balance["blockedMargin"]
    )

    # The blocked part is out of reach even though the cash is there.
    response = await client.post(
        f"/api/portfolios/{first}/withdraw",
        json={"amount": str(Decimal(balance["cash"]))},
    )
    assert response.status_code == 400
    assert "ESTIMATE" in response.json()["detail"]


# --- lifecycle rules, enforced on data -------------------------------------
async def test_a_portfolio_with_history_cannot_be_deleted(ready):
    client, first, _second = ready
    await _buy(client, first)

    response = await client.delete(f"/api/portfolios/{first}")

    assert response.status_code == 400
    assert "archive" in response.json()["detail"].lower()
    assert (await client.get(f"/api/portfolios/{first}")).status_code == 200


async def test_a_portfolio_with_only_an_opening_deposit_has_history_too(ready):
    """The deposit IS history: deleting it would lose a real money movement."""
    client, _first, second = ready

    response = await client.delete(f"/api/portfolios/{second}")

    assert response.status_code == 400


async def test_an_untouched_portfolio_can_be_deleted(ready):
    client, _first, _second = ready
    created = await client.post(
        "/api/portfolios", json={"name": "Never used", "openingBalance": "0"}
    )
    portfolio_id = created.json()["id"]

    response = await client.delete(f"/api/portfolios/{portfolio_id}")

    assert response.status_code == 204


async def test_archiving_keeps_the_history_and_hides_it_from_the_picker(ready):
    client, first, _second = ready
    await _buy(client, first)

    await client.post(f"/api/portfolios/{first}/archive")

    active = (await client.get("/api/portfolios")).json()["portfolios"]
    everything = (
        await client.get("/api/portfolios?includeArchived=true")
    ).json()["portfolios"]

    assert first not in [portfolio["id"] for portfolio in active]
    assert first in [portfolio["id"] for portfolio in everything]
    # The orders are still there, with their totals.
    orders = (await client.get(f"/api/orders?portfolioId={first}")).json()
    assert orders["total"] == 1


async def test_an_archived_portfolio_refuses_new_trades(ready):
    client, first, _second = ready
    await client.post(f"/api/portfolios/{first}/archive")

    response = await _buy(client, first)

    assert response.status_code == 400
    assert "archived" in response.json()["detail"].lower()


# --- role gates ------------------------------------------------------------
async def test_creating_and_funding_are_admin_only(api_client):
    from tests.conftest import SEED_ADMIN_EMAIL, SEED_ADMIN_PASSWORD, login_as

    await login_as(api_client, SEED_ADMIN_EMAIL, SEED_ADMIN_PASSWORD)
    await api_client.post(
        "/api/users",
        json={
            "email": "trader2@abc.com", "firstName": "Plain", "lastName": "User",
            "password": "plain-user-password", "role": "ROLE_USER",
        },
    )
    await login_as(api_client, "trader2@abc.com", "plain-user-password")

    async with session_scope() as session:
        portfolio = await PortfolioRepository(session).get_by_name(TEST_PORTFOLIO_NAME)
        portfolio_id = portfolio.id

    # Reading is allowed...
    assert (await api_client.get("/api/portfolios")).status_code == 200
    # ...moving money is not. The route dependency is the gate, not the sidebar.
    assert (
        await api_client.post("/api/portfolios", json={"name": "Mine"})
    ).status_code == 403
    assert (
        await api_client.post(
            f"/api/portfolios/{portfolio_id}/deposit", json={"amount": "1"}
        )
    ).status_code == 403
    assert (
        await api_client.post(
            f"/api/portfolios/{portfolio_id}/withdraw", json={"amount": "1"}
        )
    ).status_code == 403
    assert (
        await api_client.delete(f"/api/portfolios/{portfolio_id}")
    ).status_code == 403
