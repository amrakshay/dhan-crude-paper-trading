"""The exit, which IS this strategy.

Section 10.1 is the whole reason these tests exist and are this careful: the
same signal set held to the next CLOSE instead of the next OPEN measures
+0.428% at a 49.0% win rate against +0.617% at 71.4%, and net of the 0.30%
round trip that is +0.128% against +0.317%. A missed rotation run costs a
session's decision and is reported; a missed BTST exit costs the trade thesis.

So this file asserts, in order:

  * it sells everything, unconditionally, with no rank and no condition;
  * a sale after the window is a status of its own carrying how late it was,
    not a footnote on a normal exit;
  * a position that could NOT be sold stays open and is tried again, rather
    than the row going quiet;
  * a partial sale leaves a real position open and is recorded as such;
  * anything still open raises an ALERT;
  * an UNARMED strategy decides exactly the same thing and places nothing.
"""
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

from src.btst.database.db_models.btst_session_model import (
    ACTION_HELD,
    ACTION_SOLD,
    EXIT_DONE,
    EXIT_FAILED,
    EXIT_LATE,
    EXIT_PENDING,
    RUN_EXIT,
)
from src.btst.database.db_operations.btst_repository import (
    BtstDecisionRepository,
    BtstHoldingRepository,
    BtstSessionRepository,
)
from src.btst.services.btst_parameters import BtstParameters
from src.btst.services.execution_service import BtstExecutionService
from src.btst.services.journal_service import BtstJournalService
from src.core.time_utils import IST, utc_now
from src.daily_bars.database.db_operations.daily_bar_repository import (
    DailyBarRepository,
)
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.market.services.feed_manager import get_feed_manager
from src.portfolios.database.db_operations.portfolio_repository import (
    PortfolioRepository,
)
from src.portfolios.services.portfolio_service import PortfolioService
from src.strategies.services.strategy_registry import get_strategy_registry

STRATEGY = "nse-btst-overnight"
SEGMENT = "NSE_EQ"
SYMBOL = "WELCORP"
SECURITY_ID = "9001"

# A MONDAY, so a weekday-aware guard sees a trading day whatever day the suite
# is actually run on.
MONDAY = date(2026, 9, 21)
AT_EXIT = datetime.combine(MONDAY, time(9, 16)).replace(tzinfo=IST)
WELL_AFTER_EXIT = datetime.combine(MONDAY, time(11, 30)).replace(tzinfo=IST)
AFTER_CLOSE = datetime.combine(MONDAY, time(22, 0)).replace(tzinfo=IST)


@pytest.fixture
def definition():
    return get_strategy_registry().require(STRATEGY)


@pytest.fixture
def parameters(definition):
    return BtstParameters.from_definition(definition)


@pytest.fixture(autouse=True)
def _clean_book():
    book = get_feed_manager().book
    book.clear()
    yield
    book.clear()


@pytest.fixture(autouse=True)
def _armed():
    """ARMED by default in this file: the subject is what the exit DOES.

    The one test whose subject is the unarmed path sets it itself.
    """
    registry = get_strategy_registry()
    registry.set_armed(STRATEGY, True)
    yield
    registry.set_armed(STRATEGY, False)


async def _seed_instrument(session):
    await InstrumentRepository(session).upsert_many(
        [
            {
                "security_id": SECURITY_ID, "exchange_id": "NSE",
                "exchange_segment": SEGMENT, "segment_code": 1,
                "instrument_type": "EQUITY", "underlying_symbol": SYMBOL,
                "underlying_scrip": None, "trading_symbol": SYMBOL,
                "display_name": SYMBOL, "expiry_date": None,
                "strike_price": None, "option_type": None, "lot_size": 1,
                "tick_size": Decimal("0.05"), "is_active": True,
                "fno_eligible": False, "refreshed_at": utc_now(),
            }
        ]
    )
    await session.commit()


async def _seed_portfolio(session, opening=Decimal("1000000")) -> int:
    portfolio = await PortfolioService(session).create(
        name="BTST test", description="", strategy_keys=[STRATEGY],
        opening_balance=opening,
    )
    await session.commit()
    return portfolio.id


def _seed_book(bid=100.0, ask=100.5, quantity=100_000):
    """A depth book deep enough that a market sell fills in full."""
    book = get_feed_manager().book
    book.apply_packet(
        {
            "security_id": int(SECURITY_ID), "segment": 1,
            "ltp": (bid + ask) / 2, "volume": 500_000,
            "high": ask + 1, "low": bid - 1, "open": bid, "close": bid,
            "depth": [
                (quantity, quantity, 1, 1, bid, ask) for _ in range(5)
            ],
        }
    )


