"""The IPO dashboard's clock. The SECOND clock in this application.

**WHY IT IS NOT THE SWING SCHEDULER.** `src/swing/services/scheduler.py` drives
live strategy decisions and real (paper) orders on an armed, running strategy.
This drives a data refresh and a reminder message. Merging them would mean
editing the clock that trades in order to ship a page that does not, so the two
stay apart: `src/swing/` does not import `src/ipo/` and `src/ipo/` does not
import `src/swing/`. Duplicating a small tick loop is the accepted cost, and it
is a small one -- the interesting logic here is the guards, not the loop.

**TWO JOBS.**

* **The daily refresh**, 13:00 IST, over every non-listed IPO.
* **The closing-day sweep**, on the hour from 10:00 to 17:00 IST. Each sweep
  re-fetches ONLY the IPOs closing that day -- not the whole board -- so every
  reminder carries a current GMP, then raises one reminder covering whatever is
  still outstanding.

**A JOB THAT HAS DONE ITS WORK IS DONE, NOT DUE FOR A RETRY.** The lesson is
already in root `CLAUDE.md`, paid for in about 3,500 wasted requests: the swing
nightly re-ran every fifteen minutes from 18:15 to midnight because `RETRY_AFTER`
-- which bounds how often a FAILED job is retried -- was what decided whether a
SUCCESSFUL one ran again. Two guards here, exactly as there:

* `_done` in memory, for a process that keeps running;
* `ipo_job_runs`, keyed on the SLOT, for a restart -- because a restart clears
  the memory, and a restart is how that job got repeated all evening.

A run that FAILED records nothing, so it is retried (bounded by `RETRY_AFTER`)
rather than written off.

**A MISSED SWEEP IS SENT LATE, ONCE.** A slot is catchable only within its own
hour: a 14:00 sweep the process was down for fires at 14:20 when it comes back,
and is gone by 15:05 -- at which point the 15:00 slot is the due one. Same
asymmetry root `CLAUDE.md` records for BTST's exit: where the deadline belongs
to somebody else, bound the job at the BOTTOM and let it be late, because late
is worth something and never is worth nothing.
"""
import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Set

from src import config_utils
from src.core.time_utils import ist_now, parse_hhmm, utc_now
from src.database.session import session_scope
from src.ipo.database.db_models.ipo_model import JOB_CLOSING_SWEEP, JOB_DAILY_REFRESH
from src.ipo.database.db_operations.ipo_repository import IpoJobRunRepository
from src.ipo.services.ipo_reminder_service import IpoReminderService
from src.ipo.services.ipo_service import IpoService
from src.ipo.services.ipo_source_client import IpoSourceError
from src.logging_config import get_logger

logger = get_logger("ipo.scheduler")

TASK_NAME = "ipo-scheduler"

# Defaults. All three are configuration rather than constants because the
# reminder window is a personal preference; none of them changes what the
# feature DECIDES, only when it wakes up.
DEFAULT_DAILY_REFRESH_AT = "13:00"
DEFAULT_SWEEP_FROM = "10:00"
DEFAULT_SWEEP_TO = "17:00"
DEFAULT_INTERVAL_SECONDS = 30

# HOW LONG BEFORE A FAILED JOB IS TRIED AGAIN. Failure only -- a job that has
# SUCCEEDED for its slot is never retried, which is the distinction that cost
# 3,500 requests when it was missing from the swing nightly. Five minutes
# rather than fifteen, because a sweep only has its own hour to succeed in.
RETRY_AFTER = timedelta(minutes=5)


@dataclass
class IpoJobOutcome:
    """One job that ran, as the health surface reports it."""

    kind: str
    slot_key: str
    at: datetime
    ok: bool
    detail: str


