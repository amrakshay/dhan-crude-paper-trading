"""BTST's rule and clock hooks, as `src/strategies/` dispatches to them.

The counterpart of `src/swing/services/module_hooks.py`. That one is a thin
re-export, because the rotation's logic already existed and was not moved; this
one carries the scheduling logic itself, because BTST's jobs are new.

**Two run kinds, and both differ from the rotation's in ways that matter:**

  SCAN at ~15:20 -- decides AND buys in one pass, because what it decides on
                    (a running high, a running low, a cumulative volume) does
                    not survive the night. Bounded at both ends, like the
                    rebalance and for a sharper version of the same reason: a
                    restart at 16:00 must not scan, because it would journal a
                    session and consume its only chance to trade, and a restart
                    at 11:00 must not scan either, because a scan at 11:00
                    measures a third of a day against a rule calibrated on
                    95% of one.

  EXIT at the open -- runs even when nothing is due, and keeps running until
                    everything is out. Bounded only at the bottom, on purpose:
                    if the process was down at 09:16 the exit still has to
                    happen, late, and be recorded as late. That is the
                    opposite of the scan's rule and it is the single most
                    important asymmetry in this module. A scan that missed its
                    window has nothing useful left to do; an exit that missed
                    its window has the entire position still to sell.
"""
from datetime import date, datetime, time, timedelta
from typing import Dict, List

from src.btst.database.db_models.btst_session_model import RUN_EXIT, RUN_SCAN
from src.btst.services.btst_parameters import BtstParameters
from src.btst.services.btst_policy import (
    describe_policies,
    policy_warnings,
    validate_policy_change as _validate_policy_change,
)
from src.btst.services.btst_schedule import (
    BtstScheduleError,
    describe_settings,
    resolve_schedule,
    setting_warnings,
    validate,
)
from src.database.session import session_scope
from src.logging_config import get_logger
from src.strategies.services.scheduling import JobRun, MissedRun
from src.strategies.services.strategy_definition import StrategyDefinition
from src.strategies.services.strategy_modules import ModuleRuleRefused

logger = get_logger("btst.scheduler")

__all__ = [
    "describe_policies",
    "policy_warnings",
    "validate_policy_change",
    "describe_settings",
    "validate_setting",
    "setting_warnings",
    "tick_strategy",
    "detect_missed",
]

# How long after the scan time a restart may still scan. Short, and the
# docstring above says why: a scan is a measurement of a specific moment, and
# one taken half an hour later is a different measurement against the same
# thresholds.
SCAN_WINDOW = timedelta(minutes=10)

# How long before the exit time the book is warmed. The fill simulator needs
# depth for a name BEFORE the order; three minutes is what the rotation uses
# and there is no reason for this to differ.
WARMUP_MINUTES = 3


class SettingRefused(ModuleRuleRefused):
    """A setting value this module will not accept, with the reason."""


def validate_policy_change(
    definition: StrategyDefinition, policy: str, enforced: bool
) -> None:
    """No combination of this module's two switches is refused.

    See `btst_policy.validate_policy_change` -- it is a deliberate no-op rather
    than an absent hook, so the next reader can tell the difference between
    "nothing to check" and "somebody forgot".
    """
    _validate_policy_change(definition, policy, enforced)


def validate_setting(definition: StrategyDefinition, setting: str, value: str) -> str:
    """The normalised value, or a refusal naming what it would break."""
    try:
        return validate(definition, setting, value)
    except BtstScheduleError as error:
        raise SettingRefused(str(error)) from error


# --- the clock ------------------------------------------------------------


