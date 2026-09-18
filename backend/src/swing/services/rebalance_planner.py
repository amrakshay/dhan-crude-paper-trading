"""What the rebalance is going to do, worked out before anything is placed.

This module decides; `execution_service.py` acts. Keeping them apart is what
makes the hard cases testable without a book, a portfolio or an order table:
"equity is unknown so nothing may be sized", "breadth allows three slots and
two are already held", "this buy is skipped because the cash is short" are all
answers this module produces from plain values.

Three rules here are not style choices.

* **Sells are planned and executed BEFORE buys are planned.** The backtest
  processes pending exits before pending entries (specification section 6,
  mechanic 4) and sizing is cash-constrained, so a buy funded by that morning's
  sale must see the money. Planning both at once against one balance snapshot
  would reject buys the backtest funded -- and it would look like a bug in the
  funds code rather than in the ordering. That is why there is no single
  `plan()`: `plan_sells` and `plan_buys` are separate calls with the execution
  of the sells in between.

* **Equity that could not be computed blocks sizing entirely.** P13 sizes every
  position from total equity. `BalanceService` withholds equity when any open
  position has no mark, and the honest response to that is to buy nothing and
  say why -- not to substitute cash, not to size off the marked positions only.
  An automated decision taken on a guessed number is the failure this whole
  application is arranged to avoid.

* **A buy that does not fit is SKIPPED, not shrunk.** The backtest resizes
  (`size = min(equity / 10, cash)`), which is a different trade from the one
  the rule asked for. Recording "insufficient cash for a full position" is
  checkable; quietly buying two thirds of one is not. This is a deliberate
  divergence from the backtest and is listed in the README's known gaps.

Money is `Decimal` throughout. The indicators arriving on the snapshot are
float and stay float; the conversion happens where a price meets a quantity.
"""
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Dict, List, Optional

from src.logging_config import get_logger
from src.swing.database.db_models.swing_session_model import (
    ACTION_HELD,
    ACTION_NOT_ENTERED,
    ACTION_SKIPPED,
)
from src.swing.services.journal_service import Decision
from src.swing.services.ranking_service import RankingSnapshot
from src.swing.services.swing_parameters import SwingParameters

if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.swing.services.gate_policy import GatePolicy

logger = get_logger("swing.rebalance")

MONEY = Decimal("0.01")
ZERO = Decimal("0")

# Why a holding is being sold. Carried separately from the sentence so the
# performance report can count trail stops against rotations without parsing
# English (specification section 9.1's exit mix).
EXIT_REGIME = "REGIME"
EXIT_ROTATION = "ROTATION"


def _q(value: Decimal) -> Decimal:
    return Decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


# Which set of rules is in force this session.
VARIANT_BASELINE = "baseline"
VARIANT_OFF_GATE = "v3b-off-gate"


@dataclass(frozen=True)
class EffectiveGate:
    """What the regime gate means for THIS session, after the policy is applied.

    The baseline and the V3b off-gate variant disagree about three things once
    the index is below its 200-day SMA, and every one of them has to be
    answered consistently by the nightly run, the rebalance and the page:

      * `liquidates` -- P17 sells the whole book with the gate off. V3b does
        not: holding three names through a bear rally is the entire variant.
      * `slots` -- the graded breadth ramp with the gate on; V3b's own flat
        slot count with it off.
      * `momentum_floor` -- P6 with the gate on; V3b's own floor with it off.
        The snapshot has to be RANKED with the right floor, which is why
        `RankingService.session_snapshot` resolves it before ranking rather
        than filtering afterwards.

    V3b ships DISABLED and should stay that way. The owner's own research tested
    thirteen variants on the extended panel and not one beat holding cash:
    V3b's individual trades are good (61% win, mean +7.81%) and its total is
    still lower, because capital committed to a bear rally is not available at
    the regime flip. Per-trade edge is not portfolio edge.

    `enforced` records which of the three the answer came from, so a session
    and a trade can say afterwards which rules were being obeyed when it was
    taken. See `gate_policy.py`.
    """

    variant: str
    gate_on: bool
    liquidates: bool
    entries_allowed: bool
    slots: Optional[int]
    momentum_floor: float
    # The policy this was resolved under. Defaulted so that every existing
    # construction of an EffectiveGate in a test still reads as the
    # specification's own behaviour.
    enforce_regime: bool = True
    enforce_entry_return: bool = True

    @property
    def is_off_gate_variant(self) -> bool:
        return self.variant == VARIANT_OFF_GATE

    @property
    def relaxed(self) -> bool:
        return not self.enforce_regime or not self.enforce_entry_return


