"""Writing the decision journal.

Takes what a run decided and turns it into rows. Decides nothing itself, which
is what lets the scan be tested without a database and the journal be tested
without a feed.

Two rules, the same two the rotation's journal follows:

- **Append-only, and enforced in the repository**, not merely documented.
- **Store the INPUTS, not just the conclusion.** "Nothing qualified" cannot be
  checked later; "289 tradable, 284 with history, 31 above their 55-day high, 6
  of those on 2x volume, 2 closing strong, 1 through everything" can. Here that
  matters more than it does for the rotation, because at roughly 0.54 signals a
  session the overwhelmingly normal outcome IS that nothing qualified, and a
  journal that recorded only the conclusion would be a row of identical
  sentences.

And one that is specific to this strategy: **every live input is stored on the
decision row.** A candidate's running high, low and cumulative volume at 15:20
exist in the feed and nowhere else. An hour later the session is over and the
same numbers can only be recovered as a finished bar, which is a different
number -- that difference is exactly what section 10.3 measured and what makes
live-versus-backtest divergence checkable at all.
"""
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from src.btst.database.db_models.btst_session_model import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_SKIPPED,
)
from src.btst.services.btst_parameters import BtstParameters
from src.btst.services.btst_policy import BtstPolicy
from src.btst.services.scan_service import FILTER_STAGES, ScanResult
from src.core.time_utils import utc_now
from src.logging_config import get_logger

logger = get_logger("btst.journal")

# How many rejected names get their own row. The universe is ~289 after the
# F&O policy and writing a row per name per session is 289 rows a day to say
# "did not break out" -- which is true of almost everything almost always and
# tells nobody anything. The ones that got FAR and still failed are the
# interesting ones, so rejections are recorded from the breakout stage on, and
# the funnel counts carry the rest.
REJECTION_STAGES_WORTH_A_ROW = ("breakout", "volume", "close_strength", "trend", "momentum")
MAX_REJECTION_ROWS = 40

# HOW DEEP EACH STAGE IS, derived from the scan's own funnel so the two cannot
# drift apart. Used to order the rows before the cap applies -- see
# `rejection_decisions`, and read the bug in its docstring before changing it.
_STAGE_DEPTH = {key: depth for depth, (key, _) in enumerate(FILTER_STAGES)}
_STAGE_LABEL = dict(FILTER_STAGES)


def _money(value: Optional[float]) -> Optional[Decimal]:
    """Indicators are float; the journal's numeric columns are `Money`.

    The conversion happens HERE, at the boundary, the same way
    `ranking_service` converts at its own. Root `CLAUDE.md`: money is
    `Decimal`. A volume ratio and a CLV are not money, but they are stored in
    `Money` columns because `NUMERIC(18,4)` is the only exact numeric type this
    schema has and a float column would round them differently on two backends.
    """
    if value is None:
        return None
    return Decimal(str(round(float(value), 6)))


@dataclass
class Decision:
    """One decision about one symbol, before it becomes a row."""

    symbol: str
    action: str
    reason: str
    security_id: Optional[str] = None
    rank: Optional[int] = None
    price: Optional[float] = None
    session_high: Optional[float] = None
    session_low: Optional[float] = None
    session_volume: Optional[float] = None
    vol_ratio: Optional[float] = None
    clv: Optional[float] = None
    breakout_high: Optional[float] = None
    momentum: Optional[float] = None
    sma: Optional[float] = None
    turnover: Optional[float] = None
    quantity: Optional[int] = None
    order_id: Optional[int] = None
    fno_eligible: Optional[bool] = None


@dataclass
class SessionRecord:
    """What was written, handed back so a caller can report it."""

    session_id: int
    session_date: Optional[date]
    run_kind: str
    status: str
    decisions: int
    message: str


