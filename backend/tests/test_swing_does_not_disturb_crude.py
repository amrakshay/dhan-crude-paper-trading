"""The MCX crude module is unaffected by any of this.

Every phase of the rotation's build has had to leave the original strategy
working exactly as it was, and the changes that touch shared code are the ones
worth pinning: a per-strategy lot cap on `OrderService`, a `reason` on
`submit_paper_order`, an `automation` block the framework now interprets, a
`fno_eligible` column on `instruments`, and a market-hours clock the feed
watchdog consults.

None of them may move a crude number. The charges assertions here duplicate
figures the charges suite already owns, deliberately: this file is the one that
fails with a name saying WHY when a swing change breaks them.
"""
from decimal import Decimal

import pytest

from src.charges.services.charges_engine import ChargesEngine
from src.core.time_utils import utc_now
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.market.services.feed_manager import get_feed_manager
from src.orders.database.db_operations.order_repository import OrderRepository
from src.orders.services.order_service import OrderService, OrderValidationError
from src.portfolios.database.db_operations.portfolio_repository import (
    PortfolioRepository,
)
from src.positions.database.db_operations.position_repository import PositionRepository
from src.strategies.services import market_clock
from src.strategies.services.strategy_registry import get_strategy_registry
from tests.conftest import NEAR_OPTION_EXPIRY, TEST_PORTFOLIO_NAME

CRUDE = "mcx-crude-options"
SECURITY_ID = "576375"
SYMBOL = "CRUDEOIL 6800 CALL"
LOT_SIZE = 100

BOOK_DEPTH = [
    (2000, 2000, 9, 9, 50.0, 51.0),
    (2000, 2000, 9, 9, 49.9, 51.1),
    (0, 0, 0, 0, 0.0, 0.0),
    (0, 0, 0, 0, 0.0, 0.0),
    (0, 0, 0, 0, 0.0, 0.0),
]


@pytest.fixture
def crude():
    return get_strategy_registry().require(CRUDE)


async def _seed_instrument(db_session):
    await InstrumentRepository(db_session).upsert_many(
        [
            {
                "security_id": SECURITY_ID, "exchange_id": "MCX",
                "exchange_segment": "MCX_COMM", "segment_code": 5,
                "instrument_type": "OPTFUT", "underlying_symbol": "CRUDEOIL",
                "underlying_scrip": 294, "trading_symbol": SYMBOL,
                "display_name": SYMBOL, "expiry_date": NEAR_OPTION_EXPIRY,
                "strike_price": Decimal("6800"), "option_type": "CE",
                "lot_size": LOT_SIZE, "tick_size": Decimal("0.1"),
                "is_active": True, "refreshed_at": utc_now(),
            }
        ]
    )
    await db_session.commit()


def _seed_book():
    book = get_feed_manager().book
    book.clear()
    book.register_instrument(
        SECURITY_ID,
        {"tradingSymbol": SYMBOL, "optionType": "CE", "strikePrice": 6800.0,
         "lotSize": LOT_SIZE, "tickSize": 0.1},
    )
    book.apply_packet(
        {"security_id": SECURITY_ID, "segment": 5, "ltp": 50.5, "depth": BOOK_DEPTH}
    )
    return book


async def _portfolio_id(db_session) -> int:
    portfolio = await PortfolioRepository(db_session).get_by_name(TEST_PORTFOLIO_NAME)
    return portfolio.id


def _orders(db_session):
    return OrderService(
        OrderRepository(db_session),
        InstrumentRepository(db_session),
        PositionRepository(db_session),
        book=get_feed_manager().book,
    )


# --- the module itself --------------------------------------------------------


def test_crude_is_discretionary_and_can_never_be_armed_or_scheduled(crude):
    """No `automation` block: every order in it comes from a person."""
    registry = get_strategy_registry()

    assert crude.automation.automated is False
    assert crude.automation.armed_by_default is False
    assert crude.automation.max_lots_per_order is None
    assert registry.is_armed(CRUDE) is False
    assert crude not in registry.automated()


def test_crude_keeps_the_global_lot_cap(crude):
    """The per-strategy override exists for NSE cash, where a "lot" is one
    share. One hundred crude lots is 10,000 barrels and stays the ceiling."""
    assert OrderService._max_lots(crude) == 100      # noqa: SLF001
    assert OrderService._max_lots(None) == 100       # noqa: SLF001

    swing = get_strategy_registry().require("nse-swing-momentum")
    assert OrderService._max_lots(swing) == 50000    # noqa: SLF001


def test_crude_declares_no_closing_auction(crude):
    """MCX runs continuously to 23:30. Configuring an auction would be fiction."""
    assert crude.market_hours.closing_auction is None
    assert market_clock.continuous_close(crude, fno_eligible=True) == (
        crude.market_hours.close
    )
    assert market_clock.continuous_close(crude, fno_eligible=False) == (
        crude.market_hours.close
    )


# --- charges ------------------------------------------------------------------


