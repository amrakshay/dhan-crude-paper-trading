"""Is THIS strategy healthy, and what has it been doing?

The per-strategy sibling of `src/health/`, and the counterpart of
`swing_health_service.py`. The split is the same one: that package answers
"what is this PROCESS doing" and is genuinely process-wide; this answers "is
this strategy sound". Putting it in `src/health/` would make that package
import `src/btst/` for its own payload.

**An assembler, not a measurement.** Every figure is an existing `status()`
dict, a repository read or an `asyncio.all_tasks()` walk, and nothing is
measured on the tick path.

**Two panels exist here that the rotation's tab has no need of**, and they are
the two the handoff singled out:

  * **Did the exit run?** It is the FIRST verdict, before the clock, before the
    bars, before anything. For every other strategy in this application a
    missed run costs a session's decision and is reported; for this one it
    costs the trade thesis -- the same positions held to the next close instead
    of the next open measure a 49.0% win rate against 71.4%.
  * **Is the universe subscribed right now?** This is the only strategy whose
    decision needs live prices for ~289 instruments at one moment, and it
    subscribes them for a window rather than all session. "The window is open
    and 289 are on the feed", "the window is shut and this is fine" and "the
    window is open and nothing is subscribed" are three different answers and
    only the last is a problem.

**Admin-only**, like the rotation's: it serves live machinery state and
WARNING+ log records, which is the exposure `/api/healthcheck/*` is gated for.
"""
from datetime import timedelta
from typing import Any, Dict, List, Optional

from src.btst.database.db_models.btst_session_model import (
    EXIT_DONE,
    EXIT_FAILED,
    EXIT_LATE,
    EXIT_PENDING,
    RUN_EXIT,
    RUN_SCAN,
)
from src.btst.services.btst_parameters import BtstParameters
from src.btst.services.btst_policy import describe_policies, resolve_policy
from src.btst.services.btst_schedule import describe_settings, resolve_schedule
from src.btst.services.execution_service import max_staleness_days, staleness_reason
from src.core.time_utils import ist_now, ist_today, to_ist, utc_now
from src.logging_config import get_logger
from src.strategies.services import market_clock

logger = get_logger("btst.health")

# Which loggers count as this strategy's. The daily-bar prefixes are included
# because a bar refresh that failed is this strategy's problem too -- five of
# its seven filters read those bars -- even though the job belongs to another
# package. The rotation's tab includes them for the same reason.
BTST_LOGGER_PREFIXES = ("dcpt.btst", "dcpt.daily_bars")

# Three things this tab cannot know, stated on the payload rather than implied
# away -- the same three the rotation's tab states, because they are properties
# of the tables and the process rather than of either strategy.
NOTE_NO_AUDIT = (
    "There is no audit history. The toggle and setting tables hold the current "
    "value with its last author, and that is all."
)
NOTE_RUN_LIST_IS_PROCESS_SCOPED = (
    "The scheduler's run list is in memory and is empty after a restart, which "
    "is why 'what it last did' is also read from the journal -- which is not."
)
NOTE_SHARED_CLOCK = (
    "The clock is shared with every other automated strategy. A task row here "
    "reports the one `swing-scheduler` task, not a BTST-specific one; what it "
    "runs for THIS strategy is the run list and the journal below."
)