def _service(session, definition, parameters, clock=None):
    journal = BtstJournalService(
        BtstSessionRepository(session),
        BtstDecisionRepository(session),
        definition,
        parameters,
    )
    return BtstExecutionService(
        session=session,
        definition=definition,
        journal=journal,
        holdings=BtstHoldingRepository(session),
        bars=DailyBarRepository(session),
        instruments=InstrumentRepository(session),
        parameters=parameters,
        book=get_feed_manager().book,
        clock=(lambda: clock) if clock is not None else None,
    )


async def _open_holding(session, portfolio_id, *, entry_session=None, quantity=100):
    """A position held overnight, as the scan would have left it."""
    from src.orders.database.db_operations.order_repository import OrderRepository
    from src.orders.services.order_service import OrderService
    from src.positions.database.db_operations.position_repository import (
        PositionRepository,
    )

    order = await OrderService(
        OrderRepository(session), InstrumentRepository(session),
        PositionRepository(session), book=get_feed_manager().book,
    ).submit_paper_order(
        security_id=SECURITY_ID, side="BUY", order_type="MARKET",
        lots=quantity, portfolio_id=portfolio_id, strategy_key=STRATEGY,
    )
    holding = await BtstHoldingRepository(session).create(
        strategy_key=STRATEGY,
        portfolio_id=portfolio_id,
        security_id=SECURITY_ID,
        symbol=SYMBOL,
        # The session before the exit day by default, so the ordinary case
        # is a sale that is exactly on time.
        entry_session_date=entry_session or (MONDAY - timedelta(days=1)),
        entry_at=utc_now() - timedelta(days=1),
        entry_order_id=order.id,
        entry_price=Decimal(str(order.average_fill_price or "100")),
        quantity=int(order.filled_quantity or quantity),
        entry_gate_on=True,
        entry_regime_enforced=True,
        exit_status=EXIT_PENDING,
    )
    await session.commit()
    return holding


# --- the ordinary exit ------------------------------------------------------


async def test_the_exit_sells_everything_unconditionally(
    db_session, definition, parameters
):
    """No rank, no stop, no condition. It sells."""
    await _seed_instrument(db_session)
    portfolio_id = await _seed_portfolio(db_session)
    _seed_book()
    await _open_holding(db_session, portfolio_id)

    outcome = await _service(db_session, definition, parameters, AT_EXIT).run_exit(
        portfolio_id
    )

    assert outcome.due == 1
    assert outcome.sold == 1
    assert outcome.still_open == 0
    assert outcome.late == 0

    holdings = await BtstHoldingRepository(db_session).list_recent(STRATEGY)
    assert holdings[0].exit_status == EXIT_DONE
    assert holdings[0].exit_price is not None
    # The gap is computed from the two prices actually PAID, not from bars.
    assert holdings[0].overnight_gap is not None


async def test_an_exit_with_nothing_held_is_RECORDED_not_skipped(
    db_session, definition, parameters
):
    """"The exit ran and there was nothing to sell" and "the exit did not run"
    are the two facts the Health tab's first verdict has to tell apart.

    At about one signal every two sessions, the first of those is the ordinary
    outcome -- so it has to leave a record, or a healthy strategy would look
    identical to a dead one.
    """
    await _seed_instrument(db_session)
    portfolio_id = await _seed_portfolio(db_session)

    outcome = await _service(db_session, definition, parameters, AT_EXIT).run_exit(
        portfolio_id
    )

    assert outcome.due == 0
    assert outcome.record is not None
    assert outcome.record.run_kind == RUN_EXIT
    assert "nothing to sell" in outcome.record.message
    assert "ordinary outcome" in outcome.record.message


# --- lateness ---------------------------------------------------------------


