"""The clock, and the runs that did not happen.

Nothing else in this application runs on a schedule, so these tests are mostly
about the two properties that make an unattended job trustworthy:

* **it runs once** -- idempotence comes from the journal, not from a flag, so a
  restart between 18:15 and 18:20 must not decide the session twice; and
* **a run that did NOT happen is reported** -- if the process was down at 18:15
  the next start has to say so rather than carrying yesterday's stops forward
  as though nothing had happened.

The trading calendar is the regime index's own bar dates throughout. There is
no holiday list here and there should not be one.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from src.core.time_utils import IST, utc_now
from src.daily_bars.database.db_operations.daily_bar_repository import (
    DailyBarRepository,
)
from src.portfolios.database.db_models.portfolio_model import Portfolio
from src.portfolios.database.db_operations.portfolio_repository import (
    CashLedgerRepository,
    PortfolioRepository,
)
from src.strategies.services import market_clock
from src.strategies.services.strategy_registry import get_strategy_registry
from src.swing.database.db_models.swing_session_model import (
    RUN_NIGHTLY,
    RUN_REBALANCE,
    STATUS_COMPLETED,
)
from src.swing.database.db_operations.swing_session_repository import (
    SwingSessionRepository,
)
from src.swing.services.scheduler import SwingScheduler
from src.swing.services.swing_parameters import SwingParameters

STRATEGY = "nse-swing-momentum"
INDEX_SEGMENT = "IDX_I"

# A Thursday, inside NSE's week. Before the open, after it, and after the
# nightly window.
BEFORE_OPEN = datetime(2026, 9, 17, 8, 0, tzinfo=IST)
JUST_BEFORE_REBALANCE = datetime(2026, 9, 17, 9, 14, tzinfo=IST)
AFTER_REBALANCE = datetime(2026, 9, 17, 9, 20, tzinfo=IST)
AFTER_NIGHTLY = datetime(2026, 9, 17, 18, 30, tzinfo=IST)
SATURDAY = datetime(2026, 9, 19, 18, 30, tzinfo=IST)


@pytest.fixture
def definition():
    return get_strategy_registry().require(STRATEGY)


@pytest.fixture
def parameters(definition):
    return SwingParameters.from_definition(definition)


def _index_bars(dates, start_close=20000.0):
    return [
        {
            "symbol": "NIFTY",
            "exchange_segment": INDEX_SEGMENT,
            "security_id": "13",
            "bar_date": bar_date,
            "open": Decimal(str(start_close + index)),
            "high": Decimal(str(start_close + index + 5)),
            "low": Decimal(str(start_close + index - 5)),
            "close": Decimal(str(start_close + index)),
            "volume": 0,
            "source": "import",
        }
        for index, bar_date in enumerate(dates)
    ]


def _sessions(count, last=date(2026, 9, 17)):
    """`count` consecutive weekday-ish dates ending at `last`."""
    return [last - timedelta(days=offset) for offset in reversed(range(count))]


async def _seed_calendar(db_session, count=8):
    dates = _sessions(count)
    await DailyBarRepository(db_session).upsert_many(_index_bars(dates))
    await db_session.commit()
    return dates


async def _seed_portfolio(db_session) -> int:
    portfolio = Portfolio(name="Swing Momentum", status="ACTIVE")
    db_session.add(portfolio)
    await db_session.flush()
    await PortfolioRepository(db_session).set_strategies(portfolio.id, [STRATEGY])
    await CashLedgerRepository(db_session).add_entry(
        portfolio_id=portfolio.id, entry_type="DEPOSIT",
        amount=Decimal("1000000"), entry_at=utc_now(), note="Opening balance",
    )
    await db_session.commit()
    return portfolio.id


async def _record(db_session, portfolio_id, session_date, kind):
    """A completed run for one session, written the way the journal writes it."""
    await SwingSessionRepository(db_session).create(
        strategy_key=STRATEGY,
        portfolio_id=portfolio_id,
        session_date=session_date,
        run_kind=kind,
        status=STATUS_COMPLETED,
        started_at=utc_now(),
        completed_at=utc_now(),
        message="seeded",
    )
    await db_session.commit()


# --- missed runs --------------------------------------------------------------


async def test_a_session_with_no_decision_record_is_reported_as_missed(
    db_session
):
    """The process was down; nothing was decided; the gap has to be visible."""
    portfolio_id = await _seed_portfolio(db_session)
    dates = await _seed_calendar(db_session, count=6)
    # Everything decided except one session in the middle.
    expected = dates[:-1]
    for session_date in expected:
        if session_date == expected[2]:
            continue
        await _record(db_session, portfolio_id, session_date, RUN_NIGHTLY)
        await _record(db_session, portfolio_id, session_date, RUN_REBALANCE)

    scheduler = SwingScheduler()
    missed = await scheduler.detect_missed_runs()

    nightly = [entry for entry in missed if entry.kind == RUN_NIGHTLY]
    assert nightly and nightly[0].sessions == [expected[2]]
    assert scheduler.status()["missedRunCount"] == 2   # nightly and rebalance


async def test_nothing_is_reported_when_every_session_was_decided(db_session):
    portfolio_id = await _seed_portfolio(db_session)
    dates = await _seed_calendar(db_session, count=5)
    for session_date in dates[:-1]:
        await _record(db_session, portfolio_id, session_date, RUN_NIGHTLY)
        await _record(db_session, portfolio_id, session_date, RUN_REBALANCE)

    scheduler = SwingScheduler()
    assert await scheduler.detect_missed_runs() == []
    assert scheduler.status()["missedRunCount"] == 0


async def test_the_newest_session_is_not_yet_missed(db_session):
    """At 09:00 today's nightly has not run. It is not missed, it is not due."""
    portfolio_id = await _seed_portfolio(db_session)
    dates = await _seed_calendar(db_session, count=4)
    for session_date in dates[:-1]:
        await _record(db_session, portfolio_id, session_date, RUN_NIGHTLY)
        await _record(db_session, portfolio_id, session_date, RUN_REBALANCE)

    missed = await SwingScheduler().detect_missed_runs()
    assert missed == []
    assert dates[-1] not in [one for entry in missed for one in entry.sessions]


