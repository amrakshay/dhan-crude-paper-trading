"""The clock this strategy runs on.

Nothing else in this application runs on a schedule. Two jobs, both IST-aware
and both restart-safe:

* **nightly**, after the close (`schedule.nightly_at`, 18:15 IST): top up the
  daily bars from Dhan, recompute the regime, the breadth and the ranking,
  ratchet every trailing stop on the session's close, and write the decision
  record -- including, and especially, the sessions where the decision is to do
  nothing.
* **rebalance**, at the open (`schedule.rebalance_at`, 09:16 IST): sell the
  sell list, then buy the buy list, through `submit_paper_order`. The book is
  warmed `swing.warmup_minutes` beforehand so the fill simulator has depth for
  the names about to be traded.

**Restart-safe means a missed run is DETECTED AND REPORTED, not silently
skipped.** If the process was down at 18:15, the next start must notice and say
so rather than carrying yesterday's stops forward as though nothing happened.
`SwingSessionRepository.decided_session_dates` against the regime index's own
bar dates is what answers that -- the index's dates are the trading calendar,
which is why there is no holiday list here and should not be one.

**Idempotence is the journal's, not a flag's.** `sessions_completed_on` is what
stops a restart at 18:20 re-deciding what was decided at 18:15. The in-memory
attempt clock below only stops a FAILING job retrying every thirty seconds; it
is not what makes a successful run happen once.

Nothing here is on the tick path. The task wakes on its own interval, opens its
own database session, and does its work there -- the same arrangement as
`OrderMatcher` and the two stop monitors.
"""
import asyncio
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional

from src import config_utils
from src.core.time_utils import ist_now, parse_hhmm
from src.database.session import session_scope
from src.logging_config import get_logger
from src.strategies.services import market_clock
from src.strategies.services.scheduling import JobRun, MissedRun
from src.strategies.services.strategy_definition import StrategyDefinition
from src.swing.database.db_models.swing_session_model import (
    RUN_NIGHTLY,
    RUN_REBALANCE,
)

# Re-exported: `JobRun` and `MissedRun` moved to `src/strategies/services/
# scheduling.py` on 2026-09-19 so a second automated module could return them
# without importing this package. Every existing import of them from here still
# works, which is the point.
__all__ = ["SwingScheduler", "JobRun", "MissedRun", "TASK_NAME", "RETRY_AFTER"]

logger = get_logger("swing.scheduler")

TASK_NAME = "swing-scheduler"

# How long to wait before retrying a job that FAILED. Without it a failing
# nightly would retry every thirty seconds until midnight and fill the log with
# one problem repeated two thousand times.
# HOW LONG BEFORE A FAILED JOB IS TRIED AGAIN. Failure only: a job that has
# SUCCEEDED today is not retried at all (see `_succeeded`). That distinction is
# the whole point and was missing until 2026-09-18, when the nightly -- which
# has no upper time bound on purpose -- re-ran every fifteen minutes from 18:15
# onwards, each time pulling five hundred symbols from Dhan and each time
# correctly deciding nothing, because the journal had already recorded the
# session. Roughly 3,500 wasted requests in two hours before it was noticed.
RETRY_AFTER = timedelta(minutes=15)


