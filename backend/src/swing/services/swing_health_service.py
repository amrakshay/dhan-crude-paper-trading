"""Is ONE automated strategy healthy, and what has it actually been doing?

`src/health/` answers "what is this process doing": one feed connection, one
book, one set of background tasks, one CPU. That is the right shape for a
process and the wrong shape for the question an operator has about a strategy
that trades unattended, which is this one:

    Is it on, may it trade, are its jobs alive, are its prices fresh, what does
    it hold, what is it watching, which rules are in force and who changed them?

Today that answer is scattered across the Live tab, the system health page, the
log and nowhere at all. This module gathers it for one strategy. It lives in
`src/swing/` rather than in `src/health/` because per-strategy health is a
property of the strategy: putting it there would make the health package import
this one for its own payload. `task_inspector` goes the other way and is
imported here, which is fine -- it is a read-only helper.

Four rules this module is built around:

* **Nothing here is measured on the tick path.** Every figure is existing
  component state, a database read, or an `asyncio.all_tasks()` walk. The same
  rule the system health page follows, for the same reason: a page that
  reports on a system it distorted is reporting on a different system.
* **Undefined is not zero.** A count that could not be taken is `None`, and the
  payload carries the reason beside it. A strategy that has never traded reads
  as "nothing has happened yet", not as "everything is broken".
* **It must work with the strategy OFF.** Nothing here computes a ranking,
  touches the feed's subscription, or needs a portfolio. Every block degrades
  to an absence with an explanation.
* **It says what it does NOT know.** There is no audit history, the scheduler's
  activity log is process-scoped, and the stop watcher's counters are kept once
  per process across every strategy. All three are stated on the payload in
  plain words rather than implied away.

No secret reaches this payload: nothing here reads the config tree, the Dhan
credentials or the database URL, and the log records come from the buffer that
has already been through `RedactingFormatter`. `tests/test_no_secrets_in_logs.py`
asserts it against the endpoint.
"""
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence

from src.logging_config import get_logger
from src.strategies.services import market_clock
from src.strategies.services.strategy_definition import StrategyDefinition
from src.swing.database.db_models.swing_session_model import (
    RUN_NIGHTLY,
    RUN_REBALANCE,
)
from src.swing.services.execution_service import (
    max_staleness_days,
    staleness_reason,
)
from src.swing.services.gate_policy import describe_policies
from src.swing.services.schedule_settings import describe_settings
from src.swing.services.swing_parameters import SwingParameters

logger = get_logger("swing.health")

# The loggers whose records belong to this strategy. `daily_bars` is in the set
# deliberately: that package exists only to feed this rotation, and a refresh
# that failed overnight is precisely the silent breakage this tab is for. The
# prefixes are reported on the payload so the page can say which loggers it is
# showing rather than implying it shows everything.
SWING_LOGGER_PREFIXES = ("dcpt.swing", "dcpt.daily_bars")

# How much of the index's own calendar to read when working out how many
# SESSIONS a lagging symbol is behind. A year of sessions: beyond that the
# answer is "it has effectively stopped updating", which the payload says in
# words rather than by counting to 700.
CALENDAR_WINDOW_SESSIONS = 260

# How many lagging symbols and unresolved symbols to name. The counts are
# exact; the lists are a sample, and the payload says which is which.
SAMPLE_LIMIT = 25

NO_AUDIT_HISTORY_NOTE = (
    "This is the CURRENT value of each switch with the last person to set it "
    "-- not a history. There is no audit table, so \"the gate was relaxed at "
    "15:19 and re-enforced at 16:40\" cannot be answered here. A switch nobody "
    "has ever touched shows no author at all, which is a different fact from "
    "one set back to its shipped value."
)

PROCESS_SCOPED_RUNS_NOTE = (
    "What this process has done since it started. It lives in memory, not in "
    "the database, so it is empty after a restart -- which is not the same as "
    "nothing having happened. The decision journal on the Live tab is the "
    "durable record."
)

