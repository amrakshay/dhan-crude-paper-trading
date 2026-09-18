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
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional

from src import config_utils
from src.core.time_utils import ist_now, parse_hhmm
from src.database.session import session_scope
from src.logging_config import get_logger
from src.strategies.services import market_clock
from src.strategies.services.strategy_definition import StrategyDefinition
from src.swing.database.db_models.swing_session_model import (
    RUN_NIGHTLY,
    RUN_REBALANCE,
)

logger = get_logger("swing.scheduler")

TASK_NAME = "swing-scheduler"

# How long to wait before retrying a job that FAILED. Without it a failing
# nightly would retry every thirty seconds until midnight and fill the log with
# one problem repeated two thousand times.
RETRY_AFTER = timedelta(minutes=15)


@dataclass
class JobRun:
    """What one attempt did, for the status dict and the log."""

    strategy_key: str
    kind: str
    at: datetime
    portfolios: int = 0
    ok: bool = True
    detail: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "strategyKey": self.strategy_key,
            "kind": self.kind,
            "atIst": self.at.isoformat(),
            "portfolios": self.portfolios,
            "ok": self.ok,
            "detail": self.detail,
        }


@dataclass
class MissedRun:
    strategy_key: str
    kind: str
    sessions: List[date] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "strategyKey": self.strategy_key,
            "kind": self.kind,
            "sessions": [one.isoformat() for one in self.sessions],
            "count": len(self.sessions),
        }


class SwingScheduler:
    """One task, two jobs, every automated strategy."""

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
        self._warmed: Dict[tuple, bool] = {}

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

        for definition in registry.automated():
            if not registry.is_enabled(definition.key):
                continue
            ran.extend(await self._tick_strategy(definition, now))

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

        parameters = SwingParameters.from_definition(definition)
        rebalance_at = parse_hhmm(parameters.schedule.rebalance_at)
        nightly_at = parse_hhmm(parameters.schedule.nightly_at)
        today = now.date()

        # --- warm the book, a few minutes before the open ------------------
        warm_from = self._minus_minutes(rebalance_at, self._warmup_minutes())
        if warm_from <= now.time() < rebalance_at:
            if not self._warmed.get((definition.key, today)):
                self._warmed[(definition.key, today)] = True
                await self._warm(definition)

        # --- the rebalance --------------------------------------------------
        if now.time() >= rebalance_at and self._may_attempt(
            definition.key, RUN_REBALANCE, today, now
        ):
            ran.append(await self._run_rebalance(definition, now))

        # --- the nightly ----------------------------------------------------
        if now.time() >= nightly_at and self._may_attempt(
            definition.key, RUN_NIGHTLY, today, now
        ):
            ran.append(await self._run_nightly(definition, now))

        return ran

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
        last = self._attempted.get((strategy_key, kind, today))
        if last is None:
            return True
        return (now - last) >= RETRY_AFTER

    def _mark_attempted(self, strategy_key: str, kind: str, now: datetime) -> None:
        self._attempted[(strategy_key, kind, now.date())] = now

    # --- the jobs -----------------------------------------------------------
    async def _warm(self, definition: StrategyDefinition) -> None:
        """Subscribe the names the rebalance might trade, ahead of the open."""
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

    async def _run_rebalance(
        self, definition: StrategyDefinition, now: datetime
    ) -> JobRun:
        run = JobRun(strategy_key=definition.key, kind=RUN_REBALANCE, at=now)
        self._mark_attempted(definition.key, RUN_REBALANCE, now)
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
        try:
            refreshed = await self._refresh_bars(definition)
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
        except Exception as exc:  # noqa: BLE001
            run.ok = False
            run.detail = f"{type(exc).__name__}: {exc}"
            self.last_error = run.detail
            logger.exception("Swing nightly for %s failed", definition.key)
        return self._record(run)

    async def _refresh_bars(self, definition: StrategyDefinition) -> Optional[str]:
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

        try:
            async with session_scope() as session:
                result = await DailyBarRefreshService(
                    DailyBarRepository(session), InstrumentRepository(session)
                ).refresh_strategy(definition)
                await session.commit()
            return (
                f"Bars: {len(result.refreshed)} refreshed, {len(result.failed)} "
                f"failed, {sum(one.inserted for one in result.symbols)} inserted "
                f"in {result.duration_seconds:.0f}s."
            )
        except DailyBarRefreshError as error:
            logger.error(
                "Nightly bar refresh for %s could not run: %s. The decision below "
                "is taken on the bars already stored, which means the newest "
                "session already recorded -- nothing new is decided.",
                definition.key, error,
            )
            return f"Bars: NOT refreshed ({error})."
        except Exception as exc:  # noqa: BLE001
            logger.exception("Nightly bar refresh for %s failed", definition.key)
            return f"Bars: refresh failed ({type(exc).__name__}: {exc})."

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
        """
        from src.daily_bars.database.db_operations.daily_bar_repository import (
            DailyBarRepository,
        )
        from src.strategies.services.strategy_registry import get_strategy_registry
        from src.swing.database.db_operations.swing_session_repository import (
            SwingSessionRepository,
        )

        registry = get_strategy_registry()
        lookback = self._missed_lookback()
        found: List[MissedRun] = []

        async with session_scope() as session:
            bars = DailyBarRepository(session)
            sessions = SwingSessionRepository(session)

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
                for kind in (RUN_NIGHTLY, RUN_REBALANCE):
                    decided = set(
                        await sessions.decided_session_dates(
                            definition.key, kind, expected[0]
                        )
                    )
                    gaps = [one for one in expected if one not in decided]
                    if gaps:
                        found.append(
                            MissedRun(
                                strategy_key=definition.key, kind=kind, sessions=gaps
                            )
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

    def _should_check_missed(self, now: datetime) -> bool:
        if self.checked_for_missed_at is None:
            return True
        return self.checked_for_missed_at.date() < now.date()

    @staticmethod
    def _regime_reference(definition: StrategyDefinition):
        from src.swing.services.swing_parameters import SwingParameters

        try:
            role = SwingParameters.from_definition(definition).regime.index_role
        except Exception:  # noqa: BLE001 - a module without one is not an error here
            return None
        return definition.reference_instrument(role)

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
            schedules.append(
                {
                    "strategyKey": definition.key,
                    "label": definition.label,
                    "enabled": registry.is_enabled(definition.key),
                    "armed": registry.is_armed(definition.key),
                    "nightlyAtIst": parameters.schedule.nightly_at,
                    "rebalanceAtIst": parameters.schedule.rebalance_at,
                    "cadence": parameters.schedule.rebalance_cadence,
                    "marketOpen": market_clock.is_market_open(definition),
                }
            )

        return {
            "enabled": self._enabled(),
            "intervalSeconds": int(self._interval_seconds()),
            "warmupMinutes": self._warmup_minutes(),
            "runs": self.runs,
            "nowIst": ist_now().isoformat(),
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
