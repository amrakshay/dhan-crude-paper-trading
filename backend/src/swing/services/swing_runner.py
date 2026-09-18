"""The nightly run: decide, and write down what was decided.

This is the whole of the strategy's decision-making. It computes the snapshot,
works out what follows from it for every held name and every candidate, and
records all of it -- including, and especially, the runs where the answer is
"buy nothing".

**It places no orders.** Execution is a separate step with its own run kind, so
that the decision and the act of trading on it are separately recorded and
separately gated. With the gate off, which it has been since 2026-02-27, the
correct behaviour is to hold 100% cash and say so every session with the
numbers -- and that is the path this exercises.

Two properties the scheduler depends on:

* **Idempotent per session.** Running twice for one session date does not
  decide twice. A restart at 18:20 must not re-decide what was decided at
  18:15.
* **A closed market is recorded, not skipped.** "The job did not run" and "the
  job ran and there was no session" are different facts, and the missed-run
  detector has to tell them apart.
"""
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional

from src.core.time_utils import utc_now
from src.logging_config import get_logger
from src.strategies.services.strategy_definition import StrategyDefinition
from src.swing.database.db_models.swing_session_model import (
    ACTION_BOUGHT,
    ACTION_HELD,
    ACTION_NOT_ENTERED,
    ACTION_SKIPPED,
    RUN_NIGHTLY,
    STATUS_COMPLETED,
    STATUS_SKIPPED,
)
from src.swing.services.journal_service import Decision, SessionRecord, SwingJournalService
from src.swing.services.ranking_service import RankingService, RankingSnapshot
from src.swing.services.swing_parameters import SwingParameters

logger = get_logger("swing.runner")


class SwingRunError(Exception):
    pass


@dataclass
class Holding:
    """A position as the strategy sees it. Money stays `Decimal` elsewhere."""

    symbol: str
    security_id: str
    quantity: int