def effective_gate(
    snapshot: RankingSnapshot,
    parameters: SwingParameters,
    policy: Optional["GatePolicy"] = None,
) -> EffectiveGate:
    """Resolve the gate, V3b and the enforcement policy once, for every caller.

    PURE. `policy` is a plain frozen value rather than something read from the
    registry in here, because the tests for this function are the whole safety
    net for trading through the gate and they must not need a process-wide
    singleton. Omitting it means the YAML's defaults, i.e. the specification's
    own behaviour.

                          | liquidates | entries          | slots   | floor
      gate on             | no         | P9, if enforced  | breadth | P6
      gate off, enforced  | YES        | no               | 0       | P6
      gate off, V3b       | no         | V3b's own answer | V3b's   | V3b's
      gate off, relaxed   | no         | P9, if enforced  | breadth | P6

    The last row is the deliberate divergence. Note what it does NOT change:
    breadth still sizes the book, P6 still filters, the rotation exit still
    fires at rank > 15 and the chandelier stop is untouched. And note that P9
    is an INDEPENDENT switch -- relaxing the regime gate alone leaves the
    63-session filter blocking every new entry, which on 2026-09-17's -3.71%
    would have meant no trades at all.
    """
    from src.swing.services.gate_policy import default_policy

    regime = snapshot.regime
    off_gate = parameters.off_gate
    policy = policy or default_policy(parameters)

    def entries_under_p9() -> bool:
        """P9, if it is being enforced."""
        if not policy.enforce_entry_return:
            return True
        return bool(regime.entries_allowed)

    if regime.gate_on:
        return EffectiveGate(
            variant=VARIANT_BASELINE,
            gate_on=True,
            liquidates=False,
            entries_allowed=entries_under_p9(),
            slots=snapshot.slots,
            momentum_floor=parameters.momentum_floor,
            enforce_regime=policy.enforce_regime,
            enforce_entry_return=policy.enforce_entry_return,
        )

    if not policy.enforce_regime:
        # The gate is OFF and is not being obeyed. It was still computed and is
        # still recorded -- on the session, on every decision row and on every
        # position opened here -- which is the entire reason this is an
        # acceptable thing to switch on.
        return EffectiveGate(
            variant=VARIANT_BASELINE,
            gate_on=False,
            liquidates=False,
            entries_allowed=entries_under_p9(),
            slots=snapshot.slots,
            momentum_floor=parameters.momentum_floor,
            enforce_regime=False,
            enforce_entry_return=policy.enforce_entry_return,
        )

    if not policy.off_gate_enabled:
        return EffectiveGate(
            variant=VARIANT_BASELINE,
            gate_on=False,
            liquidates=True,
            entries_allowed=False,
            slots=0,
            momentum_floor=parameters.momentum_floor,
            enforce_regime=True,
            enforce_entry_return=policy.enforce_entry_return,
        )

    return EffectiveGate(
        variant=VARIANT_OFF_GATE,
        gate_on=False,
        liquidates=False,
        # V3b drops the 63-session entry filter by default; `require_entry_return`
        # puts it back for anyone who wants the variant without that part. The
        # P9 policy switch can only RELAX that further, never tighten it: a
        # variant that was measured without the filter is not improved by an
        # operator adding one back through a different control.
        entries_allowed=(
            True
            if not (off_gate.require_entry_return and policy.enforce_entry_return)
            else bool(regime.return_over_window is not None
                      and regime.return_over_window > parameters.regime.entry_return_minimum)
        ),
        slots=off_gate.slots,
        momentum_floor=off_gate.momentum_floor,
        enforce_regime=True,
        enforce_entry_return=policy.enforce_entry_return,
    )