async def test_a_missed_run_is_reported_and_never_silently_re_decided(db_session):
    """Reported, not repaired.

    Re-deciding a session days later, on bars that may since have been
    restated, would write a record of a decision nobody took -- which is worse
    than a gap that says what it is.
    """
    portfolio_id = await _seed_portfolio(db_session)
    dates = await _seed_calendar(db_session, count=5)

    scheduler = SwingScheduler()
    missed = await scheduler.detect_missed_runs()
    assert missed

    # Detection wrote nothing.
    rows = await SwingSessionRepository(db_session).list_recent(STRATEGY, limit=50)
    assert rows == []
    assert portfolio_id  # the portfolio exists; nothing was decided into it


# --- the clock ----------------------------------------------------------------


async def test_nothing_runs_before_its_scheduled_time(db_session, parameters):
    await _seed_portfolio(db_session)
    await _seed_calendar(db_session)

    scheduler = SwingScheduler()
    assert parameters.schedule.rebalance_at == "09:16"
    assert parameters.schedule.nightly_at == "18:15"

    ran = await scheduler.tick(now=BEFORE_OPEN)
    assert ran == []


async def test_the_rebalance_runs_after_its_time_and_the_nightly_does_not(
    db_session
):
    await _seed_portfolio(db_session)
    await _seed_calendar(db_session)

    ran = await SwingScheduler().tick(now=AFTER_REBALANCE)
    kinds = {run.kind for run in ran}
    assert kinds == {RUN_REBALANCE}


