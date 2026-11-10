"""NSE VCP Swing's rule and clock hooks, as `src/strategies/` dispatches to them.

Registered in `strategy_modules.MODULE_HOOKS`. The clock is the shared one
(`SwingScheduler`); this module only says what is due.

  NIGHTLY  from `schedule.nightly_at` until the open, never inside the
           session: it refreshes `daily_bars` through the rotation's own
           refresh, and during the session Dhan returns today's half-finished
           bar. A night that is missed is caught up the next night -- the scan
           reads every bar after each symbol's cursor -- and REPORTED by
           `detect_missed`; bases seen on the missed bars are remembered and
           never traded.
  WARM     a few minutes before the morning run, pin today's candidates.
  MORNING  from `schedule.rebalance_at` to the close: exits, then entries.
  STOP     every pass while the market is open.

The two schedule settings reuse the rotation's NAMES and its validator,
because they mean the same thing with the same constraints (analysis outside
the session, orders inside continuous trading). No policies: this module has
no regime gate to switch (VCP_REGIME.md), and `describe_policies` says so with
an empty list rather than an absent hook.
"""
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List

from src.database.session import session_scope
from src.logging_config import get_logger
from src.strategies.services import market_clock
from src.strategies.services.scheduling import JobRun, MissedRun
from src.strategies.services.strategy_definition import StrategyDefinition
from src.strategies.services.strategy_modules import ModuleRuleRefused
from src.swing.services import schedule_settings as _swing_schedule
from src.vcp.database.db_models.vcp_models import RUN_MORNING, RUN_NIGHTLY
from src.vcp.services.vcp_parameters import VcpParameters

logger = get_logger("vcp.scheduler")

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

WARMUP_MINUTES = 3
RUN_STOP = "STOP"


class SettingRefused(ModuleRuleRefused):
    """A setting value this module will not accept, with the reason."""


# --- policies: none ---------------------------------------------------------


def describe_policies(definition: StrategyDefinition) -> Dict[str, Any]:
    return {"policies": [], "contradiction": None}


def policy_warnings(definition: StrategyDefinition, policy: str, enforced: bool) -> List[str]:
    return []


def validate_policy_change(definition: StrategyDefinition, policy: str, enforced: bool) -> None:
    raise ModuleRuleRefused(
        f"{definition.label} has no switchable rules. An index regime gate was "
        f"tested and made every variant worse (VCP_REGIME.md), so none is offered."
    )


# --- settings: the rotation's two, with the rotation's validator ------------


def _resolve(definition: StrategyDefinition):
    return _swing_schedule.resolve_schedule(definition, VcpParameters.from_definition(definition))


def describe_settings(definition: StrategyDefinition) -> Dict[str, Any]:
    return _swing_schedule.describe_settings(definition, VcpParameters.from_definition(definition))


def validate_setting(definition: StrategyDefinition, setting: str, value: str) -> str:
    try:
        return _swing_schedule.validate(definition, setting, value)
    except _swing_schedule.ScheduleSettingError as error:
        raise SettingRefused(str(error)) from error


def setting_warnings(definition: StrategyDefinition, setting: str, value: str) -> List[str]:
    return _swing_schedule.setting_warnings(definition, setting, value)


# --- the clock --------------------------------------------------------------


def nightly_due(definition: StrategyDefinition, nightly_at: time, now: time) -> bool:
    """From `nightly_at` until the open, and never inside the session.

    An analysis time before the open (08:40) is due until 09:15. One after the
    close (18:15) is due from then, through midnight, until the next open.
    """
    hours = definition.market_hours
    if hours.open <= now <= hours.close:
        return False
    if nightly_at < hours.open:
        return nightly_at <= now < hours.open
    return now >= nightly_at or now < hours.open


async def tick_strategy(scheduler, definition: StrategyDefinition, now: datetime) -> List[JobRun]:
    ran: List[JobRun] = []
    if not market_clock.is_trading_day(definition, now):
        return ran
    schedule = _resolve(definition)
    today = now.date()

    # --- the live stop, first: it is the time-critical job ---------------
    if market_clock.is_market_open(definition, now=now):
        await _check_stops(scheduler, definition)

    # --- warm the book ahead of the morning ------------------------------
    warm_from = (datetime.combine(today, schedule.rebalance) - timedelta(minutes=WARMUP_MINUTES)).time()
    if warm_from <= now.time() < schedule.rebalance and not scheduler._warmed.get((definition.key, today)):  # noqa: SLF001
        scheduler._warmed[(definition.key, today)] = True  # noqa: SLF001
        await _warm(definition)

    # --- the morning -------------------------------------------------------
    if (schedule.rebalance <= now.time() <= definition.market_hours.close
            and scheduler._may_attempt(definition.key, RUN_MORNING, today, now)):  # noqa: SLF001
        ran.append(await _run_morning(scheduler, definition, now))

    # --- the nightly -------------------------------------------------------
    if (nightly_due(definition, schedule.nightly, now.time())
            and scheduler._may_attempt(definition.key, RUN_NIGHTLY, today, now)):  # noqa: SLF001
        ran.append(await _run_nightly(scheduler, definition, now))
    return ran


async def _build(session, definition):
    from src.vcp.services.execution_service import VcpExecutionService

    return VcpExecutionService(session, definition)