class BtstJournalService:
    """Writes one session and its decisions."""

    def __init__(self, sessions, decisions, definition, parameters: BtstParameters):
        self.sessions = sessions
        self.decisions = decisions
        self.definition = definition
        self.parameters = parameters

    async def already_recorded(self, session_date: date, run_kind: str) -> bool:
        """Has this run already happened for this session?

        Idempotence is the journal's, not a flag's. It matters more here than
        on the rotation: a second scan of the same session does not merely
        waste work, it buys a second set of positions.
        """
        existing = await self.sessions.sessions_completed_on(
            self.definition.key, session_date, run_kind
        )
        return bool(existing)

    async def record(
        self,
        *,
        portfolio_id: int,
        session_date: Optional[date],
        run_kind: str,
        status: str,
        message: str,
        started_at: datetime,
        scan: Optional[ScanResult] = None,
        policy: Optional[BtstPolicy] = None,
        decisions: Optional[List[Decision]] = None,
        slots: Optional[int] = None,
        picked: Optional[int] = None,
    ) -> SessionRecord:
        decisions = list(decisions or [])
        record = await self.sessions.create(
            strategy_key=self.definition.key,
            portfolio_id=int(portfolio_id),
            session_date=session_date,
            run_kind=run_kind,
            status=status,
            started_at=started_at,
            completed_at=utc_now(),
            index_symbol=scan.index_symbol if scan else None,
            index_close=_money(scan.index_close) if scan else None,
            index_sma=_money(scan.index_sma) if scan else None,
            gate_on=scan.gate_on if scan else None,
            universe_size=(scan.counts.get("universe") if scan else None),
            tradable_count=(scan.counts.get("tradable") if scan else None),
            symbols_with_bars=(scan.counts.get("history") if scan else None),
            symbols_with_quotes=(scan.counts.get("quoted") if scan else None),
            candidate_count=(len(scan.candidates) if scan else None),
            slots=slots,
            picked_count=picked,
            filter_counts_json=(
                json.dumps(scan.counts) if scan and scan.counts else None
            ),
            ranking_json=(self._ranking_json(scan) if scan else None),
            parameters_json=self._parameters_json(policy),
            regime_enforced=(policy.enforce_regime if policy else None),
            fno_excluded=(policy.exclude_fno if policy else None),
            message=(message or "")[:500],
        )

        rows = [
            self._decision_row(record.id, decision, scan, policy)
            for decision in decisions
        ]
        written = await self.decisions.add_many(rows)

        return SessionRecord(
            session_id=record.id,
            session_date=session_date,
            run_kind=run_kind,
            status=status,
            decisions=written,
            message=(message or "")[:500],
        )

    def _decision_row(
        self,
        session_id: int,
        decision: Decision,
        scan: Optional[ScanResult],
        policy: Optional[BtstPolicy],
    ) -> Dict[str, Any]:
        return {
            "session_id": session_id,
            "symbol": decision.symbol,
            "security_id": decision.security_id,
            "action": decision.action,
            "rank": decision.rank,
            "price": _money(decision.price),
            "session_high": _money(decision.session_high),
            "session_low": _money(decision.session_low),
            "session_volume": (
                int(decision.session_volume)
                if decision.session_volume is not None
                else None
            ),
            "vol_ratio": _money(decision.vol_ratio),
            "clv": _money(decision.clv),
            "breakout_high": _money(decision.breakout_high),
            "momentum": _money(decision.momentum),
            "sma": _money(decision.sma),
            "turnover_20d": _money(decision.turnover),
            "quantity": decision.quantity,
            "order_id": decision.order_id,
            "reason": (decision.reason or "")[:500],
            # Stamped on EVERY row, action and non-action alike, so a
            # performance report can be split by the rules that were in force
            # without a two-hop join through the session.
            "regime_gate_on": scan.gate_on if scan else None,
            "regime_enforced": (policy.enforce_regime if policy else None),
            "fno_excluded": (policy.exclude_fno if policy else None),
            "fno_eligible": decision.fno_eligible,
        }

    def _ranking_json(self, scan: ScanResult) -> Optional[str]:
        """The candidates as they stood, read whole and never queried into."""
        if not scan.candidates:
            return None
        return json.dumps(
            [
                {
                    "rank": one.rank,
                    "symbol": one.symbol,
                    "price": round(one.price, 4),
                    "volRatio": round(one.vol_ratio, 4),
                    "clv": round(one.clv, 4),
                    "breakoutHigh": round(one.breakout_high, 4),
                    "aboveBreakout": round(one.above_breakout, 6),
                    "momentum": round(one.momentum, 6),
                    "sma": round(one.sma, 4),
                    "sessionHigh": round(one.session_high, 4),
                    "sessionLow": round(one.session_low, 4),
                    "sessionVolume": int(one.session_volume),
                    "fnoEligible": one.fno_eligible,
                }
                for one in scan.candidates
            ]
        )

    def _parameters_json(self, policy: Optional[BtstPolicy]) -> str:
        """B1-B16 as they stood, so a record stays explainable after a change.

        The whole block, not a subset: the point of storing it is that somebody
        reading this row in six months can tell whether the rule has moved
        since, and a subset only answers that for the fields somebody thought
        to include.
        """
        parameters = self.parameters
        return json.dumps(
            {
                "liquidityFloorRupees": parameters.liquidity_floor_rupees,
                "liquidityWindowSessions": parameters.liquidity_window_sessions,
                "priceFloor": parameters.price_floor,
                "breakoutLookbackSessions": parameters.breakout_lookback_sessions,
                "volumeWindowSessions": parameters.volume_window_sessions,
                "volumeMultiple": parameters.volume_multiple,
                "closeLocationMinimum": parameters.close_location_minimum,
                "trendSmaSessions": parameters.trend_sma_sessions,
                "momentumLookbackSessions": parameters.momentum_lookback_sessions,
                "momentumSkipSessions": parameters.momentum_skip_sessions,
                "momentumFloor": parameters.momentum_floor,
                "ranking": parameters.ranking,
                "slots": parameters.slots,
                "positionSizeDivisor": parameters.position_size_divisor,
                "minimumSessions": parameters.minimum_sessions,
                "regimeSmaSessions": parameters.regime.sma_sessions,
                "scanAt": parameters.schedule.scan_at,
                "exitAt": parameters.schedule.exit_at,
                "policy": policy.as_dict() if policy else None,
            }
        )


