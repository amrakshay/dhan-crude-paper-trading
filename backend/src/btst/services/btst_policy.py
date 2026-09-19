"""Whether this strategy's own rules are ENFORCED. The sibling of
`src/swing/services/gate_policy.py`.

Read that module's docstring first -- the reasoning is identical and is not
repeated here. What is editable is never a PARAMETER of a rule: B1-B16 stay in
`conf/strategies/nse-btst-overnight.yaml` and are editable from no page. What
an operator may flip is whether a rule is OBEYED, which is the same kind of
fact as enabled and armed.

Two switches, and they are a different two from the rotation's:

    regime.enforce    B9 -- trade only while NIFTY 50 is above its 200-SMA
    fno.exclude       section 5a -- trade only names without listed derivatives

**`regime.enforce` is the SAME policy name the rotation uses**, on purpose:
both mean the index's close against its own 200-session SMA. What differs is
how much it does. It is load-bearing for a rotation that goes to cash when it
flips; here only 259 of 3,016 signals ever fire below the SMA, because a stock
making a 55-day high on twice its volume as a six-month momentum leader is
itself evidence of a healthy tape. Gate ON and gate OFF measure 18.98% and
19.27% CAGR (specification section 11) -- the gate earns its place on MAR, not
on return.

**There is no entry-return switch.** The rotation's P9 has no counterpart in
this specification, so the policy is not declared and the control is not
offered. An absent switch rather than one permanently off: "this strategy does
not have that rule" is a different fact from "it has it switched off".

**`fno.exclude` has no equivalent anywhere else**, and it is the reason this
module gets a policy of its own rather than borrowing the rotation's. It says
whether the 210 F&O-eligible names are scanned at all. On, which is how it
ships, it removes the closing auction from the problem entirely -- their
continuous session ends at 15:15 and the scan is at 15:20 -- and takes the
better half of the edge with it. Off, the universe roughly doubles, the signal
rate goes from ~0.54 a session to ~1.07, and orders on those names are refused
after 15:15 by the same per-instrument guard the rotation uses.

**There is no contradictory pair here**, which is why there is nothing in this
module resembling `GatePolicy.__post_init__`'s refusal. The two switches are
about different things -- when to trade and what to trade -- and every
combination of them is a strategy somebody could mean. `validate_policy_change`
therefore accepts everything, and says so rather than being silently absent.

**THE POLICY IS READ WHEN A RUN STARTS** and carried down as a value, never
re-read mid-run, exactly as the rotation's is. A scan half under one policy and
half under another is not one anybody can audit -- and here that matters more
than usual, because `fno.exclude` decides the UNIVERSE, so a change mid-scan
would rank two different populations against each other.
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.btst.services.btst_parameters import BtstParameters
from src.logging_config import get_logger
from src.strategies.services.strategy_definition import (
    POLICY_FNO_EXCLUDE,
    POLICY_REGIME_ENFORCE,
    StrategyDefinition,
)

logger = get_logger("btst.policy")

# The switches THIS module offers. Not `KNOWN_POLICIES`, which is the
# framework's whole vocabulary across every strategy: a page must not offer
# this strategy a control over a rule it does not have.
BTST_POLICIES = (POLICY_REGIME_ENFORCE, POLICY_FNO_EXCLUDE)


@dataclass(frozen=True)
class BtstPolicy:
    """Which of this strategy's rules are in force, right now.

    Frozen and dependency-free, like `GatePolicy`, so the tests that pin the
    scan's behaviour need no registry, no database and no process-wide
    singleton.
    """

    enforce_regime: bool
    exclude_fno: bool

    @property
    def relaxed(self) -> bool:
        """Is any rule currently not being enforced?

        `exclude_fno` is deliberately NOT part of this. Switching it off does
        not relax a rule -- it widens the universe, which is a different kind
        of change and is not a divergence from the specification: section 5a's
        own recommendation is the narrow universe, but the headline backtest is
        the wide one.
        """
        return not self.enforce_regime

    def as_dict(self) -> Dict[str, Any]:
        return {
            POLICY_REGIME_ENFORCE: self.enforce_regime,
            POLICY_FNO_EXCLUDE: self.exclude_fno,
        }


def default_policy(parameters: BtstParameters) -> BtstPolicy:
    """What the YAML alone says -- what a fresh checkout does."""
    return BtstPolicy(
        enforce_regime=parameters.regime.enforce_by_default,
        exclude_fno=parameters.exclude_fno_by_default,
    )


def defaults_as_dict(parameters: BtstParameters) -> Dict[str, bool]:
    return {
        POLICY_REGIME_ENFORCE: parameters.regime.enforce_by_default,
        POLICY_FNO_EXCLUDE: parameters.exclude_fno_by_default,
    }


def resolve_policy(
    definition: StrategyDefinition,
    parameters: Optional[BtstParameters] = None,
    overrides: Optional[Dict[str, bool]] = None,
) -> BtstPolicy:
    """The YAML's defaults with the operator's stored switches on top.

    Synchronous and cached for the same reason `is_enabled` and `is_armed` are:
    it is read from the scan and from the page, and the registry holds the
    state in memory precisely so code that cannot await can ask.

    `overrides` exists for the tests: pass a dict and the registry is not
    consulted at all.
    """
    parameters = parameters or BtstParameters.from_definition(definition)
    defaults = defaults_as_dict(parameters)

    if overrides is None:
        from src.strategies.services.strategy_registry import get_strategy_registry

        registry = get_strategy_registry()
        overrides = {}
        for policy in defaults:
            stored = registry.policy_override(definition.key, policy)
            if stored is not None:
                overrides[policy] = stored

    resolved = {**defaults, **{k: bool(v) for k, v in (overrides or {}).items()
                               if k in defaults}}
    return BtstPolicy(
        enforce_regime=bool(resolved[POLICY_REGIME_ENFORCE]),
        exclude_fno=bool(resolved[POLICY_FNO_EXCLUDE]),
    )


def describe_policies(
    definition: StrategyDefinition, parameters: Optional[BtstParameters] = None
) -> Dict[str, Any]:
    """Both the default and what is in force, for the page and the explainer.

    BOTH, always. A page that showed only the YAML would teach a gate that is
    not being obeyed; one that showed only the effective value would hide that
    the switch had been moved at all.
    """
    from src.strategies.services.strategy_definition import (
        POLICY_DESCRIPTIONS,
        POLICY_LABELS,
        POLICY_STATE_LABELS,
    )
    from src.strategies.services.strategy_registry import get_strategy_registry

    parameters = parameters or BtstParameters.from_definition(definition)
    defaults = defaults_as_dict(parameters)
    registry = get_strategy_registry()
    effective = resolve_policy(definition, parameters).as_dict()

    rows = []
    for policy, default in defaults.items():
        stored = registry.policy_override(definition.key, policy)
        on_label, off_label = POLICY_STATE_LABELS.get(
            policy, ("ENFORCED", "NOT ENFORCED")
        )
        rows.append(
            {
                "key": policy,
                "label": POLICY_LABELS.get(policy, policy),
                "onLabel": on_label,
                "offLabel": off_label,
                "description": POLICY_DESCRIPTIONS.get(policy, ""),
                "default": bool(default),
                "enforced": bool(effective[policy]),
                # None means nobody has touched the switch, which is a
                # different fact from "set to the same value as the default".
                "overridden": stored is not None,
            }
        )
    # No pair of these contradicts, so there is never a contradiction to
    # report. The key is present and null rather than absent, because the
    # framework's response model and the page both expect the field and a
    # missing one would read as an error rather than as "nothing to say".
    return {"policies": rows, "contradiction": None}


def validate_policy_change(
    definition: StrategyDefinition, policy: str, enforced: bool
) -> None:
    """Accepts every combination, and this is not an oversight.

    The rotation refuses one pair, because `off_gate.enabled` and a relaxed
    `regime.enforce` are two different answers to the same question. These two
    switches answer different questions -- WHEN to trade and WHAT to trade --
    and every combination of them is a strategy somebody could mean. A
    no-op that says so is better than an absent hook that leaves the next
    reader wondering whether the check was forgotten.
    """
    if policy not in BTST_POLICIES:
        logger.warning(
            "Ignoring a change to %r on %s: this strategy has no such rule. "
            "It has %s.",
            policy, definition.key, " and ".join(BTST_POLICIES),
        )


def policy_warnings(
    definition: StrategyDefinition, policy: str, enforced: bool
) -> List[str]:
    """What flipping this switch does -- and what it does NOT do."""
    label = definition.label
    warnings: List[str] = []

    if policy == POLICY_REGIME_ENFORCE and not enforced:
        warnings.append(
            f"{label} will take new positions while NIFTY 50 is BELOW its "
            f"200-session SMA. The gate is still computed and still recorded: "
            f"every trade opened this way carries the gate state and the policy "
            f"on its own decision row, so it can be filtered out of the numbers "
            f"later."
        )
        warnings.append(
            "This one costs less than the same switch does on the rotation. "
            "Only 259 of 3,016 signals in the whole backtest ever fire below "
            "the SMA, because a stock making a 55-day high on twice its volume "
            "as a six-month momentum leader is itself evidence of a healthy "
            "tape. Gate ON and gate OFF measure 18.98% and 19.27% CAGR; the "
            "gate earns its place on risk-adjusted return (MAR 2.05 against "
            "1.84), not on return."
        )
        warnings.append(
            "The gate-off signals are real but rare: 259 of them over eleven "
            "years, +0.497% gross, and as a standalone book 0.8% CAGR. Turning "
            "this off does not add a strategy; it removes a brake."
        )
    elif policy == POLICY_REGIME_ENFORCE and enforced:
        warnings.append(
            "New positions stop while NIFTY 50 is below its 200-session SMA. "
            "NOTHING ALREADY HELD IS AFFECTED -- every position in this "
            "strategy is sold at the next open regardless, which is the rule "
            "and not a consequence of this switch."
        )
    elif policy == POLICY_FNO_EXCLUDE and not enforced:
        warnings.append(
            f"{label} will scan the WHOLE universe, including the 210 names "
            f"with listed derivatives. Signal frequency roughly doubles, from "
            f"about 0.54 a session to about 1.07."
        )
        warnings.append(
            "IT ALSO WALKS INTO THE CLOSING AUCTION. For those names "
            "continuous cash trading ends at 15:15 and a call auction runs to "
            "15:35, so an order at the 15:20 scan cannot fill continuously and "
            "will be REFUSED and journalled, per instrument. Their session "
            "high, low and volume also stop moving at 15:15, against a closing "
            "price the auction has not set yet."
        )
        warnings.append(
            "And it takes the worse half of the edge: F&O names measured "
            "+0.512% gross per signal against the non-F&O half's +0.722%, with "
            "a lower win rate. Section 5a's own recommendation is to trade the "
            "non-F&O names only."
        )
    elif policy == POLICY_FNO_EXCLUDE and enforced:
        warnings.append(
            "Only names WITHOUT listed derivatives are scanned -- about 289 of "
            "the 499 in this universe. This is the specification's own "
            "recommendation: it removes the closing auction from the problem "
            "and carries the better half of the edge, at roughly half the "
            "signal frequency."
        )

    warnings.append(
        "It applies at the NEXT scan. A scan already in flight keeps the policy "
        "it started under -- and this one decides the universe, so a change "
        "mid-scan would rank two different populations against each other."
    )
    return warnings
