"""The rebalance: what it trades, what it refuses to trade, and why.

Most of these assert a NEGATIVE, which is the shape this repository's tests
take deliberately (backend/CLAUDE.md section 11). The gate being off must buy
nothing; a breadth below 35% must buy nothing; an unknown equity must buy
nothing rather than guess a size; a short cash balance must skip a name rather
than quietly buy a smaller one; and an UNARMED run must decide exactly what an
armed one decides and place nothing at all.

The armed cases run through `submit_paper_order` like every other order in this
application, against a real depth book, so a fill here is the same pessimistic
fill a click on the chart gets.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import pytest

from src.core.time_utils import utc_now
from src.daily_bars.database.db_operations.daily_bar_repository import (
    DailyBarRepository,
)
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.market.services.feed_manager import get_feed_manager
from src.portfolios.database.db_models.portfolio_model import Portfolio
from src.portfolios.database.db_operations.portfolio_repository import (
    CashLedgerRepository,
    PortfolioRepository,
)
from src.strategies.services.strategy_registry import get_strategy_registry
from src.swing.database.db_models.swing_session_model import (
    ACTION_BOUGHT,
    ACTION_NOT_ENTERED,
    ACTION_SKIPPED,
    ACTION_SOLD,
    RUN_REBALANCE,
)
from src.swing.database.db_operations.swing_session_repository import (
    SwingDecisionRepository,
    SwingSessionRepository,
)
from src.swing.database.db_operations.swing_stop_repository import SwingStopRepository
from src.swing.services.execution_service import SwingExecutionService
from src.swing.services.journal_service import SwingJournalService
from src.swing.services.ranking_service import RankingService
from src.swing.services.stop_service import StopService
from src.swing.services.swing_parameters import SwingParameters

STRATEGY = "nse-swing-momentum"
SEGMENT = "NSE_EQ"
INDEX_SEGMENT = "IDX_I"

# Every seeded series ENDS YESTERDAY, relative to the day the suite runs.
#
# The rebalance refuses to trade on bars more than `swing.max_bar_staleness_days`
# old -- it executes at the open of the session after the one it decided, so a
# session date days old means the refresh has not run and the ranking was
# computed from prices that have since moved. A fixed 2024 anchor would make
# every test in this file exercise that refusal instead of the behaviour it is
# about, and would have started doing so silently as the calendar moved.
LAST_SESSION = date.today() - timedelta(days=1)
# For the one test whose subject IS the staleness refusal.
STALE_SESSION = date.today() - timedelta(days=90)

# Real Nifty 500 constituents. They have to be real: `submit_paper_order`
# resolves a contract's owning strategy through `owns_instrument`, which checks
# the universe file, so an invented ticker belongs to no strategy and is
# refused before anything interesting is tested.
# Twenty of them, not six, because a ROTATION EXIT is "rank > 15" and a
# six-name universe can never produce a rank of 16.
SYMBOLS = [
    "WELCORP", "HFCL", "RADICO", "DIVISLAB", "OFSS", "ABB", "ACC", "AIAENG",
    "ALKEM", "AMBER", "ASTRAL", "ATUL", "AUBANK", "AWL", "AXISBANK",
    "BAJFINANCE", "APLAPOLLO", "APOLLOTYRE", "ASHOKLEY", "BHEL",
]
SECURITY_IDS = {symbol: str(9000 + index) for index, symbol in enumerate(SYMBOLS)}


# --- fixtures and seeding ---------------------------------------------------


@pytest.fixture
def definition():
    return get_strategy_registry().require(STRATEGY)


@pytest.fixture
def parameters(definition):
    return SwingParameters.from_definition(definition)


@pytest.fixture(autouse=True)
def _clean_book():
    book = get_feed_manager().book
    book.clear()
    get_feed_manager().pin_instruments(STRATEGY, set())
    yield
    book.clear()
    get_feed_manager().pin_instruments(STRATEGY, set())


def _arm(armed: bool = True):
    get_strategy_registry().set_armed(STRATEGY, armed)


def _bars(symbol, closes, segment=SEGMENT, volume=5_000_000, last=None):
    """A consecutive daily series ENDING on `last` (yesterday by default).

    Anchored on the END rather than the start so that every series in a test
    shares a last date -- the ranking skips a symbol whose newest bar predates
    the session being decided, and a series that ended a day early would drop
    out of the universe for a reason the test was not about.
    """
    last = last or LAST_SESSION
    start = last - timedelta(days=len(closes) - 1)
    return [
        {
            "symbol": symbol,
            "exchange_segment": segment,
            "security_id": SECURITY_IDS.get(symbol, "1"),
            "bar_date": start + timedelta(days=offset),
            "open": Decimal(str(close)),
            "high": Decimal(str(round(close * 1.01, 4))),
            "low": Decimal(str(round(close * 0.99, 4))),
            "close": Decimal(str(close)),
            "volume": volume,
            "source": "import",
        }
        for offset, close in enumerate(closes)
    ]


def _rising(count, start=100.0, step=1.0):
    return [round(start + step * index, 2) for index in range(count)]


def _falling(count, start=200.0, step=0.5):
    return [round(start - step * index, 2) for index in range(count)]


async def _seed_instruments(db_session, symbols=SYMBOLS):
    """NSE cash rows, lot size 1 -- which is what the master really publishes."""
    await InstrumentRepository(db_session).upsert_many(
        [
            {
                "security_id": SECURITY_IDS[symbol],
                "exchange_id": "NSE",
                "exchange_segment": SEGMENT,
                "segment_code": 1,
                "instrument_type": "EQUITY",
                "underlying_symbol": symbol,
                "underlying_scrip": None,
                "trading_symbol": symbol,
                "display_name": symbol,
                "expiry_date": None,
                "strike_price": None,
                "option_type": None,
                "lot_size": 1,
                "tick_size": Decimal("0.05"),
                "fno_eligible": False,
                "is_active": True,
                "refreshed_at": utc_now(),
            }
            for symbol in symbols
        ]
    )
    # COMMITTED, not merely flushed. `warm_book()` resyncs the feed, which
    # opens its own session -- on SQLite that blocks against an outer
    # transaction still holding the write lock.
    await db_session.commit()


async def _seed_portfolio(db_session, opening=Decimal("1000000")) -> int:
    portfolio = Portfolio(name="Swing Momentum", status="ACTIVE")
    db_session.add(portfolio)
    await db_session.flush()
    await PortfolioRepository(db_session).set_strategies(portfolio.id, [STRATEGY])
    await CashLedgerRepository(db_session).add_entry(
        portfolio_id=portfolio.id,
        entry_type="DEPOSIT",
        amount=opening,
        entry_at=utc_now(),
        note="Opening balance",
    )
    await db_session.commit()
    return portfolio.id


def _seed_book(symbols=SYMBOLS, ltp=100.0, size=100000):
    """A book deep enough that a full position fills without walking out."""
    book = get_feed_manager().book
    for symbol in symbols:
        security_id = SECURITY_IDS[symbol]
        book.register_instrument(
            security_id,
            {"tradingSymbol": symbol, "lotSize": 1, "tickSize": 0.05},
        )
        book.apply_packet(
            {
                "security_id": security_id,
                "segment": 1,
                "ltp": ltp,
                "depth": [
                    (size, size, 5, 5, ltp - 0.05, ltp + 0.05),
                    (size, size, 5, 5, ltp - 0.10, ltp + 0.10),
                    (size, size, 5, 5, ltp - 0.15, ltp + 0.15),
                    (0, 0, 0, 0, 0.0, 0.0),
                    (0, 0, 0, 0, 0.0, 0.0),
                ],
            }
        )
    return book


async def _seed_gate_on(db_session, symbols=SYMBOLS, sessions=280, last=None):
    """A rising index above its SMA200, and names with strong momentum."""
    repository = DailyBarRepository(db_session)
    await repository.upsert_many(
        _bars("NIFTY", _rising(sessions, 20000.0, 30.0), segment=INDEX_SEGMENT,
              last=last)
    )
    for index, symbol in enumerate(symbols):
        await repository.upsert_many(
            _bars(symbol, _rising(sessions, 50.0, 0.4 + index * 0.05), last=last)
        )
    await db_session.commit()
    return repository


async def _seed_gate_off(db_session, symbols=SYMBOLS, sessions=280):
    """An index that rose for years and then fell hard below its SMA200."""
    repository = DailyBarRepository(db_session)
    closes = _rising(sessions - 60, 20000.0, 30.0) + _falling(60, 21000.0, 120.0)
    await repository.upsert_many(_bars("NIFTY", closes, segment=INDEX_SEGMENT))
    for index, symbol in enumerate(symbols):
        await repository.upsert_many(
            _bars(symbol, _rising(len(closes), 50.0, 0.4 + index * 0.05))
        )
    await db_session.commit()
    return repository


def _service(db_session, definition, symbols=SYMBOLS, book=None):
    ranking = RankingService(DailyBarRepository(db_session), definition)
    ranking.universe_symbols = lambda: list(symbols)  # noqa: E731
    journal = SwingJournalService(
        SwingSessionRepository(db_session), SwingDecisionRepository(db_session)
    )
    stop_repository = SwingStopRepository(db_session)
    parameters = SwingParameters.from_definition(definition)
    stops = StopService(stop_repository, ranking, parameters, definition.key)
    return SwingExecutionService(
        session=db_session,
        definition=definition,
        ranking=ranking,
        journal=journal,
        stops=stops,
        parameters=parameters,
        book=book if book is not None else get_feed_manager().book,
    )


@dataclass(frozen=True)
class DecisionRow:
    """A journal row as plain values.

    Snapshotted rather than held as an ORM object: a rebalance commits and
    expires the session, and touching an attribute on an expired instance
    raises `MissingGreenlet` outside an awaited context -- the trap in
    backend/CLAUDE.md section 2.
    """

    symbol: str
    action: str
    rank: object
    score: object
    quantity: object
    reference_price: object
    order_id: object
    reason: str


def _row(row) -> DecisionRow:
    return DecisionRow(
        symbol=row.symbol, action=row.action, rank=row.rank, score=row.score,
        quantity=row.quantity, reference_price=row.reference_price,
        order_id=row.order_id, reason=row.reason,
    )


async def _decisions(db_session, session_id):
    """{symbol: row}. Convenient, and lossy where a symbol has two rows.

    A name that was sold this session can reappear lower down as a candidate
    that was skipped, so use `_rows_for` when both matter.
    """
    rows = await SwingDecisionRepository(db_session).for_session(session_id)
    return {row.symbol: _row(row) for row in rows}


async def _rows_for(db_session, session_id, symbol):
    rows = await SwingDecisionRepository(db_session).for_session(session_id)
    return [_row(row) for row in rows if row.symbol == symbol]


# --- the gate is off: sell everything, buy nothing --------------------------


async def test_with_the_gate_off_a_rebalance_buys_nothing_and_records_the_numbers(
    db_session, definition
):
    await _seed_instruments(db_session)
    portfolio_id = await _seed_portfolio(db_session)
    await _seed_gate_off(db_session)
    _seed_book()
    _arm(True)

    outcome = await _service(db_session, definition).run_rebalance(portfolio_id)

    assert outcome.buys_planned == 0
    assert outcome.buys_placed == 0

    rows = await _decisions(db_session, outcome.record.session_id)
    blocked = rows["*"]
    assert blocked.action == ACTION_NOT_ENTERED
    assert "regime gate is OFF" in blocked.reason
    # The NIFTY close and its SMA, on the record, because "gate OFF" cannot be
    # checked against anything in six months.
    assert "NIFTY" in blocked.reason
    assert "100% cash" in blocked.reason


async def test_with_the_gate_off_every_holding_is_sold_with_the_regime_reason(
    db_session, definition
):
    await _seed_instruments(db_session)
    portfolio_id = await _seed_portfolio(db_session)
    await _seed_gate_off(db_session)
    _seed_book()
    _arm(True)

    service = _service(db_session, definition)
    # Open a position by hand, the way any other order would.
    order = await service._order_service().submit_paper_order(
        security_id=SECURITY_IDS["WELCORP"], side="BUY", order_type="MARKET",
        lots=100, portfolio_id=portfolio_id,
    )
    assert order.filled_quantity == 100
    await db_session.commit()

    outcome = await service.run_rebalance(portfolio_id)

    assert outcome.sells_planned == 1
    assert outcome.sells_placed == 1
    rows = await _decisions(db_session, outcome.record.session_id)
    assert rows["WELCORP"].action == ACTION_SOLD
    assert "Regime exit" in rows["WELCORP"].reason
    assert rows["WELCORP"].order_id is not None


# --- breadth below the floor -------------------------------------------------


async def test_breadth_below_the_floor_resolves_to_zero_slots_and_buys_nothing(
    db_session, definition
):
    """Gate ON, but almost nothing above its own SMA200.

    The ramp is `round(10 x clamp((breadth - 0.35) / 0.30, 0, 1))`, so a
    breadth at or under 35% is zero slots and zero entries -- which is a
    different answer from "breadth could not be measured", and the record has
    to say which one it is.
    """
    await _seed_instruments(db_session)
    portfolio_id = await _seed_portfolio(db_session)

    repository = DailyBarRepository(db_session)
    await repository.upsert_many(
        _bars("NIFTY", _rising(280, 20000.0, 30.0), segment=INDEX_SEGMENT)
    )
    # One name rising, five falling: breadth 1/6 = 16.7%, below the 35% floor.
    await repository.upsert_many(_bars("WELCORP", _rising(280, 50.0, 0.9)))
    for symbol in SYMBOLS[1:]:
        await repository.upsert_many(
            _bars(symbol, _rising(220, 50.0, 0.9) + _falling(60, 250.0, 1.5))
        )
    await db_session.commit()
    _seed_book()
    _arm(True)

    outcome = await _service(db_session, definition).run_rebalance(portfolio_id)

    assert outcome.buys_placed == 0
    rows = await _decisions(db_session, outcome.record.session_id)
    assert "breadth ramp buys nothing" in rows["*"].reason


# --- rotation ----------------------------------------------------------------


async def test_a_rotation_exit_fires_past_the_rank_and_the_order_carries_the_reason(
    db_session, definition, parameters
):
    await _seed_instruments(db_session)
    portfolio_id = await _seed_portfolio(db_session)

    repository = DailyBarRepository(db_session)
    await repository.upsert_many(
        _bars("NIFTY", _rising(280, 20000.0, 30.0), segment=INDEX_SEGMENT)
    )
    # BHEL decays away from the leadership board; the others keep climbing.
    for symbol in SYMBOLS[:-1]:
        await repository.upsert_many(_bars(symbol, _rising(280, 50.0, 1.2)))
    await repository.upsert_many(
        _bars("BHEL", _rising(220, 50.0, 1.2) + _falling(60, 314.0, 1.0))
    )
    await db_session.commit()
    _seed_book()
    _arm(True)

    service = _service(db_session, definition)
    order = await service._order_service().submit_paper_order(
        security_id=SECURITY_IDS["BHEL"], side="BUY", order_type="MARKET",
        lots=50, portfolio_id=portfolio_id,
    )
    await db_session.commit()

    outcome = await service.run_rebalance(portfolio_id)

    sold = [
        row
        for row in await _rows_for(db_session, outcome.record.session_id, "BHEL")
        if row.action == ACTION_SOLD
    ]
    assert sold, "BHEL should have been rotated out"
    assert "Rotation exit" in sold[0].reason
    assert "> 15" in sold[0].reason
    assert sold[0].rank is not None and sold[0].rank > 15
    assert sold[0].order_id is not None

    # And the ORDER itself carries it, not just the journal.
    from src.orders.database.db_operations.order_repository import OrderRepository

    exit_order = await OrderRepository(db_session).get_by_id(sold[0].order_id)
    placed = [event for event in exit_order.events if event.event_type == "PLACED"]
    assert placed and "Rotation exit" in placed[0].message


# --- sizing ------------------------------------------------------------------


async def test_a_position_is_sized_at_a_tenth_of_equity_in_whole_shares(
    db_session, definition, parameters
):
    await _seed_instruments(db_session)
    portfolio_id = await _seed_portfolio(db_session, opening=Decimal("1000000"))
    await _seed_gate_on(db_session)
    _seed_book(ltp=100.0)
    _arm(True)

    outcome = await _service(db_session, definition).run_rebalance(portfolio_id)

    assert outcome.buys_placed > 0
    rows = await _decisions(db_session, outcome.record.session_id)
    bought = [row for row in rows.values() if row.action == ACTION_BOUGHT]
    # 10,00,000 / 10 = 1,00,000; at about 100.05 that is 999 whole shares.
    assert all(990 <= int(row.quantity) <= 1000 for row in bought)
    assert parameters.position_size_divisor == 10


async def test_a_buy_is_skipped_with_a_reason_when_the_cash_is_short(
    db_session, definition
):
    """Not resized. A smaller position is a different trade."""
    await _seed_instruments(db_session)
    # Enough for one position at a tenth of equity, not for two.
    portfolio_id = await _seed_portfolio(db_session, opening=Decimal("1000000"))
    await _seed_gate_on(db_session)
    _seed_book(ltp=100.0)
    _arm(True)

    # Take the cash down so the FIRST buy fits and the second does not.
    await CashLedgerRepository(db_session).add_entry(
        portfolio_id=portfolio_id,
        entry_type="WITHDRAWAL",
        amount=Decimal("-880000"),
        entry_at=utc_now(),
        note="Leave room for one position only",
    )
    await db_session.commit()

    outcome = await _service(db_session, definition).run_rebalance(portfolio_id)

    rows = await _decisions(db_session, outcome.record.session_id)
    short = [
        row for row in rows.values()
        if row.action == ACTION_SKIPPED and "insufficient cash" in row.reason
    ]
    assert short, {row.symbol: row.reason for row in rows.values()}
    assert "Not resized" in short[0].reason
    # And the quantity recorded is the FULL position it refused to buy, not a
    # smaller one it might have afforded.
    assert int(short[0].quantity) > 0


async def test_equity_that_cannot_be_computed_blocks_sizing_rather_than_guessing(
    db_session, definition
):
    """A position with no mark withholds equity, and P13 divides equity by ten.

    The honest answer is to buy nothing and name the reason, not to fall back
    to cash -- which ignores everything already invested.
    """
    await _seed_instruments(db_session)
    portfolio_id = await _seed_portfolio(db_session)
    await _seed_gate_on(db_session)
    _seed_book()
    _arm(True)

    service = _service(db_session, definition)
    await service._order_service().submit_paper_order(
        security_id=SECURITY_IDS["OFSS"], side="BUY", order_type="MARKET",
        lots=10, portfolio_id=portfolio_id,
    )
    await db_session.commit()

    # The feed loses that instrument: held, and unmarked.
    get_feed_manager().book.forget([SECURITY_IDS["OFSS"]])

    outcome = await service.run_rebalance(portfolio_id)

    assert outcome.equity is None
    assert outcome.buys_placed == 0
    rows = await _decisions(db_session, outcome.record.session_id)
    assert "total equity could not be computed" in rows["*"].reason
    assert "no live mark" in rows["*"].reason


# --- arming ------------------------------------------------------------------


async def test_an_unarmed_run_journals_the_identical_decision_and_places_no_order(
    db_session, definition
):
    """The two runs differ in exactly one column: `order_id`.

    That is what makes arming a safeguard rather than a mode -- an operator can
    watch it decide for weeks and then arm it knowing precisely what it would
    have done.
    """
    await _seed_instruments(db_session)
    portfolio_id = await _seed_portfolio(db_session)
    await _seed_gate_on(db_session)
    _seed_book()

    _arm(False)
    unarmed = await _service(db_session, definition).run_rebalance(portfolio_id)
    assert unarmed.armed is False
    assert unarmed.buys_placed == 0
    assert unarmed.buys_planned > 0
    assert "NOT ARMED" in unarmed.record.message

    from src.orders.database.db_operations.order_repository import OrderRepository

    orders, _total = await OrderRepository(db_session).list_orders(page=0, size=100)
    assert orders == []

    unarmed_rows = await _decisions(db_session, unarmed.record.session_id)
    assert all(row.order_id is None for row in unarmed_rows.values())

    # Now arm and re-run the same session. Same decisions, orders placed.
    _arm(True)
    armed = await _service(db_session, definition).run_rebalance(
        portfolio_id, force=True
    )
    assert armed.buys_placed == armed.buys_planned > 0

    armed_rows = await _decisions(db_session, armed.record.session_id)
    assert set(armed_rows) == set(unarmed_rows)
    for symbol, unarmed_row in unarmed_rows.items():
        armed_row = armed_rows[symbol]
        assert armed_row.action == unarmed_row.action
        assert armed_row.rank == unarmed_row.rank
        assert armed_row.quantity == unarmed_row.quantity
        if armed_row.action == ACTION_BOUGHT:
            # The only divergence the reason carries is the stop that the armed
            # run actually set; the decision itself is word-for-word the same.
            assert armed_row.reason.startswith(unarmed_row.reason)
            assert armed_row.order_id is not None
        else:
            assert armed_row.reason == unarmed_row.reason


async def test_a_discretionary_strategy_cannot_be_armed_at_all(db_session):
    """MCX crude declares no automation block, so there is nothing to arm."""
    from src.strategies.services.strategy_definition import StrategyConfigError

    registry = get_strategy_registry()
    crude = registry.require("mcx-crude-options")
    assert crude.automation.automated is False
    assert registry.is_armed("mcx-crude-options") is False
    with pytest.raises(StrategyConfigError):
        registry.set_armed("mcx-crude-options", True)


async def test_the_swing_module_ships_enabled_but_not_armed(definition):
    """The shipped default, read off the definition rather than runtime state."""
    assert definition.enabled_by_default is True
    assert definition.automation.automated is True
    assert definition.automation.armed_by_default is False


# --- ordering ----------------------------------------------------------------


async def test_sells_are_executed_before_the_buys_are_sized(db_session, definition):
    """The money from the morning's sale funds the morning's purchase.

    The backtest processes pending exits before pending entries and sizing is
    cash-constrained. With the buys planned against a pre-sale balance this
    test buys nothing, and the failure looks like the funds check being wrong.
    """
    await _seed_instruments(db_session)
    portfolio_id = await _seed_portfolio(db_session, opening=Decimal("1000000"))

    repository = DailyBarRepository(db_session)
    await repository.upsert_many(
        _bars("NIFTY", _rising(280, 20000.0, 30.0), segment=INDEX_SEGMENT)
    )
    for symbol in SYMBOLS[:-1]:
        await repository.upsert_many(_bars(symbol, _rising(280, 50.0, 1.2)))
    await repository.upsert_many(
        _bars("BHEL", _rising(220, 50.0, 1.2) + _falling(60, 314.0, 1.0))
    )
    await db_session.commit()
    _seed_book(ltp=100.0)
    _arm(True)

    service = _service(db_session, definition)
    # Spend almost everything on the name that is about to rotate out.
    await service._order_service().submit_paper_order(
        security_id=SECURITY_IDS["BHEL"], side="BUY", order_type="MARKET",
        lots=8000, portfolio_id=portfolio_id,
    )
    await db_session.commit()

    balance = await service._balance_service().balance_for(portfolio_id)
    assert balance.available < Decimal("250000")

    outcome = await service.run_rebalance(portfolio_id)

    assert outcome.sells_placed == 1
    # Funded out of the sale: without the sell-first ordering there is not
    # enough available cash for a single full position.
    assert outcome.buys_placed >= 1


# --- the off-gate variant ----------------------------------------------------


async def test_v3b_disabled_is_the_default_and_buys_nothing_with_the_gate_off(
    db_session, definition
):
    assert definition.module_section("off_gate")["enabled"] is False

    await _seed_instruments(db_session)
    portfolio_id = await _seed_portfolio(db_session)
    await _seed_gate_off(db_session)
    _seed_book()
    _arm(True)

    outcome = await _service(db_session, definition).run_rebalance(portfolio_id)

    assert outcome.buys_planned == 0
    rows = await _decisions(db_session, outcome.record.session_id)
    assert "100% cash by design" in rows["*"].reason


async def test_v3b_enabled_changes_the_slot_allocation_with_the_gate_off(
    db_session, definition
):
    """The variant's own flat slot count replaces the breadth ramp, and the
    regime exit stops liquidating the book.

    Enabling this is an informed choice AGAINST the owner's own finding that
    V3b ends lower than holding cash -- so the switch has to demonstrably do
    something, and this is where that is pinned.
    """
    import dataclasses

    from src.swing.services.rebalance_planner import (
        VARIANT_BASELINE,
        VARIANT_OFF_GATE,
        effective_gate,
    )

    await _seed_instruments(db_session)
    await _seed_portfolio(db_session)
    await _seed_gate_off(db_session)

    base = SwingParameters.from_definition(definition)
    ranking = RankingService(DailyBarRepository(db_session), definition, base)
    ranking.universe_symbols = lambda: list(SYMBOLS)  # noqa: E731
    snapshot = await ranking.session_snapshot()
    assert snapshot.regime.gate_on is False

    baseline = effective_gate(snapshot, base)
    assert baseline.variant == VARIANT_BASELINE
    assert baseline.liquidates is True
    assert baseline.slots == 0

    variant = dataclasses.replace(
        base, off_gate=dataclasses.replace(base.off_gate, enabled=True)
    )
    switched = effective_gate(snapshot, variant)
    assert switched.variant == VARIANT_OFF_GATE
    assert switched.liquidates is False
    assert switched.slots == base.off_gate.slots == 3
    assert switched.entries_allowed is True
    assert switched.momentum_floor == base.off_gate.momentum_floor


# --- the book ----------------------------------------------------------------


async def test_the_rebalance_pins_its_candidates_and_unpins_them_afterwards(
    db_session, definition
):
    """About thirty instruments, never five hundred.

    The rotation decides from daily bars over REST; the live feed is needed
    only to price the book and to fill an order, and the one process-wide
    connection's budget is not to be spent on 490 names nobody reads.
    """
    await _seed_instruments(db_session)
    portfolio_id = await _seed_portfolio(db_session)
    await _seed_gate_on(db_session)
    _seed_book()
    _arm(True)

    service = _service(db_session, definition)
    warmed = await service.warm_book()
    assert 0 < warmed["pinnedCount"] <= len(SYMBOLS)
    assert get_feed_manager().pinned_instruments(STRATEGY)

    await service.run_rebalance(portfolio_id)
    assert get_feed_manager().pinned_instruments(STRATEGY) == set()


async def test_a_candidate_with_no_live_price_is_skipped_rather_than_priced_off_a_close(
    db_session, definition
):
    await _seed_instruments(db_session)
    portfolio_id = await _seed_portfolio(db_session)
    await _seed_gate_on(db_session)
    # Everything but the top-ranked name gets a book.
    _seed_book(symbols=SYMBOLS[:-1])
    _arm(True)

    service = _service(db_session, definition)
    snapshot = await service.ranking.session_snapshot()
    top = snapshot.candidates[0].symbol
    get_feed_manager().book.forget([SECURITY_IDS[top]])

    outcome = await service.run_rebalance(portfolio_id)

    rows = await _decisions(db_session, outcome.record.session_id)
    assert rows[top].action == ACTION_SKIPPED
    assert "no live price" in rows[top].reason
    assert "not used" in rows[top].reason


# --- stale data ---------------------------------------------------------------


async def test_the_rebalance_refuses_to_trade_on_stale_bars(db_session, definition):
    """The bar refresh did not run, so the newest session is weeks old.

    The rebalance executes at the open of the session AFTER the one it decided.
    A session date days old means the ranking was computed from prices that
    have since moved, and placing orders on it would be acting on a market that
    no longer exists. The NIGHTLY run has no such problem -- recording what the
    stored data says is exactly its job.
    """
    from src.swing.database.db_models.swing_session_model import STATUS_SKIPPED

    await _seed_instruments(db_session)
    portfolio_id = await _seed_portfolio(db_session)
    await _seed_gate_on(db_session, last=STALE_SESSION)
    _seed_book()
    _arm(True)

    outcome = await _service(db_session, definition).run_rebalance(portfolio_id)

    assert outcome.buys_placed == 0
    assert outcome.sells_placed == 0
    assert outcome.record.status == STATUS_SKIPPED
    assert "Refusing to rebalance" in outcome.record.message
    assert "refresh has not run" in outcome.record.message


async def test_the_nightly_run_still_decides_on_bars_the_rebalance_would_refuse(
    db_session, definition
):
    """Recording what the stored data says is the nightly run's whole job."""
    from src.swing.database.db_models.swing_session_model import STATUS_COMPLETED
    from src.swing.services.swing_runner import SwingRunner

    await _seed_instruments(db_session)
    portfolio_id = await _seed_portfolio(db_session)
    await _seed_gate_on(db_session, last=STALE_SESSION)

    service = _service(db_session, definition)
    runner = SwingRunner(service.ranking, service.journal, definition)
    record = await runner.run_nightly(portfolio_id=portfolio_id)

    assert record.status == STATUS_COMPLETED
    assert record.decisions > 0


# --- idempotence -------------------------------------------------------------


async def test_running_the_rebalance_twice_for_one_session_does_not_trade_twice(
    db_session, definition
):
    await _seed_instruments(db_session)
    portfolio_id = await _seed_portfolio(db_session)
    await _seed_gate_on(db_session)
    _seed_book()
    _arm(True)

    service = _service(db_session, definition)
    first = await service.run_rebalance(portfolio_id)
    assert first.buys_placed > 0

    second = await service.run_rebalance(portfolio_id)
    assert second.buys_placed == 0
    assert second.record.extras.get("idempotent") is True
    assert second.record.run_kind == RUN_REBALANCE
