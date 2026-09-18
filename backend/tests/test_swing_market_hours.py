"""Orders only inside continuous trading. Analysis at any hour.

The rule the owner set on 2026-09-18: ranking, the nightly decision, the stop
ratchet and the journal may run whenever -- every buy and every sell must be
placed inside live continuous trading, so that the simulation replicates a real
market with paper money.

Half of it already held before this file existed: the stop monitor will not
evaluate a stop outside the session and will not send an exit inside the
Closing Auction Session. The REBALANCE had no check at all, which had not bitten
only because the regime gate was off and there was nothing to buy -- and
relaxing the gate removes that accident.

Two properties that are easy to get wrong and are asserted here:

* the check is PER INSTRUMENT, because F&O eligibility moves the boundary from
  15:30 to 15:15 and a run that starts at 15:14 can cross it mid-list;
* a refused order is DECIDED AND JOURNALLED and is NOT queued. The next
  rebalance re-decides from fresh bars and a fresh book; replaying yesterday's
  intent is how you trade a decision nobody would take today. This deliberately
  differs from the stop monitor, which does defer -- a triggered stop is a fact
  about a position that has already happened, not a fresh opinion.

Every clock here is frozen. A suite whose result depends on the hour it is run
is worse than no suite.
"""
from decimal import Decimal

import pytest

import test_swing_execution as ex
from sqlalchemy import update
from src.instruments.database.db_models.instrument_model import Instrument
from src.strategies.services.strategy_registry import get_strategy_registry
from src.swing.database.db_models.swing_session_model import (
    ACTION_BOUGHT,
    ACTION_SKIPPED,
    RUN_NIGHTLY,
)

STRATEGY = ex.STRATEGY


@pytest.fixture
def definition():
    return get_strategy_registry().require(STRATEGY)


async def _mark_fno(db_session, symbols):
    """Make these names F&O-eligible, which moves their close to 15:15.

    Derived in production from the master's own FUTSTK rows -- 210 of the 499
    universe names as of 2026-09-18 -- and never a list anybody maintains.
    """
    await db_session.execute(
        update(Instrument)
        .where(Instrument.underlying_symbol.in_(list(symbols)))
        .values(fno_eligible=True)
    )
    await db_session.commit()


async def test_a_rebalance_at_2200_decides_and_journals_and_places_nothing(
    db_session, definition
):
    await ex._seed_instruments(db_session)
    portfolio_id = await ex._seed_portfolio(db_session)
    await ex._seed_gate_on(db_session)
    ex._seed_book(ltp=100.0)
    ex._arm(True)

    outcome = await ex._service(
        db_session, definition, clock=lambda: ex._at(ex.AFTER_CLOSE)
    ).run_rebalance(portfolio_id)

    # It DECIDED. The decision is valid; it is the execution that is not.
    assert outcome.buys_planned > 0
    assert outcome.buys_placed == 0
    assert outcome.refused_outside_hours == outcome.buys_planned
    assert outcome.record is not None

    rows = await ex._decisions(db_session, outcome.record.session_id)
    refused = [
        row for row in rows.values()
        if row.action == ACTION_SKIPPED and "outside continuous trading" in row.reason
    ]
    assert refused, {row.symbol: row.reason for row in rows.values()}
    # The reason carries the time and says it is not queued.
    assert "22:00 IST" in refused[0].reason
    assert "not queued" in refused[0].reason
    assert refused[0].order_id is None
    # And the run says so at the top, not only per row.
    assert "not in continuous trading" in outcome.record.message


async def test_a_rebalance_at_1130_places_its_orders(db_session, definition):
    """The positive case, so the guard cannot pass by refusing everything."""
    await ex._seed_instruments(db_session)
    portfolio_id = await ex._seed_portfolio(db_session)
    await ex._seed_gate_on(db_session)
    ex._seed_book(ltp=100.0)
    ex._arm(True)

    outcome = await ex._service(
        db_session, definition, clock=lambda: ex._at(ex.IN_HOURS)
    ).run_rebalance(portfolio_id)

    assert outcome.buys_placed > 0
    assert outcome.refused_outside_hours == 0
    rows = await ex._decisions(db_session, outcome.record.session_id)
    bought = [row for row in rows.values() if row.action == ACTION_BOUGHT]
    assert bought and all(row.order_id is not None for row in bought)


