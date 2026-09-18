"""Writing the decision journal.

One entry point, `record_session`, used by every run: the nightly job, the
rebalance and a manual recomputation. It takes a `RankingSnapshot` -- the thing
the strategy actually saw -- and the decisions taken from it, and writes both
in one append-only transaction.

Two rules, borrowed from `order_events` and `cash_ledger`:

* **Append-only.** Nothing here updates a record. A run that reconsiders writes
  a new session; a correction is a new decision.
* **Store the inputs, not just the conclusion.** Every number the gate, the
  breadth and the slot count were computed from is a column, and the ranking
  and the configuration in force are stored whole. A record that cannot be
  checked against the data that produced it is an opinion.
"""
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from src.core.time_utils import utc_now
from src.logging_config import get_logger
from src.swing.database.db_models.swing_session_model import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    SwingSession,
)
from src.swing.database.db_operations.swing_session_repository import (
    SwingDecisionRepository,
    SwingSessionRepository,
)
from src.swing.services.ranking_service import RankingSnapshot

logger = get_logger("swing.journal")

# How many ranked names to store per session. Every HELD name's rank is stored
# regardless (see `record_session`), because a rotation exit is justified by a
# rank and the operator must be able to see it; beyond that the tail is
# unbounded and rarely read.
RANKING_STORED = 60


def _money(value: Optional[float]) -> Optional[Decimal]:
    """Indicators are float; the journal's numeric columns are `Money`."""
    if value is None:
        return None
    return Decimal(str(round(float(value), 4)))


@dataclass
class Decision:
    """One decision about one symbol, taken or deliberately not taken."""

    symbol: str
    action: str
    reason: str
    rank: Optional[int] = None
    score: Optional[float] = None
    quantity: Optional[int] = None
    reference_price: Optional[Decimal] = None
    order_id: Optional[int] = None

    def as_row(self, session_id: int) -> Dict[str, Any]:
        return {
            "session_id": session_id,
            "symbol": self.symbol,
            "action": self.action,
            "reason": self.reason[:500],
            "rank": self.rank,
            "score": _money(self.score),
            "quantity": self.quantity,
            "reference_price": self.reference_price,
            "order_id": self.order_id,
        }


@dataclass
class SessionRecord:
    """What was written, so a caller can report it without re-reading."""

    session_id: int
    session_date: date
    run_kind: str
    status: str
    decisions: int = 0
    message: Optional[str] = None
    extras: Dict[str, Any] = field(default_factory=dict)


