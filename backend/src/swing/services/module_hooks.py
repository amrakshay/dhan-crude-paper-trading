"""The rotation's rule hooks, as `src/strategies/` dispatches to them.

A seam, not an implementation. Everything here already existed and is unchanged
-- what a regime gate means still lives in `gate_policy.py` beside the runner
that reads it, and what a clock time means still lives in
`schedule_settings.py` beside the scheduler. This module only presents them
under the names `strategy_modules.hooks_for` looks for, so that the framework
can find the SECOND automated module without importing either by name.

Adding this changed no behaviour of the rotation, which is live and armed. The
framework used to import these same three modules directly; now it imports this
one, which imports them.
"""
from datetime import date, datetime
from typing import Dict, List

from src.strategies.services.scheduling import JobRun, MissedRun
from src.strategies.services.strategy_definition import (
    KNOWN_POLICIES,
    StrategyDefinition,
)
from src.strategies.services.strategy_modules import ModuleRuleRefused
from src.swing.services.gate_policy import (
    GatePolicyError,
    describe_policies,
    policy_warnings,
    resolve_gate_policy,
)
from src.swing.services.schedule_settings import (
    ScheduleSettingError,
    describe_settings,
    setting_warnings,
    validate,
)

__all__ = [
    "describe_policies",
    "policy_warnings",
    "validate_policy_change",
    "describe_settings",
    "validate_setting",
    "setting_warnings",
    "tick_strategy",
    "detect_missed",
]


class PolicyRefused(ModuleRuleRefused):
    """A policy change this module will not accept, with the reason."""


class SettingRefused(ModuleRuleRefused):
    """A setting value this module will not accept, with the reason."""


def validate_policy_change(
    definition: StrategyDefinition, policy: str, enforced: bool
) -> None:
    """Refuse a combination that contradicts itself, before anything is stored.

    The rotation has exactly one such pair: `off_gate.enabled` together with a
    relaxed `regime.enforce`. Both say what to do below the 200-session SMA and
    they say different things. `GatePolicy.__post_init__` is where that lives,
    so this reaches it by building the policy the operator is asking for.
    """
    from src.strategies.services.strategy_registry import get_strategy_registry

    registry = get_strategy_registry()
    overrides: Dict[str, bool] = {}
    for known in KNOWN_POLICIES:
        stored = registry.policy_override(definition.key, known)
        if stored is not None:
            overrides[known] = stored
    overrides[policy] = bool(enforced)

    try:
        resolve_gate_policy(definition, overrides=overrides)
    except GatePolicyError as error:
        raise PolicyRefused(str(error)) from error


def validate_setting(definition: StrategyDefinition, setting: str, value: str) -> str:
    """The normalised value, or a refusal naming what it would break."""
    try:
        return validate(definition, setting, value)
    except ScheduleSettingError as error:
        raise SettingRefused(str(error)) from error


# --- the clock ------------------------------------------------------------
#
# Both of these delegate straight back into `SwingScheduler`. The rotation's
# job bodies were not moved, not rewritten and not wrapped -- it is live and
# armed, and the change on 2026-09-19 was to WHO CALLS THEM, not to what they
# do. BTST's equivalents are new code in `src/btst/services/module_hooks.py`
# and share none of this.


async def tick_strategy(
    scheduler, definition: StrategyDefinition, now: datetime
) -> List[JobRun]:
    """Run whatever the rotation has due: the warm-up, the rebalance, the
    nightly."""
    return await scheduler._tick_strategy(definition, now)  # noqa: SLF001


async def detect_missed(
    definition: StrategyDefinition, session, expected: List[date]
) -> List[MissedRun]:
    """Which of these sessions has no NIGHTLY / REBALANCE record.

    The calendar is handed in -- a date NSE published a bar for is a date NSE
    traded, whoever is asking -- and what is checked against it is this
    module's own two run kinds and its own journal.

    Bounded below by the first session this strategy ever COMPLETED. Nothing
    before the journal existed can be missing from it, and without that bound a
    fresh installation reports every date in the lookback as missed, including
    ones from before the module was written.
    """
    from src.swing.database.db_operations.swing_session_repository import (
        SwingSessionRepository,
    )
    from src.swing.database.db_models.swing_session_model import (
        RUN_NIGHTLY,
        RUN_REBALANCE,
    )

    sessions = SwingSessionRepository(session)
    live_from = await sessions.first_completed_session(definition.key)
    if live_from is None:
        # Never completed a run. Not a gap -- no history at all, which is a
        # different state and one the Live tab already shows.
        return []
    expected = [one for one in expected if one >= live_from]
    if not expected:
        return []

    found: List[MissedRun] = []
    for kind in (RUN_NIGHTLY, RUN_REBALANCE):
        decided = set(
            await sessions.decided_session_dates(definition.key, kind, expected[0])
        )
        gaps = [one for one in expected if one not in decided]
        if gaps:
            found.append(
                MissedRun(strategy_key=definition.key, kind=kind, sessions=gaps)
            )
    return found