class BtstHealthService:
    """Assembles `GET /api/btst/health`. Reads only."""

    def __init__(self, session, definition, parameters: Optional[BtstParameters] = None):
        self.session = session
        self.definition = definition
        self.parameters = parameters or BtstParameters.from_definition(definition)

    @classmethod
    def for_strategy(cls, session, strategy_key: str) -> "BtstHealthService":
        from src.btst.services.btst_service import BtstService

        service = BtstService.for_strategy(session, strategy_key)
        return cls(session, service.definition, service.parameters)

    async def payload(self, portfolio_id: Optional[int] = None) -> Dict[str, Any]:
        return {
            "strategyKey": self.definition.key,
            "label": self.definition.label,
            # FIRST, deliberately. For this strategy the exit is the edge.
            "exit": await self._exit_verdict(portfolio_id),
            "working": self._working_now(),
            "feed": self._feed(),
            "schedule": self._schedule(),
            "data": await self._data(),
            "money": await self._money(portfolio_id),
            "rules": self._rules(),
            "problems": self._problems(),
            "notes": [
                NOTE_NO_AUDIT,
                NOTE_RUN_LIST_IS_PROCESS_SCOPED,
                NOTE_SHARED_CLOCK,
            ],
            "systemHealthPage": "/health",
        }

    # --- "Did the exit run?" ------------------------------------------------
    async def _exit_verdict(self, portfolio_id: Optional[int]) -> Dict[str, Any]:
        """The panel this whole tab exists for.

        Three states, because two would lie: nothing is held (fine, and the
        ordinary case); something is held and its exit is not due yet (fine);
        something is held and its exit WAS due (a problem, and the one thing on
        this page that should be red).
        """
        from src.btst.database.db_operations.btst_repository import (
            BtstHoldingRepository,
            BtstSessionRepository,
        )

        holdings_repository = BtstHoldingRepository(self.session)
        sessions = BtstSessionRepository(self.session)
        schedule = resolve_schedule(self.definition, self.parameters)
        now = ist_now()

        open_holdings = await holdings_repository.open_for(
            self.definition.key, portfolio_id
        )
        counts = await holdings_repository.exit_counts(self.definition.key)
        last_exit = await sessions.latest(self.definition.key, RUN_EXIT)

        overdue = []
        # Every open position with WHEN ITS EXIT IS DUE, which is the health
        # question the Live tab's book does not answer: that one says what is
        # held and why it was bought, this one says whether each is still
        # inside the window it is supposed to leave in. The due moment is
        # computed here once and sent absolute, so the tab can say "in 40
        # minutes" without a second opinion about the calendar.
        rows = []
        for holding in open_holdings:
            due_at = schedule.exit_alarm_at(
                holding.entry_session_date + timedelta(days=1)
            ).replace(tzinfo=now.tzinfo)
            late = now > due_at
            if late:
                overdue.append(holding)
            rows.append(
                {
                    "symbol": holding.symbol,
                    "quantity": holding.quantity,
                    "entrySessionDate": holding.entry_session_date.isoformat(),
                    "exitStatus": holding.exit_status,
                    "exitReason": holding.exit_reason,
                    "dueAtIst": due_at.isoformat(),
                    "overdue": late,
                    "minutesLate": (
                        int((now - due_at).total_seconds() // 60) if late else None
                    ),
                }
            )

        if overdue:
            tone = "bad"
            verdict = (
                f"{len(overdue)} position(s) are still held past their exit. "
                f"The overnight gap is this strategy's entire edge and holding "
                f"through a session gives it back -- the same positions held to "
                f"the next close measure a 49.0% win rate against 71.4%."
            )
        elif open_holdings:
            tone = "ok"
            verdict = (
                f"{len(open_holdings)} position(s) held overnight, due out at "
                f"{schedule.exit_at} IST. Nothing is late."
            )
        else:
            tone = "ok"
            verdict = (
                "Nothing is held. At about one signal every two sessions this "
                "is the ordinary state, not an idle strategy."
            )

        return {
            "tone": tone,
            "verdict": verdict,
            "openCount": len(open_holdings),
            "overdueCount": len(overdue),
            "holdings": rows,
            "overdue": [
                {
                    "symbol": one.symbol,
                    "quantity": one.quantity,
                    "entrySessionDate": one.entry_session_date.isoformat(),
                    "exitStatus": one.exit_status,
                    "exitReason": one.exit_reason,
                }
                for one in overdue
            ],
            "exitAtIst": schedule.exit_at,
            "alarmAfterMinutes": schedule.exit_alarm_after_minutes,
            "counts": counts,
            # Counted across the whole history, because ONE late exit is worth
            # knowing about and a tab showing only the newest row would lose it.
            "lateExitsEver": counts.get(EXIT_LATE, 0),
            "failedEver": counts.get(EXIT_FAILED, 0),
            "lastExitRun": (
                {
                    "sessionDate": (
                        last_exit.session_date.isoformat()
                        if last_exit.session_date
                        else None
                    ),
                    "status": last_exit.status,
                    "message": last_exit.message,
                    "atIst": (
                        to_ist(last_exit.started_at).isoformat()
                        if last_exit.started_at
                        else None
                    ),
                }
                if last_exit is not None
                else None
            ),
        }

    # --- "Is it working right now?" -----------------------------------------
    def _working_now(self) -> Dict[str, Any]:
        from src.health.services import task_inspector
        from src.strategies.services.strategy_registry import get_strategy_registry
        from src.swing.services.swing_health_service import task_description

        registry = get_strategy_registry()
        enabled = registry.is_enabled(self.definition.key)

        flags = task_inspector.feed_flags()
        table = task_inspector.inspect(
            is_synthetic=flags["is_synthetic"], feed_running=flags["feed_running"]
        )
        by_name = {row["name"]: row for row in table["tasks"]}

        row = by_name.get("swing-scheduler")
        jobs = [
            {
                "name": "swing-scheduler",
                "role": "the clock (shared with every automated strategy)",
                "state": row["state"] if row else "not started",
                # Narrowed to THIS strategy: the task is process-wide, and
                # painting this tab red for a clock BTST is not using would be
                # the same "off is not broken" mistake the rotation's tab
                # avoids.
                "expected": bool(row and row["expected"] and enabled),
                "instances": row["instances"] if row else 0,
                "description": row["description"] if row else task_description(
                    "swing-scheduler"
                ),
            }
        ]

        return {
            "enabled": enabled,
            "automated": self.definition.automation.automated,
            "armed": registry.is_armed(self.definition.key),
            "armedByDefault": self.definition.automation.armed_by_default,
            "market": {
                "open": market_clock.is_market_open(self.definition),
                "tradingDay": market_clock.is_trading_day(self.definition),
                "opensAtIst": self.definition.market_hours.open.strftime("%H:%M"),
                "closesAtIst": self.definition.market_hours.close.strftime("%H:%M"),
            },
            "jobs": {
                "rows": jobs,
                "healthy": all(
                    row["state"] == "running" or not row["expected"] for row in jobs
                ),
            },
            # NO STOP MONITOR ROW, and its absence is the point rather than an
            # omission. B15 is "none": every position is opened near the close
            # and sold at the next open, so the only risk window is one in
            # which no order can execute at any price. A row saying "not
            # running" would invite somebody to fix it.
            "stopWatcher": {
                "applicable": False,
                "note": (
                    "This strategy has no stop and none is possible, so there "
                    "is no stop watcher to report. Its only risk window is "
                    "overnight, when no order can execute at any price."
                ),
            },
        }

    # --- "Is the universe subscribed?" --------------------------------------
    def _feed(self) -> Dict[str, Any]:
        """Three states where two would lie.

        A subscription that was BUILT and matched nothing, one that was never
        built, and one holding instruments are three different answers -- and
        here there is a fourth that matters more: the WINDOW. Outside it,
        holding no instruments is exactly right; inside it, holding none means
        no prices are arriving for the scan that is about to happen.
        """
        from src.market.services.feed_manager import get_feed_manager

        manager = get_feed_manager()
        state = manager.strategy_state.get(self.definition.key)
        subscription = self.definition.subscription
        now = ist_now()
        window_open = subscription.window_is_open(now.time())
        count = state.instrument_count if state is not None else None
        universe_size = (
            len(self.definition.universe) if self.definition.universe else 0
        )

        if state is None:
            tone, verdict = "off", (
                "No subscription has been built for this strategy at all, which "
                "is what 'switched off' looks like."
            )
        elif window_open and not count:
            tone, verdict = "bad", (
                "The universe window is OPEN and nothing is subscribed. The "
                "scan reads each candidate's running high, low and cumulative "
                "volume from the live book, so with no subscription it will "
                "qualify nothing and say so."
            )
        elif window_open:
            tone, verdict = "ok", (
                f"The universe window is open and {count} instrument(s) are on "
                f"the shared feed."
            )
        else:
            tone, verdict = "ok", (
                f"The universe window is shut, so only what is held is "
                f"subscribed ({count or 0}). It opens at "
                f"{subscription.window_opens_at.strftime('%H:%M') if subscription.window_opens_at else '?'} "
                f"IST."
            )

        return {
            "tone": tone,
            "verdict": verdict,
            "subscribed": state is not None,
            "instrumentCount": count,
            "universeSize": universe_size,
            "windowOpen": window_open,
            "windowOpensAtIst": (
                subscription.window_opens_at.strftime("%H:%M")
                if subscription.window_opens_at
                else None
            ),
            "windowClosesAtIst": (
                subscription.window_closes_at.strftime("%H:%M")
                if subscription.window_closes_at
                else None
            ),
            "lastResyncMs": manager.last_resync_ms,
            "note": (
                "This strategy is the only one that subscribes its whole "
                "universe, and it does so for a window rather than all "
                "session. There is exactly one upstream connection for the "
                "process; its health is on the system health page."
            ),
        }

    # --- what it will do next, and what it last did -------------------------
    def _schedule(self) -> Dict[str, Any]:
        from src.swing.services.scheduler import get_swing_scheduler

        status = get_swing_scheduler().status()
        schedule = resolve_schedule(self.definition, self.parameters)
        mine = [
            run
            for run in (status.get("recent") or [])
            if run.get("strategyKey") == self.definition.key
        ]
        missed = [
            entry
            for entry in (status.get("missedRuns") or [])
            if entry.get("strategyKey") == self.definition.key
        ]
        return {
            "scanAtIst": schedule.scan_at,
            "exitAtIst": schedule.exit_at,
            "activity": status.get("activity"),
            "recent": mine,
            "missedRuns": missed,
            "note": NOTE_RUN_LIST_IS_PROCESS_SCOPED,
        }

    # --- are the bars fresh enough to scan on? ------------------------------
    async def _data(self) -> Dict[str, Any]:
        from src.daily_bars.database.db_operations.daily_bar_repository import (
            DailyBarRepository,
        )

        bars = DailyBarRepository(self.session)
        universe = (
            sorted(self.definition.universe.symbols)
            if self.definition.universe
            else []
        )
        latest = await bars.latest_dates(self.definition.exchange_segment, universe)
        newest = max(latest.values()) if latest else None
        # The SAME sentence the scan would refuse with, lifted from the
        # execution service rather than restated -- a second description of the
        # rule would drift from the one that actually refuses.
        reason = staleness_reason(newest)

        return {
            "tone": "bad" if reason else "ok",
            "verdict": reason or (
                f"The newest stored session is {newest.isoformat()}, inside the "
                f"{max_staleness_days()}-day limit."
                if newest
                else "No bars."
            ),
            "newestSession": newest.isoformat() if newest else None,
            "symbolsWithBars": len(latest),
            "universeSize": len(universe),
            "stalenessLimitDays": max_staleness_days(),
            "note": (
                "Five of this strategy's seven filters come from these bars -- "
                "the 55-day high, both 20-day averages, the 200-day SMA and the "
                "six-month momentum. They are shared with the swing rotation, "
                "whose nightly job refreshes them."
            ),
        }

    # --- can it afford to trade tomorrow? -----------------------------------
    async def _money(self, portfolio_id: Optional[int]) -> Dict[str, Any]:
        """The four figures, and what this strategy would deploy against them.

        A health question rather than a decoration: B12 sizes each position at
        total equity divided by the slot count, and the funds check happens
        again at the fill, so a book whose equity cannot be computed buys
        nothing and a book without the cash buys less than it decided to. Both
        are worth knowing at 15:19 rather than at 15:21.

        `BalanceService` is the only place cash, blocked margin, available and
        equity are computed (root `CLAUDE.md` section 3a); this reads it and
        adds nothing of its own.
        """
        if portfolio_id is None:
            return {
                "tone": "off",
                "verdict": (
                    "No portfolio is in scope, so there is no book to report "
                    "on. Pick one in the header."
                ),
                "balance": None,
                "slots": self.parameters.slots,
                "positionSizeDivisor": self.parameters.position_size_divisor,
                "note": None,
            }

        from src.portfolios.services.balance_service import BalanceService

        balance = (await BalanceService(self.session).balance_for(portfolio_id)).as_dict()
        withheld = balance.get("equity") is None
        return {
            # Withheld equity is not a fault of this strategy's -- it means a
            # position somewhere in the book has no live mark -- but it DOES
            # stop this strategy sizing an entry, so it is reported as
            # something to look at rather than as normal.
            "tone": "bad" if withheld else "ok",
            "verdict": (
                (
                    "Equity cannot be computed: "
                    + (
                        ", ".join(balance.get("unmarkedStrategies") or [])
                        or "an open position"
                    )
                    + " has no live mark. B12 sizes every entry as total equity "
                    "divided by the slot count, so the next scan would buy "
                    "nothing and say why."
                )
                if withheld
                else (
                    f"Equity {balance['equity']}, of which {balance['available']} "
                    f"is available. B12 would size each of "
                    f"{self.parameters.slots} slot(s) at equity divided by "
                    f"{self.parameters.position_size_divisor}."
                )
            ),
            "balance": balance,
            "slots": self.parameters.slots,
            "positionSizeDivisor": self.parameters.position_size_divisor,
            "note": (
                "Blocked margin is a configured approximation, never what a "
                "broker would hold. Funds are checked at placement AND again "
                "at the fill, so an entry that became unaffordable is refused "
                "rather than part-filled to fit."
            ),
        }

    # --- which rules are in force, and who last moved each -------------------
    def _rules(self) -> Dict[str, Any]:
        policies = describe_policies(self.definition, self.parameters)
        settings = describe_settings(self.definition, self.parameters)
        return {
            "policies": policies["policies"],
            "settings": settings["settings"],
            "note": NOTE_NO_AUDIT,
        }

    # --- recent WARNING+ records for this strategy --------------------------
    def _problems(self, limit: int = 100) -> Dict[str, Any]:
        """This strategy's own WARNING+ records, newest first.

        Filtered by LOGGER NAME rather than by reading the text: every record
        carries the logger it came from, and `get_logger("btst.execution")` is
        `dcpt.btst.execution`. Records are already formatted through
        `RedactingFormatter` -- the buffer stores them that way precisely
        because an HTTP endpoint reads it.
        """
        from src import log_buffer

        if log_buffer.get_handler() is None:
            return {
                "records": [],
                "available": False,
                "note": (
                    "The in-memory log buffer is not installed in this "
                    "process, so recent problems cannot be shown at all. "
                    "`logs/app.log` is the durable record."
                ),
                "loggerPrefixes": list(BTST_LOGGER_PREFIXES),
                "bufferNote": None,
                "totalSeen": None,
            }

        handler = log_buffer.get_handler()
        mine = [
            record
            for record in handler.recent()
            if str(record.get("logger", "")).startswith(BTST_LOGGER_PREFIXES)
        ]
        counts = handler.counts()
        return {
            "records": mine[:limit],
            "available": True,
            "loggerPrefixes": list(BTST_LOGGER_PREFIXES),
            "totalSeen": sum(counts.values()) if counts else 0,
            "countsByLevel": counts,
            "note": (
                "Warnings and errors from this strategy's own components and "
                "from the daily-bar jobs that feed it."
            ),
            "bufferNote": (
                "IN MEMORY and process-scoped: this list is empty after a "
                "restart, which is not the same as nothing having gone wrong. "
                "`logs/app.log` is the durable record."
            ),
        }