class SwingRunner:
    """Runs one nightly decision and journals it."""

    def __init__(
        self,
        ranking: RankingService,
        journal: SwingJournalService,
        definition: StrategyDefinition,
        parameters: Optional[SwingParameters] = None,
    ):
        self.ranking = ranking
        self.journal = journal
        self.definition = definition
        self.parameters = parameters or SwingParameters.from_definition(definition)

    async def run_nightly(
        self,
        portfolio_id: int,
        holdings: Optional[List[Holding]] = None,
        as_of: Optional[date] = None,
        force: bool = False,
    ) -> SessionRecord:
        """Decide one session and record it. Places no orders."""
        started = utc_now()
        holdings = list(holdings or [])
        held_symbols = [holding.symbol for holding in holdings]

        snapshot = await self.ranking.snapshot(as_of=as_of)
        regime = snapshot.regime

        if regime.bar_date is None:
            # No index bar means no session to decide. Recorded so the run is
            # visible; the calendar is the index's own dates.
            return await self.journal.record_skipped(
                strategy_key=self.definition.key,
                portfolio_id=portfolio_id,
                run_kind=RUN_NIGHTLY,
                session_date=as_of or started.date(),
                message=regime.reason,
            )

        session_date = snapshot.as_of
        if not force:
            already = await self.journal.sessions.sessions_completed_on(
                self.definition.key, session_date, RUN_NIGHTLY
            )
            if already:
                existing = already[0]
                logger.info(
                    "Swing nightly for %s was already recorded (session %s); not "
                    "deciding twice.",
                    self.definition.key, session_date.isoformat(),
                )
                return SessionRecord(
                    session_id=existing.id,
                    session_date=session_date,
                    run_kind=RUN_NIGHTLY,
                    status=existing.status,
                    decisions=0,
                    message="Already recorded for this session; nothing re-decided.",
                    extras={"idempotent": True},
                )

        decisions = self._decide(snapshot, holdings)

        return await self.journal.record_session(
            strategy_key=self.definition.key,
            portfolio_id=portfolio_id,
            run_kind=RUN_NIGHTLY,
            snapshot=snapshot,
            decisions=decisions,
            status=STATUS_COMPLETED,
            started_at=started,
            held_symbols=held_symbols,
        )

    # --- the decision itself ----------------------------------------------
    def _decide(
        self, snapshot: RankingSnapshot, holdings: List[Holding]
    ) -> List[Decision]:
        parameters = self.parameters
        regime = snapshot.regime
        held = {holding.symbol: holding for holding in holdings}
        decisions: List[Decision] = []

        # 1. Every holding, with the rank that justifies keeping or selling it.
        for symbol, holding in sorted(held.items()):
            rank = snapshot.rank_of(symbol)
            if not regime.gate_on:
                decisions.append(
                    Decision(
                        symbol=symbol,
                        action=ACTION_HELD,
                        rank=rank,
                        quantity=holding.quantity,
                        reason=(
                            f"Regime exit due at the next open: {regime.index_symbol} "
                            f"is below its {parameters.regime.sma_sessions}-session SMA."
                        ),
                    )
                )
                continue
            if rank is None:
                decisions.append(
                    Decision(
                        symbol=symbol,
                        action=ACTION_HELD,
                        rank=None,
                        quantity=holding.quantity,
                        reason=(
                            "Rotation exit due at the next open: no longer ranked "
                            f"({snapshot.skipped.get(symbol, 'failed a filter')})."
                        ),
                    )
                )
                continue
            if rank > parameters.rotation_exit_rank:
                decisions.append(
                    Decision(
                        symbol=symbol,
                        action=ACTION_HELD,
                        rank=rank,
                        quantity=holding.quantity,
                        reason=(
                            f"Rotation exit due at the next open: rank {rank} > "
                            f"{parameters.rotation_exit_rank}."
                        ),
                    )
                )
                continue
            decisions.append(
                Decision(
                    symbol=symbol,
                    action=ACTION_HELD,
                    rank=rank,
                    quantity=holding.quantity,
                    reason=f"Held: rank {rank} is within {parameters.rotation_exit_rank}.",
                )
            )

        # 2. Why nothing is being entered, or which names would be.
        blocked = self._entry_block_reason(snapshot, len(held))
        if blocked is not None:
            decisions.append(
                Decision(symbol="*", action=ACTION_NOT_ENTERED, reason=blocked)
            )
            return decisions

        openings = min(snapshot.slots, parameters.max_positions) - len(held)
        taken = 0
        for candidate in snapshot.candidates:
            if candidate.symbol in held:
                continue
            if taken >= openings:
                decisions.append(
                    Decision(
                        symbol=candidate.symbol,
                        action=ACTION_SKIPPED,
                        rank=candidate.rank,
                        score=candidate.score,
                        reason=(
                            f"Skipped: slots full ({snapshot.slots} allowed by a "
                            f"breadth of {snapshot.breadth * 100:.1f}%, "
                            f"{len(held)} already held)."
                        ),
                    )
                )
                if candidate.rank >= parameters.max_positions:
                    break
                continue
            decisions.append(
                Decision(
                    symbol=candidate.symbol,
                    action=ACTION_BOUGHT,
                    rank=candidate.rank,
                    score=candidate.score,
                    reason=(
                        f"Entry due at the next open: rank {candidate.rank}, "
                        f"score {candidate.score:.1f}, momentum "
                        f"{candidate.momentum * 100:.1f}%."
                    ),
                )
            )
            taken += 1

        return decisions

    def _entry_block_reason(
        self, snapshot: RankingSnapshot, held_count: int
    ) -> Optional[str]:
        """Why no new position is opened, or None if entries are allowed.

        Each branch carries the numbers, because a record that says only "no
        entries" cannot be checked against anything later.
        """
        parameters = self.parameters
        regime = snapshot.regime

        if not regime.gate_on:
            shortfall = regime.shortfall_percent
            gap = f", needs {shortfall:.1f}% to reclaim it" if shortfall is not None else ""
            if parameters.off_gate.enabled:
                return (
                    f"Off-gate variant is ENABLED, which the owner's own research "
                    f"found ends lower than holding cash. "
                    f"{regime.index_symbol} {regime.close:,.1f} below SMA "
                    f"{regime.sma200:,.1f}{gap}. Not yet implemented, so nothing "
                    f"is entered."
                )
            return (
                f"Not entered: the regime gate is OFF. {regime.index_symbol} "
                f"{regime.close:,.1f} is below its "
                f"{parameters.regime.sma_sessions}-session SMA "
                f"{regime.sma200:,.1f}{gap}. The book holds 100% cash by design."
            )

        if not regime.entries_allowed:
            shown = (
                "undefined"
                if regime.return_over_window is None
                else f"{regime.return_over_window * 100:.2f}%"
            )
            return (
                f"Not entered: the {parameters.regime.entry_return_sessions}-session "
                f"return filter blocks new entries. {regime.index_symbol} return is "
                f"{shown}, which is not above "
                f"{parameters.regime.entry_return_minimum:.2%}. Existing positions "
                f"are unaffected."
            )

        if snapshot.breadth is None:
            return (
                "Not entered: breadth could not be measured -- no name in the "
                "universe passed the liquidity and price floors with a defined "
                "SMA200. Refusing to size from a breadth that is unknown rather "
                "than treating it as zero."
            )

        if snapshot.slots is None or snapshot.slots <= 0:
            return (
                f"Not entered: breadth {snapshot.breadth * 100:.1f}% "
                f"({snapshot.above_sma_count} of {snapshot.liquid_count} above "
                f"their own SMA200) allows {snapshot.slots} slots. The graded "
                f"breadth ramp buys nothing at or below "
                f"{parameters.breadth_lower * 100:.0f}%."
            )

        if held_count >= min(snapshot.slots, parameters.max_positions):
            return (
                f"Not entered: {held_count} positions held against "
                f"{snapshot.slots} slots allowed by a breadth of "
                f"{snapshot.breadth * 100:.1f}%."
            )

        if not snapshot.candidates:
            return (
                f"Not entered: no name passed the filters. "
                f"{snapshot.liquid_count} liquid, "
                f"{snapshot.above_sma_count} above their own SMA200, none clearing "
                f"the {parameters.momentum_floor:.0%} momentum floor."
            )

        return None

    # --- reporting ---------------------------------------------------------
    @staticmethod
    def summarise(record: SessionRecord, snapshot: Optional[RankingSnapshot]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "sessionId": record.session_id,
            "sessionDate": record.session_date.isoformat(),
            "runKind": record.run_kind,
            "status": record.status,
            "decisions": record.decisions,
            "message": record.message,
        }
        if snapshot is not None:
            payload["snapshot"] = snapshot.as_dict()
        return payload