class IpoScheduler:
    """The IPO dashboard's own clock. Knows nothing about any strategy."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self.runs = 0
        self.last_error: Optional[str] = None
        self.history: List[IpoJobOutcome] = []
        # (job kind, slot key) that have SUCCEEDED in this process.
        self._done: Set[tuple] = set()
        # (job kind, slot key) -> when it was last attempted, so a failing job
        # backs off instead of retrying every thirty seconds.
        self._attempted: Dict[tuple, datetime] = {}

    # --- configuration -----------------------------------------------------
    @staticmethod
    def _enabled() -> bool:
        return config_utils.get_property_value_boolean("ipo.scheduler_enabled", True)

    @staticmethod
    def _interval_seconds() -> float:
        return float(
            config_utils.get_property_value_int(
                "ipo.scheduler_interval_seconds", DEFAULT_INTERVAL_SECONDS
            )
        )

    @staticmethod
    def _daily_refresh_at() -> time:
        return parse_hhmm(
            config_utils.get_property_value(
                "ipo.daily_refresh_at", DEFAULT_DAILY_REFRESH_AT
            )
        )

    @staticmethod
    def _sweep_window() -> tuple:
        return (
            parse_hhmm(
                config_utils.get_property_value("ipo.reminder_from", DEFAULT_SWEEP_FROM)
            ),
            parse_hhmm(
                config_utils.get_property_value("ipo.reminder_to", DEFAULT_SWEEP_TO)
            ),
        )

    # --- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        if not self._enabled():
            logger.info(
                "The IPO scheduler is disabled (ipo.scheduler_enabled=false); "
                "neither the daily refresh nor the closing-day reminders will run."
            )
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name=TASK_NAME)
        logger.info(
            "IPO scheduler started (daily refresh at %s IST, reminders %s-%s IST, "
            "checking the clock every %.0f s)",
            self._daily_refresh_at().strftime("%H:%M"),
            self._sweep_window()[0].strftime("%H:%M"),
            self._sweep_window()[1].strftime("%H:%M"),
            self._interval_seconds(),
        )
        try:
            await self.bootstrap()
        except Exception:  # noqa: BLE001 - never block startup on this
            logger.exception("The IPO bootstrap refresh failed; 13:00 will retry")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None

    async def _run(self) -> None:
        while not self._stopping:
            await asyncio.sleep(self._interval_seconds())
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                logger.exception("IPO scheduler pass failed")

    # --- the guards --------------------------------------------------------
    async def _already_done(self, kind: str, slot_key: str) -> bool:
        if (kind, slot_key) in self._done:
            return True
        async with session_scope() as session:
            if await IpoJobRunRepository(session).has_run(kind, slot_key):
                # Seen on disk but not in memory: this process restarted. Cache
                # it so the next tick does not ask the database again.
                self._done.add((kind, slot_key))
                return True
        return False

    def _may_attempt(self, kind: str, slot_key: str, now: datetime) -> bool:
        last = self._attempted.get((kind, slot_key))
        if last is not None and now - last < RETRY_AFTER:
            return False
        self._attempted[(kind, slot_key)] = now
        return True

    @staticmethod
    def _daily_slot(day: date) -> str:
        return day.isoformat()

    @staticmethod
    def _sweep_slot(moment: datetime) -> str:
        return f"{moment:%Y-%m-%d}|{moment:%H}"

    # --- one pass ----------------------------------------------------------
    async def tick(self, now: Optional[datetime] = None) -> List[IpoJobOutcome]:
        """Check the clock and run whatever is due. Safe to call directly."""
        self.runs += 1
        now = now or ist_now()
        ran: List[IpoJobOutcome] = []

        if await self._daily_refresh_due(now):
            ran.append(await self.run_daily_refresh(now))

        if await self._sweep_is_due(now) is not None:
            ran.append(await self.run_closing_sweep(now))

        return ran

    async def _daily_refresh_due(self, now: datetime) -> bool:
        """After the configured time, once a day.

        NO UPPER BOUND, deliberately -- the same choice the swing nightly
        makes. Fetching the board is valid at any hour, so a process that came
        up at 22:00 should still do today's refresh. What stops it running
        again at 22:30 is the slot guard, not a window.
        """
        if now.time() < self._daily_refresh_at():
            return False
        slot = self._daily_slot(now.date())
        if await self._already_done(JOB_DAILY_REFRESH, slot):
            return False
        return self._may_attempt(JOB_DAILY_REFRESH, slot, now)

    def _sweep_due_slot(self, now: datetime) -> Optional[str]:
        """The hour slot due right now, or None. Says nothing about whether it
        has already run -- that is `_sweep_is_due`.

        A slot lasts its own hour and no longer, which is what makes a missed
        14:00 arrive at 14:20 and NOT at 15:05: at 15:05 the 15:00 reminder is
        the one that belongs, and yesterday's intent is not replayed.

        The boundaries, since they are the thing worth being sure of:
        09:59 -> nothing (before the window), 10:00 -> slot 10, 17:00 -> slot
        17, and 17:01 -> STILL slot 17. The last is deliberate: the 17:00
        reminder is the last one of a closing day and the most valuable, so if
        the process was down at 17:00 it goes out at 17:01 rather than not at
        all. What stops it going out twice is the slot guard, not the clock.
        """
        start, end = self._sweep_window()
        if now.time() < start or now.hour > end.hour:
            return None
        return self._sweep_slot(now)

    async def _sweep_is_due(self, now: datetime) -> Optional[str]:
        slot = self._sweep_due_slot(now)
        if slot is None:
            return None
        if await self._already_done(JOB_CLOSING_SWEEP, slot):
            return None
        if not self._may_attempt(JOB_CLOSING_SWEEP, slot, now):
            return None
        return slot

    # --- the jobs ----------------------------------------------------------
    async def bootstrap(self) -> Optional[IpoJobOutcome]:
        """A first refresh on a database that has never had one.

        Without it a fresh install shows three empty tabs until 13:00 the next
        day, which reads as a broken page rather than an empty one. It consumes
        TODAY's daily slot, so it cannot turn into a second refresh an hour
        later, and it does nothing at all once there is data.
        """
        now = ist_now()
        slot = self._daily_slot(now.date())
        if await self._already_done(JOB_DAILY_REFRESH, slot):
            return None
        async with session_scope() as session:
            if await IpoService(session).ipos.count() > 0:
                return None
        logger.info("No IPOs are stored yet; fetching the board once at startup")
        return await self.run_daily_refresh(now)

    async def run_daily_refresh(self, now: Optional[datetime] = None) -> IpoJobOutcome:
        """Fetch the whole board and store it."""
        now = now or ist_now()
        slot = self._daily_slot(now.date())
        started = utc_now()
        try:
            async with session_scope() as session:
                result = await IpoService(session).refresh(today=now.date())
                await session.commit()
            detail = (
                f"{result.mainboard_rows} mainboard row(s): {result.created} new, "
                f"{result.updated} updated, {result.skipped_already_listed} "
                f"already listed and skipped"
            )
            return await self._record(JOB_DAILY_REFRESH, slot, started, True, detail)
        except IpoSourceError as exc:
            await self._report_source_failure(str(exc))
            return await self._record(
                JOB_DAILY_REFRESH, slot, started, False, str(exc)
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("The daily IPO refresh failed")
            return await self._record(
                JOB_DAILY_REFRESH, slot, started, False,
                f"{type(exc).__name__}: {exc}",
            )

    async def run_closing_sweep(
        self, now: Optional[datetime] = None, *, force: bool = False
    ) -> IpoJobOutcome:
        """Refresh today's closers, then remind about whatever is outstanding.

        **THE REFRESH IS BEST-EFFORT AND THE REMINDER IS NOT.** If the source
        cannot be reached, a health alert is raised and the sweep continues
        with the stored GMP labelled stale. The reminder is the point; the GMP
        is context.
        """
        now = now or ist_now()
        slot = self._sweep_slot(now)
        started = utc_now()
        today = now.date()
        fetch_note = ""

        try:
            async with session_scope() as session:
                service = IpoService(session)
                closing = await service.ipos.closing_on(today)
                if closing:
                    try:
                        await service.refresh(
                            today=today,
                            only_source_ids=[row.source_id for row in closing],
                        )
                        await session.commit()
                    except IpoSourceError as exc:
                        await session.rollback()
                        fetch_note = f" (GMP not refreshed: {exc})"
                        await self._report_source_failure(str(exc))

            async with session_scope() as session:
                service = IpoService(session)
                outstanding = await service.outstanding_closing_today(today)
                outcome = await IpoReminderService(session).send_sweep(
                    outstanding, now
                )
                await session.commit()

            detail = (
                f"{outcome.outstanding} outstanding, "
                f"{'reminder raised' if outcome.sent else outcome.reason}"
                f"{fetch_note}"
            )
            return await self._record(
                JOB_CLOSING_SWEEP, slot, started, True, detail, force=force
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("The IPO closing-day sweep failed")
            return await self._record(
                JOB_CLOSING_SWEEP, slot, started, False,
                f"{type(exc).__name__}: {exc}", force=force,
            )

    async def _report_source_failure(self, detail: str) -> None:
        try:
            async with session_scope() as session:
                await IpoReminderService(session).report_source_failure(detail)
                await session.commit()
        except Exception:  # noqa: BLE001 - an alert must never break its caller
            logger.warning("Could not record the IPO source failure", exc_info=True)

    async def _record(
        self,
        kind: str,
        slot: str,
        started: datetime,
        ok: bool,
        detail: str,
        *,
        force: bool = False,
    ) -> IpoJobOutcome:
        """Write the slot's record, unless this was a forced manual run.

        A manual run must not consume a scheduled slot: pressing Refresh at
        13:55 should not be what stops the 14:00 reminder going out.
        """
        if ok and not force:
            self._done.add((kind, slot))
            async with session_scope() as session:
                await IpoJobRunRepository(session).record(
                    job_kind=kind,
                    slot_key=slot,
                    started_at=started,
                    finished_at=utc_now(),
                    ok=True,
                    detail=detail[:500],
                )
                await session.commit()
        if not ok:
            self.last_error = detail
        outcome = IpoJobOutcome(
            kind=kind, slot_key=slot, at=started, ok=ok, detail=detail
        )
        self.history.append(outcome)
        del self.history[:-20]
        logger.info("IPO job %s[%s]: %s", kind, slot, detail)
        return outcome

    # --- what the health surface reads ------------------------------------
    def status(self) -> dict:
        last = self.history[-1] if self.history else None
        return {
            "enabled": self._enabled(),
            "running": self._task is not None and not self._task.done(),
            "runs": self.runs,
            "lastError": self.last_error,
            "dailyRefreshAt": self._daily_refresh_at().strftime("%H:%M"),
            "reminderFrom": self._sweep_window()[0].strftime("%H:%M"),
            "reminderTo": self._sweep_window()[1].strftime("%H:%M"),
            "lastJob": (
                {
                    "kind": last.kind,
                    "slotKey": last.slot_key,
                    "ok": last.ok,
                    "detail": last.detail,
                }
                if last
                else None
            ),
        }


# --- the singleton ---------------------------------------------------------
_scheduler: Optional[IpoScheduler] = None


def get_ipo_scheduler() -> IpoScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = IpoScheduler()
    return _scheduler


async def shutdown_ipo_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        await _scheduler.stop()
        _scheduler = None