async def test_a_sale_after_the_window_is_EXITED_LATE_with_the_delay_recorded(
    db_session, definition, parameters
):
    """A status of its own, not a footnote on a normal exit.

    Section 10.1 is the reason: holding past the open gives the gap back, and a
    late exit recorded as a normal one would quietly turn this strategy into a
    worse one while every report went on looking fine.
    """
    await _seed_instrument(db_session)
    portfolio_id = await _seed_portfolio(db_session)
    _seed_book()
    # Entered the session before the one whose open it should have left at.
    await _open_holding(
        db_session, portfolio_id, entry_session=MONDAY - timedelta(days=1)
    )

    outcome = await _service(
        db_session, definition, parameters, WELL_AFTER_EXIT
    ).run_exit(portfolio_id)

    assert outcome.sold == 1
    assert outcome.late == 1

    holding = (await BtstHoldingRepository(db_session).list_recent(STRATEGY))[0]
    assert holding.exit_status == EXIT_LATE
    # 09:16 to 11:30 is 134 minutes.
    assert holding.exit_delay_minutes == pytest.approx(134, abs=2)

    decisions = await BtstDecisionRepository(db_session).for_session(
        outcome.record.session_id
    )
    assert decisions[0].action == ACTION_SOLD
    assert "SOLD LATE" in decisions[0].reason
    assert "10.1" in decisions[0].reason


async def test_lateness_is_measured_against_the_session_the_sale_was_DUE(
    db_session, definition, parameters
):
    """Not against today's exit time.

    A holding stuck for three days must read as three days late, not as a few
    minutes late each morning -- which is what measuring against today would
    produce, and would make a badly stuck position look like a trivial one.
    """
    await _seed_instrument(db_session)
    portfolio_id = await _seed_portfolio(db_session)
    _seed_book()
    holding = await _open_holding(
        db_session, portfolio_id, entry_session=MONDAY - timedelta(days=7)
    )

    service = _service(db_session, definition, parameters, AT_EXIT)
    minutes = service._minutes_late(holding, AT_EXIT)  # noqa: SLF001
    # Entered on Monday 2026-09-14, due at Tuesday 09:16, and it is Monday
    # 2026-09-21 09:16 -- six days late, not "a few minutes".
    assert minutes == 6 * 24 * 60


# --- failure leaves the position OPEN ---------------------------------------


async def test_an_exit_outside_continuous_trading_leaves_the_position_OPEN(
    db_session, definition, parameters
):
    """FAILED, not "done with a note".

    `open_for()` counts FAILED as open, so the next pass tries again and the
    alarm keeps firing. A failure that marked the row finished would leave a
    position held indefinitely with nothing saying so -- which for this
    strategy is the worst outcome available.
    """
    await _seed_instrument(db_session)
    portfolio_id = await _seed_portfolio(db_session)
    _seed_book()
    await _open_holding(db_session, portfolio_id)

    outcome = await _service(
        db_session, definition, parameters, AFTER_CLOSE
    ).run_exit(portfolio_id)

    assert outcome.sold == 0
    assert outcome.failed == 1
    assert outcome.still_open == 1

    repository = BtstHoldingRepository(db_session)
    holding = (await repository.list_recent(STRATEGY))[0]
    assert holding.exit_status == EXIT_FAILED
    assert "outside continuous trading" in holding.exit_reason
    # STILL OPEN, so the next pass picks it up again.
    assert [one.id for one in await repository.open_for(STRATEGY, portfolio_id)] == [
        holding.id
    ]


async def test_a_partial_sale_leaves_a_real_position_open_and_says_so(
    db_session, definition, parameters
):
    """A thin book sells some of it, and the rest is still held.

    Recorded FAILED rather than DONE, because the row's whole job is to answer
    "is anything still held" -- and after a partial exit something is.
    """
    await _seed_instrument(db_session)
    portfolio_id = await _seed_portfolio(db_session)
    _seed_book(quantity=100_000)
    await _open_holding(db_session, portfolio_id, quantity=100)
    # Now make the book far too thin to absorb the whole position.
    _seed_book(quantity=10)

    outcome = await _service(db_session, definition, parameters, AT_EXIT).run_exit(
        portfolio_id
    )

    assert outcome.sold == 0
    assert outcome.failed == 1
    holding = (await BtstHoldingRepository(db_session).list_recent(STRATEGY))[0]
    assert holding.exit_status == EXIT_FAILED
    assert "still held" in holding.exit_reason

    decisions = await BtstDecisionRepository(db_session).for_session(
        outcome.record.session_id
    )
    assert decisions[0].action == ACTION_HELD
    assert "PARTIAL exit" in decisions[0].reason