async def test_the_evening_runs_the_nightly_and_NOT_the_rebalance(db_session):
    """Changed 2026-09-18, deliberately, and this is why.

    The rebalance trigger is bounded at BOTH ends now. It used to fire on any
    tick after 09:16, which meant a process started in the evening ran one --
    and because idempotence is the JOURNAL's, that run wrote a REBALANCE record
    for the session, so the next morning's real rebalance declined to trade it.
    A restart at 19:00 quietly consumed the only chance to trade that session.

    It could not have placed anything anyway: an order outside continuous
    trading is refused per instrument. So the unbounded window bought nothing
    but a journal entry that blocked a real run.

    The NIGHTLY is deliberately still unbounded. Recording what the stored data
    says is its whole job and 18:30 is exactly when it should happen.
    """
    await _seed_portfolio(db_session)
    await _seed_calendar(db_session)

    ran = await SwingScheduler().tick(now=AFTER_NIGHTLY)
    assert {run.kind for run in ran} == {RUN_NIGHTLY}


async def test_nothing_runs_on_a_day_the_exchange_does_not_trade(db_session):
    """Saturday. The session calendar is the index's own bar dates, so a
    weekend has no session to decide and no Dhan refresh worth spending."""
    await _seed_portfolio(db_session)
    await _seed_calendar(db_session)

    assert await SwingScheduler().tick(now=SATURDAY) == []


async def test_a_failing_job_is_not_retried_every_thirty_seconds(db_session):
    """Idempotence is the journal's job; this clock only bounds the NOISE.

    Without it a nightly with no Dhan credentials would report the same
    problem two thousand times before midnight.
    """
    await _seed_portfolio(db_session)
    await _seed_calendar(db_session)

    scheduler = SwingScheduler()
    first = await scheduler.tick(now=AFTER_NIGHTLY)
    assert first

    # Thirty seconds later: the attempt clock refuses.
    second = await scheduler.tick(now=AFTER_NIGHTLY + timedelta(seconds=30))
    assert second == []

    # Sixteen minutes later it tries again.
    third = await scheduler.tick(now=AFTER_NIGHTLY + timedelta(minutes=16))
    assert third


async def test_a_disabled_strategy_is_not_scheduled(db_session):
    await _seed_portfolio(db_session)
    await _seed_calendar(db_session)

    registry = get_strategy_registry()
    registry.set_enabled(STRATEGY, False)
    try:
        assert await SwingScheduler().tick(now=AFTER_NIGHTLY) == []
    finally:
        registry.set_enabled(STRATEGY, True)


async def test_the_book_is_warmed_before_the_rebalance_not_after(db_session):
    """The fill simulator needs depth for a name BEFORE the order."""
    await _seed_portfolio(db_session)
    await _seed_calendar(db_session)

    scheduler = SwingScheduler()
    ran = await scheduler.tick(now=JUST_BEFORE_REBALANCE)
    # Warming is not a JOB -- nothing is decided and nothing is traded --
    # but it has happened by the time the rebalance is due.
    assert ran == []
    assert scheduler._warmed.get((STRATEGY, JUST_BEFORE_REBALANCE.date()))  # noqa: SLF001


# --- status -------------------------------------------------------------------


async def test_the_status_names_the_schedule_and_the_arming_state(db_session):
    await _seed_portfolio(db_session)
    await _seed_calendar(db_session)

    status = SwingScheduler().status()
    assert status["enabled"] is True
    schedule = [one for one in status["schedules"] if one["strategyKey"] == STRATEGY]
    assert schedule
    assert schedule[0]["nightlyAtIst"] == "18:15"
    assert schedule[0]["rebalanceAtIst"] == "09:16"
    assert schedule[0]["cadence"] == "daily"
    # Enabled and ARMED are reported separately, because they are two switches.
    assert schedule[0]["enabled"] is True
    assert schedule[0]["armed"] is False


