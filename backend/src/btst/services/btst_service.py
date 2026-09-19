"""Read models for the BTST page. Decides nothing.

The counterpart of `swing_service.py`. Every method here answers a question the
page asks and takes no action, so the page can be opened at any hour, with the
strategy off, with nothing ever traded, and still render.

Section 3 of `frontend/CLAUDE.md` applies hardest to two things on this page:

- **"Nothing qualified today" is the NORMAL state.** At roughly 0.54 signals a
  session, most days produce nothing. A page that looked broken when nothing
  qualified would be wrong about the strategy far more often than it was right,
  so every payload carries the funnel counts -- "289 tradable, 284 measured, 31
  above their 55-day high, 6 on 2x volume, 0 closing strong" reads as a working
  scan, and a bare "no candidates" does not.

- **Undefined is not zero.** A CLV that could not be computed, an equity figure
  `BalanceService` withheld, a gap on a position that has not been sold yet --
  each comes back null with the reason, never as a number that reads as a
  measurement.
"""
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from src.btst.database.db_models.btst_session_model import (
    EXIT_DONE,
    EXIT_FAILED,
    EXIT_LATE,
    EXIT_PENDING,
    RUN_EXIT,
    RUN_SCAN,
)
from src.btst.services.btst_parameters import BtstParameters, RANK_VOL_RATIO
from src.btst.services.btst_policy import describe_policies, resolve_policy
from src.btst.services.btst_schedule import describe_settings, resolve_schedule
from src.btst.services.scan_service import FILTER_STAGES
from src.core.time_utils import ist_now, to_ist
from src.logging_config import get_logger
from src.strategies.services import market_clock

logger = get_logger("btst.service")

# Said on every payload, above the numbers, and composed HERE rather than in
# the page -- `frontend/CLAUDE.md` section 5d: the specification's own figures
# are a survivorship-biased, in-sample backtest of a rule that has never traded
# a rupee, and showing this book's numbers beside them without saying so
# invites exactly the wrong comparison.
NO_TRACK_RECORD = (
    "No live track record. Every figure in the specification is an in-sample, "
    "survivorship-biased backtest of a rule that has never traded a rupee, and "
    "its own author recommends paper-trading this for 8-12 weeks before "
    "funding it. That is what this book is."
)


class BtstServiceError(Exception):
    pass


def _number(value) -> Optional[float]:
    return None if value is None else float(value)


def _money(value) -> Optional[str]:
    return None if value is None else str(value)