async def tick_strategy(
    scheduler, definition: StrategyDefinition, now: datetime
) -> List[JobRun]:
    """Run whatever is due: the warm-up, the exit, the scan.

    **The exit is checked BEFORE the scan**, and the order is not incidental.
    They cannot both be due on the same pass under any sane configuration, but
    if a clock or a setting ever put them close together, selling what is held
    must happen before buying more -- the same reasoning the rotation applies
    when it executes its sells before sizing its buys.
    """
    ran: List[JobRun] = []
    parameters = BtstParameters.from_definition(definition)
    # Read on every pass rather than cached at start-up, so a change from the
    # Configuration tab takes effect at the next run instead of at the next
    # restart -- the same way a toggle does.
    schedule = resolve_schedule(definition, parameters)
    today = now.date()

    # --- warm the book, a few minutes before the exit ---------------------
    warm_from = _minus_minutes(schedule.exit, WARMUP_MINUTES)
    if warm_from <= now.time() < schedule.exit:
        if not scheduler._warmed.get((definition.key, today)):  # noqa: SLF001
            scheduler._warmed[(definition.key, today)] = True  # noqa: SLF001
            await _warm(scheduler, definition)

    # --- the exit ---------------------------------------------------------
    #
    # BOUNDED ONLY AT THE BOTTOM, and that is the asymmetry this module turns
    # on. The rotation's rebalance has an upper bound so a restart at 19:00
    # cannot consume the next session's only chance to trade. THE EXIT HAS NO
    # SUCH LUXURY: a position that was not sold at 09:16 is still held, and the
    # right response at 11:00 is to sell it and record that it was late, not to
    # wait for tomorrow. Section 10.1 -- the overnight gap is the whole edge
    # and holding through a session gives it back.
    #
    # It also runs when nothing is due, and records that, because "the exit ran
    # and there was nothing to sell" and "the exit did not run" are the two
    # facts the Health tab's first verdict has to tell apart.
    if (
        schedule.exit <= now.time() <= definition.market_hours.close
        and scheduler._may_attempt(definition.key, RUN_EXIT, today, now)  # noqa: SLF001
    ):
        ran.append(await _run_exit(scheduler, definition, now))

    # --- the scan ---------------------------------------------------------
    #
    # BOUNDED AT BOTH ENDS, and tightly. A scan is a measurement of one moment
    # in the session: section 10.3 validated the 15:20 snapshot specifically,
    # at 83.4% precision against the closing signal, and a scan an hour later
    # or an hour earlier is not covered by that. Worse, a late scan would
    # journal the session and -- because idempotence is the journal's -- the
    # real scan the next day would find the session already recorded.
    scan_until = _plus(schedule.scan, SCAN_WINDOW)
    if (
        schedule.scan <= now.time() <= min(scan_until, definition.market_hours.close)
        and scheduler._may_attempt(definition.key, RUN_SCAN, today, now)  # noqa: SLF001
    ):
        ran.append(await _run_scan(scheduler, definition, now))

    return ran


async def _warm(scheduler, definition: StrategyDefinition) -> None:
    """Subscribe what the exit is about to sell, ahead of the open."""
    scheduler._begin(f"warming the book for {definition.label}")  # noqa: SLF001
    try:
        async with session_scope() as session:
            service = await _build(session, definition)
            result = await service.warm_book()
        logger.info(
            "BTST warmed the book for %s: %s instrument(s) pinned %s minutes "
            "before the exit.",
            definition.key, result.get("pinnedCount"), WARMUP_MINUTES,
        )
    except Exception as exc:  # noqa: BLE001 - a warm-up failure is not fatal
        logger.warning(
            "Could not warm the book for %s before the exit (%s). The exit "
            "will still run; names with no depth are recorded as still held.",
            definition.key, exc,
        )
    finally:
        scheduler._idle()  # noqa: SLF001