async def test_at_1520_an_fno_name_is_refused_and_a_non_fno_name_is_not(
    db_session, definition
):
    """THE reason the check is per instrument rather than once for the run.

    Continuous cash trading for an F&O-eligible name ended at 15:15 when the
    Closing Auction Session went live on 3 August 2026; everything else trades
    continuously to 15:30. One answer for the whole list would be wrong for
    half of it on any afternoon.
    """
    await ex._seed_instruments(db_session)
    portfolio_id = await ex._seed_portfolio(db_session)
    await ex._seed_gate_on(db_session)
    ex._seed_book(ltp=100.0)
    ex._arm(True)

    # Half the universe, so whichever names the ranking picks the run contains
    # both kinds.
    fno = set(ex.SYMBOLS[::2])
    await _mark_fno(db_session, fno)

    outcome = await ex._service(
        db_session, definition, clock=lambda: ex._at(ex.IN_AUCTION)
    ).run_rebalance(portfolio_id)

    rows = await ex._decisions(db_session, outcome.record.session_id)
    placed = {
        row.symbol for row in rows.values()
        if row.action == ACTION_BOUGHT and row.order_id is not None
    }
    refused = {
        row.symbol: row.reason for row in rows.values()
        if row.action == ACTION_SKIPPED and "Not placed at 15:20" in row.reason
    }

    assert placed, "a non-F&O name should still trade at 15:20"
    assert refused, "an F&O-eligible name should not trade at 15:20"
    assert placed.isdisjoint(fno)
    assert set(refused).issubset(fno)
    # And it names the auction rather than saying "outside hours", because the
    # two are different facts.
    assert "Closing Auction Session" in next(iter(refused.values()))


async def test_the_nightly_run_still_decides_at_1815_and_places_nothing(
    db_session, definition
):
    """Analysis may run at any hour. That is the other half of the rule.

    The nightly job has no market-hours check and must not grow one: it
    computes, ratchets every trailing stop and writes the record, and it places
    no order under any circumstances -- which is why 18:15 is a perfectly good
    time for it.
    """
    from src.daily_bars.database.db_operations.daily_bar_repository import (
        DailyBarRepository,
    )
    from src.swing.database.db_operations.swing_session_repository import (
        SwingDecisionRepository,
        SwingSessionRepository,
    )
    from src.swing.services.journal_service import SwingJournalService
    from src.swing.services.ranking_service import RankingService
    from src.swing.services.swing_runner import SwingRunner

    await ex._seed_instruments(db_session)
    portfolio_id = await ex._seed_portfolio(db_session)
    await ex._seed_gate_on(db_session)
    ex._arm(True)

    ranking = RankingService(DailyBarRepository(db_session), definition)
    ranking.universe_symbols = lambda: list(ex.SYMBOLS)  # noqa: E731
    runner = SwingRunner(
        ranking,
        SwingJournalService(
            SwingSessionRepository(db_session), SwingDecisionRepository(db_session)
        ),
        definition,
    )
    record = await runner.run_nightly(portfolio_id=portfolio_id)

    assert record.run_kind == RUN_NIGHTLY
    assert record.decisions > 0
    rows = await SwingDecisionRepository(db_session).for_session(record.session_id)
    # Not one of them produced an order. The nightly run has no order path at
    # all, which is what makes running it off-market correct rather than lucky.
    assert all(row.order_id is None for row in rows)


def test_the_nightly_runner_has_no_order_path_at_all():
    """Structural, not behavioural: it cannot place even if asked to.

    A market-hours guard on the rebalance would be worth little if the nightly
    run could quietly grow one.
    """
    import inspect

    from src.swing.services import swing_runner

    source = inspect.getsource(swing_runner)
    assert "submit_paper_order" not in source


def test_the_generic_order_path_was_deliberately_left_alone():
    """The guard is swing-local, and that was a decision rather than an oversight.

    A generic guard in `submit_paper_order` would be harder to bypass, and it
    would change MCX crude and the chart's one-click entry -- neither of which
    asked for it -- and it could not journal a swing decision, which is the half
    of the requirement that makes a refusal auditable. If it is ever made
    generic it must be a per-strategy policy read from the YAML with the MCX
    module unchanged; `tests/test_swing_does_not_disturb_crude.py` pins that.
    """
    import inspect

    from src.orders.services import order_service

    source = inspect.getsource(order_service)
    assert "can_execute_continuously" not in source


async def test_the_scheduler_does_not_fire_a_rebalance_after_the_close(definition):
    """A restart in the evening must not consume the next session's rebalance.

    The trigger is bounded at BOTH ends. Idempotence is the journal's, so an
    evening run that decides and journals -- which is what the per-instrument
    guard leaves it able to do -- would write a REBALANCE record for the
    session, and the next morning's real rebalance would then decline to trade
    it. A process restarted at 19:00 would quietly eat the only chance to
    trade that session.

    The nightly is deliberately NOT bounded: recording what the stored data
    says is its whole job, at any hour.
    """
    from src.swing.services.scheduler import SwingScheduler

    scheduler = SwingScheduler()
    fired = []

    async def _rebalance(one, now):
        fired.append(("rebalance", now.time()))

    async def _nightly(one, now):
        fired.append(("nightly", now.time()))

    scheduler._run_rebalance = _rebalance
    scheduler._run_nightly = _nightly

    # 09:16 is the schedule: it fires.
    await scheduler._tick_strategy(definition, ex._at(ex.IN_HOURS))
    assert [kind for kind, _ in fired] == ["rebalance"]

    # 22:00 on a fresh process: the nightly still runs, the rebalance does not.
    fired.clear()
    scheduler = SwingScheduler()
    scheduler._run_rebalance = _rebalance
    scheduler._run_nightly = _nightly
    await scheduler._tick_strategy(definition, ex._at(ex.AFTER_CLOSE))
    assert [kind for kind, _ in fired] == ["nightly"]
