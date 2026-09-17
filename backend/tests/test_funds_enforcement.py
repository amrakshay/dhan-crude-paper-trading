"""Paper money runs out.

Two checks, deliberately: at placement, and again at the fill. A resting limit
order can sit for hours while other trades spend the money it was affordable
against, and the whole point of the second check is that the first one has
expired by then.

A refused fill is REJECTED, not partially filled to fit. Filling what fits
would be the simulator quietly substituting a different trade for the one that
was placed -- the same reasoning as backend/CLAUDE.md section 4, which is why
several tests in this repository assert that a fill does NOT happen.
"""
from decimal import Decimal

import pytest

from src.core.time_utils import utc_now
from src.database.session import get_session_factory, session_scope
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.market.services.feed_manager import get_feed_manager
from src.orders.database.db_operations.order_repository import OrderRepository
from src.orders.services.order_service import OrderService
from src.portfolios.database.db_operations.portfolio_repository import (
    PortfolioRepository,
)
from src.positions.database.db_operations.position_repository import PositionRepository
from tests.conftest import NEAR_OPTION_EXPIRY, TEST_PORTFOLIO_NAME

SECURITY_ID = "576375"
SYMBOL = "CRUDEOIL 6800 CALL"

# Deep enough to fill 10 lots without walking out of the book.
BOOK_DEPTH = [
    (2000, 2000, 9, 9, 50.0, 51.0),
    (2000, 2000, 9, 9, 49.9, 51.1),
    (2000, 2000, 9, 9, 49.8, 51.2),
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
                    "exchange_id": "MCX", "exchange_segment": "MCX_COMM",
                    "segment_code": 5, "instrument_type": "OPTFUT",
                    "underlying_symbol": "CRUDEOIL", "underlying_scrip": 294,
                    "trading_symbol": SYMBOL, "display_name": SYMBOL,
                    "expiry_date": NEAR_OPTION_EXPIRY,
                    "strike_price": Decimal("6800"), "option_type": "CE",
                    "lot_size": 100, "tick_size": Decimal("0.1"),
                    "is_active": True, "refreshed_at": utc_now(),
                }
            ]
        )
        await session.commit()
    finally:
        await session.close()


def _seed_book(ltp=50.5, depth=BOOK_DEPTH):
    book = get_feed_manager().book
    book.clear()
    book.register_instrument(
        SECURITY_ID,
        {
            "tradingSymbol": SYMBOL, "optionType": "CE", "strikePrice": 6800.0,
            "lotSize": 100, "tickSize": 0.1,
        },
    )
    book.apply_packet(
        {"security_id": SECURITY_ID, "segment": 5, "ltp": ltp, "depth": depth}
    )
    return book


async def _portfolio_id() -> int:
    async with session_scope() as session:
        portfolio = await PortfolioRepository(session).get_by_name(TEST_PORTFOLIO_NAME)
        return portfolio.id


async def _set_available(client, portfolio_id: int, target: Decimal):
    """Withdraw until roughly `target` is available."""
    balance = (await client.get(f"/api/portfolios/{portfolio_id}")).json()["balance"]
    surplus = Decimal(balance["available"]) - target
    if surplus > 0:
        response = await client.post(
            f"/api/portfolios/{portfolio_id}/withdraw", json={"amount": str(surplus)}
        )
        assert response.status_code == 200, response.text


@pytest.fixture
async def funded(auth_client):
    await _seed_instrument()
    _seed_book()
    portfolio_id = await _portfolio_id()
    yield auth_client, portfolio_id
    get_feed_manager().book.clear()


