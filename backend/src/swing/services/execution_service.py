"""The rebalance: act on the decision, through the ordinary paper-order path.

At the open, sell what the rule says to sell, then buy what it says to buy, and
write down every one of those acts and every non-act. This is the only module
in `src/swing/` that places an order.

Five rules it exists to keep.

* **Sells fill before buys are even PLANNED.** The backtest processes pending
  exits before pending entries and sizing is cash-constrained (specification
  section 6, mechanic 4). Planning both against one balance snapshot would
  reject buys the backtest funded out of that morning's sales, and the symptom
  would look like a bug in the funds code rather than in the ordering.

* **No privileged fill path.** Every order goes through `submit_paper_order`
  like a click on the chart does: it crosses the spread, walks the five visible
  levels and partially fills when depth runs out. Fills will differ from the
  backtest's `open x (1 +/- 0.05%)` model, and measuring that difference is the
  point (specification section 14.3).

* **Arming gates ORDERS, not decisions.** An unarmed run computes the same
  snapshot, produces the same plan and writes the same journal rows; what it
  does not do is place anything. The rows are identical down to the reason
  sentence, and `order_id` being null is what distinguishes them -- so an armed
  and an unarmed run can be diffed and only the orders differ.

* **The book is warmed first.** The fill simulator needs a depth book for a
  name BEFORE the order, not after it. `FeedManager.pin_instruments()` adds the
  handful about to be traded to the `positions` subscription; the scheduler
  calls `warm_book` a few minutes ahead and the rebalance pins again in case it
  did not. Roughly thirty instruments, never five hundred.

* **Every order carries its reason.** It goes onto the PLACED order event, and
  the journal ties the decision to the order through `SwingDecision.order_id`.

* **ORDERS ONLY INSIDE CONTINUOUS TRADING.** Analysis may run at any hour --
  ranking, deciding, ratcheting stops and journalling are all fine at midnight.
  Placing is not: this simulator fills against a depth book, and outside the
  session that book holds prices nobody can trade at. The check is
  `market_clock.can_execute_continuously`, made PER INSTRUMENT immediately
  before each order rather than once for the run, because F&O eligibility
  changes the answer after 15:15 and a run that starts at 15:14 can cross the
  boundary mid-list. A refused order is journalled with its reason and the time.

  The intents are NOT queued for the next open. The next rebalance re-decides
  from fresh bars and a fresh book, and replaying yesterday's intent is how you
  trade a decision nobody would take today. This deliberately differs from the
  stop monitor, which DOES defer: a triggered stop is a fact about a position
  that has already happened, not a fresh opinion, so it waits and fires at the
  next open.

  The guard lives HERE rather than in `submit_paper_order`. A generic guard
  would be harder to bypass, but it would change MCX crude and the chart's
  one-click entry -- neither of which asked for it -- and it could not journal
  a swing decision, which is the half of this requirement that makes the
  refusal auditable. If it is ever made generic it must be a per-strategy
  policy read from the YAML, with the MCX module's behaviour unchanged;
  `tests/test_swing_does_not_disturb_crude.py` pins exactly that.
"""
import asyncio
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from src.constants import OrderSide, OrderType
from src.core.time_utils import utc_now
from src.logging_config import get_logger
from src.strategies.services import market_clock
from src.strategies.services.strategy_definition import StrategyDefinition
from src.swing.database.db_models.swing_session_model import (
    ACTION_BOUGHT,
    ACTION_NOT_ENTERED,
    ACTION_SKIPPED,
    ACTION_SOLD,
    RUN_REBALANCE,
    STATUS_COMPLETED,
)
from src.swing.database.db_models.swing_stop_model import (
    EXIT_REGIME,
    EXIT_ROTATION,
)
from src.swing.services.gate_policy import GatePolicy, resolve_gate_policy
from src.swing.services.journal_service import Decision, SessionRecord, SwingJournalService
from src.swing.services.ranking_service import RankingService, RankingSnapshot
from src.swing.services.rebalance_planner import (
    BuyIntent,
    Holding,
    RebalancePlanner,
    SellIntent,
    effective_gate,
)
from src.swing.services.stop_service import StopService
from src.swing.services.swing_parameters import SwingParameters

logger = get_logger("swing.execution")


def max_staleness_days() -> int:
    """How many calendar days of staleness the rebalance will still trade on."""
    from src import config_utils

    return config_utils.get_property_value_int("swing.max_bar_staleness_days", 5)