@dataclass(frozen=True)
class Holding:
    """One open position as the rotation sees it.

    `quantity` is the whole position. The rotation never sells part of one --
    a name is in the book or it is not.

    `entry_regime_enforced` is THE POLICY THIS POSITION WAS OPENED UNDER, read
    from its own `swing_stops` row, not the policy in force now. A position
    opened while the regime gate was being observed rather than enforced keeps
    that: turning enforcement back on stops new entries, it does not liquidate
    a book that was opened under the other rule. Those positions leave by
    rotation (rank > 15) or by their trailing stop like any other.

    It defaults to True -- i.e. NOT exempt -- so a position whose policy cannot
    be established is liquidated by P17 exactly as the specification says. An
    exemption that failed open would quietly carry a book through a regime exit
    on no evidence at all, which is the wrong direction to be wrong in.
    """

    symbol: str
    security_id: str
    quantity: int
    entry_regime_enforced: bool = True


@dataclass(frozen=True)
class SellIntent:
    symbol: str
    security_id: str
    quantity: int
    kind: str                       # EXIT_REGIME | EXIT_ROTATION
    reason: str
    rank: Optional[int] = None


@dataclass(frozen=True)
class BuyIntent:
    symbol: str
    security_id: str
    quantity: int
    rank: int
    score: float
    reference_price: Decimal
    estimated_debit: Decimal
    reason: str


@dataclass
class SellPlan:
    sells: List[SellIntent] = field(default_factory=list)
    holds: List[Decision] = field(default_factory=list)
    # Holdings P17's liquidation did not REACH, because they were opened while
    # the regime gate was not being enforced. Not the same as "kept": each one
    # still goes through the rotation exit below, so a name can be here and in
    # `sells` at once.
    exempt: List[str] = field(default_factory=list)

    @property
    def symbols(self) -> List[str]:
        return [intent.symbol for intent in self.sells]


@dataclass
class BuyPlan:
    buys: List[BuyIntent] = field(default_factory=list)
    notes: List[Decision] = field(default_factory=list)
    # Why no entry was attempted at all, where that is the answer. None means
    # entries were allowed and the notes explain each individual candidate.
    blocked: Optional[str] = None
    equity: Optional[Decimal] = None
    target_position_value: Optional[Decimal] = None

    @property
    def symbols(self) -> List[str]:
        return [intent.symbol for intent in self.buys]