async def test_a_discretionary_strategy_is_never_scheduled(db_session):
    """MCX crude declares no automation block, so the scheduler ignores it."""
    registry = get_strategy_registry()
    automated = {definition.key for definition in registry.automated()}
    assert automated == {STRATEGY}
    assert "mcx-crude-options" not in automated


# --- the feed watchdog, carried here from phase 4 -----------------------------


def test_the_feed_watchdog_does_not_reconnect_a_closed_market(monkeypatch):
    """A book held across the close is subscribed and silent for sixteen hours.

    The watchdog reconnects after 40 s without a data frame and Dhan's protocol
    pings never reach the message loop, so before this it reconnected every 45
    seconds until the next open -- burning one of Dhan's five connection slots
    and drowning the real dead-feed signal in noise.
    """
    registry = get_strategy_registry()
    definitions = registry.enabled()
    assert definitions, "the baseline fixture enables every strategy"

    # 02:00 IST on a Thursday: MCX (09:00-23:30) and NSE (09:15-15:30) are both
    # shut.
    assert market_clock.any_market_open(
        definitions, now=datetime(2026, 9, 17, 2, 0, tzinfo=IST)
    ) is False
    # Midday: MCX and NSE are both open.
    assert market_clock.any_market_open(
        definitions, now=datetime(2026, 9, 17, 11, 0, tzinfo=IST)
    ) is True
    # 20:00: NSE is shut, MCX is not -- and one open market is enough, because
    # one connection carries both.
    assert market_clock.any_market_open(
        definitions, now=datetime(2026, 9, 17, 20, 0, tzinfo=IST)
    ) is True
    # Sunday.
    assert market_clock.any_market_open(
        definitions, now=datetime(2026, 9, 20, 11, 0, tzinfo=IST)
    ) is False


def test_the_closing_auction_counts_as_open_but_not_as_continuous(definition):
    """Two different questions, and the stop monitor asks both.

    The exchange is still running a session during the auction and the feed is
    still publishing, so the market is OPEN. Whether an order could EXECUTE
    continuously is separate, and for an F&O-eligible name after 15:15 the
    answer is no.
    """
    in_auction = datetime(2026, 9, 17, 15, 20, tzinfo=IST)

    assert market_clock.is_market_open(definition, now=in_auction) is True
    assert market_clock.can_execute_continuously(
        definition, fno_eligible=True, now=in_auction
    ) is False
    assert market_clock.can_execute_continuously(
        definition, fno_eligible=False, now=in_auction
    ) is True
    assert market_clock.in_closing_auction(
        definition, fno_eligible=True, now=in_auction
    ) is True
    assert market_clock.in_closing_auction(
        definition, fno_eligible=False, now=in_auction
    ) is False


def test_mcx_declares_no_closing_auction():
    """A commodity session has none, and configuring one would be a fiction."""
    crude = get_strategy_registry().require("mcx-crude-options")
    assert crude.market_hours.closing_auction is None
    assert market_clock.continuous_close(crude, fno_eligible=True) == (
        crude.market_hours.close
    )


# --- once a day, not every fifteen minutes ---------------------------------
def test_a_job_that_succeeded_is_not_retried_today():
    """RETRY_AFTER is for FAILURES. This is the loop it used to cause.

    The nightly has no upper time bound on purpose -- recording what the stored
    data says is valid at any hour. That made `_may_attempt` come round again
    every RETRY_AFTER until midnight, and on 2026-09-18 it did: a full
    five-hundred-symbol Dhan refresh every fifteen minutes from 18:15, each one
    correctly deciding nothing because the journal already had the session.
    """
    from datetime import timedelta

    from src.core.time_utils import ist_now
    from src.swing.services.scheduler import RETRY_AFTER, SwingScheduler
    from src.swing.database.db_models.swing_session_model import RUN_NIGHTLY

    scheduler = SwingScheduler()
    now = ist_now()
    today = now.date()
    key = "nse-swing-momentum"

    assert scheduler._may_attempt(key, RUN_NIGHTLY, today, now) is True

    scheduler._mark_attempted(key, RUN_NIGHTLY, now)
    scheduler._mark_succeeded(key, RUN_NIGHTLY, now)

    # Not now, and not after the retry window either -- it is DONE.
    assert scheduler._may_attempt(key, RUN_NIGHTLY, today, now) is False
    later = now + RETRY_AFTER + timedelta(minutes=1)
    assert scheduler._may_attempt(key, RUN_NIGHTLY, today, later) is False
    much_later = now + timedelta(hours=5)
    assert scheduler._may_attempt(key, RUN_NIGHTLY, today, much_later) is False