def staleness_reason(session_date: date) -> Optional[str]:
    """Why the stored bars are too old to trade on, or None.

    Measured in CALENDAR days rather than sessions, deliberately: the only
    calendar this application has is the stored index bars, and those are
    precisely what is in doubt here. Five days covers a Thursday close followed
    by a long weekend; a longer exchange holiday will refuse and say so, which
    is the safe direction.

    Module-level so the health tab can show the operator the SAME sentence the
    rebalance would refuse with, rather than a second description of the same
    rule that could drift from it.
    """
    from src.core.time_utils import ist_today

    behind = (ist_today() - session_date).days
    limit = max_staleness_days()
    if behind <= limit:
        return None
    return (
        f"Refusing to rebalance: the newest stored session is "
        f"{session_date.isoformat()}, {behind} day(s) ago, against a limit "
        f"of {limit}. The daily-bar refresh has not run, and trading on a "
        f"ranking computed from prices that old would be acting on a market "
        f"that no longer exists. Nothing was placed. Run "
        f"scripts/refresh_daily_bars.py, or let the nightly job run."
    )[:500]


class SwingExecutionError(Exception):
    pass


@dataclass
class RebalanceOutcome:
    record: Optional[SessionRecord] = None
    armed: bool = False
    sells_placed: int = 0
    buys_placed: int = 0
    sells_planned: int = 0
    buys_planned: int = 0
    # Orders an ARMED run decided on and did not place because the market was
    # not in continuous trading. Counted separately from "planned but not
    # placed" for any other reason, because the decision was valid and it is
    # the execution that was not.
    refused_outside_hours: int = 0
    equity: Optional[Decimal] = None
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "sessionId": self.record.session_id if self.record else None,
            "sessionDate": (
                self.record.session_date.isoformat() if self.record else None
            ),
            "status": self.record.status if self.record else None,
            "armed": self.armed,
            "sellsPlanned": self.sells_planned,
            "sellsPlaced": self.sells_placed,
            "buysPlanned": self.buys_planned,
            "buysPlaced": self.buys_placed,
            "refusedOutsideHours": self.refused_outside_hours,
            "equity": str(self.equity) if self.equity is not None else None,
            "message": self.record.message if self.record else None,
            "notes": list(self.notes),
        }