class SwingJournalService:
    def __init__(self, sessions: SwingSessionRepository, decisions: SwingDecisionRepository):
        self.sessions = sessions
        self.decisions = decisions

    async def record_session(
        self,
        strategy_key: str,
        portfolio_id: int,
        run_kind: str,
        snapshot: Optional[RankingSnapshot],
        decisions: Optional[List[Decision]] = None,
        status: str = STATUS_COMPLETED,
        message: Optional[str] = None,
        session_date: Optional[date] = None,
        started_at=None,
        held_symbols: Optional[List[str]] = None,
    ) -> SessionRecord:
        """Write one run and its decisions. Never updates an existing row."""
        decisions = list(decisions or [])
        held_symbols = list(held_symbols or [])
        now = utc_now()

        resolved_date = session_date or (snapshot.as_of if snapshot else None)
        if resolved_date is None:
            raise ValueError(
                "A journal entry needs the session it decided. Pass session_date "
                "when there is no snapshot to take it from."
            )

        fields: Dict[str, Any] = {
            "strategy_key": strategy_key,
            "portfolio_id": int(portfolio_id),
            "session_date": resolved_date,
            "run_kind": run_kind,
            "status": status,
            "started_at": started_at or now,
            "completed_at": now,
            "message": (message or "")[:500] or None,
        }

        if snapshot is not None:
            regime = snapshot.regime
            fields.update(
                {
                    "index_symbol": regime.index_symbol,
                    "index_close": _money(regime.close),
                    "index_sma": _money(regime.sma200),
                    "gate_on": regime.gate_on,
                    "index_return_over_window": _money(regime.return_over_window),
                    "entries_allowed": regime.entries_allowed,
                    "breadth_above": snapshot.above_sma_count,
                    "breadth_liquid": snapshot.liquid_count,
                    "slots": snapshot.slots,
                    "universe_size": snapshot.universe_size,
                    "symbols_with_bars": snapshot.symbols_with_bars,
                    "candidate_count": len(snapshot.candidates),
                    "ranking_json": json.dumps(
                        self._ranking_payload(snapshot, held_symbols)
                    ),
                    "parameters_json": json.dumps(snapshot.parameters_digest),
                    "filter_counts_json": json.dumps(
                        self._filter_counts(snapshot)
                    ),
                }
            )
            if not fields["message"]:
                fields["message"] = regime.reason[:500]

        record: SwingSession = await self.sessions.create(**fields)
        session_id = record.id
        written = await self.decisions.add_many(
            [decision.as_row(session_id) for decision in decisions]
        )

        logger.info(
            "Swing journal: %s %s session %s recorded as %s with %s decision(s)%s",
            strategy_key, run_kind, resolved_date.isoformat(), status, written,
            f" -- {fields['message']}" if fields.get("message") else "",
        )
        return SessionRecord(
            session_id=session_id,
            session_date=resolved_date,
            run_kind=run_kind,
            status=status,
            decisions=written,
            message=fields.get("message"),
        )

    @staticmethod
    def _ranking_payload(
        snapshot: RankingSnapshot, held_symbols: List[str]
    ) -> Dict[str, Any]:
        """The top N, plus every HELD name's rank whatever it is.

        A holding that has fallen to rank 40 is exactly the row a rotation exit
        has to be explained by, and truncating the list at 15 would throw it
        away in the one case it matters.
        """
        top = [candidate.as_dict() for candidate in snapshot.candidates[:RANKING_STORED]]
        shown = {entry["symbol"] for entry in top}
        held = []
        for symbol in held_symbols:
            if symbol in shown:
                continue
            rank = snapshot.rank_of(symbol)
            held.append(
                {
                    "symbol": symbol,
                    "rank": rank,
                    "ranked": rank is not None,
                    "skipReason": snapshot.skipped.get(symbol),
                }
            )
        return {"top": top, "held": held, "storedTop": RANKING_STORED}

    @staticmethod
    def _filter_counts(snapshot: RankingSnapshot) -> Dict[str, int]:
        """How many names each filter removed, by reason."""
        counts: Dict[str, int] = {}
        for reason in snapshot.skipped.values():
            counts[reason] = counts.get(reason, 0) + 1
        counts["candidates"] = len(snapshot.candidates)
        return counts

    async def record_skipped(
        self,
        strategy_key: str,
        portfolio_id: int,
        run_kind: str,
        session_date: date,
        message: str,
    ) -> SessionRecord:
        """A run that had nothing to decide -- a closed market, usually.

        Recorded rather than silently not happening, because "the job did not
        run" and "the job ran and there was no session" are different facts and
        the missed-run detector has to tell them apart.
        """
        return await self.record_session(
            strategy_key=strategy_key,
            portfolio_id=portfolio_id,
            run_kind=run_kind,
            snapshot=None,
            status=STATUS_SKIPPED,
            message=message,
            session_date=session_date,
        )

    async def record_failure(
        self,
        strategy_key: str,
        portfolio_id: int,
        run_kind: str,
        session_date: date,
        message: str,
    ) -> SessionRecord:
        return await self.record_session(
            strategy_key=strategy_key,
            portfolio_id=portfolio_id,
            run_kind=run_kind,
            snapshot=None,
            status=STATUS_FAILED,
            message=message,
            session_date=session_date,
        )