def test_a_job_that_FAILED_is_still_retried_after_the_window():
    """The retry must survive the fix, or a transient failure loses the day."""
    from datetime import timedelta

    from src.core.time_utils import ist_now
    from src.swing.services.scheduler import RETRY_AFTER, SwingScheduler
    from src.swing.database.db_models.swing_session_model import RUN_NIGHTLY

    scheduler = SwingScheduler()
    now = ist_now()
    today = now.date()
    key = "nse-swing-momentum"

    # Attempted and NOT marked successful -- that is what a failure looks like.
    scheduler._mark_attempted(key, RUN_NIGHTLY, now)

    assert scheduler._may_attempt(key, RUN_NIGHTLY, today, now) is False
    too_soon = now + RETRY_AFTER - timedelta(minutes=1)
    assert scheduler._may_attempt(key, RUN_NIGHTLY, today, too_soon) is False
    due = now + RETRY_AFTER + timedelta(seconds=1)
    assert scheduler._may_attempt(key, RUN_NIGHTLY, today, due) is True


def test_success_on_one_day_does_not_block_the_next():
    """The guard is per DAY. A strategy that ran last night runs tonight."""
    from datetime import timedelta

    from src.core.time_utils import ist_now
    from src.swing.services.scheduler import SwingScheduler
    from src.swing.database.db_models.swing_session_model import RUN_NIGHTLY

    scheduler = SwingScheduler()
    now = ist_now()
    key = "nse-swing-momentum"

    scheduler._mark_succeeded(key, RUN_NIGHTLY, now)
    assert scheduler._may_attempt(key, RUN_NIGHTLY, now.date(), now) is False

    tomorrow = now + timedelta(days=1)
    assert scheduler._may_attempt(key, RUN_NIGHTLY, tomorrow.date(), tomorrow) is True


async def test_ran_on_day_is_keyed_on_when_the_job_RAN(db_session):
    """Not on the session it decided.

    `session_date` is the index's newest bar date and does not move on a day
    the vendor published nothing -- which is precisely the day the scheduler
    needs to know it has already done its work.
    """
    from datetime import timedelta

    from src.core.time_utils import ist_today, utc_now
    from src.swing.database.db_models.swing_session_model import (
        RUN_NIGHTLY,
        STATUS_COMPLETED,
    )
    from src.swing.database.db_operations.swing_session_repository import (
        SwingSessionRepository,
    )

    repository = SwingSessionRepository(db_session)
    today = ist_today()

    assert await repository.ran_on_day("nse-swing-momentum", RUN_NIGHTLY, today) is False

    # A session decided TODAY about a bar date from days ago -- exactly the
    # shape that fooled the old guard.
    await repository.create(
        strategy_key="nse-swing-momentum",
        portfolio_id=1,
        session_date=today - timedelta(days=4),
        run_kind=RUN_NIGHTLY,
        status=STATUS_COMPLETED,
        started_at=utc_now(),
    )
    await db_session.commit()

    assert await repository.ran_on_day("nse-swing-momentum", RUN_NIGHTLY, today) is True
    # And it does not leak across days.
    assert (
        await repository.ran_on_day(
            "nse-swing-momentum", RUN_NIGHTLY, today - timedelta(days=1)
        )
        is False
    )