def rejection_decisions(scan: ScanResult, limit: int = MAX_REJECTION_ROWS) -> List[Decision]:
    """The near-misses, as decision rows, DEEPEST FIRST.

    Not every rejection. The universe is ~289 names and almost all of them fail
    at the first filter almost every day; a row each would be 289 rows a
    session saying "did not break out", which is noise that makes the
    interesting rows harder to find rather than easier. The funnel counts on
    the session carry the whole population; these are the names that got past
    the liquidity and price filters and still failed, which are the ones
    somebody looks for.

    **ORDER BEFORE THE CAP, and that is the whole point of this function.**
    It used to slice `scan.rejections` in scan order, which is the universe's
    order, which is ALPHABETICAL. On 2026-09-21 the funnel was 241 -> 7 at the
    breakout, so 234 names failed there; the forty rows went to 3MINDIA
    through BHARTIHEXA saying "not above its 55-day high", and the ONE name
    that cleared B4, B5, B6 and B7 and died at B8 -- the only row anybody
    would have looked for -- was crowded out and lost. The funnel counts said
    a name had reached B8 and the journal could not say which.
    """
    from src.btst.database.db_models.btst_session_model import ACTION_SKIPPED

    interesting = [
        one for one in scan.rejections if one.stage in REJECTION_STAGES_WORTH_A_ROW
    ]
    # Deepest first, then alphabetical so a session's rows are stable between
    # runs. `-depth` rather than `reverse=True` so the symbol tiebreak stays
    # ascending.
    interesting.sort(key=lambda one: (-_STAGE_DEPTH.get(one.stage, 0), one.symbol))
    return [
        Decision(
            symbol=one.symbol,
            action=ACTION_SKIPPED,
            # The STAGE, spelled out. "Did not qualify: below the volume
            # multiple" does not say how far the name got; "Reached B5 volume
            # multiple" does, and how far it got is the question these rows
            # exist to answer.
            reason=(
                f"Did not qualify at "
                f"{_STAGE_LABEL.get(one.stage, one.stage)}: {one.reason}"
            ),
            security_id=one.security_id,
            price=one.price,
            session_high=one.session_high,
            session_low=one.session_low,
            session_volume=one.session_volume,
            vol_ratio=one.vol_ratio,
            clv=one.clv,
            breakout_high=one.breakout_high,
            momentum=one.momentum,
            sma=one.sma,
            turnover=one.turnover,
        )
        for one in interesting[:limit]
    ]
