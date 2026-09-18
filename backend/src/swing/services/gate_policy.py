"""Whether this strategy's regime rules are ENFORCED, and where that is decided.

Read root `CLAUDE.md` section 3a first. It says the YAML says what a strategy
IS, the database says whether it is ON, and nothing about a strategy's rates,
specs or margin model is editable from the UI. **That rule survives this module
and this module fits inside it**, because what becomes editable here is not a
PARAMETER of a rule. P8's 200 sessions, P9's 63 sessions and its zero
threshold, P6's momentum floor, P14's 3.5 x ATR -- all of those stay in
`conf/strategies/nse-swing-momentum.yaml` and are editable from nowhere. What an
operator may flip is whether a rule is OBEYED, which is the same kind of fact as
enabled and armed: runtime state overlaid on a default the YAML declares.

Say that out loud wherever this comes up, because the next person to read it
will otherwise assume the rule was simply abandoned. It was not. It is still
computed, still recorded on every session, and now also recorded on every
decision and every position, precisely so that trades taken while a rule was
relaxed can be filtered out of the numbers afterwards.

Three switches, all booleans, all per strategy:

    regime.enforce                  P8 and P17
    regime.enforce_entry_return     P9
    off_gate.enabled                the V3b variant

Each default comes from the strategy's own YAML; the live value is a
`feature_toggles` row under `SCOPE_POLICY` with `toggle_key` of
"<strategy_key>/<policy>". The registry stores the override and knows nothing
about momentum; this module resolves the two into one frozen value.

**THE POLICY IS READ WHEN A RUN STARTS.** It is passed down as a value, never
re-read mid-run. A decision taken half under one policy and half under another
is not a decision anybody can audit.

**What relaxing the gate does NOT change.** The breadth ramp still sizes the
book, the momentum floor still applies, the rotation exit still fires at rank
> 15, and the chandelier stop is untouched. The only thing that changes is
whether P8/P17 and P9 stop anything.

**What it costs**, stated once, because the divergence is deliberate:
section 11 of the specification tested thirteen ways of trading while the gate
is off and NOT ONE beat holding cash -- the best of them, V3b, has genuinely
good individual trades (38 over 11 years, 61% win, mean +7.81%) and still ends
lower than the baseline, because capital committed to a bear rally is not
available at the regime flip, which is exactly when the best trades fire.
"""
from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.logging_config import get_logger
from src.strategies.services.strategy_definition import (
    POLICY_OFF_GATE_ENABLED,
    POLICY_REGIME_ENFORCE,
    POLICY_REGIME_ENFORCE_ENTRY_RETURN,
    StrategyDefinition,
)
from src.swing.services.swing_parameters import SwingParameters

logger = get_logger("swing.policy")


class GatePolicyError(Exception):
    """Two operator-chosen switches that contradict each other."""


@dataclass(frozen=True)
class GatePolicy:
    """Which of this strategy's regime rules are in force, right now.

    Frozen and dependency-free on purpose: `effective_gate()` takes one of
    these as a plain value, so the tests that pin the gate's behaviour -- which
    are the entire safety net for this change -- need no registry, no database
    and no process-wide singleton.
    """

    enforce_regime: bool
    enforce_entry_return: bool
    off_gate_enabled: bool

    def __post_init__(self) -> None:
        # REFUSED, not resolved by precedence. Both of these say what to do
        # while the index is below its SMA and they say different things: V3b
        # holds a smaller book at its own momentum floor and its own flat slot
        # count, while a relaxed gate trades the ordinary rule through the
        # gate on the breadth ramp. A silent precedence rule between two
        # switches an operator chose is exactly what nobody remembers six
        # months later, and the UI is not the place to enforce it -- a stored
        # row, a script or a future caller would walk straight past that.
        if self.off_gate_enabled and not self.enforce_regime:
            raise GatePolicyError(
                "'off_gate.enabled' and a relaxed 'regime.enforce' are two "
                "different overrides of the same thing: what to do while the "
                "index is below its 200-session SMA. V3b holds a small book at "
                "its own slot count and momentum floor; a relaxed regime gate "
                "trades the ordinary rule on the breadth ramp. Choose one -- "
                "switch off 'Trade the off-gate variant', or switch 'Enforce "
                "the regime gate' back on."
            )

    @property
    def relaxed(self) -> bool:
        """Is any rule currently not being enforced?"""
        return not self.enforce_regime or not self.enforce_entry_return

    def as_dict(self) -> Dict[str, Any]:
        return {
            POLICY_REGIME_ENFORCE: self.enforce_regime,
            POLICY_REGIME_ENFORCE_ENTRY_RETURN: self.enforce_entry_return,
            POLICY_OFF_GATE_ENABLED: self.off_gate_enabled,
        }


def default_policy(parameters: SwingParameters) -> GatePolicy:
    """What the YAML alone says -- what a fresh checkout does."""
    return GatePolicy(
        enforce_regime=parameters.regime.enforce_by_default,
        enforce_entry_return=parameters.regime.enforce_entry_return_by_default,
        off_gate_enabled=parameters.off_gate.enabled,
    )


def defaults_as_dict(parameters: SwingParameters) -> Dict[str, bool]:
    """The YAML defaults, without constructing a `GatePolicy`.

    Separate from `default_policy` because the defaults are reported on the
    Strategies page and the explainer BESIDE the effective values, and a YAML
    that itself declared the contradictory pair must still be describable when
    the service refuses to build it.
    """
    return {
        POLICY_REGIME_ENFORCE: parameters.regime.enforce_by_default,
        POLICY_REGIME_ENFORCE_ENTRY_RETURN: (
            parameters.regime.enforce_entry_return_by_default
        ),
        POLICY_OFF_GATE_ENABLED: parameters.off_gate.enabled,
    }