class SwingScheduler:
    """The only clock in this application, and every automated strategy's.

    THE NAME IS HISTORY, AND IS KEPT ON PURPOSE. It was written for the
    rotation and ran only the rotation until 2026-09-19; it now also runs
    BTST. Renaming the class would be cosmetic, but the TASK name
    (`swing-scheduler`) is load-bearing -- it is a key in `TASK_DESCRIPTIONS`
    and `expected_task_names`, both health surfaces judge the task against it,
    and nothing is gained by making a live installation's health page report a
    task that has "gone missing" and a new one that is "unexpected".

    What each strategy DOES on the clock is not here. `tick` resolves the
    strategy's own module through `strategy_modules.hooks_for` and asks it, so
    the run kinds are per module rather than the hardcoded NIGHTLY/REBALANCE
    pair they used to be. The rotation's job bodies below are untouched by
    that change; only their caller moved.
    """

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self.runs = 0
        self.last_error: Optional[str] = None
        self.history: List[JobRun] = []
        self.missed: List[MissedRun] = []
        self.checked_for_missed_at: Optional[datetime] = None
        # (strategy_key, kind, IST date) -> when it was last attempted.
        self._attempted: Dict[tuple, datetime] = {}
        # (strategy_key, kind, IST date) for jobs that have SUCCEEDED. A
        # success is final for the day; only a failure is retried.
        self._succeeded: set = set()
        self._warmed: Dict[tuple, bool] = {}
        # WHAT IT IS DOING RIGHT NOW, as a state rather than a log line. The
        # scheduler is the only thing that knows, and a page that says nothing
        # while a 746-second bar refresh runs looks broken rather than busy.
        # None means idle -- which is a real answer, not a missing one.
        self.activity: Optional[str] = None
        self.activity_since: Optional[datetime] = None
        # How far through a long job it is, or None when nothing long is
        # running. None is NOT zero percent: a job that has not started and
        # a job that has done none of its work look identical as a number
        # and are different states, so the pages render only the first.
        self.progress: Optional[Dict[str, Any]] = None

    # --- configuration -----------------------------------------------------
    @staticmethod
    def _enabled() -> bool:
        return config_utils.get_property_value_boolean("swing.scheduler_enabled", True)

    @staticmethod
    def _interval_seconds() -> float:
        return float(
            config_utils.get_property_value_int("swing.scheduler_interval_seconds", 30)
        )

    @staticmethod
    def _warmup_minutes() -> int:
        return config_utils.get_property_value_int("swing.warmup_minutes", 3)

    @staticmethod
    def _missed_lookback() -> int:
        return config_utils.get_property_value_int(
            "swing.missed_run_lookback_sessions", 10
        )

    # --- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        if not self._enabled():
            logger.info(
                "The swing scheduler is disabled (swing.scheduler_enabled=false); "
                "nothing will run on a clock."
            )
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name=TASK_NAME)
        logger.info(
            "Swing scheduler started (checking the clock every %.0f s)",
            self._interval_seconds(),
        )
        # Look for missed runs IMMEDIATELY rather than at the first job. A
        # process that was down overnight has to say so at startup, which is
        # the moment an operator is actually looking.
        try:
            await self.detect_missed_runs()
        except Exception:  # noqa: BLE001 - never block startup on this
            logger.exception("Could not check for missed swing runs at startup")

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
        interval = self._interval_seconds()
        while not self._stopping:
            await asyncio.sleep(interval)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                logger.exception("Swing scheduler pass failed")

    # --- one pass ----------------------------------------------------------
    async def tick(self, now: Optional[datetime] = None) -> List[JobRun]:
        """Check the clock and run whatever is due. Safe to call directly."""
        from src.strategies.services.strategy_registry import get_strategy_registry

        self.runs += 1
        now = now or ist_now()
        ran: List[JobRun] = []
        registry = get_strategy_registry()

        from src.strategies.services.strategy_modules import hooks_for

        for definition in registry.automated():
            if not registry.is_enabled(definition.key):
                continue
            # WHAT this strategy does on the clock belongs to its own module.
            # Until 2026-09-19 there was one automated module and this loop
            # called the rotation's jobs directly; the run kinds are per module
            # now. The rotation's `_tick_strategy` below is unchanged and is
            # what its hook calls.
            hooks = hooks_for(definition)
            if hooks is None or not hasattr(hooks, "tick_strategy"):
                continue
            try:
                ran.extend(await hooks.tick_strategy(self, definition, now))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one module must not stop another
                self.last_error = f"{definition.key}: {type(exc).__name__}: {exc}"
                logger.exception(
                    "Scheduled work for %s failed; other strategies are "
                    "unaffected.",
                    definition.key,
                )

        # Once a day, after the nightly window, check that nothing was missed.
        if self._should_check_missed(now):
            await self.detect_missed_runs()

        return ran

    async def _tick_strategy(
        self, definition: StrategyDefinition, now: datetime
    ) -> List[JobRun]:
        from src.swing.services.swing_parameters import SwingParameters

        ran: List[JobRun] = []
        if not market_clock.is_trading_day(definition, now):
            # Not a weekday the exchange trades. The session CALENDAR is the
            # index's own bar dates and a holiday simply has no bar, so a run
            # today would record a SKIPPED row for yesterday's session -- but
            # there is no point spending a Dhan refresh on it.
            return ran

        from src.swing.services.schedule_settings import resolve_schedule

        parameters = SwingParameters.from_definition(definition)
        # The EFFECTIVE times: the YAML's, with the operator's on top. Read on
        # every pass rather than cached at start-up, so a change from the
        # Configuration tab takes effect at the next run instead of at the next
        # restart -- the same way a toggle does.
        schedule = resolve_schedule(definition, parameters)
        rebalance_at = schedule.rebalance
        nightly_at = schedule.nightly
        today = now.date()

        # --- warm the book, a few minutes before the open ------------------
        warm_from = self._minus_minutes(rebalance_at, self._warmup_minutes())
        if warm_from <= now.time() < rebalance_at:
            if not self._warmed.get((definition.key, today)):
                self._warmed[(definition.key, today)] = True
                await self._warm(definition)

        # --- the rebalance --------------------------------------------------
        #
        # BOUNDED AT BOTH ENDS, and the upper bound is load-bearing. The lower
        # one is the schedule; without the upper one, any restart later in the
        # day fires a rebalance -- and because idempotence is the JOURNAL's, that
        # run writes a REBALANCE record for the session and the next morning's
        # real rebalance then declines to trade it. A process restarted at 19:00
        # would quietly consume the next session's only chance to trade.
        #
        # It is also the honest reading of the rule: P19 executes at the OPEN.
        # A rebalance at 19:00 could place nothing anyway -- the per-instrument
        # guard in the execution service refuses it -- so the only thing the
        # unbounded window bought was a journal entry that blocked a real run.
        #
        # A rebalance that was genuinely missed is REPORTED by the missed-run
        # detector and never silently re-decided days later, which is the same
        # rule the nightly follows. The nightly has no upper bound on purpose:
        # recording what the stored data says is exactly its job, at any hour.
        if (
            rebalance_at <= now.time() <= definition.market_hours.close
            and self._may_attempt(definition.key, RUN_REBALANCE, today, now)
        ):
            ran.append(await self._run_rebalance(definition, now))

        # --- the nightly ----------------------------------------------------
        if now.time() >= nightly_at and self._may_attempt(
            definition.key, RUN_NIGHTLY, today, now
        ):
            ran.append(await self._run_nightly(definition, now))

        return ran

    @staticmethod
    def next_occurrence(
        at: time, trading_days, now: Optional[datetime] = None
    ) -> Optional[datetime]:
        """The next IST moment this job is due, as an absolute timestamp.

        Absolute rather than "18:15" so the page can count down LOCALLY instead
        of re-fetching every second (frontend/CLAUDE.md section 4). The
        scheduler knows the times and never computed the next occurrence, which
        is why the Live tab could say when a job runs but not when it next
        runs.

        NOT HOLIDAY-AWARE, and the payload says so. There is deliberately no
        holiday list anywhere in this application -- the trading calendar is the
        regime index's own bar dates -- so this answers the narrower question
        of the next trading WEEKDAY at that time. On an exchange holiday the
        countdown runs down and the job records a skipped session, which is the
        cheap failure; inventing a holiday list to avoid it would be a second
        calendar to go stale.

        None when the strategy trades on no weekday at all, which is a
        configuration nobody meant rather than a countdown of zero.
        """
        now = now or ist_now()
        days = {int(day) for day in trading_days}
        if not days:
            return None
        for offset in range(0, 8):
            candidate = datetime.combine(
                (now + timedelta(days=offset)).date(), at
            ).replace(tzinfo=now.tzinfo)
            if candidate <= now or candidate.weekday() not in days:
                continue
            return candidate
        return None

    @staticmethod
    def _minus_minutes(value: time, minutes: int) -> time:
        base = datetime.combine(date(2000, 1, 1), value) - timedelta(minutes=minutes)
        return base.time()

    def _may_attempt(
        self, strategy_key: str, kind: str, today: date, now: datetime
    ) -> bool:
        """Rate-limit ATTEMPTS. Idempotence is the journal's job, not this.

        A successful run is recorded and `sessions_completed_on` stops the next
        one. This only stops a run that FAILED -- no credentials, no bars, a
        Dhan outage -- retrying every thirty seconds and reporting the same
        problem two thousand times before midnight.
        """
        # A job that has already done its work today is DONE, not due for a
        # retry. Without this the nightly -- which is deliberately unbounded at
        # the top end -- comes round again every RETRY_AFTER until midnight.
        if (strategy_key, kind, today) in self._succeeded:
            return False
        last = self._attempted.get((strategy_key, kind, today))
        if last is None:
            return True
        return (now - last) >= RETRY_AFTER

    def _mark_attempted(self, strategy_key: str, kind: str, now: datetime) -> None:
        self._attempted[(strategy_key, kind, now.date())] = now

    def _mark_succeeded(self, strategy_key: str, kind: str, now: datetime) -> None:
        """Done for today. Only a FAILED job comes round again."""
        self._succeeded.add((strategy_key, kind, now.date()))

    def _begin(self, activity: str) -> None:
        self.activity = activity
        self.activity_since = ist_now()
        # A new activity owns its own progress. Leaving the previous job's
        # behind would show a finished bar against a job that has not started
        # counting.
        self.progress = None
        logger.debug("Swing scheduler: %s", activity)

    def _idle(self) -> None:
        self.activity = None
        self.activity_since = None
        self.progress = None

    def _set_progress(self, done: int, total: int, item: str) -> None:
        """How far through the current job it is.

        Called from a worker's own loop, so it does nothing but assign a small
        dict -- no I/O, no logging, no computation. The percentage and the
        estimated finish are worked out by whoever RENDERS this, because they
        are presentation and because the page already ticks once a second and
        can move them between polls.

        `total` of zero is reported as a total of zero rather than suppressed:
        "nothing to do" is a real outcome of a refresh and reads differently
        from "not running".
        """
        self.progress = {
            "done": int(done),
            "total": int(total),
            "item": item or None,
            "startedAtIst": (
                self.activity_since.isoformat() if self.activity_since else None
            ),
            "atIst": ist_now().isoformat(),
        }

    # --- the jobs -----------------------------------------------------------
    async def _warm(self, definition: StrategyDefinition) -> None:
        """Subscribe the names the rebalance might trade, ahead of the open."""
        self._begin(f"warming the book for {definition.label}")
        try:
            async with session_scope() as session:
                service, _ = await self._build(session, definition)
                result = await service.warm_book()
            logger.info(
                "Swing scheduler warmed the book for %s: %s instrument(s) pinned "
                "%s minutes before the rebalance.",
                definition.key, result.get("pinnedCount"), self._warmup_minutes(),
            )
        except Exception as exc:  # noqa: BLE001 - a warm-up failure is not fatal
            logger.warning(
                "Could not warm the book for %s before the rebalance (%s). Names "
                "with no depth will be skipped with a recorded reason.",
                definition.key, exc,
            )
        finally:
            self._idle()

    async def _run_rebalance(
        self, definition: StrategyDefinition, now: datetime
    ) -> JobRun:
        run = JobRun(strategy_key=definition.key, kind=RUN_REBALANCE, at=now)
        self._mark_attempted(definition.key, RUN_REBALANCE, now)
        self._begin(f"rebalancing {definition.label}")
        try:
            portfolios = await self._portfolios(definition.key)
            if not portfolios:
                run.detail = (
                    "No active portfolio runs this strategy, so there is no book "
                    "to rebalance. Attach it to one on the Portfolios page."
                )
                logger.warning("%s: %s", definition.key, run.detail)
                return self._record(run)

            placed = 0
            for portfolio_id in portfolios:
                async with session_scope() as session:
                    service, _ = await self._build(session, definition)
                    outcome = await service.run_rebalance(portfolio_id)
                    await session.commit()
                placed += outcome.sells_placed + outcome.buys_placed
            run.portfolios = len(portfolios)
            run.detail = f"{placed} order(s) placed across {len(portfolios)} portfolio(s)."
        except Exception as exc:  # noqa: BLE001 - one job must not kill the task
            run.ok = False
            run.detail = f"{type(exc).__name__}: {exc}"
            self.last_error = run.detail
            logger.exception("Swing rebalance for %s failed", definition.key)
        finally:
            self._idle()
        return self._record(run)

    async def _run_nightly(
        self, definition: StrategyDefinition, now: datetime
    ) -> JobRun:
        """Refresh the bars, then decide, then ratchet the stops.

        The refresh comes first because everything after it reads the bars it
        writes. It is slow -- about 0.6 s per symbol, a measured 747 s for five
        hundred -- which is comfortable at 18:15 and impossible at 09:16, and
        is the whole reason `daily_bars` exists.
        """
        run = JobRun(strategy_key=definition.key, kind=RUN_NIGHTLY, at=now)
        self._mark_attempted(definition.key, RUN_NIGHTLY, now)

        # ALREADY RUN TODAY? Then there is nothing to do, and asking BEFORE the
        # refresh is the point: the refresh is the twelve-minute part. The
        # in-memory guard in `_may_attempt` covers a running process; this
        # covers a RESTART, which clears that memory and is how a five-hundred
        # symbol pull got repeated all evening.
        #
        # Keyed on when the job RAN, not on the session it decided: the session
        # date is the index's newest bar date and does not move on a day the
        # vendor has published nothing, so it cannot answer this question.
        if await self._already_ran_today(definition.key, RUN_NIGHTLY, now):
            self._mark_succeeded(definition.key, RUN_NIGHTLY, now)
            run.detail = (
                "Already run today, so the bars were not pulled again. The "
                "nightly is a once-a-day job and re-running it after a restart "
                "has nothing to add -- it re-fetches five hundred symbols and "
                "the journal then declines to re-decide the session. Use \"Run "
                "analysis now\" if you genuinely want it re-run; that forces."
            )
            logger.info("Swing nightly for %s: %s", definition.key, run.detail)
            return self._record(run)

        self._begin(f"running the nightly decision for {definition.label}")
        try:
            refreshed, refresh_ok = await self._refresh_bars(definition)
            if refreshed is not None:
                run.detail = refreshed

            portfolios = await self._portfolios(definition.key)
            if not portfolios:
                run.detail = (
                    f"{run.detail} No active portfolio runs this strategy, so "
                    f"nothing was decided."
                ).strip()
                logger.warning("%s: %s", definition.key, run.detail)
                return self._record(run)

            decided = 0
            for portfolio_id in portfolios:
                async with session_scope() as session:
                    service, runner = await self._build(session, definition)
                    record = await runner.run_nightly(
                        portfolio_id=portfolio_id,
                        holdings=await service._holdings(portfolio_id),  # noqa: SLF001
                    )
                    await session.commit()
                decided += record.decisions
            run.portfolios = len(portfolios)
            run.detail = (
                f"{run.detail} {decided} decision(s) recorded across "
                f"{len(portfolios)} portfolio(s)."
            ).strip()
            # Done for today -- but ONLY if the bars actually came down.
            #
            # A run whose refresh failed still journals a session, on the bars
            # already stored, so "it recorded something" is not the same as "it
            # did its job". Credentials fixed at 18:30 should still get fresh
            # bars at 18:45, which is what RETRY_AFTER was always for. A run
            # that DID refresh has nothing left to retry, and retrying it is
            # what pulled five hundred symbols every fifteen minutes.
            if refresh_ok:
                self._mark_succeeded(definition.key, RUN_NIGHTLY, now)
        except Exception as exc:  # noqa: BLE001
            run.ok = False
            run.detail = f"{type(exc).__name__}: {exc}"
            self.last_error = run.detail
            logger.exception("Swing nightly for %s failed", definition.key)
        finally:
            self._idle()
        return self._record(run)

    async def _already_ran_today(
        self, strategy_key: str, run_kind: str, now: datetime
    ) -> bool:
        """Has this job written a session today? Never fatal.

        A database that cannot be read must not stop the nightly running --
        that would turn a transient read error into a missed session, which is
        the more expensive failure. It returns False and the run proceeds.
        """
        from src.swing.database.db_operations.swing_session_repository import (
            SwingSessionRepository,
        )

        try:
            async with session_scope() as session:
                return await SwingSessionRepository(session).ran_on_day(
                    strategy_key, run_kind, now.date()
                )
        except Exception:  # noqa: BLE001 - see the docstring
            logger.warning(
                "Could not check whether the %s job already ran today; running "
                "it rather than risking a missed session.",
                run_kind, exc_info=True,
            )
            return False

    async def _refresh_bars(
        self, definition: StrategyDefinition
    ) -> tuple[Optional[str], bool]:
        """Top up `daily_bars`. Never fatal: stale bars decide a stale session.

        A failure here does NOT produce a wrong decision, because the session
        being decided is the regime index's own newest bar date -- so a failed
        refresh means yesterday's session, which was already recorded, and the
        idempotence check declines to decide it again. The failure is reported
        rather than hidden.
        """
        from src.daily_bars.database.db_operations.daily_bar_repository import (
            DailyBarRepository,
        )
        from src.daily_bars.services.daily_bar_service import (
            DailyBarRefreshError,
            DailyBarRefreshService,
        )
        from src.instruments.database.db_operations.instrument_repository import (
            InstrumentRepository,
        )

        self._begin(f"refreshing daily bars for {definition.label}")
        try:
            async with session_scope() as session:
                result = await DailyBarRefreshService(
                    DailyBarRepository(session), InstrumentRepository(session)
                ).refresh_strategy(definition, on_progress=self._set_progress)
                await session.commit()
            return (
                f"Bars: {len(result.refreshed)} refreshed, {len(result.failed)} "
                f"failed, {sum(one.inserted for one in result.symbols)} inserted "
                f"in {result.duration_seconds:.0f}s.",
                True,
            )
        except DailyBarRefreshError as error:
            logger.error(
                "Nightly bar refresh for %s could not run: %s. The decision below "
                "is taken on the bars already stored, which means the newest "
                "session already recorded -- nothing new is decided.",
                definition.key, error,
            )
            return f"Bars: NOT refreshed ({error}).", False
        except Exception as exc:  # noqa: BLE001
            logger.exception("Nightly bar refresh for %s failed", definition.key)
            return f"Bars: refresh failed ({type(exc).__name__}: {exc})."
        finally:
            # The nightly job sets its own activity again immediately after
            # this returns; clearing here would blink "idle" for one poll.
            self._begin(f"running the nightly decision for {definition.label}")

    # --- missed runs --------------------------------------------------------
    async def detect_missed_runs(self) -> List[MissedRun]:
        """Which sessions have no decision record, and should have one.

        The trading calendar is the regime index's own bar dates: a date NSE
        published a bar for is a date NSE traded. Comparing that against
        `decided_session_dates` is the whole detector -- no holiday list, no
        second source to go stale.

        Reported, never repaired. Re-deciding a session days later on bars that
        have since been restated would write a record of a decision nobody
        took, which is worse than a gap that says what it is.

        **A MISSED RUN IS A HOLE IN THE JOURNAL, NOT THE ABSENCE OF ONE.** The
        detector used to compare the last N trading dates against the journal
        with no notion of when the strategy began existing, so on a fresh
        installation every one of those dates was reported as missed -- ten
        sessions from before the module was written, which nothing could have
        run and nobody could act on. Once alerts started forwarding it, that
        arrived on somebody's phone every five minutes.

        The boundary is the first time this strategy wrote ANYTHING to the
        journal: a journal cannot be missing a record from before it existed.
        A strategy that has never written one has not missed anything -- it has
        never run, which is a different state and one the Live tab already
        shows.
        """
        from src.daily_bars.database.db_operations.daily_bar_repository import (
            DailyBarRepository,
        )
        from src.strategies.services.strategy_modules import hooks_for
        from src.strategies.services.strategy_registry import get_strategy_registry

        registry = get_strategy_registry()
        lookback = self._missed_lookback()
        found: List[MissedRun] = []

        async with session_scope() as session:
            bars = DailyBarRepository(session)

            for definition in registry.automated():
                reference = self._regime_reference(definition)
                if reference is None:
                    continue
                calendar = await bars.trading_dates(
                    reference.symbol, reference.exchange_segment, limit=lookback + 1
                )
                if len(calendar) < 2:
                    continue
                # The NEWEST session is excluded: at 09:00 today's nightly has
                # not run yet and is not missed, it is not due.
                expected = calendar[:-1][-lookback:]
                if not expected:
                    continue

                # WHICH RUN KINDS a strategy owes a record for is the strategy's
                # own question, and so is which journal answers it. The
                # calendar above is not -- a date NSE published a bar for is a
                # date NSE traded, whoever is asking.
                hooks = hooks_for(definition)
                if hooks is None or not hasattr(hooks, "detect_missed"):
                    continue
                try:
                    found.extend(
                        await hooks.detect_missed(definition, session, expected)
                    )
                except Exception:  # noqa: BLE001 - one module must not stop another
                    logger.exception(
                        "Could not check %s for missed runs; other strategies "
                        "are unaffected.",
                        definition.key,
                    )

        self.missed = found
        self.checked_for_missed_at = ist_now()
        for entry in found:
            logger.warning(
                "MISSED %s run(s) for %s: no %s record for %s. Nothing is "
                "re-decided -- a decision recorded days late, on bars that may "
                "since have been restated, would be a record of a decision "
                "nobody took. Trailing stops were NOT recomputed on those "
                "sessions.",
                len(entry.sessions), entry.strategy_key, entry.kind,
                ", ".join(one.isoformat() for one in entry.sessions),
            )
        if not found:
            logger.info("Swing missed-run check: no gaps in the decision journal.")
        return found

    @staticmethod
    async def _accountable_from(sessions, strategy_key: str, calendar: List[date]):
        """The earliest session this strategy can be held responsible for.

        The first session it actually COMPLETED a run for. Everything before
        that is not a gap in its journal -- it is the time before its journal
        had anything in it, and nothing was running to miss those sessions.

        Returns None when it has never completed a run, which is a different
        state entirely and one the Live tab already shows.
        """
        return await sessions.first_completed_session(strategy_key)

    def _should_check_missed(self, now: datetime) -> bool:
        if self.checked_for_missed_at is None:
            return True
        return self.checked_for_missed_at.date() < now.date()

    @staticmethod
    def _regime_reference(definition: StrategyDefinition):
        """The index whose bar dates ARE this application's trading calendar.

        Read straight off the YAML's `regime.index_role` rather than through a
        module's parameters class. Both automated modules declare one and mean
        the same thing by it, and going through `SwingParameters` would make
        the calendar lookup fail for every strategy that is not the rotation --
        silently, because the failure is caught and returns None.
        """
        role = (definition.module_section("regime") or {}).get("index_role")
        if not role:
            return None
        return definition.reference_instrument(str(role))

    # --- wiring -------------------------------------------------------------
    @staticmethod
    async def _build(session, definition: StrategyDefinition):
        """One execution service and one nightly runner, on one session."""
        from src.daily_bars.database.db_operations.daily_bar_repository import (
            DailyBarRepository,
        )
        from src.swing.database.db_operations.swing_session_repository import (
            SwingDecisionRepository,
            SwingSessionRepository,
        )
        from src.swing.database.db_operations.swing_stop_repository import (
            SwingStopRepository,
        )
        from src.swing.services.execution_service import SwingExecutionService
        from src.swing.services.journal_service import SwingJournalService
        from src.swing.services.ranking_service import RankingService
        from src.swing.services.stop_service import StopService
        from src.swing.services.swing_parameters import SwingParameters
        from src.swing.services.swing_runner import SwingRunner

        parameters = SwingParameters.from_definition(definition)
        ranking = RankingService(DailyBarRepository(session), definition, parameters)
        journal = SwingJournalService(
            SwingSessionRepository(session), SwingDecisionRepository(session)
        )
        stops = StopService(
            SwingStopRepository(session), ranking, parameters, definition.key
        )
        service = SwingExecutionService(
            session=session,
            definition=definition,
            ranking=ranking,
            journal=journal,
            stops=stops,
            parameters=parameters,
        )
        runner = SwingRunner(ranking, journal, definition, parameters, stops=stops)
        return service, runner

    @staticmethod
    async def _portfolios(strategy_key: str) -> List[int]:
        """Every ACTIVE portfolio this strategy runs in.

        Not one configured portfolio: the same strategy may run in several and
        their books must not mix, so each gets its own decision record and its
        own orders.
        """
        from src.portfolios.database.db_operations.portfolio_repository import (
            PortfolioRepository,
        )

        async with session_scope() as session:
            rows = await PortfolioRepository(session).portfolios_running(strategy_key)
            return [row.id for row in rows]

    def _record(self, run: JobRun) -> JobRun:
        self.history.append(run)
        # Twenty is enough to see a week of both jobs; this is a status dict,
        # not a second journal. The real record is in `swing_sessions`.
        self.history = self.history[-20:]
        logger.info(
            "Swing scheduler ran %s for %s: %s",
            run.kind, run.strategy_key, run.detail or ("ok" if run.ok else "failed"),
        )
        return run

    # --- reporting ----------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        from src.strategies.services.strategy_registry import get_strategy_registry

        registry = get_strategy_registry()
        schedules = []
        for definition in registry.automated():
            from src.swing.services.swing_parameters import SwingParameters

            try:
                parameters = SwingParameters.from_definition(definition)
            except Exception:  # noqa: BLE001
                continue
            from src.swing.services.schedule_settings import resolve_schedule

            trading_days = definition.market_hours.trading_days
            schedule = resolve_schedule(definition, parameters)
            next_nightly = self.next_occurrence(schedule.nightly, trading_days)
            next_rebalance = self.next_occurrence(schedule.rebalance, trading_days)
            schedules.append(
                {
                    "strategyKey": definition.key,
                    "label": definition.label,
                    "enabled": registry.is_enabled(definition.key),
                    "armed": registry.is_armed(definition.key),
                    # The times in force, which are not necessarily the YAML's.
                    "nightlyAtIst": schedule.nightly_at,
                    "rebalanceAtIst": schedule.rebalance_at,
                    "nightlyAtDefaultIst": parameters.schedule.nightly_at,
                    "rebalanceAtDefaultIst": parameters.schedule.rebalance_at,
                    # Absolute, so the page counts down locally rather than
                    # polling once a second. Null when it cannot be computed --
                    # a countdown that cannot be worked out says so rather than
                    # showing 00:00.
                    "nextNightlyAtIst": (
                        next_nightly.isoformat() if next_nightly else None
                    ),
                    "nextRebalanceAtIst": (
                        next_rebalance.isoformat() if next_rebalance else None
                    ),
                    # Weekday only. See `next_occurrence`.
                    "nextRunHolidayAware": False,
                    "cadence": parameters.schedule.rebalance_cadence,
                    "marketOpen": market_clock.is_market_open(definition),
                    "marketOpensAtIst": (
                        definition.market_hours.open.strftime("%H:%M")
                    ),
                    "marketClosesAtIst": (
                        definition.market_hours.close.strftime("%H:%M")
                    ),
                }
            )

        return {
            "enabled": self._enabled(),
            "intervalSeconds": int(self._interval_seconds()),
            "warmupMinutes": self._warmup_minutes(),
            "runs": self.runs,
            "nowIst": ist_now().isoformat(),
            # What it is doing RIGHT NOW. None means idle, which is a real
            # state: "idle" and "the scheduler is not running" are different
            # answers and the page renders them differently.
            "running": self._task is not None and not self._task.done(),
            "activity": self.activity,
            # None whenever nothing long is running, which the pages render
            # as "no bar" rather than as an empty one.
            "progress": self.progress,
            "activitySinceIst": (
                self.activity_since.isoformat() if self.activity_since else None
            ),
            "schedules": schedules,
            "recent": [run.as_dict() for run in reversed(self.history)],
            # Reported, never repaired. See `detect_missed_runs`.
            "missedRuns": [entry.as_dict() for entry in self.missed],
            "missedRunCount": sum(len(entry.sessions) for entry in self.missed),
            "checkedForMissedAtIst": (
                self.checked_for_missed_at.isoformat()
                if self.checked_for_missed_at
                else None
            ),
            "error": self.last_error,
        }


_scheduler: Optional[SwingScheduler] = None


def get_swing_scheduler() -> SwingScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = SwingScheduler()
    return _scheduler


async def shutdown_swing_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        await _scheduler.stop()
        _scheduler = None