NO_REFRESH_HISTORY_NOTE = (
    "The outcome of each nightly bar refresh -- how many symbols were "
    "refreshed, failed or could not be resolved -- is kept only on the run "
    "list above, in this process's memory. It is not persisted, so a refresh "
    "that failed before the last restart left no trace here. The bar coverage "
    "below IS read from the database and is unaffected by a restart; "
    "`logs/app.log` is the durable record of the runs themselves."
)

PROCESS_WIDE_COUNTERS_NOTE = (
    "These four counters are kept ONCE PER PROCESS, across every automated "
    "strategy, not per strategy. With one automated module running they are "
    "this strategy's figures; with two they would be the sum and this line is "
    "what stops that being read as a per-strategy number."
)


def _iso(value: Optional[datetime]) -> Optional[str]:
    """A stored naive-UTC timestamp, as an IST string WITH its offset.

    Every timestamp in this database is naive UTC, and converting to IST
    happens at the edges (`src/core/time_utils.py`). This is an edge. Emitting
    the naive value would send `2026-09-18T11:46:38`, which a browser parses as
    ELEVEN FORTY-SIX LOCAL -- so the page would report a switch as having been
    moved five and a half hours before it was, beside an IST clock reading the
    correct time. Caught on 2026-09-18 by looking at the rendered page.
    """
    if value is None:
        return None
    from src.core.time_utils import to_ist

    return to_ist(value).isoformat()