def test_crude_charges_are_unchanged():
    """The worked example from the charges suite, restated here.

    Duplicated on purpose: when a change to the shared charges engine for the
    equity card moves a commodity number, this is the test whose NAME says so.
    """
    engine = ChargesEngine.for_strategy_key(CRUDE)

    buy = engine.compute_order_charges("BUY", Decimal("100"), LOT_SIZE, 1)
    sell = engine.compute_order_charges("SELL", Decimal("150"), LOT_SIZE, 1)
    round_trip = engine.compute_round_trip_charges(
        Decimal("100"), Decimal("150"), LOT_SIZE, 1
    )

    assert round_trip.total == buy.total + sell.total
    assert round_trip.total == Decimal("67.36")
    # CTT is a COMMODITY tax, paid on the sell side of the premium. The equity
    # card pays STT on both legs instead, and adding it must not have taught
    # this engine anything new.
    assert round_trip.ctt == sell.ctt
    assert buy.ctt == Decimal("0")


def test_the_documented_zerodha_divergence_is_still_exactly_one_paisa():
    """A real 1-paise gap, never tuned away (root CLAUDE.md section 7).

    Zerodha computes the SEBI fee once on the combined round-trip turnover;
    this engine charges and rounds per ORDER, because every order carries its
    own auditable charges row.
    """
    import copy

    rates = copy.deepcopy(ChargesEngine.load_rates())
    rates["rounding"]["mode"] = "broker_compatible"
    engine = ChargesEngine(rates=rates)

    result = engine.compute_round_trip_charges(
        Decimal("100"), Decimal("150"), LOT_SIZE, 1
    )
    assert result.total - Decimal("67.05") == Decimal("0.01")


# --- fills and P&L ------------------------------------------------------------


async def test_a_crude_fill_is_byte_identical(db_session):
    """Same book, same slippage, same walk: the numbers are the ones this
    simulator has always produced."""
    await _seed_instrument(db_session)
    _seed_book()
    portfolio_id = await _portfolio_id(db_session)

    order = await _orders(db_session).submit_paper_order(
        security_id=SECURITY_ID, side="BUY", order_type="MARKET",
        lots=1, portfolio_id=portfolio_id,
    )

    # Level 1 ask 51.00 plus one tick of adverse slippage.
    assert order.status == "FILLED"
    assert order.quantity == 100
    assert Decimal(str(order.average_fill_price)) == Decimal("51.1000")

    # Re-read rather than touching the relationship on the just-created
    # instance: `fills` was never loaded on it, and a lazy load outside an
    # awaited context raises MissingGreenlet (backend/CLAUDE.md section 2).
    client_order_id = order.client_order_id
    await db_session.commit()
    db_session.expire_all()
    stored = await OrderRepository(db_session).get_by_client_order_id(client_order_id)
    assert len(stored.fills) == 1
    assert stored.fills[0].slippage_ticks == 1


async def test_a_crude_round_trip_pnl_is_unchanged(db_session):
    await _seed_instrument(db_session)
    _seed_book()
    portfolio_id = await _portfolio_id(db_session)

    service = _orders(db_session)
    await service.submit_paper_order(
        security_id=SECURITY_ID, side="BUY", order_type="MARKET",
        lots=1, portfolio_id=portfolio_id,
    )
    await service.submit_paper_order(
        security_id=SECURITY_ID, side="SELL", order_type="MARKET",
        lots=1, is_close_order=True, portfolio_id=portfolio_id,
    )
    await db_session.commit()

    position = (
        await PositionRepository(db_session).list_all(
            include_closed=True, strategy_key=CRUDE, portfolio_id=portfolio_id
        )
    )[0]

    # Bought at 51.10, sold at 49.90 (bid 50.00 less one tick): -1.20 x 100.
    assert position.is_open is False
    assert Decimal(str(position.realized_pnl)) == Decimal("-120.00")


async def test_the_reason_argument_is_optional_and_changes_nothing_for_a_human_order(
    db_session
):
    """`reason` was added for the rotation's system-written orders. An order
    placed by a person has no reason beyond their clicking, and its PLACED
    event must read exactly as it always did."""
    await _seed_instrument(db_session)
    _seed_book()
    portfolio_id = await _portfolio_id(db_session)

    order = await _orders(db_session).submit_paper_order(
        security_id=SECURITY_ID, side="BUY", order_type="MARKET",
        lots=1, portfolio_id=portfolio_id,
    )
    client_order_id = order.client_order_id
    await db_session.commit()
    db_session.expire_all()
    stored = await OrderRepository(db_session).get_by_client_order_id(client_order_id)
    placed = [event for event in stored.events if event.event_type == "PLACED"][0]
    assert placed.message == "BUY 1 lot(s) MARKET"
    assert "--" not in placed.message


async def test_crude_still_refuses_an_order_over_its_lot_cap(db_session):
    await _seed_instrument(db_session)
    _seed_book()
    portfolio_id = await _portfolio_id(db_session)

    with pytest.raises(OrderValidationError) as error:
        await _orders(db_session).submit_paper_order(
            security_id=SECURITY_ID, side="BUY", order_type="MARKET",
            lots=101, portfolio_id=portfolio_id,
        )
    assert "maximum of 100" in str(error.value)