async def _run_scan(scheduler, definition: StrategyDefinition, now: datetime) -> JobRun:
    run = JobRun(strategy_key=definition.key, kind=RUN_SCAN, at=now)
    scheduler._mark_attempted(definition.key, RUN_SCAN, now)  # noqa: SLF001

    # ALREADY SCANNED TODAY? A second scan does not merely repeat work, it
    # BUYS A SECOND SET OF POSITIONS. The in-memory guard covers a running
    # process; this covers a restart, which clears that memory.
    if await _already_ran_today(definition.key, RUN_SCAN, now):
        scheduler._mark_succeeded(definition.key, RUN_SCAN, now)  # noqa: SLF001
        run.detail = (
            "Already scanned today. A second scan of the same session would "
            "buy a second set of positions, so it is refused rather than "
            "de-duplicated afterwards."
        )
        logger.info("BTST scan for %s: %s", definition.key, run.detail)
        return scheduler._record(run)  # noqa: SLF001

    scheduler._begin(f"scanning the session for {definition.label}")  # noqa: SLF001
    try:
        portfolios = await scheduler._portfolios(definition.key)  # noqa: SLF001
        if not portfolios:
            run.detail = (
                "No active portfolio runs this strategy, so there was nothing "
                "to scan for. Attach it to one on the Portfolios page."
            )
            logger.warning("%s: %s", definition.key, run.detail)
            return scheduler._record(run)  # noqa: SLF001

        placed = 0
        candidates = 0
        for portfolio_id in portfolios:
            async with session_scope() as session:
                service = await _build(session, definition)
                outcome = await service.run_scan(portfolio_id)
                await session.commit()
            placed += outcome.placed
            candidates = max(candidates, outcome.candidates)
        run.portfolios = len(portfolios)
        run.detail = (
            f"{candidates} candidate(s), {placed} order(s) placed across "
            f"{len(portfolios)} portfolio(s)."
        )
        scheduler._mark_succeeded(definition.key, RUN_SCAN, now)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001 - one job must not kill the task
        run.ok = False
        run.detail = f"{type(exc).__name__}: {exc}"
        scheduler.last_error = run.detail
        logger.exception("BTST scan for %s failed", definition.key)
    finally:
        scheduler._idle()  # noqa: SLF001
    return scheduler._record(run)  # noqa: SLF001


async def _run_exit(scheduler, definition: StrategyDefinition, now: datetime) -> JobRun:
    """Sell everything. The most defended job in this application.

    **It is NOT marked succeeded while anything is still open.** That is the
    difference from every other job here: `_mark_succeeded` is what stops a job
    being retried for the rest of the day, and a position that did not sell is
    precisely the thing that must be tried again. The rotation's nightly
    applies the same shape to its bar refresh -- a run whose refresh failed is
    not done -- and this is that rule pointed at the thing that matters most.
    """
    run = JobRun(strategy_key=definition.key, kind=RUN_EXIT, at=now)
    scheduler._mark_attempted(definition.key, RUN_EXIT, now)  # noqa: SLF001
    scheduler._begin(f"exiting overnight positions for {definition.label}")  # noqa: SLF001

    try:
        portfolios = await scheduler._portfolios(definition.key)  # noqa: SLF001
        if not portfolios:
            run.detail = (
                "No active portfolio runs this strategy, so there is nothing "
                "to exit."
            )
            return scheduler._record(run)  # noqa: SLF001

        sold = late = still_open = due = 0
        for portfolio_id in portfolios:
            async with session_scope() as session:
                service = await _build(session, definition)
                outcome = await service.run_exit(portfolio_id)
                await session.commit()
            due += outcome.due
            sold += outcome.sold
            late += outcome.late
            still_open += outcome.still_open

        run.portfolios = len(portfolios)
        run.detail = f"{sold} of {due} position(s) sold."
        if late:
            run.detail = f"{run.detail} {late} LATE."
        if still_open:
            run.ok = False
            run.detail = (
                f"{run.detail} {still_open} STILL OPEN -- the next pass will "
                f"try again."
            )
        else:
            # Done for today ONLY when the book is actually flat. A job that
            # left a position held has not done its work, whatever it recorded.
            scheduler._mark_succeeded(definition.key, RUN_EXIT, now)  # noqa: SLF001

        # The pins are given back once the book is flat, and only then: a name
        # still to be sold still needs a depth book to sell into.
        if not still_open:
            try:
                async with session_scope() as session:
                    service = await _build(session, definition)
                    await service.release_book()
            except Exception:  # noqa: BLE001 - never fail an exit on cleanup
                logger.warning(
                    "Could not release the pinned instruments for %s after the "
                    "exit; they will be released at the next resync.",
                    definition.key, exc_info=True,
                )
    except Exception as exc:  # noqa: BLE001
        run.ok = False
        run.detail = f"{type(exc).__name__}: {exc}"
        scheduler.last_error = run.detail
        logger.exception("BTST exit for %s failed", definition.key)
    finally:
        scheduler._idle()  # noqa: SLF001
    return scheduler._record(run)  # noqa: SLF001