async def _check_stops(scheduler, definition) -> None:
    try:
        async with session_scope() as session:
            service = await _build(session, definition)
            sold = await service.check_stops()
            await session.commit()
        if sold:
            logger.info("VCP stop monitor sold %s position(s) for %s.", sold, definition.key)
    except Exception:  # noqa: BLE001 - a stop pass must never kill the clock
        logger.exception("VCP stop check for %s failed; it runs again next pass.", definition.key)


async def _warm(definition) -> None:
    try:
        async with session_scope() as session:
            pinned = await (await _build(session, definition)).warm_book()
        logger.info("VCP warmed %s candidate(s) for %s.", pinned, definition.key)
    except Exception:  # noqa: BLE001 - the morning still runs, and pins itself
        logger.warning("Could not warm the book for %s.", definition.key, exc_info=True)


async def _ran_today(definition, kind: str, now: datetime) -> bool:
    from src.vcp.database.db_operations.vcp_repository import VcpSessionRepository

    try:
        async with session_scope() as session:
            return await VcpSessionRepository(session).ran_on_day(definition.key, kind, now.date())
    except Exception:  # noqa: BLE001 - run rather than miss; the journal guards again
        logger.warning("Could not read whether %s ran today.", kind, exc_info=True)
        return False


async def _run_nightly(scheduler, definition, now: datetime) -> JobRun:
    run = JobRun(strategy_key=definition.key, kind=RUN_NIGHTLY, at=now)
    scheduler._mark_attempted(definition.key, RUN_NIGHTLY, now)  # noqa: SLF001
    if await _ran_today(definition, RUN_NIGHTLY, now):
        scheduler._mark_succeeded(definition.key, RUN_NIGHTLY, now)  # noqa: SLF001
        run.detail = "Already ran today."
        return scheduler._record(run)  # noqa: SLF001
    try:
        # The rotation's refresh, unchanged: same universe file, so after its
        # own 08:35 nightly this is a fast pass over symbols already current.
        refreshed, refresh_ok = await scheduler._refresh_bars(definition)  # noqa: SLF001
        scheduler._begin(f"scanning for VCP bases ({definition.label})")  # noqa: SLF001
        async with session_scope() as session:
            service = await _build(session, definition)
            outcome = await service.run_nightly(progress=scheduler._set_progress)  # noqa: SLF001
            await session.commit()
        run.ok = outcome.ok and bool(refresh_ok)
        run.detail = " ".join(x for x in (refreshed, outcome.detail) if x)
        if run.ok:
            scheduler._mark_succeeded(definition.key, RUN_NIGHTLY, now)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001 - one job must not kill the task
        run.ok = False
        run.detail = f"{type(exc).__name__}: {exc}"
        scheduler.last_error = run.detail
        logger.exception("VCP nightly for %s failed", definition.key)
    finally:
        scheduler._idle()  # noqa: SLF001
    return scheduler._record(run)  # noqa: SLF001


async def _run_morning(scheduler, definition, now: datetime) -> JobRun:
    run = JobRun(strategy_key=definition.key, kind=RUN_MORNING, at=now)
    scheduler._mark_attempted(definition.key, RUN_MORNING, now)  # noqa: SLF001
    scheduler._begin(f"placing the morning orders for {definition.label}")  # noqa: SLF001
    try:
        portfolios = await scheduler._portfolios(definition.key)  # noqa: SLF001
        if not portfolios:
            run.detail = "No portfolio runs this strategy; attach it on the Portfolios page."
            return scheduler._record(run)  # noqa: SLF001
        details, ok = [], True
        session_date = None
        for portfolio_id in portfolios:
            async with session_scope() as session:
                service = await _build(session, definition)
                outcome = await service.run_morning(portfolio_id)
                await session.commit()
            details.append(f"portfolio {portfolio_id}: {outcome.detail}")
            ok = ok and outcome.ok
        async with session_scope() as session:
            service = await _build(session, definition)
            from src.vcp.database.db_operations.vcp_repository import VcpSessionRepository
            from src.vcp.database.db_models.vcp_models import STATUS_COMPLETED

            nightly = await VcpSessionRepository(session).latest(
                definition.key, RUN_NIGHTLY, statuses=[STATUS_COMPLETED])
            if nightly is not None:
                session_date = nightly.session_date
                await service.close_out_candidates(session_date)
            await service.release_book()
            await session.commit()
        run.portfolios = len(portfolios)
        run.ok = ok
        run.detail = " | ".join(details)
        if ok:
            scheduler._mark_succeeded(definition.key, RUN_MORNING, now)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        run.ok = False
        run.detail = f"{type(exc).__name__}: {exc}"
        scheduler.last_error = run.detail
        logger.exception("VCP morning for %s failed", definition.key)
    finally:
        scheduler._idle()  # noqa: SLF001
    return scheduler._record(run)  # noqa: SLF001


async def detect_missed(definition: StrategyDefinition, session, expected: List[date]) -> List[MissedRun]:
    """Sessions with no NIGHTLY record, from the first one ever completed."""
    from src.vcp.database.db_operations.vcp_repository import VcpSessionRepository

    sessions = VcpSessionRepository(session)
    live_from = await sessions.first_completed_session(definition.key)
    if live_from is None:
        return []
    expected = [one for one in expected if one >= live_from]
    if not expected:
        return []
    decided = set(await sessions.decided_session_dates(definition.key, RUN_NIGHTLY, expected[0]))
    gaps = [one for one in expected if one not in decided]
    return [MissedRun(strategy_key=definition.key, kind=RUN_NIGHTLY, sessions=gaps)] if gaps else []