class RebalancePlanner:
    """Turns a snapshot plus a book plus money into two lists and the reasons."""

    def __init__(
        self,
        parameters: SwingParameters,
        strategy_key: str,
        policy: Optional["GatePolicy"] = None,
    ):
        self.parameters = parameters
        self.strategy_key = strategy_key
        # Read ONCE, by whoever built this planner, and carried as a value. Not
        # re-read per call and never re-read mid-run: a decision taken half
        # under one policy and half under another is not one anybody can audit.
        self.policy = policy

    def gate(self, snapshot: RankingSnapshot) -> "EffectiveGate":
        return effective_gate(snapshot, self.parameters, self.policy)

    # --- sells -------------------------------------------------------------
    def plan_sells(
        self, snapshot: RankingSnapshot, holdings: List[Holding]
    ) -> SellPlan:
        """P17 then P16: the regime exit, else the rotation exit.

        A holding that is neither is recorded as HELD with the rank that kept
        it, because "why is this still here" is as much a decision as "why was
        this sold".
        """
        parameters = self.parameters
        gate = self.gate(snapshot)
        plan = SellPlan()

        for holding in sorted(holdings, key=lambda one: one.symbol):
            rank = snapshot.rank_of(holding.symbol)

            if gate.liquidates and holding.entry_regime_enforced:
                # P17. Every holding, regardless of its rank: the gate is the
                # kill switch and it does not negotiate with a good position.
                plan.sells.append(
                    SellIntent(
                        symbol=holding.symbol,
                        security_id=holding.security_id,
                        quantity=holding.quantity,
                        kind=EXIT_REGIME,
                        rank=rank,
                        reason=self._regime_exit_reason(snapshot),
                    )
                )
                continue

            if gate.liquidates:
                # Opened while the gate was being OBSERVED rather than
                # enforced, so P17 does not reach it. It is not held for ever:
                # it falls through to the rotation exit and its trailing stop
                # below, exactly like every other holding. Recorded here so the
                # reason on the row names the policy rather than leaving a
                # position that "should have been sold" unexplained.
                plan.exempt.append(holding.symbol)

            if rank is None:
                plan.sells.append(
                    SellIntent(
                        symbol=holding.symbol,
                        security_id=holding.security_id,
                        quantity=holding.quantity,
                        kind=EXIT_ROTATION,
                        rank=None,
                        reason=(
                            f"Rotation exit: {holding.symbol} is no longer ranked "
                            f"({snapshot.skipped.get(holding.symbol, 'failed a filter')})."
                        ),
                    )
                )
                continue

            if rank > parameters.rotation_exit_rank:
                plan.sells.append(
                    SellIntent(
                        symbol=holding.symbol,
                        security_id=holding.security_id,
                        quantity=holding.quantity,
                        kind=EXIT_ROTATION,
                        rank=rank,
                        reason=(
                            f"Rotation exit: rank {rank} > "
                            f"{parameters.rotation_exit_rank}."
                        ),
                    )
                )
                continue

            held_reason = (
                f"Held: rank {rank} is within {parameters.rotation_exit_rank}."
            )
            if gate.liquidates:
                held_reason = (
                    f"Held through a regime exit: this position was opened "
                    f"while the regime gate was NOT being enforced, and it "
                    f"keeps the policy it was opened under. P17 does not reach "
                    f"it; it leaves by rotation (rank > "
                    f"{parameters.rotation_exit_rank}) or by its trailing stop. "
                    f"Rank {rank}."
                )
            plan.holds.append(
                Decision(
                    symbol=holding.symbol,
                    action=ACTION_HELD,
                    rank=rank,
                    quantity=holding.quantity,
                    reason=held_reason,
                )
            )

        return plan

    def _regime_exit_reason(self, snapshot: RankingSnapshot) -> str:
        regime = snapshot.regime
        shortfall = regime.shortfall_percent
        gap = f", needs {shortfall:.1f}% to reclaim it" if shortfall is not None else ""
        close = "unknown" if regime.close is None else f"{regime.close:,.1f}"
        sma = "unknown" if regime.sma200 is None else f"{regime.sma200:,.1f}"
        return (
            f"Regime exit: {regime.index_symbol} {close} is below its "
            f"{self.parameters.regime.sma_sessions}-session SMA {sma}{gap}. "
            f"The book goes to 100% cash."
        )

    # --- buys --------------------------------------------------------------
    def plan_buys(
        self,
        snapshot: RankingSnapshot,
        holdings: List[Holding],
        equity: Optional[Decimal],
        available_cash: Decimal,
        prices: Dict[str, Optional[Decimal]],
        security_ids: Dict[str, str],
        unmarked_reason: Optional[str] = None,
    ) -> BuyPlan:
        """P11 slots, P13 sizing, walked down the ranking.

        `holdings` is the book AFTER the sells have been executed. `prices` and
        `security_ids` are keyed by symbol and are supplied by the caller,
        which is what keeps this module free of the book and the database.

        `equity` of None means `BalanceService` withheld it; `unmarked_reason`
        is the explanation to record. Nothing is sized in that case.
        """
        parameters = self.parameters
        gate = self.gate(snapshot)
        plan = BuyPlan(equity=equity)
        held = {holding.symbol for holding in holdings}

        blocked = self._entry_block_reason(
            snapshot, gate, len(held), equity, unmarked_reason
        )
        if blocked is not None:
            plan.blocked = blocked
            plan.notes.append(
                Decision(symbol="*", action=ACTION_NOT_ENTERED, reason=blocked)
            )
            return plan

        # Equity is not None here: `_entry_block_reason` refuses first.
        target = (Decimal(equity) / Decimal(parameters.position_size_divisor))
        plan.target_position_value = _q(target)

        openings = min(gate.slots, parameters.max_positions) - len(held)
        remaining_cash = Decimal(available_cash)
        taken = 0

        for candidate in snapshot.candidates:
            if candidate.symbol in held:
                continue
            if taken >= openings:
                plan.notes.append(
                    Decision(
                        symbol=candidate.symbol,
                        action=ACTION_SKIPPED,
                        rank=candidate.rank,
                        score=candidate.score,
                        reason=self._slots_full_reason(snapshot, gate, len(held)),
                    )
                )
                # The tail beyond the top of the book is unbounded; record
                # enough of it to explain the boundary and stop.
                if candidate.rank >= parameters.max_positions:
                    break
                continue

            security_id = security_ids.get(candidate.symbol)
            if not security_id:
                plan.notes.append(
                    Decision(
                        symbol=candidate.symbol,
                        action=ACTION_SKIPPED,
                        rank=candidate.rank,
                        score=candidate.score,
                        reason=(
                            f"Skipped: {candidate.symbol} has no active row in "
                            f"the instrument master, so there is nothing to "
                            f"place an order against. Refresh the master."
                        ),
                    )
                )
                continue

            price = prices.get(candidate.symbol)
            if price is None or Decimal(price) <= 0:
                # Never size off the daily close. The rule buys at the next
                # session's open, and what that costs is a live price or
                # nothing at all.
                plan.notes.append(
                    Decision(
                        symbol=candidate.symbol,
                        action=ACTION_SKIPPED,
                        rank=candidate.rank,
                        score=candidate.score,
                        reason=(
                            f"Skipped: no live price for {candidate.symbol}, so "
                            f"the position cannot be sized. The daily close is "
                            f"not used -- this order fills at today's market, "
                            f"not yesterday's."
                        ),
                    )
                )
                continue

            price = Decimal(price)
            quantity = int(target // price)
            if quantity <= 0:
                plan.notes.append(
                    Decision(
                        symbol=candidate.symbol,
                        action=ACTION_SKIPPED,
                        rank=candidate.rank,
                        score=candidate.score,
                        reference_price=_q(price),
                        reason=(
                            f"Skipped: a position of {_q(target)} buys 0 whole "
                            f"shares of {candidate.symbol} at {_q(price)}."
                        ),
                    )
                )
                continue

            debit = self.estimated_debit(price, quantity)
            if debit > remaining_cash:
                shortfall = _q(debit - remaining_cash)
                plan.notes.append(
                    Decision(
                        symbol=candidate.symbol,
                        action=ACTION_SKIPPED,
                        rank=candidate.rank,
                        score=candidate.score,
                        quantity=quantity,
                        reference_price=_q(price),
                        reason=(
                            f"Skipped: insufficient cash. {quantity} share(s) at "
                            f"{_q(price)} would cost {_q(debit)} including "
                            f"charges, against {_q(remaining_cash)} available -- "
                            f"short by {shortfall}. Not resized: a smaller "
                            f"position is a different trade from the one the "
                            f"rule asked for."
                        ),
                    )
                )
                continue

            plan.buys.append(
                BuyIntent(
                    symbol=candidate.symbol,
                    security_id=security_id,
                    quantity=quantity,
                    rank=candidate.rank,
                    score=candidate.score,
                    reference_price=_q(price),
                    estimated_debit=_q(debit),
                    reason=(
                        f"Entry: rank {candidate.rank}, score "
                        f"{candidate.score:.1f}, momentum "
                        f"{candidate.momentum * 100:.1f}%. Sized at "
                        f"{_q(target)} (equity {_q(Decimal(equity))} / "
                        f"{parameters.position_size_divisor}) = {quantity} "
                        f"share(s) at about {_q(price)}."
                    ),
                )
            )
            remaining_cash -= debit
            taken += 1

        return plan

    @staticmethod
    def _slots_full_reason(
        snapshot: RankingSnapshot, gate: EffectiveGate, held_count: int
    ) -> str:
        if gate.is_off_gate_variant:
            return (
                f"Skipped: slots full ({gate.slots} allowed by the V3b off-gate "
                f"variant while the regime gate is OFF, {held_count} already held)."
            )
        breadth = (
            "unknown"
            if snapshot.breadth is None
            else f"{snapshot.breadth * 100:.1f}%"
        )
        return (
            f"Skipped: slots full ({gate.slots} allowed by a breadth of "
            f"{breadth}, {held_count} already held)."
        )

    def estimated_debit(self, price: Decimal, quantity: int) -> Decimal:
        """Consideration plus this strategy's own charges, for one buy.

        The same figure `OrderService._check_funds` computes, arrived at
        through the rate card rather than repeated: an equity delivery buy pays
        STT, the transaction charge, SEBI, stamp duty and GST, and planning
        against the bare consideration would put every buy one rejection away
        from the funds check.
        """
        from src.charges.services.charges_engine import ChargesEngine

        price = Decimal(price)
        quantity = int(quantity)
        consideration = _q(price * quantity)
        charges = Decimal(
            str(
                ChargesEngine.for_strategy_key(
                    self.strategy_key
                ).compute_order_charges_for_quantity(
                    side="BUY", premium=price, quantity=quantity, lot_size=1
                ).total
            )
        )
        return consideration + charges

    # --- why not -----------------------------------------------------------
    def _entry_block_reason(
        self,
        snapshot: RankingSnapshot,
        gate: EffectiveGate,
        held_count: int,
        equity: Optional[Decimal],
        unmarked_reason: Optional[str],
    ) -> Optional[str]:
        """Why no new position is opened, or None if entries may proceed.

        Every branch carries the numbers it decided on. A record that says only
        "no entries" cannot be checked against anything later, which is the
        whole reason the journal exists.
        """
        parameters = self.parameters
        regime = snapshot.regime

        # `liquidates` rather than "the gate is off": with the gate off and
        # ENFORCED the book goes to cash, so entries are obviously blocked --
        # but with the gate off and NOT enforced the ordinary rules apply and
        # this branch must not swallow the session. The gate state is still
        # recorded, on the session and on every decision row.
        if gate.liquidates:
            shortfall = regime.shortfall_percent
            gap = f", needs {shortfall:.1f}% to reclaim it" if shortfall is not None else ""
            close = "unknown" if regime.close is None else f"{regime.close:,.1f}"
            sma = "unknown" if regime.sma200 is None else f"{regime.sma200:,.1f}"
            return (
                f"Not entered: the regime gate is OFF. {regime.index_symbol} "
                f"{close} is below its {parameters.regime.sma_sessions}-session "
                f"SMA {sma}{gap}. The book holds 100% cash by design."
            )

        if not gate.entries_allowed:
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

        if gate.is_off_gate_variant:
            # V3b's slot count is flat and configured, so breadth does not
            # gate it. It is still recorded, because an operator reading this
            # in six months needs to see that the variant -- not the ramp --
            # is what allowed the entry.
            if gate.slots is None or gate.slots <= 0:
                return (
                    f"Not entered: the V3b off-gate variant is enabled but "
                    f"allows {gate.slots} slot(s), so nothing is bought while "
                    f"the regime gate is OFF."
                )
        else:
            if snapshot.breadth is None:
                return (
                    "Not entered: breadth could not be measured -- no name in the "
                    "universe passed the liquidity and price floors with a defined "
                    "SMA200. Refusing to size from a breadth that is unknown rather "
                    "than treating it as zero."
                )

            if gate.slots is None or gate.slots <= 0:
                return (
                    f"Not entered: breadth {snapshot.breadth * 100:.1f}% "
                    f"({snapshot.above_sma_count} of {snapshot.liquid_count} above "
                    f"their own SMA200) allows {gate.slots} slots. The graded "
                    f"breadth ramp buys nothing at or below "
                    f"{parameters.breadth_lower * 100:.0f}%."
                )

        if held_count >= min(gate.slots, parameters.max_positions):
            allowed_by = (
                f"the V3b off-gate variant"
                if gate.is_off_gate_variant
                else f"a breadth of {snapshot.breadth * 100:.1f}%"
            )
            return (
                f"Not entered: {held_count} position(s) held against "
                f"{gate.slots} slot(s) allowed by {allowed_by}."
            )

        if equity is None:
            # The honesty rule applied to an automated decision. P13 divides
            # TOTAL equity by ten; there is no sensible substitute for a number
            # that could not be computed, and cash is not it -- it ignores
            # everything already invested.
            return (
                "Not entered: total equity could not be computed, so no "
                "position can be sized. "
                + (unmarked_reason or "At least one open position has no live mark.")
                + " Sizing from cash instead would ignore everything already "
                "invested, and guessing an equity figure is exactly what this "
                "application refuses to do."
            )

        if not snapshot.candidates:
            return (
                f"Not entered: no name passed the filters. "
                f"{snapshot.liquid_count} liquid, "
                f"{snapshot.above_sma_count} above their own SMA200, none clearing "
                f"the {gate.momentum_floor:.0%} momentum floor."
            )

        return None
