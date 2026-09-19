"""Is the IPO dashboard doing its job, and what has it been doing.

The same split root `CLAUDE.md` draws between the two health surfaces, applied
to a third thing. `/health` answers "what is this PROCESS doing" -- one feed,
one book, one CPU, the task table -- and this answers "is THIS FEATURE
healthy". Neither copies the other: a figure that is not about the IPO
dashboard stays on the process page and the Status tab LINKS to it.

**It is not a strategy health tab either.** `swing_health_service` reports on
machinery that arms and trades; there is none here. What there is, and what
nothing else surfaces, is the answer to the only question this feature can
really fail at: **did the reminder actually go out at 14:00, and if not, why.**
`ipo_job_runs` is the record and this is what reads it.

Admin-only for the same reason the other two are: it serves job detail lines
and machinery state.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.time_utils import IST, ist_now, to_ist, utc_now
from src.ipo.database.db_models.ipo_model import (
    JOB_CLOSING_SWEEP,
    JOB_DAILY_REFRESH,
)
from src.ipo.database.db_operations.ipo_repository import (
    IpoJobRunRepository,
    IpoRepository,
)
from src.ipo.services.ipo_service import IpoService
# The host is imported, never spelled again: `tests/test_outbound_hosts.py`
# asserts exactly one module names it, and a health page repeating the literal
# would fail that build for no benefit.
from src.ipo.services.ipo_source_client import IPO_SOURCE_HOST


def _iso(moment: Optional[datetime]) -> Optional[str]:
    """Naive UTC out of the database -> an IST-offset ISO string.

    The same shape the alert catalogue serves, so the page formats one kind of
    timestamp rather than two.
    """
    if moment is None:
        return None
    return to_ist(moment).isoformat()


@dataclass
class SweepExpectation:
    """What the reminder sweeps should do today, and what they have done.

    Kept as a dataclass rather than assembled inline because the interesting
    case is the one with no IPO closing: then "0 of 0 sweeps ran" is CORRECT
    and a page that printed it as a gap would report a fault every day but a
    handful.
    """

    closing_today: int
    outstanding: int
    expected_slots: List[str]
    completed_slots: List[str]
    missed_slots: List[str]


class IpoHealthService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.runs = IpoJobRunRepository(session)
        self.ipos = IpoRepository(session)
        self.service = IpoService(session)

    async def payload(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        from src.ipo.services.scheduler import get_ipo_scheduler

        moment = now or ist_now()
        scheduler = get_ipo_scheduler()
        state = scheduler.status()
        today = moment.date()

        sweeps = await self._sweeps(today, moment, state)
        last_refresh = await self.runs.latest(JOB_DAILY_REFRESH)
        freshness = await self._freshness(today)

        return {
            "clock": {
                "enabled": state["enabled"],
                "running": state["running"],
                # Named so the Status tab can point at the row on /health
                # rather than re-reporting the task table here.
                "taskName": "ipo-scheduler",
                "runs": state["runs"],
                "lastError": state["lastError"],
                "dailyRefreshAt": state["dailyRefreshAt"],
                "reminderFrom": state["reminderFrom"],
                "reminderTo": state["reminderTo"],
                "nextDailyRefreshAt": _next_daily(
                    state["dailyRefreshAt"],
                    moment,
                    done=await self.runs.has_run(
                        JOB_DAILY_REFRESH, today.isoformat()
                    ),
                ),
                "nextSweepAt": _next_sweep(
                    state["reminderFrom"],
                    state["reminderTo"],
                    moment,
                    # NO SWEEP IS SCHEDULED when nothing closes today. Saying
                    # "next sweep 14:00" on a day with no closing IPO would
                    # promise a message that is never coming.
                    has_work=sweeps.closing_today > 0,
                ),
            },
            "sweeps": {
                "closingToday": sweeps.closing_today,
                "outstanding": sweeps.outstanding,
                "expectedSlots": sweeps.expected_slots,
                "completedSlots": sweeps.completed_slots,
                # A slot inside today's window that has passed with no record.
                # The honest name for it: the process was down, or the sweep
                # failed every retry inside its hour.
                "missedSlots": sweeps.missed_slots,
            },
            "source": {
                "host": IPO_SOURCE_HOST,
                "lastRefreshAt": _iso(
                    last_refresh.finished_at if last_refresh else None
                ),
                "lastRefreshDetail": last_refresh.detail if last_refresh else None,
                # A FAILED run records nothing durable on purpose (it must be
                # allowed to retry), so failures are read from the scheduler's
                # in-memory history and disappear on a restart. Said here
                # rather than left to look like "no failures".
                "recentFailures": [
                    {
                        "kind": item.kind,
                        "slotKey": item.slot_key,
                        "at": _iso(item.at),
                        "detail": item.detail,
                    }
                    for item in scheduler.history
                    if not item.ok
                ],
                "failuresAreProcessLocal": True,
            },
            "freshness": freshness,
            "jobs": [
                {
                    "kind": row.job_kind,
                    "slotKey": row.slot_key,
                    "startedAt": _iso(row.started_at),
                    "finishedAt": _iso(row.finished_at),
                    "ok": bool(row.ok),
                    "detail": row.detail,
                }
                for row in await self.runs.recent(limit=20)
            ],
            "notes": [
                "A job that has done its work is DONE, not due for a retry. "
                "Every slot here is guarded twice: in memory for a running "
                "process, and by its row for a restart.",
                "Only SUCCESSFUL runs are recorded. A failed one deliberately "
                "leaves no row, so it can be retried inside its own hour -- "
                "which is why failures above are process-local and vanish on a "
                "restart.",
                "A sweep slot lasts its own hour and no longer. A missed 14:00 "
                "is sent late at 14:20; at 15:05 the 15:00 reminder is the one "
                "that belongs, and yesterday's intent is never replayed.",
                "Figures about the process itself -- the task table, CPU, the "
                "feed -- are on the System Health page. This page does not "
                "restate them.",
            ],
        }

    async def _sweeps(
        self, today: date, moment: datetime, state: Dict[str, Any]
    ) -> SweepExpectation:
        closing = await self.service.closing_today(today)
        outstanding = [view for view in closing if view.is_outstanding]

        start = _parse(state["reminderFrom"])
        end = _parse(state["reminderTo"])
        expected = [
            f"{today.isoformat()}|{hour:02d}"
            for hour in range(start.hour, end.hour + 1)
        ]
        completed = []
        for slot in expected:
            if await self.runs.has_run(JOB_CLOSING_SWEEP, slot):
                completed.append(slot)

        missed: List[str] = []
        if closing:
            # Only an hour that has fully passed can be called missed -- the
            # current one may still be about to run.
            for slot in expected:
                hour = int(slot.rsplit("|", 1)[1])
                if hour < moment.hour and slot not in completed:
                    missed.append(slot)

        return SweepExpectation(
            closing_today=len(closing),
            outstanding=len(outstanding),
            expected_slots=expected if closing else [],
            completed_slots=completed,
            missed_slots=missed,
        )

    async def _freshness(self, today: date) -> Dict[str, Any]:
        """How old the stored GMP readings are, counted rather than asserted."""
        views = await self.service.tracked(today, utc_now())
        stale = [view for view in views if view.gmp.is_stale]
        captured = [
            view.gmp.captured_at for view in views if view.gmp.captured_at is not None
        ]
        return {
            "storedIpos": await self.ipos.count(),
            "trackedNotListed": len(views),
            "staleReadings": len(stale),
            "staleCompanies": [view.company_name for view in stale][:10],
            "oldestCaptureAt": _iso(min(captured)) if captured else None,
            "newestCaptureAt": _iso(max(captured)) if captured else None,
        }


def _parse(hhmm: str) -> time:
    from src.core.time_utils import parse_hhmm

    return parse_hhmm(hhmm)


def _next_daily(hhmm: str, moment: datetime, *, done: bool) -> Optional[str]:
    """When the daily refresh is next due, as an absolute IST timestamp.

    Absolute rather than "13:00" so the page can count down locally instead of
    re-fetching every second -- the same choice `SwingScheduler.next_occurrence`
    makes, and NOT holiday-aware for the same reason: there is no holiday list
    in this application and this does not add one.
    """
    at = _parse(hhmm)
    due = datetime.combine(moment.date(), at, tzinfo=IST)
    if done or moment >= due:
        due = due + timedelta(days=1)
    return due.isoformat()


def _next_sweep(
    from_hhmm: str, to_hhmm: str, moment: datetime, *, has_work: bool
) -> Optional[str]:
    """The next reminder sweep, or None when none is scheduled.

    None is a real answer here, not a missing one: with no IPO closing today
    there is nothing to remind about and no sweep will run. A time printed
    anyway would promise a message that is not coming.
    """
    if not has_work:
        return None
    start, end = _parse(from_hhmm), _parse(to_hhmm)
    if moment.time() < start:
        return datetime.combine(moment.date(), start, tzinfo=IST).isoformat()
    if moment.hour >= end.hour:
        return None
    nxt = moment.replace(
        hour=moment.hour + 1, minute=0, second=0, microsecond=0
    )
    return nxt.isoformat()
