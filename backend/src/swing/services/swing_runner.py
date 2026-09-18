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
from datetime import date
from decimal import Decimal
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
from src.swing.services.rebalance_planner import (
    Holding,
    RebalancePlanner,
    effective_gate,
)
from src.swing.services.stop_service import StopService
from src.swing.services.swing_parameters import SwingParameters

logger = get_logger("swing.runner")

# The nightly run does not size a position, so it has no equity to pass to the
# shared "why were there no entries" logic. A sentinel rather than None,
# because None there means "equity could not be computed", which is a real
# answer the rebalance records and the nightly run must not claim.
_NIGHTLY_EQUITY_NOT_NEEDED = Decimal("0")


class SwingRunError(Exception):
    pass


class SwingRunner:
    """Runs one nightly decision and journals it."""

    def __init__(
        self,
        ranking: RankingService,
        journal: SwingJournalService,
        definition: StrategyDefinition,
        parameters: Optional[SwingParameters] = None,
        stops: Optional["StopService"] = None,
    ):
        self.ranking = ranking
        self.journal = journal
        self.definition = definition
        self.parameters = parameters or SwingParameters.from_definition(definition)
        # P15's nightly ratchet. Optional so the decision half of this runner
        # can be exercised without a stop table; the scheduler always supplies
        # one, because a nightly run that decides and does not move the stops
        # is half a nightly run.
        self.stops = stops

    def _equity_segment(self) -> str:
        for instrument_set in self.definition.instrument_sets:
            if instrument_set.from_universe:
                return instrument_set.exchange_segment
        return self.definition.exchange_segment

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

        snapshot = await self.ranking.session_snapshot(as_of=as_of)
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

        # P15. Every open position's stop is raised on this session's close,
        # BEFORE the record is written, so the journal entry for the session
        # carries the stop moves it caused.
        if self.stops is not None:
            ratchet = await self.stops.ratchet_all(
                session_date, self._equity_segment()
            )
            decisions.extend(ratchet.moved)
            logger.info(
                "Swing nightly %s: %s stop(s) recomputed on the close of %s, "
                "%s left where they were.",
                self.definition.key, ratchet.moved_count,
                session_date.isoformat(), ratchet.unchanged,
            )

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
        """What the strategy would do at the next open, with no money involved.

        The sell half is the REBALANCE'S OWN PLANNER, run against the same
        snapshot, so the nightly record and the morning's orders cannot
        disagree about which holdings are due out or why. The buy half is
        deliberately not the planner: at 18:15 there is no live price to size
        against, so this records WHICH names would be entered and leaves the
        quantities to the rebalance, where a price exists.
        """
        parameters = self.parameters
        gate = effective_gate(snapshot, parameters)
        planner = RebalancePlanner(parameters, self.definition.key)
        held = {holding.symbol: holding for holding in holdings}
        decisions: List[Decision] = []

        # 1. Every holding, with the rank that justifies keeping or selling it.
        #    Recorded as HELD -- it IS still held tonight -- with a reason that
        #    says what is due to happen at the next open.
        sell_plan = planner.plan_sells(snapshot, list(holdings))
        for intent in sell_plan.sells:
            decisions.append(
                Decision(
                    symbol=intent.symbol,
                    action=ACTION_HELD,
                    rank=intent.rank,
                    quantity=intent.quantity,
                    reason=f"Due at the next open -- {intent.reason}",
                )
            )
        decisions.extend(sell_plan.holds)
        decisions.sort(key=lambda one: one.symbol)

        # 2. Why nothing is being entered, or which names would be.
        blocked = planner._entry_block_reason(  # noqa: SLF001 - one decision, one place
            snapshot,
            gate,
            # The book as it will be AFTER the exits above: a rotation exit
            # frees its slot for the same session's entry, which is what the
            # backtest's "exits fill first" ordering means.
            len(held) - len(sell_plan.sells),
            # The nightly run does not size, so equity being unknown is not a
            # reason to record "nothing entered" here -- the rebalance is where
            # that bites, and it says so there.
            equity=_NIGHTLY_EQUITY_NOT_NEEDED,
            unmarked_reason=None,
        )
        if blocked is not None:
            decisions.append(
                Decision(symbol="*", action=ACTION_NOT_ENTERED, reason=blocked)
            )
            return decisions

        remaining = {symbol for symbol in held} - {
            intent.symbol for intent in sell_plan.sells
        }
        openings = min(gate.slots, parameters.max_positions) - len(remaining)
        taken = 0
        for candidate in snapshot.candidates:
            if candidate.symbol in remaining:
                continue
            if taken >= openings:
                decisions.append(
                    Decision(
                        symbol=candidate.symbol,
                        action=ACTION_SKIPPED,
                        rank=candidate.rank,
                        score=candidate.score,
                        reason=planner._slots_full_reason(  # noqa: SLF001
                            snapshot, gate, len(remaining)
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
                        f"{candidate.momentum * 100:.1f}%. The quantity is "
                        f"decided at the open, against a live price."
                    ),
                )
            )
            taken += 1

        return decisions

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
