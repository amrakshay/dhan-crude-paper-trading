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

        return {
            "strategyKey": self.definition.key,
            "label": self.definition.label,
            "enabled": registry.is_enabled(self.definition.key),
            "armed": registry.is_armed(self.definition.key),
            "armedByDefault": self.definition.automation.armed_by_default,
            "noTrackRecord": NO_TRACK_RECORD,
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
            "holdings": [self._holding_row(one) for one in open_holdings],
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
        }

    def _next_occurrence(self, at_text: str) -> Optional[str]:
        from src.core.time_utils import parse_hhmm
        from src.swing.services.scheduler import SwingScheduler

        moment = SwingScheduler.next_occurrence(
            parse_hhmm(at_text), self.definition.market_hours.trading_days
        )
        return moment.isoformat() if moment is not None else None

    def _holding_row(self, holding) -> Dict[str, Any]:
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
            "notEditable": (
                "B1-B16 -- the 55-day breakout, the 2x volume multiple, the "
                "0.8 close-location floor, the 200-day trend filter, the "
                "six-month momentum floor, the ranking and the five slots -- "
                "live in conf/strategies/nse-btst-overnight.yaml and are "
                "editable from no page. The 'How it works' tab renders them "
                "read-only from that same file. What is editable here is "
                "whether a rule is OBEYED and WHEN the strategy wakes up, "
                "which are runtime facts rather than parameters of the rule."
            ),
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