class BtstService:
    """Everything the page reads."""

    def __init__(self, session, definition, parameters: Optional[BtstParameters] = None):
        self.session = session
        self.definition = definition
        self.parameters = parameters or BtstParameters.from_definition(definition)

    @classmethod
    def for_strategy(cls, session, strategy_key: str) -> "BtstService":
        from src.strategies.services.strategy_registry import get_strategy_registry

        definition = get_strategy_registry().get(strategy_key)
        if definition is None:
            raise BtstServiceError(f"Unknown strategy module {strategy_key!r}")
        if not definition.automation.automated:
            raise BtstServiceError(
                f"{definition.label} declares no automation block, so it takes "
                f"no decisions of its own and has nothing to journal."
            )
        try:
            parameters = BtstParameters.from_definition(definition)
        except Exception as error:  # noqa: BLE001 - name the file and the key
            raise BtstServiceError(str(error)) from error
        return cls(session, definition, parameters)

    # --- collaborators ------------------------------------------------------
    def _sessions(self):
        from src.btst.database.db_operations.btst_repository import (
            BtstSessionRepository,
        )

        return BtstSessionRepository(self.session)

    def _decisions(self):
        from src.btst.database.db_operations.btst_repository import (
            BtstDecisionRepository,
        )

        return BtstDecisionRepository(self.session)

    def _holdings(self):
        from src.btst.database.db_operations.btst_repository import (
            BtstHoldingRepository,
        )

        return BtstHoldingRepository(self.session)

    # --- the Live tab -------------------------------------------------------
    async def status(self, portfolio_id: Optional[int] = None) -> Dict[str, Any]:
        """What it is doing now, and what it last decided."""
        from src.strategies.services.strategy_registry import get_strategy_registry

        registry = get_strategy_registry()
        schedule = resolve_schedule(self.definition, self.parameters)
        policy = resolve_policy(self.definition, self.parameters)
        now = ist_now()

        latest_scan = await self._sessions().latest(self.definition.key, RUN_SCAN)
        latest_exit = await self._sessions().latest(self.definition.key, RUN_EXIT)
        open_holdings = await self._holdings().open_for(
            self.definition.key, portfolio_id
        )
        # WHY each open position was bought, from the decision that placed it.
        # The rotation's page has a ranking table because it holds ten names
        # for weeks and the ranking is what decides which; this one holds up to
        # five for eighteen hours, and what is worth knowing about each is the
        # measurement that qualified it -- which is on the decision row and
        # nowhere else once the session is over.
        entry_decisions = await self._decisions().by_order_ids(
            self.definition.key, [one.entry_order_id for one in open_holdings]
        )

        policies = describe_policies(self.definition, self.parameters)
        settings = describe_settings(self.definition, self.parameters)

        balance = None
        if portfolio_id is not None:
            balance = (await self._balances().balance_for(portfolio_id)).as_dict()

        return {
            "strategyKey": self.definition.key,
            "label": self.definition.label,
            # The strategy's own one-liner, from the YAML, so the page's header
            # is not a second description that drifts from the file.
            "description": self.definition.description,
            "enabled": registry.is_enabled(self.definition.key),
            "automated": self.definition.automation.automated,
            "armed": registry.is_armed(self.definition.key),
            "armedByDefault": self.definition.automation.armed_by_default,
            "noTrackRecord": NO_TRACK_RECORD,
            # WHAT THE CLOCK IS DOING, this second. Composed here rather than
            # taken from the scheduler's own `schedules` list, which is built
            # out of `SwingParameters` and therefore skips this strategy
            # entirely -- see `_scheduler_payload`.
            "scheduler": self._scheduler_payload(),
            # WHICH RULES ARE OBEYED, beside the YAML's default for each, so
            # the page can never show a gate that looks on while nothing acts
            # on it. `contradiction` is always null here and the key is present
            # on purpose: this strategy's two switches answer different
            # questions and no pair of them contradicts.
            "policies": policies["policies"],
            "policyContradiction": policies["contradiction"],
            "settings": settings["settings"],
            # Four figures, never one (frontend/CLAUDE.md section 3). Null when
            # no portfolio is in scope, which is not a balance of zero.
            "balance": balance,
            # THE CLOSING AUCTION, which this strategy needs stated more than
            # the rotation does: its scan is at 15:20, inside the window.
            "closingAuction": self._closing_auction_payload(),
            # When the universe joins the feed and when it leaves. Activity
            # rather than health: the verdict on whether prices are actually
            # ARRIVING is the Health tab's, and this is only the clock.
            "subscriptionWindow": self._subscription_window(now),
            "market": {
                "open": market_clock.is_market_open(self.definition, now=now),
                "tradingDay": market_clock.is_trading_day(self.definition, now=now),
                "nowIst": now.isoformat(),
                "opensAtIst": self.definition.market_hours.open.strftime("%H:%M"),
                "closesAtIst": self.definition.market_hours.close.strftime("%H:%M"),
            },
            "schedule": {
                "scanAtIst": schedule.scan_at,
                "exitAtIst": schedule.exit_at,
                "exitAlarmAfterMinutes": schedule.exit_alarm_after_minutes,
                # Absolute, so the page counts down LOCALLY rather than
                # re-fetching every second (frontend/CLAUDE.md section 4).
                "nextScanIst": self._next_occurrence(schedule.scan_at),
                "nextExitIst": self._next_occurrence(schedule.exit_at),
            },
            "policy": policy.as_dict(),
            "holdings": [
                self._holding_row(one, entry_decisions.get(one.entry_order_id))
                for one in open_holdings
            ],
            "lastScan": self._session_row(latest_scan),
            "lastExit": self._session_row(latest_exit),
            # The distinction the page turns on. A scan that ran and found
            # nothing is a healthy scan; the page has to say which.
            "signalFrequencyNote": (
                "About one signal every two sessions with F&O names excluded "
                "(1.07 a session across the whole universe). Most days produce "
                "nothing, and nothing is the ordinary outcome rather than a "
                "fault."
            ),
            # ONE LINE, not the whole table. Section 9.4's year-by-year decay
            # is the most important fact about this strategy and it is on the
            # "How it works" tab in full; repeating it here would put the same
            # ten rows on two tabs, where the second copy is the one that goes
            # stale. What the Live tab carries is the sentence, and a link.
            "exitTimingNote": (
                "THE EXIT IS THE STRATEGY. Sold at the next OPEN the "
                "specification measures +0.617% at a 71.4% win rate; the "
                "identical signals held to the next CLOSE measure +0.428% at "
                "49.0%. A position that does not leave at the open is not a "
                "delayed trade, it is a different one."
            ),
        }

    # --- the clock ----------------------------------------------------------
    def _scheduler_payload(self) -> Dict[str, Any]:
        """What the shared clock is doing, narrowed to THIS strategy.

        The scheduler already reports itself, and the rotation's page reads
        `status()["schedules"]` to find its own row. **This strategy is not in
        that list**: those rows are built by constructing `SwingParameters` for
        every automated module, and a definition that raises is skipped -- and
        this one raises, because it has no `breadth_lower`. Nothing consumed
        the omission before, since the only reader looked itself up by key.

        So the times come from `btst_schedule` -- which owns them -- and only
        the genuinely process-wide facts are taken from the scheduler. The runs
        and the missed runs are filtered to this strategy's key, which
        `JobRun`/`MissedRun` carry precisely so two modules can share one clock.
        """
        from src.swing.services.scheduler import get_swing_scheduler

        status = get_swing_scheduler().status()
        mine_recent = [
            run
            for run in (status.get("recent") or [])
            if run.get("strategyKey") == self.definition.key
        ]
        mine_missed = [
            entry
            for entry in (status.get("missedRuns") or [])
            if entry.get("strategyKey") == self.definition.key
        ]
        progress = status.get("progress")
        return {
            "schedulerEnabled": status.get("enabled"),
            # "idle" and "the scheduler is not running" are different answers
            # and the page renders them differently.
            "running": status.get("running"),
            "activity": status.get("activity"),
            "activitySinceIst": status.get("activitySinceIst"),
            "nowIst": status.get("nowIst"),
            "intervalSeconds": status.get("intervalSeconds"),
            "recent": mine_recent,
            "missedRuns": mine_missed,
            "missedRunCount": sum(
                len(entry.get("sessions") or []) for entry in mine_missed
            ),
            "checkedForMissedAtIst": status.get("checkedForMissedAtIst"),
            "error": status.get("error"),
            "progress": progress,
            # WHOSE long job it is. `progress` is a single process-wide field
            # with no owner on it, and the only thing that ever sets it is the
            # rotation's nightly bar refresh -- so a page rendering it without
            # this sentence would report another strategy's twelve-minute job
            # as though this one were running it. It is shown here rather than
            # hidden because those are the bars five of this strategy's seven
            # filters read.
            "progressNote": (
                "This is the shared daily-bar refresh, run by the rotation's "
                "nightly job. It is not this strategy's own work -- it has no "
                "long job of its own -- but it is what keeps the bars five of "
                "these filters read up to date."
                if progress
                else None
            ),
            "clockNote": (
                "One clock serves every automated strategy, so what it is "
                "doing this second may belong to another one. The runs and "
                "missed sessions listed here are this strategy's only."
            ),
            "runListNote": (
                "The run list is in memory and is empty after a restart, which "
                "is not the same as nothing having run. The decision history "
                "below is the durable record."
            ),
        }

    def _closing_auction_payload(self) -> Optional[Dict[str, Any]]:
        """The CAS note, which matters more here than on the rotation.

        NSE's Closing Auction Session ends continuous cash trading at 15:15 for
        F&O-eligible names, and this strategy's scan is at 15:20 -- INSIDE that
        window. That is the whole reason `fno.exclude` exists, so the page says
        it rather than leaving the reader to notice that two configured times
        are on the wrong side of each other.
        """
        auction = self.definition.market_hours.closing_auction
        if auction is None:
            return None
        schedule = resolve_schedule(self.definition, self.parameters)
        scan_is_inside = schedule.scan > auction.continuous_close
        return {
            "appliesTo": auction.applies_to,
            "continuousClose": auction.continuous_close.strftime("%H:%M"),
            "auctionClose": auction.auction_close.strftime("%H:%M"),
            "scanAtIst": schedule.scan_at,
            "scanIsInsideTheWindow": scan_is_inside,
            "note": (
                (
                    f"Continuous cash trading ends at "
                    f"{auction.continuous_close.strftime('%H:%M')} for "
                    f"F&O-eligible names, and this strategy scans at "
                    f"{schedule.scan_at} -- INSIDE that window. No order could "
                    f"fill continuously for one of those names at that time, "
                    f"which is why F&O names are excluded from the universe "
                    f"rather than scanned and then refused. The guard is per "
                    f"instrument and runs immediately before each order."
                )
                if scan_is_inside
                else (
                    f"Continuous cash trading ends at "
                    f"{auction.continuous_close.strftime('%H:%M')} for "
                    f"F&O-eligible names. This strategy scans at "
                    f"{schedule.scan_at}, before that."
                )
            ),
        }

    def _subscription_window(self, now) -> Optional[Dict[str, Any]]:
        """When the universe is on the feed.

        This is the only strategy that subscribes its whole universe, and it
        does so for a window rather than all session -- so "nothing is
        subscribed" is the correct state for most of the day and a page that
        did not say when the window opens would read as a fault.
        """
        subscription = self.definition.subscription
        opens = subscription.window_opens_at
        closes = subscription.window_closes_at
        if opens is None and closes is None:
            return None
        return {
            "kind": subscription.kind,
            "opensAtIst": opens.strftime("%H:%M") if opens else None,
            "closesAtIst": closes.strftime("%H:%M") if closes else None,
            "open": subscription.window_is_open(now.time()),
            "universeSize": (
                len(self.definition.universe) if self.definition.universe else None
            ),
            "note": (
                "Outside this window the strategy subscribes only what it "
                "holds, exactly like every other module. Whether prices are "
                "actually arriving is on the Health tab."
            ),
        }

    def _balances(self):
        from src.portfolios.services.balance_service import BalanceService

        return BalanceService(self.session)

    def _next_occurrence(self, at_text: str) -> Optional[str]:
        from src.core.time_utils import parse_hhmm
        from src.swing.services.scheduler import SwingScheduler

        moment = SwingScheduler.next_occurrence(
            parse_hhmm(at_text), self.definition.market_hours.trading_days
        )
        return moment.isoformat() if moment is not None else None

    def _holding_row(self, holding, entry=None) -> Dict[str, Any]:
        return {
            "id": holding.id,
            "symbol": holding.symbol,
            "securityId": holding.security_id,
            "quantity": holding.quantity,
            "entrySessionDate": holding.entry_session_date.isoformat(),
            "entryAtIst": to_ist(holding.entry_at).isoformat() if holding.entry_at else None,
            "entryPrice": _money(holding.entry_price),
            "exitStatus": holding.exit_status,
            "exitPrice": _money(holding.exit_price),
            "exitedAtIst": (
                to_ist(holding.exited_at).isoformat() if holding.exited_at else None
            ),
            "exitDelayMinutes": holding.exit_delay_minutes,
            "exitReason": holding.exit_reason,
            # Null until it is sold. A position still held has no realised gap,
            # and showing 0.00% would be a measurement of something that has
            # not happened.
            "overnightGap": _money(holding.overnight_gap),
            "entryGateOn": holding.entry_gate_on,
            "entryRegimeEnforced": holding.entry_regime_enforced,
            # WHY it was bought, from the decision that placed it. Null when
            # the decision cannot be found -- a position entered by hand, or
            # one whose order id was never recorded -- which is a different
            # answer from "there was no reason", so the page says so.
            "entry": (
                None
                if entry is None
                else {
                    "reason": entry.reason,
                    "rank": entry.rank,
                    "price": _money(entry.price),
                    "volRatio": _number(entry.vol_ratio),
                    "clv": _number(entry.clv),
                    "breakoutHigh": _money(entry.breakout_high),
                    "momentum": _number(entry.momentum),
                    "sessionHigh": _money(entry.session_high),
                    "sessionLow": _money(entry.session_low),
                }
            ),
        }

    def _session_row(self, record) -> Optional[Dict[str, Any]]:
        if record is None:
            return None
        return {
            "id": record.id,
            "sessionDate": (
                record.session_date.isoformat() if record.session_date else None
            ),
            "runKind": record.run_kind,
            "status": record.status,
            "startedAtIst": (
                to_ist(record.started_at).isoformat() if record.started_at else None
            ),
            "completedAtIst": (
                to_ist(record.completed_at).isoformat() if record.completed_at else None
            ),
            "message": record.message,
            "gateOn": record.gate_on,
            "indexSymbol": record.index_symbol,
            "indexClose": _money(record.index_close),
            "indexSma": _money(record.index_sma),
            "candidateCount": record.candidate_count,
            "pickedCount": record.picked_count,
            "slots": record.slots,
            "regimeEnforced": record.regime_enforced,
            "fnoExcluded": record.fno_excluded,
            "funnel": self._funnel(record),
        }

    @staticmethod
    def _funnel(record) -> List[Dict[str, Any]]:
        """The filter counts, in the specification's own order.

        Rendered from `FILTER_STAGES` so the page and the scan cannot drift
        into listing the filters differently. A stage with no stored count
        comes back null rather than zero -- an older record that predates a
        stage did not measure it, which is not the same as measuring none.
        """
        try:
            counts = json.loads(record.filter_counts_json or "{}")
        except (TypeError, ValueError):
            counts = {}
        return [
            {"key": key, "label": label, "count": counts.get(key)}
            for key, label in FILTER_STAGES
        ]

    # --- the decision history ----------------------------------------------
    async def history(self, limit: int = 30) -> Dict[str, Any]:
        """Recent runs with their decisions. The journal, as a page reads it."""
        records = await self._sessions().list_recent(self.definition.key, limit=limit)
        rows = []
        for record in records:
            decisions = await self._decisions().for_session(record.id)
            row = self._session_row(record)
            row["decisions"] = [self._decision_row(one) for one in decisions]
            rows.append(row)
        return {"sessions": rows, "noTrackRecord": NO_TRACK_RECORD}

    @staticmethod
    def _decision_row(decision) -> Dict[str, Any]:
        return {
            "symbol": decision.symbol,
            "action": decision.action,
            "rank": decision.rank,
            "reason": decision.reason,
            "quantity": decision.quantity,
            "orderId": decision.order_id,
            # The live inputs, which exist nowhere else once the session is
            # over. This is why this module has its own journal.
            "price": _money(decision.price),
            "sessionHigh": _money(decision.session_high),
            "sessionLow": _money(decision.session_low),
            "sessionVolume": decision.session_volume,
            "volRatio": _number(decision.vol_ratio),
            "clv": _number(decision.clv),
            "breakoutHigh": _money(decision.breakout_high),
            "momentum": _number(decision.momentum),
            "sma": _money(decision.sma),
            "regimeGateOn": decision.regime_gate_on,
            "regimeEnforced": decision.regime_enforced,
            "fnoExcluded": decision.fno_excluded,
            "fnoEligible": decision.fno_eligible,
        }

    # --- the Live tab's results summary -------------------------------------
    async def performance(self) -> Dict[str, Any]:
        """What the overnight gaps have actually been.

        Computed from `btst_holdings`, which stores the gap realised between
        the two prices actually PAID. Not from the bars: the backtest's number
        is `open(T+1) / close(T) - 1` with a 0.05%-a-side assumption, and the
        difference between that and what a pessimistic fill simulator gets is
        the most valuable number this exercise produces (section 10.2).
        """
        holdings = await self._holdings().list_recent(self.definition.key, limit=500)
        closed = [
            one
            for one in holdings
            if one.exit_status in (EXIT_DONE, EXIT_LATE) and one.overnight_gap is not None
        ]
        counts = await self._holdings().exit_counts(self.definition.key)

        if not closed:
            return {
                "trades": 0,
                # Undefined, not zero. No closed round trip means no measured
                # edge, which is a different answer from an edge of nothing.
                "meanGap": None,
                "winRate": None,
                "best": None,
                "worst": None,
                "lateExits": counts.get(EXIT_LATE, 0),
                "exitCounts": counts,
                "noTrackRecord": NO_TRACK_RECORD,
                "benchmark": self._benchmark(),
            }

        gaps = [Decimal(str(one.overnight_gap)) for one in closed]
        wins = sum(1 for gap in gaps if gap > 0)
        return {
            "trades": len(gaps),
            "meanGap": str(sum(gaps) / Decimal(len(gaps))),
            "winRate": float(wins) / float(len(gaps)),
            "best": str(max(gaps)),
            "worst": str(min(gaps)),
            "lateExits": counts.get(EXIT_LATE, 0),
            "exitCounts": counts,
            "noTrackRecord": NO_TRACK_RECORD,
            "benchmark": self._benchmark(),
        }

    @staticmethod
    def _benchmark() -> Dict[str, Any]:
        """What the specification measured, for the page to show BESIDE this
        book's numbers rather than instead of them."""
        return {
            "grossMeanGap": 0.00617,
            "grossWinRate": 0.714,
            "netMeanGap": 0.00317,
            "costRoundTrip": 0.0030,
            "note": (
                "The specification's gross +0.617% at a 71.4% win rate is the "
                "whole-period, in-sample figure. The last three years average "
                "+0.171% net per trade -- 58% of it -- and the one "
                "no-look-ahead test returned +1.6% over 13 months at a 37% win "
                "rate. Expect this book to look worse than the backtest: the "
                "fill simulator here pays the far touch and walks the book, "
                "against the backtest's flat 0.05% a side. That gap is the "
                "most valuable number this exercise produces and is reported, "
                "never tuned away."
            ),
        }

    # --- the Configuration tab ---------------------------------------------
    def configuration(self) -> Dict[str, Any]:
        policies = describe_policies(self.definition, self.parameters)
        settings = describe_settings(self.definition, self.parameters)
        return {
            "strategyKey": self.definition.key,
            "label": self.definition.label,
            "policies": policies["policies"],
            "contradiction": policies["contradiction"],
            "settings": settings["settings"],
            # What is NOT editable here, said on the payload rather than
            # composed by the page: B1-B16 are in the YAML and are editable
            # from nowhere, which is what keeps the file greppable against the
            # specification's own table.
            # Composed from the PARAMETERS, not typed out. It listed "the
            # 55-day breakout, the 2x volume multiple, the 0.8 close-location
            # floor... and the five slots" as literal text until 2026-09-19 --
            # which made the one sentence on the page insisting those numbers
            # live in the YAML the only place on the page that restated them,
            # and it would have gone on saying 55 after the file changed.
            "notEditable": (
                f"B1-B16 -- the "
                f"{self.parameters.breakout_lookback_sessions}-session "
                f"breakout, the {self.parameters.volume_multiple:g}x volume "
                f"multiple, the {self.parameters.close_location_minimum} "
                f"close-location floor, the "
                f"{self.parameters.trend_sma_sessions}-session trend filter, "
                f"the {self.parameters.momentum_floor:.0%} momentum floor, the "
                f"ranking and the {self.parameters.slots} slots -- live in "
                f"conf/strategies/{self.definition.key}.yaml and are editable "
                f"from no page. The 'How it works' tab renders them read-only "
                f"from that same file. What is editable here is whether a rule "
                f"is OBEYED and WHEN the strategy wakes up, which are runtime "
                f"facts rather than parameters of the rule."
            ),
            # The specific things somebody WILL look for here and not find,
            # named rather than left to the B-numbers above. The rotation's tab
            # does the same for its rebalance cadence.
            "alsoNotEditable": [
                (
                    "THE EXIT ITSELF. It sells everything at the configured "
                    "time, unconditionally -- no rank is consulted and there is "
                    "no condition under which a position is kept. Only WHEN it "
                    "runs is a setting; THAT it runs is the strategy."
                ),
                (
                    f"THE SLOT COUNT and the position size. B11 is "
                    f"{self.parameters.slots} and B12 divides total equity by "
                    f"{self.parameters.position_size_divisor}. Both are in the "
                    f"YAML, because how much of the book one signal consumes is "
                    f"a parameter of the rule and not a runtime fact."
                ),
                (
                    "THE STOP. B15 is none and none is possible -- the only "
                    "risk window is overnight, when no order can execute at any "
                    "price. There is no control here because there is nothing "
                    "to switch on."
                ),
            ],
        }

    # --- the "How it works" tab ---------------------------------------------
    def explain(self) -> Dict[str, Any]:
        """Every number read from the YAML. The page restates nothing.

        Hardcoding "55-day high" in JSX would be a second source of truth (root
        `CLAUDE.md` section 7) and it would go on saying 55 for as long as it
        took somebody to notice the configuration had changed.
        """
        parameters = self.parameters
        policy = resolve_policy(self.definition, parameters)
        schedule = resolve_schedule(self.definition, parameters)
        reference = self.definition.reference_instrument(parameters.regime.index_role)

        rows = [
            ("B1", "Universe", (
                f"{self.definition.universe.name if self.definition.universe else '?'} "
                f"({len(self.definition.universe) if self.definition.universe else 0} "
                f"symbols)"
            )),
            ("B2", "Liquidity floor", (
                f"20-session mean of close x volume >= "
                f"Rs {parameters.liquidity_floor_rupees:,.0f} "
                f"({parameters.liquidity_floor_rupees / 10_000_000:,.0f} crore)"
            )),
            ("B3", "Price floor", f"Rs {parameters.price_floor:,.0f}"),
            ("B4", "Breakout", (
                f"price above the PRIOR session's "
                f"{parameters.breakout_lookback_sessions}-session high"
            )),
            ("B5", "Volume", (
                f"volume so far >= {parameters.volume_multiple:g} x the "
                f"{parameters.volume_window_sessions}-session average SHARE volume"
            )),
            ("B6", "Close strength", (
                f"CLV = (price - low) / (high - low) > "
                f"{parameters.close_location_minimum}, measured on the session so far"
            )),
            ("B7", "Trend", (
                f"price above its own {parameters.trend_sma_sessions}-session SMA"
            )),
            ("B8", "Momentum", (
                f"close[t-{parameters.momentum_skip_sessions}] / "
                f"close[t-{parameters.momentum_lookback_sessions}] - 1 > "
                f"{parameters.momentum_floor:.0%}"
            )),
            ("B9", "Regime gate", (
                f"{reference.label if reference else 'the index'} close above its "
                f"{parameters.regime.sma_sessions}-session SMA "
                f"({'ENFORCED' if policy.enforce_regime else 'NOT ENFORCED'})"
            )),
            ("B10", "Ranking", (
                f"{parameters.ranking} descending"
                + (
                    ""
                    if parameters.ranking != RANK_VOL_RATIO
                    else " -- as B10 specifies"
                )
            )),
            ("B11", "Slots", str(parameters.slots)),
            ("B12", "Position size", (
                f"total equity / {parameters.position_size_divisor}, whole "
                f"shares, floored"
            )),
            ("B13", "Entry", f"market order at {schedule.scan_at} IST"),
            ("B14", "Exit", (
                f"market order at {schedule.exit_at} IST the next session, "
                f"UNCONDITIONALLY"
            )),
            ("B15", "Stop", "NONE, and none is possible"),
            ("B16", "Idle cash", "not modelled -- see the caveats"),
        ]

        return {
            "strategyKey": self.definition.key,
            "label": self.definition.label,
            "description": self.definition.description,
            "noTrackRecord": NO_TRACK_RECORD,
            "parameters": [
                {"code": code, "name": name, "value": value}
                for code, name, value in rows
            ],
            "policy": policy.as_dict(),
            "schedule": {
                "scanAtIst": schedule.scan_at,
                "exitAtIst": schedule.exit_at,
            },
            # The day, as configured. Here so the explainer's timeline can be
            # DRAWN from the file rather than from five numbers typed into
            # JSX -- the one diagram in this module that is mostly times, and
            # so the one most exposed to a second source of truth.
            "marketHours": {
                "open": self.definition.market_hours.open.strftime("%H:%M"),
                "close": self.definition.market_hours.close.strftime("%H:%M"),
                "timezone": self.definition.market_hours.timezone,
                "closingAuction": self._closing_auction_payload(),
            },
            "subscription": {
                "kind": self.definition.subscription.kind,
                "windowOpensAtIst": (
                    self.definition.subscription.window_opens_at.strftime("%H:%M")
                    if self.definition.subscription.window_opens_at
                    else None
                ),
                "windowClosesAtIst": (
                    self.definition.subscription.window_closes_at.strftime("%H:%M")
                    if self.definition.subscription.window_closes_at
                    else None
                ),
            },
            # The thresholds the pictures label, as NUMBERS rather than as the
            # sentences in `parameters` above. The funnel diagram writes "> 2x"
            # beside a bar and the CLV diagram shades the top fifth of a
            # candle; both need the value, not its description, and parsing it
            # back out of the prose would be the second source of truth this
            # payload exists to prevent.
            # THE FUNNEL'S STAGES, in the scan's own order, each with what it
            # TESTS. Rendered from `FILTER_STAGES` like everything else that
            # names these filters, so the picture and the scan cannot drift
            # into listing them differently -- and the test sentences are
            # composed here, from the parameters, rather than typed beside the
            # bars in JSX where they would go on saying 55 after the YAML
            # changed.
            "funnelStages": self._funnel_stages(parameters, schedule.scan_at),
            "thresholds": {
                "breakoutLookbackSessions": parameters.breakout_lookback_sessions,
                "volumeMultiple": parameters.volume_multiple,
                "volumeWindowSessions": parameters.volume_window_sessions,
                "closeLocationMinimum": parameters.close_location_minimum,
                "trendSmaSessions": parameters.trend_sma_sessions,
                "momentumFloor": parameters.momentum_floor,
                "momentumLookbackSessions": parameters.momentum_lookback_sessions,
                "momentumSkipSessions": parameters.momentum_skip_sessions,
                "liquidityFloorRupees": parameters.liquidity_floor_rupees,
                "priceFloor": _number(parameters.price_floor),
                "slots": parameters.slots,
            },
            "regimeIndex": (
                {
                    "symbol": reference.symbol,
                    "label": reference.label,
                    "segment": reference.exchange_segment,
                    # Read, never traded: Dhan's ids are unique per SEGMENT and
                    # id 13 is NIFTY in IDX_I and ABB in NSE_EQ, which is in
                    # this universe.
                    "traded": False,
                }
                if reference is not None
                else None
            ),
            "exitTiming": {
                "nextOpenMeanGap": 0.00617,
                "nextOpenWinRate": 0.714,
                "nextCloseMeanGap": 0.00428,
                "nextCloseWinRate": 0.490,
                "note": (
                    "THE EXIT IS THE STRATEGY. The identical signal set held to "
                    "the next CLOSE instead of the next OPEN measures +0.428% "
                    "at a 49.0% win rate against +0.617% at 71.4% -- the gap is "
                    "given back during the session. Net of the 0.30% round trip "
                    "that is +0.128% against +0.317%."
                ),
            },
            "decay": {
                "rows": [
                    {"year": 2017, "meanGap": 0.00290, "winRate": 0.584, "trades": 197},
                    {"year": 2018, "meanGap": -0.00130, "winRate": 0.440, "trades": 91},
                    {"year": 2019, "meanGap": 0.00257, "winRate": 0.600, "trades": 55},
                    {"year": 2020, "meanGap": 0.00231, "winRate": 0.535, "trades": 228},
                    {"year": 2021, "meanGap": 0.00691, "winRate": 0.662, "trades": 494},
                    {"year": 2022, "meanGap": 0.00260, "winRate": 0.622, "trades": 222},
                    {"year": 2023, "meanGap": 0.00258, "winRate": 0.586, "trades": 519},
                    {"year": 2024, "meanGap": 0.00190, "winRate": 0.538, "trades": 485},
                    {"year": 2025, "meanGap": 0.00132, "winRate": 0.421, "trades": 214},
                    {"year": 2026, "meanGap": 0.00142, "winRate": 0.440, "trades": 25},
                ],
                "note": (
                    "THE EDGE HAS DECAYED, and this table is the single most "
                    "important thing on this page. 2021 alone contributed "
                    "+101.3% of a strategy whose whole-period CAGR is 19.0%. "
                    "The last three years average +0.171% net per trade -- 58% "
                    "of the full-period +0.295% -- with the win rate down from "
                    "57% to 50%. Whether that is permanent decay or a lull is "
                    "the open question this paper record exists to answer."
                ),
            },
            "recommendation": (
                "The specification's author does NOT recommend funding this "
                "yet: paper-trade it for 8-12 weeks first (section 16). At "
                "about one signal every two sessions, twelve weeks yields "
                "roughly thirty trades with F&O names excluded -- enough to "
                "begin telling +0.29% a trade from +0.03%. Running it here IS "
                "that recommendation being followed."
            ),
            "caveats": self._caveats(),
        }

    @staticmethod
    def _funnel_stages(parameters: BtstParameters, scan_at: str) -> List[Dict[str, Any]]:
        """Each filter stage with what it tests, keyed as the journal keys it.

        The keys come from `FILTER_STAGES`, which is also what
        `_funnel` renders a stored record with -- so the explainer's picture
        and the recorded census line up stage for stage by construction rather
        than by two lists being kept in step by hand.
        """
        # SHORT on purpose: these are captions beside a bar in a diagram, not
        # prose. A sentence that runs past the picture's own width is clipped
        # by the viewBox and teaches nothing, so each says the rule and its
        # number and stops. The full descriptions are the `parameters` rows.
        tests = {
            "universe": "B1 -- the configured universe file",
            "tradable": f"F&O names excluded -- the scan is at {scan_at}",
            "history": (
                f"enough bars for every lookback (to "
                f"{parameters.trend_sma_sessions})"
            ),
            "quoted": "a usable session high, low and volume",
            "liquidity": (
                f"B2 -- Rs "
                f"{parameters.liquidity_floor_rupees / 10_000_000:,.0f} crore a day"
            ),
            "price_floor": f"B3 -- above Rs {parameters.price_floor:,.0f}",
            "breakout": (
                f"B4 -- above the prior "
                f"{parameters.breakout_lookback_sessions}-session high"
            ),
            "volume": (
                f"B5 -- {parameters.volume_multiple:g}x the "
                f"{parameters.volume_window_sessions}-session average"
            ),
            "close_strength": (
                f"B6 -- closing in the top "
                f"{(1 - float(parameters.close_location_minimum)) * 100:.0f}% "
                f"of the range"
            ),
            "trend": f"B7 -- above its {parameters.trend_sma_sessions}-session SMA",
            "momentum": f"B8 -- six-month return above {parameters.momentum_floor:.0%}",
        }

        return [
            {"key": key, "label": label, "test": tests.get(key, "")}
            for key, label in FILTER_STAGES
        ]

    @staticmethod
    def _caveats() -> List[str]:
        """Specification section 15, carried across rather than summarised.

        The README's "Known gaps" carries the same list. Both exist because a
        limitation that is written down once, in a document nobody opens, is a
        limitation that gets lost.
        """
        return [
            "NO LIVE TRACK RECORD. Zero rupees have ever traded this rule.",
            "NO HOLDOUT. 22 candidate signals x 2 exit timings were scanned "
            "over 726,209 symbol-days and then refined seven times, all on the "
            "full period. Selection bias is present and unquantified.",
            "THE EDGE HAS DECAYED: +0.69% a trade in 2021 against +0.13% in "
            "2025, and it is roughly flat over the last two years.",
            "THE ONE GENUINELY NO-LOOK-AHEAD TEST returned +1.6% over 13 "
            "months at a 37% win rate. It is the most honest single number in "
            "the specification, and that window is also its weakest patch.",
            "SURVIVORSHIP BIAS: today's Nifty 500 looked at backwards. No "
            "point-in-time index membership is available.",
            "IT DIES AT +0.30% OF EXTRA SLIPPAGE (CAGR -0.6%). This is the "
            "tightest constraint in the whole specification, and the fill "
            "simulator here is deliberately pessimistic -- expect this book to "
            "look worse than the backtest and do not tune the simulator to "
            "close the gap.",
            "NO STOP IS POSSIBLE. The worst observed overnight gap is -6.04%; "
            "a larger one than anything in the sample can happen.",
            "COSTS ARE BRUTAL AT A ONE-DAY HOLD: about 0.30% round trip "
            "against a gross edge of ~0.62%. Half the edge is friction, "
            "because delivery STT is charged on BOTH legs.",
            "NO DATA EXISTS FOR THE POST-CAS REGIME. Every figure describes "
            "pre-August-2026 market structure.",
            "IDLE CASH IS NOT MODELLED. The backtest assumes 6.5% a year on "
            "undeployed capital, which is an assumption rather than a "
            "simulated instrument, and this application has no liquid-fund "
            "instrument at all. This book's cash earns nothing, so its returns "
            "are lower than the specification's by that amount.",
            "ALL GAINS ARE SHORT-TERM AND EVERY FIGURE IS PRE-TAX.",
        ]