def _date_iso(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if value is not None else None


class SwingHealthService:
    """The Health tab's payload. Reads only; writes and decides nothing."""

    def __init__(
        self,
        session,
        definition: StrategyDefinition,
        parameters: Optional[SwingParameters] = None,
    ):
        self.session = session
        self.definition = definition
        self.parameters = parameters or SwingParameters.from_definition(definition)

    # --- collaborators ------------------------------------------------------
    def _bars(self):
        from src.daily_bars.database.db_operations.daily_bar_repository import (
            DailyBarRepository,
        )

        return DailyBarRepository(self.session)

    def _instruments(self):
        from src.instruments.database.db_operations.instrument_repository import (
            InstrumentRepository,
        )

        return InstrumentRepository(self.session)

    def _stops(self):
        from src.swing.database.db_operations.swing_stop_repository import (
            SwingStopRepository,
        )

        return SwingStopRepository(self.session)

    def _sessions(self):
        from src.swing.database.db_operations.swing_session_repository import (
            SwingSessionRepository,
        )

        return SwingSessionRepository(self.session)

    def _equity_segment(self) -> str:
        for instrument_set in self.definition.instrument_sets:
            if instrument_set.from_universe:
                return instrument_set.exchange_segment
        return self.definition.exchange_segment

    # --- the payload --------------------------------------------------------
    async def health(self, portfolio_id: Optional[int] = None) -> Dict[str, Any]:
        from src.core.time_utils import ist_now

        return {
            "strategyKey": self.definition.key,
            "label": self.definition.label,
            "nowIst": ist_now().isoformat(),
            "working": self._working_now(),
            "schedule": await self._schedule(),
            "data": await self._data(),
            "holdings": await self._holdings(portfolio_id),
            "rules": await self._rules(),
            "problems": self._problems(),
            # Everything process-wide stays on the system health page rather
            # than being copied here. CPU, memory, uptime, the upstream socket,
            # the browser sockets, the credentials and the schema revision are
            # not facts about this strategy.
            "systemHealthPage": "/health",
        }

    # --- "Is it working right now?" -----------------------------------------
    def _working_now(self) -> Dict[str, Any]:
        from src.health.services import task_inspector
        from src.market.services.feed_manager import get_feed_manager
        from src.strategies.services.strategy_registry import get_strategy_registry

        registry = get_strategy_registry()
        enabled = registry.is_enabled(self.definition.key)
        automated = self.definition.automation.automated

        flags = task_inspector.feed_flags()
        table = task_inspector.inspect(
            is_synthetic=flags["is_synthetic"], feed_running=flags["feed_running"]
        )
        by_name = {row["name"]: row for row in table["tasks"]}
        jobs = [
            self._job_row(by_name, "swing-scheduler", "the clock", enabled),
            self._job_row(by_name, "swing-stop-monitor", "the stop watcher", enabled),
        ]

        manager = get_feed_manager()
        state = manager.strategy_state.get(self.definition.key)

        return {
            "enabled": enabled,
            "automated": automated,
            # Two switches, reported as two. `armed` is armed AND enabled, so
            # it is reported beside the arming row rather than instead of it.
            "armed": registry.is_armed(self.definition.key),
            "armedByDefault": self.definition.automation.armed_by_default,
            "market": {
                "open": market_clock.is_market_open(self.definition),
                "opensAtIst": self.definition.market_hours.open.strftime("%H:%M"),
                "closesAtIst": self.definition.market_hours.close.strftime("%H:%M"),
                "tradingDay": market_clock.is_trading_day(self.definition),
                "timezone": self.definition.market_hours.timezone,
            },
            "jobs": {
                "rows": jobs,
                # A strategy that is on and armed with a dead scheduler is the
                # failure this whole tab exists to make visible.
                "healthy": all(
                    row["state"] == "running" or not row["expected"] for row in jobs
                ),
            },
            "feed": {
                # Absent from the feed's per-strategy map is NOT zero
                # instruments: it means no subscription has been built for this
                # strategy at all, which is what "off" looks like.
                "subscribed": state is not None,
                "instrumentCount": state.instrument_count if state is not None else None,
                "lastResyncMs": manager.last_resync_ms,
                "note": (
                    "Instruments this strategy holds on the shared upstream "
                    "feed. There is exactly one upstream connection for the "
                    "whole process; its health is on the system health page."
                ),
            },
        }

    @staticmethod
    def _job_row(
        by_name: Dict[str, Dict[str, Any]],
        name: str,
        role: str,
        strategy_enabled: bool = True,
    ) -> Dict[str, Any]:
        """One background task, judged against whether it SHOULD be running.

        A task absent from `asyncio.all_tasks()` while expected is what "it
        died" looks like -- there is no other signal, because a task that
        raised and finished simply stops appearing.

        **`expected` is narrowed to THIS strategy**, and that became necessary
        on 2026-09-19 when a second automated module arrived. These tasks are
        process-wide: `task_inspector` expects the scheduler whenever ANY
        automated strategy is enabled, which is right for the system health
        page. It is wrong for a strategy's own tab. With BTST on and the
        rotation off, the shared scheduler is genuinely expected -- and saying
        so HERE would paint the rotation's tab red for a task it is not using,
        which is exactly the "off is not broken" rule this tab already follows
        for everything else. Red stays reserved for a job that should be
        running FOR THIS STRATEGY and is not.
        """
        row = by_name.get(name)
        if row is None:
            # Neither running nor expected: nothing automated is on, or the
            # task's own config switch is off. Not a failure, and not a running
            # task.
            return {
                "name": name,
                "role": role,
                "state": "not started",
                "expected": False,
                "instances": 0,
                "description": task_description(name),
            }
        return {
            "name": name,
            "role": role,
            "state": row["state"],
            "expected": bool(row["expected"] and strategy_enabled),
            "instances": row["instances"],
            "description": row["description"],
        }

    # --- "What it will do next, and what it last did" -----------------------
    async def _schedule(self) -> Dict[str, Any]:
        from src.swing.services.scheduler import get_swing_scheduler

        status = get_swing_scheduler().status()
        mine = [
            one
            for one in (status.get("schedules") or [])
            if one.get("strategyKey") == self.definition.key
        ]
        schedule = mine[0] if mine else None

        recent = [
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
            # "not running", "idle" and "busy" are three different states and
            # the page renders them differently.
            "running": status.get("running"),
            "enabled": status.get("enabled"),
            "activity": status.get("activity"),
            "activitySinceIst": status.get("activitySinceIst"),
            # How far through a long job it is. None when nothing long is
            # running -- the nightly bar refresh is twelve minutes at Dhan's
            # rate limits, which is long enough that "working" and "hung" look
            # identical without this.
            "progress": status.get("progress"),
            "intervalSeconds": status.get("intervalSeconds"),
            "warmupMinutes": status.get("warmupMinutes"),
            "clockChecks": status.get("runs"),
            "error": status.get("error"),
            "schedule": schedule,
            # From the in-memory run list: what THIS PROCESS has done.
            "lastAnalysis": _first(recent, RUN_NIGHTLY),
            "lastOrderPlacement": _first(recent, RUN_REBALANCE),
            "recent": recent,
            "recentNote": PROCESS_SCOPED_RUNS_NOTE,
            # From the JOURNAL: what the strategy has ever done. The two are
            # reported separately and deliberately. After a restart the list
            # above is empty while these are not, and a page that showed only
            # the first would say "nothing has run" about a strategy that ran
            # last night.
            "journal": await self._journal(),
            # Reported, never repaired. A session with no record is detected
            # against the regime index's own bar dates and is NOT silently
            # re-decided days later on bars that may since have been restated.
            "missedRuns": missed,
            "missedRunCount": sum(entry.get("count", 0) for entry in missed),
            "checkedForMissedAtIst": status.get("checkedForMissedAtIst"),
            "closingAuction": self._closing_auction(),
        }

    async def _journal(self) -> Dict[str, Any]:
        """The durable answer to "what did it last do", from `swing_sessions`.

        Append-only and never edited, so this is the record the in-memory run
        list above is only a recent window onto. Idempotence comes from here
        too -- a restart at 18:20 does not re-decide 18:15 because the session
        is already journalled.
        """
        sessions = self._sessions()
        return {
            "recordedSessions": await self.sessions_count(),
            "latestAnalysis": _session_row(
                await sessions.latest(self.definition.key, RUN_NIGHTLY)
            ),
            "latestOrderPlacement": _session_row(
                await sessions.latest(self.definition.key, RUN_REBALANCE)
            ),
            "note": (
                "The append-only decision journal, which survives a restart. "
                "The run list above does not."
            ),
        }

    async def sessions_count(self) -> int:
        return await self._sessions().count_for(self.definition.key)

    def _closing_auction(self) -> Optional[Dict[str, Any]]:
        auction = self.definition.market_hours.closing_auction
        if auction is None:
            return None
        return {
            "appliesTo": auction.applies_to,
            "continuousCloseIst": auction.continuous_close.strftime("%H:%M"),
            "auctionCloseIst": auction.auction_close.strftime("%H:%M"),
            "note": (
                f"For F&O-eligible names continuous cash trading ends at "
                f"{auction.continuous_close.strftime('%H:%M')} IST. A trailing "
                f"stop that fires after that is recorded as triggered and its "
                f"exit waits for the next session's open -- this simulator has "
                f"no model of a call auction."
            ),
        }

    # --- "The data it decides on" -------------------------------------------
    async def _data(self) -> Dict[str, Any]:
        from src.core.time_utils import ist_today

        segment = self._equity_segment()
        bars = self._bars()
        coverage = await bars.coverage(segment)

        newest = _parse_date(coverage.get("lastDate"))
        # The rebalance's OWN rule and its OWN sentence, not a second
        # description of either. If the limit moves or the wording changes,
        # this moves with it.
        limit_days = max_staleness_days()
        days_behind = (ist_today() - newest).days if newest is not None else None
        refusal = staleness_reason(newest) if newest is not None else None

        index = self.definition.reference_instrument(self.parameters.regime.index_role)
        calendar: List[date] = []
        if index is not None:
            calendar = await bars.trading_dates(
                index.symbol, index.exchange_segment, limit=CALENDAR_WINDOW_SESSIONS
            )

        universe = self.definition.universe
        symbols = sorted(universe.symbols) if universe is not None else []
        latest_by_symbol = await bars.latest_dates(segment, symbols)

        lagging = self._lagging(symbols, latest_by_symbol, newest, calendar)
        resolution = await self._universe_resolution(
            symbols, segment, with_bars=len(latest_by_symbol)
        )

        instruments = self._instruments()
        return {
            "segment": segment,
            "bars": {
                "symbols": coverage.get("symbols"),
                "rows": coverage.get("bars"),
                "oldestSession": coverage.get("firstDate"),
                "newestSession": coverage.get("lastDate"),
            },
            "staleness": {
                "newestSession": coverage.get("lastDate"),
                # Calendar days, matching what the rebalance itself measures.
                # Five days covers a Thursday close before a long weekend.
                "daysBehind": days_behind,
                "limitDays": limit_days,
                "wouldRefuse": None if newest is None else refusal is not None,
                "refusalReason": refusal,
                "calendarSessionsStored": len(calendar) if calendar else None,
                "calendarWindow": CALENDAR_WINDOW_SESSIONS,
                "calendarSymbol": index.label if index is not None else None,
                "note": (
                    "Measured in calendar days, which is what the rebalance "
                    "itself measures against. How many SESSIONS have passed "
                    "since the newest stored bar is not knowable here: this "
                    "application's trading calendar IS the index's own stored "
                    "bar dates, so a session it has no bar for is a session it "
                    "cannot see."
                ),
            },
            "laggingSymbols": lagging,
            "universe": resolution,
            "instrumentMaster": {
                "activeRows": await instruments.count_active(),
                "activeRowsInSegment": await instruments.count_active(segment),
                "lastRefreshedAt": _iso(await instruments.last_refreshed_at()),
            },
            "refreshHistoryNote": NO_REFRESH_HISTORY_NOTE,
        }

    @staticmethod
    def _lagging(
        symbols: Sequence[str],
        latest_by_symbol: Dict[str, date],
        newest: Optional[date],
        calendar: Sequence[date],
    ) -> Dict[str, Any]:
        """Symbols whose newest bar is behind the rest of the table.

        These are the names that silently drop out of the ranking: a symbol
        with no bar on the session is SKIPPED rather than forward-filled, so it
        simply stops being a candidate without anything failing.
        """
        if newest is None:
            return {
                "count": None,
                "behind": [],
                "missingEntirely": None,
                "note": (
                    "No bars are stored at all, so nothing can be behind "
                    "anything. Run the bootstrap import."
                ),
            }

        behind: List[Dict[str, Any]] = []
        missing = 0
        for symbol in symbols:
            last = latest_by_symbol.get(symbol)
            if last is None:
                missing += 1
                continue
            if last >= newest:
                continue
            sessions = sum(1 for one in calendar if one > last) if calendar else None
            behind.append(
                {
                    "symbol": symbol,
                    "lastSession": last.isoformat(),
                    "daysBehind": (newest - last).days,
                    # None rather than a guess when the symbol fell out before
                    # the window this read covers.
                    "sessionsBehind": sessions,
                    "sessionsBehindIsAtLeast": bool(
                        calendar and sessions == len(calendar)
                    ),
                }
            )
        behind.sort(key=lambda row: row["lastSession"])
        return {
            "count": len(behind),
            "behind": behind[:SAMPLE_LIMIT],
            "sampleLimit": SAMPLE_LIMIT,
            "missingEntirely": missing,
            "note": (
                "A symbol with no bar on the session is SKIPPED, never "
                "forward-filled -- so a name that stops updating stops being a "
                "candidate rather than staying rankable on a stale close."
            ),
        }

    async def _universe_resolution(
        self, symbols: Sequence[str], segment: str, with_bars: int
    ) -> Dict[str, Any]:
        """How much of the configured universe the instrument master can resolve.

        Resolved through `DailyBarRefreshService.targets_for`, which is the one
        place that decides what "resolved" means -- from the master's own
        active rows rather than from the universe file's advisory security ids.
        Reimplementing that here would be a second answer to the same question.
        """
        universe = self.definition.universe
        unresolved: List[str] = []
        resolved: Optional[int] = None
        error: Optional[str] = None
        try:
            from src.daily_bars.services.daily_bar_service import (
                DailyBarRefreshService,
            )

            service = DailyBarRefreshService(self._bars(), self._instruments())
            targets, unresolved = await service.targets_for(self.definition)
            # The reference instruments are appended to every target list and
            # are not universe members, so they are not counted here.
            reference_symbols = {
                one.symbol for one in self.definition.reference_instruments.values()
            }
            resolved = len(
                [one for one in targets if one[0] not in reference_symbols]
            )
        except Exception as problem:  # noqa: BLE001 - the tab must still render
            error = str(problem)
            logger.warning(
                "Could not resolve the universe for %s: %s",
                self.definition.key,
                problem,
            )

        return {
            "name": universe.name if universe is not None else None,
            "configured": len(symbols) if universe is not None else None,
            "resolved": resolved,
            "unresolvedCount": len(unresolved) if resolved is not None else None,
            "unresolved": sorted(unresolved)[:SAMPLE_LIMIT],
            "withBars": with_bars,
            "fnoEligible": await self._instruments().count_fno_eligible(
                segment, symbols
            ),
            "error": error,
            "note": (
                "A symbol with no active row in the instrument master cannot "
                "be fetched or traded and is permanently out of the ranking. "
                "F&O eligibility is derived from the master's own FUTSTK rows "
                "and is never configured; it is what moves an order's cut-off "
                "from the close to the end of continuous trading."
            ),
        }

    # --- "What it holds, and what it is watching" ---------------------------
    async def _holdings(self, portfolio_id: Optional[int]) -> Dict[str, Any]:
        from src.swing.services.stop_monitor import get_swing_stop_monitor

        stops = self._stops()
        active = await stops.list_active(self.definition.key, portfolio_id)
        triggered = await stops.list_triggered(self.definition.key)

        positions: Optional[int] = None
        unmarked: Optional[int] = None
        balance = None
        if portfolio_id is not None:
            from src.portfolios.services.balance_service import BalanceService
            from src.positions.database.db_operations.position_repository import (
                PositionRepository,
            )

            rows = await PositionRepository(self.session).list_all(
                include_closed=False,
                strategy_key=self.definition.key,
                portfolio_id=portfolio_id,
            )
            balances = BalanceService(self.session)
            open_rows = [row for row in rows if int(row.net_quantity or 0) != 0]
            positions = len(open_rows)
            unmarked = sum(
                1
                for row in open_rows
                if balances.mark_for(row.security_id) is None
            )
            # The ONLY place cash, blocked margin, available and equity are
            # computed. Four figures, never one.
            balance = (await balances.balance_for(portfolio_id)).as_dict()

        monitor = get_swing_stop_monitor().status()
        return {
            "portfolioId": portfolio_id,
            "positions": positions,
            "unmarkedPositions": unmarked,
            "positionsNote": (
                None
                if portfolio_id is not None
                else "No portfolio selected, so nothing about a book can be shown."
            ),
            "stops": {
                "active": len(active),
                # A stop ROW exists from entry; a stop LEVEL needs an ATR14.
                # The two came apart when every entry started writing a row, and
                # only the second one protects anything.
                "withoutLevel": sum(1 for row in active if row.stop_price is None),
                "triggeredWaiting": len(triggered),
                "triggeredNote": (
                    "Triggered and waiting for the next session's open. A stop "
                    "that fires inside the Closing Auction Session for an "
                    "F&O-eligible name is recorded as triggered and does not "
                    "fill, because this simulator has no model of a call "
                    "auction."
                ),
                "closed": await self._closed_stop_count(portfolio_id),
            },
            "watcher": {
                "enabled": monitor.get("enabled"),
                "running": monitor.get("running"),
                "intervalMs": monitor.get("intervalMs"),
                "lastPassAtIst": monitor.get("lastPassAtIst"),
                "watching": monitor.get("watching"),
                "unprotected": monitor.get("unprotected"),
                "unmarked": monitor.get("unmarked"),
                "nearestSymbol": monitor.get("nearestSymbol"),
                "nearestPercent": monitor.get("nearestPercent"),
                "error": monitor.get("error"),
                "passes": monitor.get("runs"),
                "triggered": monitor.get("triggered"),
                "exitsPlaced": monitor.get("exitsPlaced"),
                "deferredToAuction": monitor.get("deferredToAuction"),
                # Flagged, not fixed. See the note.
                "countersAreProcessWide": True,
                "countersNote": PROCESS_WIDE_COUNTERS_NOTE,
            },
            "money": balance,
            "rateCard": self._rate_card(),
        }

    async def _closed_stop_count(self, portfolio_id: Optional[int]) -> Optional[int]:
        rows = await self._stops().history(
            self.definition.key, portfolio_id, limit=5000
        )
        return sum(1 for row in rows if row.exit_kind is not None)

    def _rate_card(self) -> Dict[str, Any]:
        from src.charges.services.charges_engine import ChargesEngine

        try:
            engine = ChargesEngine.for_strategy(self.definition)
            return {
                "card": engine.rate_card_name,
                "version": engine.version,
                "error": None,
            }
        except Exception as problem:  # noqa: BLE001 - the tab must still render
            logger.warning(
                "Could not read the rate card for %s: %s",
                self.definition.key,
                problem,
            )
            return {"card": None, "version": None, "error": str(problem)}

    # --- "The rules it is running under" ------------------------------------
    async def _rules(self) -> Dict[str, Any]:
        """A read-only mirror of the Configuration tab, with who last moved each.

        `describe_policies` and `describe_settings` already report the shipped
        default beside what is in force -- BOTH, always, because a page showing
        only the file would teach a rule nothing obeys and one showing only the
        effective value would hide that a switch had been moved. This adds the
        third column: who moved it and when.
        """
        from src.strategies.database.db_models.feature_toggle_model import (
            SCOPE_AUTOMATION,
            SCOPE_POLICY,
            SCOPE_STRATEGY,
        )
        from src.strategies.database.db_operations.feature_toggle_repository import (
            FeatureToggleRepository,
        )
        from src.strategies.database.db_operations.strategy_setting_repository import (
            StrategySettingRepository,
        )
        from src.users.database.db_operations.user_repository import UserRepository

        key = self.definition.key
        policies = describe_policies(self.definition, self.parameters)
        settings = describe_settings(self.definition, self.parameters)

        toggles = FeatureToggleRepository(self.session)
        policy_audit = await toggles.audit_for(
            SCOPE_POLICY, [f"{key}/{row['key']}" for row in policies["policies"]]
        )
        switch_audit = {
            "enabled": (await toggles.audit_for(SCOPE_STRATEGY, [key])).get(key),
            "armed": (await toggles.audit_for(SCOPE_AUTOMATION, [key])).get(key),
        }
        setting_audit = await StrategySettingRepository(self.session).audit_for(key)

        wanted = [
            entry["updated_by_user_id"]
            for entry in list(policy_audit.values())
            + list(setting_audit.values())
            + [one for one in switch_audit.values() if one]
            if entry and entry.get("updated_by_user_id") is not None
        ]
        labels = await UserRepository(self.session).labels_for(wanted)

        return {
            "policies": [
                {**row, **_audit_fields(policy_audit.get(f"{key}/{row['key']}"), labels)}
                for row in policies["policies"]
            ],
            "contradiction": policies["contradiction"],
            "settings": [
                {**row, **_audit_fields(setting_audit.get(row["key"]), labels)}
                for row in settings["settings"]
            ],
            "switches": {
                "enabled": _audit_fields(switch_audit["enabled"], labels),
                "armed": _audit_fields(switch_audit["armed"], labels),
            },
            "auditNote": NO_AUDIT_HISTORY_NOTE,
            "editedOn": "/swing?tab=configuration",
            "notEditableNote": (
                "P1-P19 -- the momentum floor, the ATR multiple, the breadth "
                "ramp, the rank cut-off and every lookback -- and the rebalance "
                "cadence live in this strategy's YAML and are editable from no "
                "page. The \"How it works\" tab renders them read-only from that "
                "same file."
            ),
        }

    # --- "Recent problems" --------------------------------------------------
    def _problems(self, limit: int = 100) -> Dict[str, Any]:
        """This strategy's own WARNING+ records, newest first.

        Filtered by LOGGER NAME rather than by reading the text: every record
        carries the logger it came from, and `get_logger("swing.stops")` is
        `dcpt.swing.stops`. Records are already formatted through
        `RedactingFormatter` -- the buffer stores them that way precisely
        because an HTTP endpoint reads it.
        """
        from src import log_buffer

        handler = log_buffer.get_handler()
        if handler is None:
            return {
                "records": [],
                "available": False,
                "note": (
                    "The in-memory log buffer is not installed in this "
                    "process, so recent problems cannot be shown at all. "
                    "`logs/app.log` is the durable record."
                ),
                "loggerPrefixes": list(SWING_LOGGER_PREFIXES),
                "bufferNote": None,
                "totalSeen": None,
            }

        mine = [
            record
            for record in handler.recent()
            if str(record.get("logger", "")).startswith(SWING_LOGGER_PREFIXES)
        ]
        counts = handler.counts()
        return {
            "records": mine[:limit],
            "available": True,
            "loggerPrefixes": list(SWING_LOGGER_PREFIXES),
            "totalSeen": sum(counts.values()) if counts else 0,
            "countsByLevel": counts,
            "note": (
                "Warnings and errors from this strategy's own components and "
                "from the daily-bar jobs that feed it."
            ),
            "bufferNote": (
                "IN MEMORY and process-scoped: this list is empty after a "
                "restart, which is not the same as nothing having gone wrong. "
                "`logs/app.log` is the durable record. The counts are of every "
                "record the buffer has seen, including ones it has since "
                "dropped."
            ),
        }


def task_description(name: str) -> str:
    from src.health.services.task_inspector import TASK_DESCRIPTIONS

    return TASK_DESCRIPTIONS.get(name, "Not a task this build knows about")


def _audit_fields(
    entry: Optional[Dict[str, Any]], labels: Dict[int, str]
) -> Dict[str, Any]:
    """Who last wrote a row and when, or an explicit absence.

    Three different answers, kept apart. NO ROW means nobody has ever touched
    the switch, which is not the same as its having been set back to the
    shipped value. A row with no user id means the change was not attributed
    (a startup default, a script). A user id that resolves to nobody means the
    author's account has since been deleted -- the foreign key is SET NULL on
    delete, so this is rare, but "deleted account" and "not recorded" are
    different facts and neither is "unknown".
    """
    if not entry:
        return {"updatedAt": None, "updatedBy": None, "everChanged": False}

    user_id = entry.get("updated_by_user_id")
    if user_id is None:
        author = "not recorded"
    else:
        author = labels.get(int(user_id)) or "an account that no longer exists"
    return {
        "updatedAt": _iso(entry.get("updated_at")),
        "updatedBy": author,
        "everChanged": True,
    }


def _session_row(record) -> Optional[Dict[str, Any]]:
    """One journal row, reduced to what a health panel needs.

    Deliberately NOT the whole record: the ranking, the parameters and the
    filter census are on the Live tab's decision history, which is where a
    reader who wants them should go.
    """
    if record is None:
        return None
    return {
        "sessionDate": _date_iso(record.session_date),
        "runKind": record.run_kind,
        "status": record.status,
        "startedAt": _iso(record.started_at),
        "completedAt": _iso(record.completed_at),
        "message": record.message,
    }


def _first(runs: Sequence[Dict[str, Any]], kind: str) -> Optional[Dict[str, Any]]:
    for run in runs:
        if run.get("kind") == kind:
            return run
    return None


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:  # pragma: no cover - the repository formats these
        return None