class SwingExecutionService:
    """One rebalance, end to end."""

    def __init__(
        self,
        session,
        definition: StrategyDefinition,
        ranking: RankingService,
        journal: SwingJournalService,
        stops: StopService,
        parameters: Optional[SwingParameters] = None,
        book=None,
        policy: Optional[GatePolicy] = None,
        clock=None,
    ):
        self.session = session
        self.definition = definition
        self.ranking = ranking
        self.journal = journal
        self.stops = stops
        self.parameters = parameters or SwingParameters.from_definition(definition)
        # Which of this strategy's own rules are being enforced, resolved ONCE
        # when the service is built and carried as a value from here on. Not
        # re-read mid-run: a rebalance decided half under one policy and half
        # under another is not one anybody can audit. See `gate_policy.py`.
        self.policy = policy or resolve_gate_policy(definition, self.parameters)
        self.planner = RebalancePlanner(
            self.parameters, definition.key, self.policy
        )
        self._book = book
        # What time it is, in IST. Injectable so the market-hours guard below
        # can be exercised at 22:00 and at 15:20 without waiting for either --
        # and so the suite does not quietly change behaviour depending on when
        # it is run. Production passes nothing and gets the wall clock.
        self._clock = clock

    # --- collaborators, reached inside methods (backend/CLAUDE.md section 1) --
    @property
    def book(self):
        if self._book is None:
            from src.market.services.feed_manager import get_feed_manager

            self._book = get_feed_manager().book
        return self._book

    def _balance_service(self):
        from src.portfolios.services.balance_service import BalanceService

        return BalanceService(self.session, book=self._book)

    def _order_service(self):
        from src.instruments.database.db_operations.instrument_repository import (
            InstrumentRepository,
        )
        from src.orders.database.db_operations.order_repository import OrderRepository
        from src.orders.services.order_service import OrderService
        from src.positions.database.db_operations.position_repository import (
            PositionRepository,
        )

        return OrderService(
            OrderRepository(self.session),
            InstrumentRepository(self.session),
            PositionRepository(self.session),
            book=self._book,
        )

    def _instruments(self):
        from src.instruments.database.db_operations.instrument_repository import (
            InstrumentRepository,
        )

        return InstrumentRepository(self.session)

    def _equity_segment(self) -> str:
        for instrument_set in self.definition.instrument_sets:
            if instrument_set.from_universe:
                return instrument_set.exchange_segment
        return self.definition.exchange_segment

    # --- warming the book ---------------------------------------------------
    async def warm_book(
        self, as_of: Optional[date] = None, snapshot: Optional[RankingSnapshot] = None
    ) -> Dict[str, Any]:
        """Subscribe the names this rebalance might trade, ahead of trading them.

        The fill simulator has nothing to fill against until a depth book
        exists, and a subscription takes a moment to produce one. Pinning the
        top of the ranking -- not the universe -- keeps this at about thirty
        instruments against a connection budget of five thousand.

        Idempotent and safe to call twice; the scheduler calls it a few minutes
        before the open and `run_rebalance` calls it again in case the process
        was restarted in between.
        """
        from src.market.services.feed_manager import get_feed_manager

        snapshot = snapshot or await self.ranking.session_snapshot(as_of=as_of)
        gate = effective_gate(snapshot, self.parameters)
        # Deliberately wider than the slot count: names are skipped for want of
        # cash or a price, and the next one down has to be tradable too.
        wanted = min(
            max(int(gate.slots or 0), 0) + self.parameters.max_positions,
            len(snapshot.candidates),
        )
        symbols = [candidate.symbol for candidate in snapshot.candidates[:wanted]]
        security_ids = await self._security_ids(symbols)

        manager = get_feed_manager()
        manager.pin_instruments(self.definition.key, set(security_ids.values()))
        effect = await manager.resync()
        logger.info(
            "Warmed the book for %s: pinned %s candidate(s) ahead of the "
            "rebalance -- %s",
            self.definition.key, len(security_ids), effect,
        )
        return {
            "pinned": sorted(security_ids),
            "pinnedCount": len(security_ids),
            "effect": effect,
        }

    async def _unpin(self) -> None:
        """Give the connection back. What is held stays subscribed anyway."""
        from src.market.services.feed_manager import get_feed_manager

        manager = get_feed_manager()
        manager.pin_instruments(self.definition.key, set())
        try:
            await manager.resync()
        except Exception:  # noqa: BLE001 - unpinning must not fail a rebalance
            logger.exception(
                "Could not resync after unpinning the rebalance candidates; the "
                "extra subscriptions stay until the next resync."
            )

    # --- the rebalance ------------------------------------------------------
    async def run_rebalance(
        self,
        portfolio_id: int,
        as_of: Optional[date] = None,
        force: bool = False,
        warm_seconds: float = 0.0,
    ) -> RebalanceOutcome:
        """Sell, then buy, then record. Places nothing unless ARMED."""
        from src.strategies.services.strategy_registry import get_strategy_registry

        started = utc_now()
        registry = get_strategy_registry()
        outcome = RebalanceOutcome(armed=registry.is_armed(self.definition.key))

        if not registry.is_enabled(self.definition.key):
            raise SwingExecutionError(
                f"The {self.definition.label} strategy is switched off, so it "
                f"decides nothing and trades nothing. Switch it back on from "
                f"Strategies & Features."
            )

        snapshot = await self.ranking.session_snapshot(as_of=as_of)
        if snapshot.regime.bar_date is None:
            record = await self.journal.record_skipped(
                strategy_key=self.definition.key,
                portfolio_id=portfolio_id,
                run_kind=RUN_REBALANCE,
                session_date=as_of or started.date(),
                message=snapshot.regime.reason,
            )
            outcome.record = record
            return outcome

        session_date = snapshot.as_of

        # THE BARS MUST BE FRESH. The rebalance executes at the open of the
        # session AFTER the one it decided, on signals from that session's
        # close -- so a session date days old means the bar refresh has not
        # run and the decision would be taken on prices that have since moved.
        # The nightly run has no such problem: recording what the stored data
        # says is exactly its job. Placing orders on it is not.
        stale = self._staleness_reason(session_date)
        if stale is not None:
            logger.error("Swing rebalance refused for %s: %s", self.definition.key, stale)
            outcome.notes.append(stale)
            outcome.record = await self.journal.record_skipped(
                strategy_key=self.definition.key,
                portfolio_id=portfolio_id,
                run_kind=RUN_REBALANCE,
                session_date=session_date,
                message=stale,
            )
            return outcome

        if not force:
            already = await self.journal.sessions.sessions_completed_on(
                self.definition.key, session_date, RUN_REBALANCE
            )
            if already:
                existing = already[0]
                logger.info(
                    "Swing rebalance for %s was already recorded (session %s); "
                    "not trading twice.",
                    self.definition.key, session_date.isoformat(),
                )
                outcome.record = SessionRecord(
                    session_id=existing.id,
                    session_date=session_date,
                    run_kind=RUN_REBALANCE,
                    status=existing.status,
                    decisions=0,
                    message="Already rebalanced for this session; nothing re-traded.",
                    extras={"idempotent": True},
                )
                return outcome

        segment = self._equity_segment()
        # Resolved ONCE for the whole run, from the policy this service was
        # built with. Everything below -- the sells, the buys, the journal
        # stamp and each position's own entry record -- reads this same value.
        gate = self.planner.gate(snapshot)
        decisions: List[Decision] = []
        holdings = await self._holdings(portfolio_id)
        held_symbols = [holding.symbol for holding in holdings]

        # --- 1. sells ------------------------------------------------------
        sell_plan = self.planner.plan_sells(snapshot, holdings)
        outcome.sells_planned = len(sell_plan.sells)
        decisions.extend(sell_plan.holds)
        if sell_plan.exempt:
            logger.info(
                "Swing %s: P17's regime exit did NOT reach %s holding(s), "
                "because they were opened while the gate was not being "
                "enforced: %s. Each is still subject to the rotation exit and "
                "its trailing stop, so some of them may be sold below for "
                "those reasons.",
                self.definition.key, len(sell_plan.exempt),
                ", ".join(sell_plan.exempt),
            )

        for intent in sell_plan.sells:
            decision = await self._execute_sell(portfolio_id, intent, outcome)
            decisions.append(decision)

        # COMMITTED HERE, always, for two reasons. The sells have to be VISIBLE
        # to the balance and the position book before the buys are sized; and
        # `resync()` below opens its OWN session (backend/CLAUDE.md section 2),
        # which on SQLite blocks against an outer transaction that still holds
        # uncommitted writes. A rebalance is a sequence of durable steps, not
        # one atomic act -- an order that filled has filled.
        await self.session.commit()
        self.session.expire_all()

        # --- 2. warm the book, then the buys -------------------------------
        if outcome.armed:
            # Re-read rather than subtract: a market sell can PARTIALLY fill
            # when the visible depth runs out, and the residual is still held.
            # Assuming the exit completed would let the rebalance buy into a
            # slot that is not actually free.
            remaining = await self._holdings(portfolio_id)
        else:
            # Nothing was placed, so the book is unchanged -- but the DECISION
            # is that those names are out, and the slot arithmetic has to
            # follow the decision or an unarmed run would record a different
            # buy list from the armed one it is meant to mirror.
            sold = {intent.symbol for intent in sell_plan.sells}
            remaining = [
                holding for holding in holdings if holding.symbol not in sold
            ]

        try:
            await self.warm_book(snapshot=snapshot)
            if warm_seconds > 0:
                # A subscription does not produce a depth book instantly. The
                # scheduler warms minutes ahead; a manual run can wait here.
                await asyncio.sleep(warm_seconds)
        except Exception:  # noqa: BLE001 - a feed problem must not lose the record
            logger.exception(
                "Could not warm the book before the rebalance; names with no "
                "depth will be skipped with a recorded reason."
            )

        balance = await self._balance_service().balance_for(portfolio_id)
        outcome.equity = balance.equity
        unmarked_reason = None
        if balance.equity is None:
            unmarked_reason = (
                f"{balance.unmarked_positions} open position(s) have no live "
                f"mark"
                + (
                    f" ({', '.join(balance.unmarked_strategies)})"
                    if balance.unmarked_strategies
                    else ""
                )
                + "."
            )

        candidate_symbols = [candidate.symbol for candidate in snapshot.candidates]
        security_ids = await self._security_ids(candidate_symbols)
        prices = {
            symbol: self._balance_service().mark_for(security_id)
            for symbol, security_id in security_ids.items()
        }

        buy_plan = self.planner.plan_buys(
            snapshot=snapshot,
            holdings=remaining,
            equity=balance.equity,
            available_cash=balance.available,
            prices=prices,
            security_ids=security_ids,
            unmarked_reason=unmarked_reason,
        )
        outcome.buys_planned = len(buy_plan.buys)
        decisions.extend(buy_plan.notes)

        for intent in buy_plan.buys:
            decision = await self._execute_buy(
                portfolio_id, intent, session_date, segment, outcome, gate
            )
            decisions.append(decision)

        # --- 3. record ------------------------------------------------------
        message = self._session_message(outcome, buy_plan.blocked)
        record = await self.journal.record_session(
            strategy_key=self.definition.key,
            portfolio_id=portfolio_id,
            run_kind=RUN_REBALANCE,
            snapshot=snapshot,
            decisions=decisions,
            status=STATUS_COMPLETED,
            message=message,
            started_at=started,
            held_symbols=held_symbols,
            gate=gate,
        )
        outcome.record = record

        # The record is durable before the pins are given back, and for the
        # same reason the sells were: `resync()` opens its own session.
        await self.session.commit()
        await self._unpin()

        logger.info(
            "Swing rebalance %s session %s: %s sell(s) and %s buy(s) planned, "
            "%s and %s placed (%s)",
            self.definition.key, session_date.isoformat(),
            outcome.sells_planned, outcome.buys_planned,
            outcome.sells_placed, outcome.buys_placed,
            "ARMED" if outcome.armed else "NOT ARMED -- nothing was placed",
        )
        return outcome

    # --- may an order be placed for THIS instrument, right now? -------------
    async def _execution_refusal(self, security_id: str) -> Optional[str]:
        """Why an order for this instrument cannot be placed now, or None.

        Asked per instrument and immediately before each order. F&O eligibility
        moves the boundary from 15:30 to 15:15, so one answer for the whole run
        would be wrong for half the list on any afternoon rebalance.
        """
        now = self._now()
        fno_eligible = await self._is_fno_eligible(security_id)
        if market_clock.can_execute_continuously(
            self.definition, fno_eligible, now=now
        ):
            return None

        if market_clock.in_closing_auction(self.definition, fno_eligible, now=now):
            auction = self.definition.market_hours.closing_auction
            return (
                f"Not placed at {now.strftime('%H:%M')} IST: continuous cash "
                f"trading for this F&O-eligible name ended at "
                f"{auction.continuous_close.strftime('%H:%M')} and the Closing "
                f"Auction Session runs to "
                f"{auction.auction_close.strftime('%H:%M')}. An order sent now "
                f"would land in an auction this simulator does not model. The "
                f"decision stands; it is not queued -- the next rebalance "
                f"decides again from fresh bars."
            )
        return (
            f"Not placed at {now.strftime('%H:%M')} IST: outside continuous "
            f"trading ({self.definition.market_hours.open.strftime('%H:%M')}-"
            f"{self.definition.market_hours.close.strftime('%H:%M')} IST, "
            f"Mon-Fri). Filling against the last session's depth book would be "
            f"a price nobody could have traded at. The decision stands; it is "
            f"not queued -- the next rebalance decides again from fresh bars."
        )

    def _now(self):
        from src.core.time_utils import ist_now

        return self._clock() if self._clock is not None else ist_now()

    async def _is_fno_eligible(self, security_id: str) -> bool:
        instrument = await self._instruments().get_by_security_id(str(security_id))
        return bool(instrument is not None and instrument.fno_eligible)

    @staticmethod
    def _max_staleness_days() -> int:
        return max_staleness_days()

    def _staleness_reason(self, session_date: date) -> Optional[str]:
        return staleness_reason(session_date)

    def _session_message(
        self, outcome: RebalanceOutcome, blocked: Optional[str]
    ) -> str:
        if not outcome.armed:
            return (
                f"NOT ARMED: {outcome.sells_planned} sell(s) and "
                f"{outcome.buys_planned} buy(s) were decided and recorded, and "
                f"no order was placed. Arming is a separate switch from "
                f"enabling, on the Strategies & Features page."
            )[:500]
        head = (
            f"Rebalanced: {outcome.sells_placed} of {outcome.sells_planned} "
            f"sell(s) and {outcome.buys_placed} of {outcome.buys_planned} "
            f"buy(s) placed."
        )
        if outcome.refused_outside_hours:
            head = (
                f"{head} {outcome.refused_outside_hours} order(s) were NOT "
                f"placed because the market was not in continuous trading; the "
                f"decision stands and is recorded, and nothing is queued for "
                f"the next open."
            )
        if blocked:
            head = f"{head} {blocked}"
        return head[:500]

    # --- placing ------------------------------------------------------------
    async def _execute_sell(
        self, portfolio_id: int, intent: SellIntent, outcome: RebalanceOutcome
    ) -> Decision:
        """One exit. Closing orders are never refused for funds."""
        exit_kind = EXIT_REGIME if intent.kind == "REGIME" else EXIT_ROTATION
        stop = await self.stops.stops.get_active(portfolio_id, intent.security_id)

        if not outcome.armed:
            return Decision(
                symbol=intent.symbol,
                action=ACTION_SOLD,
                rank=intent.rank,
                quantity=intent.quantity,
                reason=intent.reason,
            )

        refusal = await self._execution_refusal(intent.security_id)
        if refusal is not None:
            outcome.refused_outside_hours += 1
            outcome.notes.append(f"{intent.symbol}: {refusal}")
            logger.warning("Swing exit for %s not placed. %s", intent.symbol, refusal)
            return Decision(
                symbol=intent.symbol,
                action=ACTION_SKIPPED,
                rank=intent.rank,
                quantity=intent.quantity,
                reason=f"{refusal} ({intent.reason})",
            )

        from src.orders.services.order_service import OrderValidationError

        try:
            order = await self._order_service().submit_paper_order(
                security_id=intent.security_id,
                side=OrderSide.SELL.value,
                order_type=OrderType.MARKET.value,
                # NSE cash: LOT_SIZE is 1, so lots and shares are the same
                # number and `lots * lot_size` is the quantity. Not routed
                # through `quantity_override`, which would leave order.lots
                # disagreeing with order.quantity / order.lot_size.
                lots=int(intent.quantity),
                is_close_order=True,
                portfolio_id=portfolio_id,
                reason=intent.reason,
            )
        except OrderValidationError as error:
            outcome.notes.append(f"{intent.symbol}: {error}")
            logger.warning(
                "Swing exit for %s was refused: %s", intent.symbol, error
            )
            return Decision(
                symbol=intent.symbol,
                action=ACTION_SKIPPED,
                rank=intent.rank,
                quantity=intent.quantity,
                reason=f"Exit refused: {error} ({intent.reason})",
            )

        outcome.sells_placed += 1
        if stop is not None:
            await self.stops.close_for_exit(
                stop, exit_kind, order_id=order.id, note=intent.reason
            )
        return Decision(
            symbol=intent.symbol,
            action=ACTION_SOLD,
            rank=intent.rank,
            quantity=intent.quantity,
            reference_price=(
                Decimal(str(order.average_fill_price))
                if order.average_fill_price is not None
                else None
            ),
            order_id=order.id,
            reason=intent.reason,
        )

    async def _execute_buy(
        self,
        portfolio_id: int,
        intent: BuyIntent,
        session_date: date,
        segment: str,
        outcome: RebalanceOutcome,
        gate,
    ) -> Decision:
        if not outcome.armed:
            return Decision(
                symbol=intent.symbol,
                action=ACTION_BOUGHT,
                rank=intent.rank,
                score=intent.score,
                quantity=intent.quantity,
                reference_price=intent.reference_price,
                reason=intent.reason,
            )

        refusal = await self._execution_refusal(intent.security_id)
        if refusal is not None:
            outcome.refused_outside_hours += 1
            outcome.notes.append(f"{intent.symbol}: {refusal}")
            logger.warning("Swing entry for %s not placed. %s", intent.symbol, refusal)
            return Decision(
                symbol=intent.symbol,
                action=ACTION_SKIPPED,
                rank=intent.rank,
                score=intent.score,
                quantity=intent.quantity,
                reference_price=intent.reference_price,
                reason=f"{refusal} ({intent.reason})",
            )

        from src.orders.services.order_service import OrderValidationError

        try:
            order = await self._order_service().submit_paper_order(
                security_id=intent.security_id,
                side=OrderSide.BUY.value,
                order_type=OrderType.MARKET.value,
                lots=int(intent.quantity),
                portfolio_id=portfolio_id,
                reason=intent.reason,
            )
        except OrderValidationError as error:
            outcome.notes.append(f"{intent.symbol}: {error}")
            logger.warning("Swing entry for %s was refused: %s", intent.symbol, error)
            return Decision(
                symbol=intent.symbol,
                action=ACTION_SKIPPED,
                rank=intent.rank,
                score=intent.score,
                quantity=intent.quantity,
                reference_price=intent.reference_price,
                reason=f"Entry refused: {error} ({intent.reason})",
            )

        outcome.buys_placed += 1
        filled = int(order.filled_quantity or 0)
        stop_note = ""
        if filled > 0:
            # P14, off the price actually paid rather than the price planned.
            view = await self.ranking.symbol_view(
                intent.symbol, segment, as_of=session_date
            )
            stop = await self.stops.open_for_entry(
                portfolio_id=portfolio_id,
                symbol=intent.symbol,
                security_id=intent.security_id,
                quantity=filled,
                entry_price=Decimal(str(order.average_fill_price)),
                entry_session=session_date,
                entry_atr=view.atr14 if view is not None else None,
                entry_order_id=order.id,
                # What the regime was doing, and which of its rules were being
                # obeyed, at the moment this position was opened. Read back per
                # position by `plan_sells`, which is the whole of "a position
                # keeps the policy it was opened under".
                entry_gate_on=gate.gate_on,
                entry_regime_enforced=gate.enforce_regime,
            )
            stop_note = (
                f" Chandelier stop set at {stop.stop_price}."
                if stop.stop_price is not None
                else " NO STOP SET: ATR14 was not available, so this position is "
                "unprotected until the next nightly ratchet computes one."
            )
            if not gate.enforce_regime:
                stop_note = (
                    f"{stop_note} Opened with the regime gate "
                    f"{'ON' if gate.gate_on else 'OFF'} and NOT enforced; "
                    f"recorded as such so it can be filtered out of the "
                    f"numbers later."
                )
        elif order.filled_quantity == 0:
            stop_note = (
                " Nothing filled, so no stop was set."
            )

        return Decision(
            symbol=intent.symbol,
            action=ACTION_BOUGHT,
            rank=intent.rank,
            score=intent.score,
            quantity=filled or intent.quantity,
            reference_price=(
                Decimal(str(order.average_fill_price))
                if order.average_fill_price is not None
                else intent.reference_price
            ),
            order_id=order.id,
            reason=f"{intent.reason}{stop_note}",
        )

    # --- lookups ------------------------------------------------------------
    async def _holdings(self, portfolio_id: int) -> List[Holding]:
        """The open book for THIS strategy in THIS portfolio.

        Keyed on both, never on the security alone: the same strategy running
        in two portfolios is two books and they must not mix.
        """
        from src.positions.database.db_operations.position_repository import (
            PositionRepository,
        )

        positions = await PositionRepository(self.session).list_all(
            include_closed=False,
            strategy_key=self.definition.key,
            portfolio_id=portfolio_id,
        )
        holdings: List[Holding] = []
        for position in positions:
            net = int(position.net_quantity or 0)
            if net <= 0:
                # A short cannot exist in this strategy -- it never sells to
                # open -- and a zero is a closed row that has not been tidied.
                continue
            # THE POLICY THIS POSITION WAS OPENED UNDER, from its own stop row,
            # not the policy in force now. A position opened while the regime
            # gate was being observed rather than enforced is not liquidated
            # when enforcement comes back on -- see `plan_sells`.
            #
            # A missing row, or a row from before the column existed, reads as
            # ENFORCED: the exemption has to fail towards the specification's
            # own behaviour rather than towards carrying a book through a
            # regime exit on no evidence.
            stop = await self.stops.stops.get_active(
                portfolio_id, position.security_id
            )
            enforced = True
            if stop is not None and stop.entry_regime_enforced is not None:
                enforced = bool(stop.entry_regime_enforced)
            holdings.append(
                Holding(
                    symbol=position.trading_symbol,
                    security_id=position.security_id,
                    quantity=net,
                    entry_regime_enforced=enforced,
                )
            )
        return holdings

    async def _security_ids(self, symbols: List[str]) -> Dict[str, str]:
        """symbol -> Dhan security id, from the instrument master.

        Resolved against the master rather than the universe file, whose own
        ids are advisory and four of which already disagree with it.
        """
        if not symbols:
            return {}
        rows = await self._instruments().list_by_symbols(
            sorted(set(symbols)), self._equity_segment()
        )
        return {
            row.underlying_symbol: row.security_id
            for row in rows
            if row.is_active
        }