async def detect_missed(
    definition: StrategyDefinition, session, expected: List[date]
) -> List[MissedRun]:
    """Which of these sessions has no SCAN record.

    **Only the scan.** An EXIT record exists only for a session that HELD
    something, and at roughly half a signal a session most days hold nothing --
    so comparing exits against the trading calendar would report every quiet
    day as a missed run. Whether an exit actually happened is answered by
    `btst_holdings`, where a position that did not get out stays open and
    raises its own alert; that is a stronger check than a missing journal row,
    because it is about the position rather than about the paperwork.

    Bounded below by the first session this strategy ever COMPLETED, for the
    same reason the rotation's is: a journal cannot be missing a record from
    before it existed.
    """
    from src.btst.database.db_operations.btst_repository import BtstSessionRepository

    sessions = BtstSessionRepository(session)
    live_from = await sessions.first_completed_session(definition.key)
    if live_from is None:
        return []
    expected = [one for one in expected if one >= live_from]
    if not expected:
        return []

    decided = set(
        await sessions.decided_session_dates(definition.key, RUN_SCAN, expected[0])
    )
    gaps = [one for one in expected if one not in decided]
    if not gaps:
        return []
    return [MissedRun(strategy_key=definition.key, kind=RUN_SCAN, sessions=gaps)]


# --- wiring ---------------------------------------------------------------


async def _build(session, definition: StrategyDefinition):
    """One execution service on one database session."""
    from src.btst.database.db_operations.btst_repository import (
        BtstDecisionRepository,
        BtstHoldingRepository,
        BtstSessionRepository,
    )
    from src.btst.services.execution_service import BtstExecutionService
    from src.btst.services.journal_service import BtstJournalService
    from src.daily_bars.database.db_operations.daily_bar_repository import (
        DailyBarRepository,
    )
    from src.instruments.database.db_operations.instrument_repository import (
        InstrumentRepository,
    )

    parameters = BtstParameters.from_definition(definition)
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
    )


async def _already_ran_today(strategy_key: str, run_kind: str, now: datetime) -> bool:
    """Has this job written a session today? Never fatal.

    A database that cannot be read must not stop the exit running -- that would
    turn a transient read error into a book held through a second session. It
    returns False and the run proceeds, which for the exit is the safe
    direction and for the scan is guarded again by the journal.
    """
    from src.btst.database.db_operations.btst_repository import BtstSessionRepository

    try:
        async with session_scope() as session:
            return await BtstSessionRepository(session).ran_on_day(
                strategy_key, run_kind, now.date()
            )
    except Exception:  # noqa: BLE001 - see the docstring
        logger.warning(
            "Could not check whether the %s job already ran today; running it "
            "rather than risking a missed run.",
            run_kind, exc_info=True,
        )
        return False


def _minus_minutes(value: time, minutes: int) -> time:
    base = datetime.combine(date(2000, 1, 1), value) - timedelta(minutes=minutes)
    return base.time()


def _plus(value: time, delta: timedelta) -> time:
    base = datetime.combine(date(2000, 1, 1), value) + delta
    return base.time()