async def test_a_position_still_open_after_the_exit_raises_an_ALERT(
    db_session, definition, parameters
):
    """A log line saying so is read by nobody at 09:30.

    Root `CLAUDE.md`: an alert is a ROW first and an HTTP call second, so this
    asserts the row. `CONDITION` dedupe rather than `WINDOW`, because a stuck
    position is a STATE -- this codebase has already produced the flood twice
    and a hundred messages about one stuck position is how the message that
    mattered fails to arrive.
    """
    from src.connections.database.db_operations.alert_repository import (
        AlertRepository,
    )
    from src.connections.services.alert_catalogue import EVENT_BTST_EXIT_INCOMPLETE

    await _seed_instrument(db_session)
    portfolio_id = await _seed_portfolio(db_session)
    _seed_book()
    await _open_holding(db_session, portfolio_id)

    await _service(db_session, definition, parameters, AFTER_CLOSE).run_exit(
        portfolio_id
    )
    await db_session.commit()

    alerts = await AlertRepository(db_session).recent(limit=20)
    mine = [one for one in alerts if EVENT_BTST_EXIT_INCOMPLETE in (one.dedupe_key or "")]
    assert mine, "a position still held after the exit must raise an alert"
    assert mine[0].strategy_key == STRATEGY
    assert "49.0%" in mine[0].body


# --- arming -----------------------------------------------------------------


async def test_an_unarmed_exit_decides_the_same_thing_and_places_nothing(
    db_session, definition, parameters
):
    """Arming is what makes it spend money, not what makes it think.

    The record is the same down to the position and the quantity; only the
    order is missing. That is what makes arming a safeguard rather than a mode.
    """
    get_strategy_registry().set_armed(STRATEGY, False)

    await _seed_instrument(db_session)
    portfolio_id = await _seed_portfolio(db_session)
    _seed_book()
    await _open_holding(db_session, portfolio_id)

    outcome = await _service(db_session, definition, parameters, AT_EXIT).run_exit(
        portfolio_id
    )

    assert outcome.armed is False
    assert outcome.sold == 0
    assert outcome.due == 1
    assert "NOT ARMED" in outcome.record.message

    decisions = await BtstDecisionRepository(db_session).for_session(
        outcome.record.session_id
    )
    assert decisions[0].action == ACTION_HELD
    assert decisions[0].quantity == 100
    assert decisions[0].order_id is None

    # And the position is untouched: an unarmed run must not mark it exited.
    holding = (await BtstHoldingRepository(db_session).list_recent(STRATEGY))[0]
    assert holding.exit_status == EXIT_PENDING


# --- the journal ------------------------------------------------------------


async def test_the_journal_refuses_to_be_edited_or_deleted(db_session):
    """Append-only, and enforced rather than documented.

    `BaseRepository` hands every subclass an `update` and a `delete`, so a rule
    that lives only in a docstring is a convention. This is the table somebody
    opens in six months to find out why the system sold something.
    """
    for repository in (
        BtstSessionRepository(db_session),
        BtstDecisionRepository(db_session),
    ):
        with pytest.raises(NotImplementedError):
            await repository.update(1, {})
        with pytest.raises(NotImplementedError):
            await repository.delete(1)


async def test_the_holdings_table_may_be_updated_but_never_deleted(db_session):
    """The one table here that is not append-only, and the limit of that.

    It holds the open question "did the exit run", which is answered by
    changing it. Deleting a row would take the realised overnight gap out of
    every report with it.
    """
    repository = BtstHoldingRepository(db_session)
    with pytest.raises(NotImplementedError):
        await repository.delete(1)
    # `update` is deliberately NOT refused here.
    assert hasattr(repository, "update")


async def test_a_friday_entry_sold_on_the_monday_is_NOT_late(
    db_session, definition, parameters
):
    """The weekend is not lateness, and this was a real bug.

    `entry_session + 1 calendar day` puts a Friday entry's exit due on the
    Saturday, so the Monday sale reads as two days late -- a LATE status and an
    alert on every Friday signal the strategy ever takes, which at about one
    signal every two sessions is a large share of them. The due date is the next
    TRADING day.
    """
    friday = date(2026, 9, 18)
    monday = date(2026, 9, 21)
    assert friday.weekday() == 4 and monday.weekday() == 0

    await _seed_instrument(db_session)
    portfolio_id = await _seed_portfolio(db_session)
    _seed_book()
    holding = await _open_holding(db_session, portfolio_id, entry_session=friday)

    service = _service(db_session, definition, parameters, AT_EXIT)
    assert service._due_at(holding).date() == monday  # noqa: SLF001
    assert service._minutes_late(holding, AT_EXIT) == 0  # noqa: SLF001

    outcome = await service.run_exit(portfolio_id)
    assert outcome.sold == 1
    assert outcome.late == 0
    holding = (await BtstHoldingRepository(db_session).list_recent(STRATEGY))[0]
    assert holding.exit_status == EXIT_DONE