def resolve_gate_policy(
    definition: StrategyDefinition,
    parameters: Optional[SwingParameters] = None,
    overrides: Optional[Dict[str, bool]] = None,
) -> GatePolicy:
    """The YAML's defaults with the operator's stored switches on top.

    Synchronous and cached for the same reason `is_enabled` and `is_armed` are:
    it is read from the rebalance, the nightly run and the page, and the
    registry holds the state in memory precisely so code that cannot await can
    ask. `StrategyStateService` refreshes it at startup and after every change.

    `overrides` exists for the tests: pass a dict and the registry is not
    consulted at all.
    """
    parameters = parameters or SwingParameters.from_definition(definition)
    defaults = defaults_as_dict(parameters)

    if overrides is None:
        from src.strategies.services.strategy_registry import get_strategy_registry

        registry = get_strategy_registry()
        overrides = {}
        for policy in defaults:
            stored = registry.policy_override(definition.key, policy)
            if stored is not None:
                overrides[policy] = stored

    resolved = {**defaults, **{k: bool(v) for k, v in (overrides or {}).items()}}
    return GatePolicy(
        enforce_regime=bool(resolved[POLICY_REGIME_ENFORCE]),
        enforce_entry_return=bool(resolved[POLICY_REGIME_ENFORCE_ENTRY_RETURN]),
        off_gate_enabled=bool(resolved[POLICY_OFF_GATE_ENABLED]),
    )


def describe_policies(
    definition: StrategyDefinition, parameters: Optional[SwingParameters] = None
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

    parameters = parameters or SwingParameters.from_definition(definition)
    defaults = defaults_as_dict(parameters)
    registry = get_strategy_registry()

    rows = []
    contradiction = None
    try:
        effective = resolve_gate_policy(definition, parameters).as_dict()
    except GatePolicyError as error:
        # Describable even when unusable: an operator looking at the page is
        # exactly who has to see which pair is refused and why.
        contradiction = str(error)
        effective = dict(defaults)
        for policy in defaults:
            stored = registry.policy_override(definition.key, policy)
            if stored is not None:
                effective[policy] = stored

    for policy, default in defaults.items():
        stored = registry.policy_override(definition.key, policy)
        on_label, off_label = POLICY_STATE_LABELS.get(
            policy, ("ENFORCED", "NOT ENFORCED")
        )
        rows.append(
            {
                "key": policy,
                "label": POLICY_LABELS.get(policy, policy),
                # Two of these switches are about whether a rule is obeyed and
                # one is about whether a variant is traded. "NOT ENFORCED" on
                # the third would be a word that does not mean what it says.
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
    return {"policies": rows, "contradiction": contradiction}


def policy_warnings(
    definition: StrategyDefinition, policy: str, enforced: bool
) -> list:
    """What flipping this switch does -- and what it does NOT do.

    Both halves matter and the second is the one that gets missed. Re-enforcing
    the regime gate is a settings change, not a liquidation: new entries stop
    and the open book is left exactly where it is, because every position keeps
    the policy it was opened under. A dialog that did not say so would be
    inviting an operator to expect a sale that is never coming.
    """
    label = definition.label
    warnings: list = []

    if policy == POLICY_REGIME_ENFORCE and not enforced:
        warnings.append(
            f"{label} will buy while the index is BELOW its 200-session SMA, "
            f"and will not sell the book when the gate flips off. The gate is "
            f"still computed and still recorded: every trade opened this way "
            f"carries the gate state and the policy on its own record, so it "
            f"can be filtered out of the numbers later."
        )
        warnings.append(
            "This is a deliberate divergence from the specification. Section 11 "
            "tested thirteen ways of trading while the gate is off and NOT ONE "
            "beat holding cash -- the best of them has genuinely good individual "
            "trades (61% win rate, mean +7.81%) and still ends lower than the "
            "baseline, because capital committed to a bear rally is not "
            "available at the regime flip, which is exactly when the best "
            "trades fire."
        )
        warnings.append(
            "It changes nothing else: breadth still sizes the book, the "
            "momentum floor still applies, the rotation exit still fires at "
            "rank > 15, and the chandelier stop is untouched."
        )
    elif policy == POLICY_REGIME_ENFORCE and enforced:
        warnings.append(
            f"New entries stop while the index is below its 200-session SMA. "
            f"POSITIONS ALREADY OPEN ARE NOT SOLD -- they keep the policy they "
            f"were opened under and will leave by rotation (rank > 15) or by "
            f"their trailing stop. This is a settings change, not a "
            f"liquidation."
        )
    elif policy == POLICY_REGIME_ENFORCE_ENTRY_RETURN and not enforced:
        warnings.append(
            f"{label} will take new entries even when the index's 63-session "
            f"return is negative. It never forced an exit either way; what "
            f"stops is the block on NEW positions."
        )
    elif policy == POLICY_REGIME_ENFORCE_ENTRY_RETURN and enforced:
        warnings.append(
            "New entries are blocked while the index's 63-session return is "
            "not above zero. Open positions are unaffected -- this filter has "
            "never forced an exit."
        )
    elif policy == POLICY_OFF_GATE_ENABLED and enforced:
        warnings.append(
            "The V3b variant holds a small book at its own slot count and its "
            "own momentum floor while the regime gate is off, instead of "
            "holding cash. The owner's own research tested thirteen such "
            "variants on the extended panel and not one beat holding cash."
        )
        warnings.append(
            "It cannot be combined with a relaxed regime gate: both say what "
            "to do below the SMA, and they say different things."
        )

    warnings.append(
        "It applies at the NEXT decision. A run already in flight keeps the "
        "policy it started under -- a decision taken half under one policy and "
        "half under another is not one anybody can audit."
    )
    return warnings
