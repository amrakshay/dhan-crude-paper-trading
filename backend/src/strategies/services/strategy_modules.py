"""Which package owns a strategy's own rules, looked up by key.

`src/strategies/` is the framework. It knows the NAMES of the policies and the
settings -- that is what lets it ignore a stored row for something that no
longer exists -- and it deliberately does not learn what any of them MEAN. A
regime gate, a scan time and an F&O exclusion are facts about a strategy, and
they belong to the strategy's own package.

Until 2026-09-19 that lookup did not need to exist, because there was one
automated module and the framework could simply
`from src.swing.services.gate_policy import ...`. Both call sites said so in a
comment: *"a SECOND one needs a lookup by strategy key here rather than a
second import"*. This is that lookup, and nothing else changed -- the swing
functions are the same functions.

**It resolves to a MODULE PATH, imported inside the call.** Root
`backend/CLAUDE.md` section 5a: `src.strategies` must stay import-light,
because every low-level module reads the registry, and importing
`src.swing.anything` at module scope here would close a loop the application
cannot start through.

**A module that is not listed has no policies and no settings**, which is a
real answer rather than a missing one: `mcx-crude-options` is discretionary and
has a person in front of every order. `hooks_for` returns `None` for it, and
every caller already handles the empty case because the discretionary branch
came first.
"""
from importlib import import_module
from types import ModuleType
from typing import Dict, Optional

from src.logging_config import get_logger

logger = get_logger("strategies.modules")


class ModuleRuleRefused(Exception):
    """A strategy module refusing a change, with the reason in the message.

    Declared HERE, in the framework, rather than in each module: the framework
    has to be able to tell "this operator asked for something this strategy
    cannot run" (a 400 with the module's own sentence) from "this module is
    broken" (a 500 nobody should see as a validation message). Catching bare
    `Exception` at the seam would turn every AttributeError into a plausible
    refusal, which is how a bug gets shipped looking like a rule.
    """

# strategy key -> the module exposing that strategy's rule hooks.
#
# The hook module is a thin re-export rather than the implementation: what a
# regime gate means still lives in `gate_policy.py` beside the runner that
# reads it, and what a scan time means still lives beside the scheduler. This
# is only the seam the framework dispatches through.
MODULE_HOOKS: Dict[str, str] = {
    "nse-swing-momentum": "src.swing.services.module_hooks",
    "nse-btst-overnight": "src.btst.services.module_hooks",
}

# What a hook module must provide. Named here so a module that half-implements
# the protocol fails loudly at the seam rather than at the call site, with the
# strategy's name in the message.
REQUIRED_HOOKS = (
    "describe_policies",
    "policy_warnings",
    "validate_policy_change",
    "describe_settings",
    "validate_setting",
    "setting_warnings",
)


def hooks_for(definition) -> Optional[ModuleType]:
    """The module owning this strategy's rules, or `None` if it owns none.

    `None` for a discretionary module and for anything not listed. It is not an
    error: a strategy with nobody scheduling it has no rule to enforce and no
    clock to move.
    """
    if definition is None or not definition.automation.automated:
        return None
    path = MODULE_HOOKS.get(definition.key)
    if path is None:
        logger.warning(
            "Strategy %s declares an automation block but no module is "
            "registered for it in MODULE_HOOKS, so it will be offered no "
            "policy switches and no settings.",
            definition.key,
        )
        return None

    module = import_module(path)
    missing = [name for name in REQUIRED_HOOKS if not hasattr(module, name)]
    if missing:
        raise AttributeError(
            f"{path} is registered as the rule module for {definition.key} but "
            f"does not provide {', '.join(missing)}."
        )
    return module