# --- at placement ----------------------------------------------------------
async def test_an_order_beyond_available_funds_is_rejected_at_placement(funded):
    client, portfolio_id = funded
    # One lot costs ~5,100 plus charges. Leave 1,000.
    await _set_available(client, portfolio_id, Decimal("1000"))

    response = await client.post(
        "/api/orders",
        json={
            "securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET",
            "lots": 1, "portfolioId": portfolio_id,
        },
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "Insufficient funds" in detail
    assert "short by" in detail, "the message must name the shortfall"


async def test_a_refused_order_leaves_no_row_and_no_ledger_entry(funded):
    client, portfolio_id = funded
    await _set_available(client, portfolio_id, Decimal("1000"))
    before = (await client.get(f"/api/portfolios/{portfolio_id}/ledger")).json()

    await client.post(
        "/api/orders",
        json={
            "securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET",
            "lots": 1, "portfolioId": portfolio_id,
        },
    )

    after = (await client.get(f"/api/portfolios/{portfolio_id}/ledger")).json()
    orders = (await client.get("/api/orders")).json()

    assert orders["total"] == 0, "validation refuses before any row is written"
    assert after["total"] == before["total"]
    assert after["balance"]["cash"] == before["balance"]["cash"]


async def test_an_affordable_order_is_placed_normally(funded):
    client, portfolio_id = funded
    await _set_available(client, portfolio_id, Decimal("100000"))

    response = await client.post(
        "/api/orders",
        json={
            "securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET",
            "lots": 1, "portfolioId": portfolio_id,
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "FILLED"


async def test_closing_an_existing_position_is_never_refused_for_funds(funded):
    """Closing releases money. Refusing it would trap a trader in a position."""
    client, portfolio_id = funded
    await _set_available(client, portfolio_id, Decimal("100000"))
    opened = await client.post(
        "/api/orders",
        json={
            "securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET",
            "lots": 1, "portfolioId": portfolio_id,
        },
    )
    assert opened.status_code == 201

    # Drain the portfolio to nothing.
    balance = (await client.get(f"/api/portfolios/{portfolio_id}")).json()["balance"]
    await client.post(
        f"/api/portfolios/{portfolio_id}/withdraw",
        json={"amount": balance["available"]},
    )

    positions = (
        await client.get(f"/api/positions?portfolioId={portfolio_id}")
    ).json()["positions"]
    response = await client.post(
        f"/api/positions/{positions[0]['id']}/close", json={"orderType": "MARKET"}
    )

    assert response.status_code == 200, response.text


# --- at the fill -----------------------------------------------------------
async def test_a_resting_order_that_became_unaffordable_is_rejected_at_fill(funded):
    """The second check. Placed affordably, then the money went elsewhere."""
    client, portfolio_id = funded
    await _set_available(client, portfolio_id, Decimal("20000"))

    # A limit far below the market: it rests rather than filling.
    resting = await client.post(
        "/api/orders",
        json={
            "securityId": SECURITY_ID, "side": "BUY", "orderType": "LIMIT",
            "lots": 1, "limitPrice": "40.0", "portfolioId": portfolio_id,
        },
    )
    assert resting.status_code == 201
    assert resting.json()["status"] == "OPEN"

    # The money goes elsewhere while the order rests.
    await _set_available(client, portfolio_id, Decimal("500"))

    # The market trades through the limit.
    _seed_book(
        ltp=39.0,
        depth=[
            (2000, 2000, 9, 9, 38.9, 39.0),
            (0, 0, 0, 0, 0.0, 0.0),
            (0, 0, 0, 0, 0.0, 0.0),
            (0, 0, 0, 0, 0.0, 0.0),
            (0, 0, 0, 0, 0.0, 0.0),
        ],
    )

    async with session_scope() as session:
        service = OrderService(
            order_repository=OrderRepository(session),
            instrument_repository=InstrumentRepository(session),
            position_repository=PositionRepository(session),
            book=get_feed_manager().book,
        )
        await service.try_fill_open_orders()
        await session.commit()

    order = (await client.get("/api/orders")).json()["orders"][0]

    assert order["status"] == "REJECTED"
    assert order["filledQuantity"] == 0, "not partially filled to fit"
    assert "Insufficient funds" in order["rejectionReason"]

    # The rejection is a timestamped event, like every other transition.
    events = [event for event in order["events"] if event["eventType"] == "REJECTED"]
    assert len(events) == 1
    assert events[0]["eventAt"]
    assert "not now" in events[0]["message"]


async def test_a_resting_order_that_is_still_affordable_fills_normally(funded):
    client, portfolio_id = funded
    await _set_available(client, portfolio_id, Decimal("50000"))

    resting = await client.post(
        "/api/orders",
        json={
            "securityId": SECURITY_ID, "side": "BUY", "orderType": "LIMIT",
            "lots": 1, "limitPrice": "40.0", "portfolioId": portfolio_id,
        },
    )
    assert resting.json()["status"] == "OPEN"

    _seed_book(
        ltp=39.0,
        depth=[
            (2000, 2000, 9, 9, 38.9, 39.0),
            (0, 0, 0, 0, 0.0, 0.0),
            (0, 0, 0, 0, 0.0, 0.0),
            (0, 0, 0, 0, 0.0, 0.0),
            (0, 0, 0, 0, 0.0, 0.0),
        ],
    )

    async with session_scope() as session:
        service = OrderService(
            order_repository=OrderRepository(session),
            instrument_repository=InstrumentRepository(session),
            position_repository=PositionRepository(session),
            book=get_feed_manager().book,
        )
        await service.try_fill_open_orders()
        await session.commit()

    order = (await client.get("/api/orders")).json()["orders"][0]

    assert order["status"] == "FILLED"
    assert order["filledQuantity"] == 100


# --- the ticket must be able to show this before the click ------------------
async def test_the_preview_reports_affordability_against_a_named_portfolio(funded):
    """Order entry shows the debit beside the available balance, and the
    Confirm button is gated on `affordable` -- the same rule that already puts
    charges and the net debit on screen before anything is confirmed."""
    client, portfolio_id = funded
    await _set_available(client, portfolio_id, Decimal("100000"))

    preview = (
        await client.post(
            "/api/orders/preview",
            json={
                "securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET",
                "lots": 1, "portfolioId": portfolio_id,
            },
        )
    ).json()

    assert preview["affordable"] is True
    assert Decimal(preview["estimatedDebit"]) > 0
    assert Decimal(preview["availableBalance"]) > Decimal(preview["estimatedDebit"])


async def test_the_preview_says_an_order_does_not_fit_before_it_is_placed(funded):
    client, portfolio_id = funded
    await _set_available(client, portfolio_id, Decimal("1000"))

    preview = (
        await client.post(
            "/api/orders/preview",
            json={
                "securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET",
                "lots": 1, "portfolioId": portfolio_id,
            },
        )
    ).json()

    assert preview["affordable"] is False
    assert Decimal(preview["estimatedDebit"]) > Decimal(preview["availableBalance"])


async def test_a_preview_without_a_portfolio_still_prices_the_order(funded):
    """A preview spends nothing. Requiring a portfolio would hide the cost of a
    trade exactly when the trader is deciding which book to put it in."""
    client, _portfolio_id = funded

    preview = (
        await client.post(
            "/api/orders/preview",
            json={
                "securityId": SECURITY_ID, "side": "BUY", "orderType": "MARKET",
                "lots": 1,
            },
        )
    ).json()

    assert preview["charges"] is not None
    assert preview["affordable"] is None, "no portfolio named, so no verdict"
