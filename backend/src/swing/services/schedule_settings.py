"""WHEN this strategy wakes up, and why those two times are not P1-P19.

The sibling of `gate_policy.py`. That module answers "is this rule enforced",
which is a boolean; this one answers "at what time", which is a value -- and
the two are stored in different tables for exactly that reason.

**Read root `CLAUDE.md` section 3a before adding anything here.** It says the
YAML says what a strategy IS and nothing about its rates, specs or margin model
is editable from a page, and these two settings fit inside that rather than
bending it:

  * The momentum floor, the ATR multiple, the breadth ramp, the rank cut-off,
    every lookback and the rebalance CADENCE define the strategy. They were
    chosen in-sample against the specification's own table, the YAML has to
    stay greppable against it, and they are editable from nowhere. P18 -- the
    cadence -- is the one that most looks like a schedule and is not: daily
    measured 23.4% CAGR at -22.2% drawdown against weekly's 19.9% and -18.3%,
    and picking between them is choosing a different strategy.
  * These two are the clock the machine wakes up on. Moving the order placement
    from 09:16 to 09:30 does not change what the rule says; it changes when
    this installation acts on it, like `swing.scheduler_interval_seconds`.

Each has a HARD constraint that is about correctness rather than taste, and
both are refused rather than warned about:

  * **The analysis time may not fall inside the session.** It refreshes
    `daily_bars` from Dhan, and Dhan hands back TODAY'S FORMING BAR during
    trading hours -- which would be stored as a finished daily bar. Every
    ranking, ATR and chandelier stop afterwards reads that row and nothing
    downstream can tell it was half a day's trading. It is silent corruption of
    the one table the whole strategy is computed from.
  * **The order placement time must fall inside continuous trading.** An order
    outside the session is refused per instrument, so a time outside it would
    configure a strategy that decides every day and never trades.

Both are read when a RUN starts, like the gate policy, and a change applies to
the next run rather than to one already in flight.
"""
from dataclasses import dataclass
from datetime import time
from typing import Any, Dict, List, Optional

from src.core.time_utils import parse_hhmm
from src.logging_config import get_logger
from src.strategies.services.strategy_definition import (
    SETTING_NIGHTLY_AT,
    SETTING_REBALANCE_AT,
    StrategyDefinition,
)
from src.swing.services.swing_parameters import SwingParameters

logger = get_logger("swing.schedule")


class ScheduleSettingError(Exception):
    """A time that would break something, with the reason in the message."""


@dataclass(frozen=True)
class EffectiveSchedule:
    """The two clock times actually in force, as `HH:MM` strings."""

    nightly_at: str
    rebalance_at: str

    @property
    def nightly(self) -> time:
        return parse_hhmm(self.nightly_at)

    @property
    def rebalance(self) -> time:
        return parse_hhmm(self.rebalance_at)


def _parse(value: str, label: str) -> time:
    text = str(value).strip()
    try:
        return parse_hhmm(text)
    except Exception as error:  # noqa: BLE001 - any parse failure is the same answer
        raise ScheduleSettingError(
            f"{label}: {value!r} is not a time of day. Use 24-hour HH:MM, for "
            f"example 18:15."
        ) from error


def validate(
    definition: StrategyDefinition, setting: str, value: str
) -> str:
    """Refuse a time that would break something. Returns the normalised value.

    Refused rather than warned about: both failures are silent afterwards. A
    forming bar stored as a daily bar looks exactly like a real one, and a
    strategy whose order time sits outside the session simply never trades and
    journals a refusal every morning that nobody is watching for.
    """
    hours = definition.market_hours
    label = (
        "Analysis of stocks" if setting == SETTING_NIGHTLY_AT else "Order placement time"
    )
    parsed = _parse(value, label)
    normalised = parsed.strftime("%H:%M")

    if setting == SETTING_NIGHTLY_AT:
        if hours.open <= parsed <= hours.close:
            raise ScheduleSettingError(
                f"{normalised} is inside the trading session "
                f"({hours.open.strftime('%H:%M')}-{hours.close.strftime('%H:%M')} "
                f"IST). The analysis refreshes daily bars from Dhan, and during "
                f"the session Dhan returns today's HALF-FINISHED bar -- which "
                f"would be stored as a finished daily bar and silently change "
                f"every ranking, ATR and trailing stop computed from it "
                f"afterwards. Pick a time after the close (the shipped default "
                f"is 18:15) or before the open."
            )
        return normalised

    if setting == SETTING_REBALANCE_AT:
        continuous_close = hours.close
        auction = hours.closing_auction
        if not (hours.open <= parsed <= continuous_close):
            raise ScheduleSettingError(
                f"{normalised} is outside continuous trading "
                f"({hours.open.strftime('%H:%M')}-"
                f"{continuous_close.strftime('%H:%M')} IST). An order sent "
                f"outside the session is refused per instrument, so this would "
                f"configure a strategy that decides every day and never trades."
            )
        if auction is not None and parsed > auction.continuous_close:
            # Legal, and it costs something specific, so it is said rather than
            # refused: this is a preference, not a correctness failure.
            logger.warning(
                "Order placement time %s is after %s, when continuous trading "
                "ends for F&O-eligible names. Those orders will be refused and "
                "journalled; the rest of the market still trades to %s.",
                normalised,
                auction.continuous_close.strftime("%H:%M"),
                continuous_close.strftime("%H:%M"),
            )
        return normalised

    raise ScheduleSettingError(f"Unknown setting {setting!r}.")


def resolve_schedule(
    definition: StrategyDefinition,
    parameters: Optional[SwingParameters] = None,
    overrides: Optional[Dict[str, str]] = None,
) -> EffectiveSchedule:
    """The YAML's times with the operator's stored ones on top.

    Synchronous and cached for the same reason `is_enabled` and the gate policy
    are: the scheduler reads it from code that cannot await.

    A stored value that no longer validates -- because the market hours moved
    under it, say -- is IGNORED with a loud log rather than applied or raised.
    The scheduler must keep running on the YAML's own times in that case; a
    strategy that stops deciding because a stored string went stale would be a
    worse failure than one running on its shipped schedule.
    """
    parameters = parameters or SwingParameters.from_definition(definition)
    schedule = parameters.schedule
    values = {
        SETTING_NIGHTLY_AT: schedule.nightly_at,
        SETTING_REBALANCE_AT: schedule.rebalance_at,
    }

    if overrides is None:
        from src.strategies.services.strategy_registry import get_strategy_registry

        registry = get_strategy_registry()
        overrides = {}
        for setting in values:
            stored = registry.setting_override(definition.key, setting)
            if stored is not None:
                overrides[setting] = stored

    for setting, stored in (overrides or {}).items():
        if setting not in values:
            continue
        try:
            values[setting] = validate(definition, setting, stored)
        except ScheduleSettingError as error:
            logger.error(
                "Ignoring the stored value %r for %s on %s: %s. Running on the "
                "configured default %r instead.",
                stored, setting, definition.key, error, values[setting],
            )

    return EffectiveSchedule(
        nightly_at=values[SETTING_NIGHTLY_AT],
        rebalance_at=values[SETTING_REBALANCE_AT],
    )


def describe_settings(
    definition: StrategyDefinition, parameters: Optional[SwingParameters] = None
) -> Dict[str, Any]:
    """Both the shipped value and the one in force, for the Configuration tab.

    BOTH, always, for the same reason the policies report both: a page showing
    only the file would describe a schedule nothing runs on, and one showing
    only the effective value would hide that it had been moved.
    """
    from src.strategies.services.strategy_definition import (
        SETTING_DESCRIPTIONS,
        SETTING_LABELS,
    )
    from src.strategies.services.strategy_registry import get_strategy_registry

    parameters = parameters or SwingParameters.from_definition(definition)
    registry = get_strategy_registry()
    effective = resolve_schedule(definition, parameters)
    hours = definition.market_hours
    auction = hours.closing_auction

    defaults = {
        SETTING_NIGHTLY_AT: parameters.schedule.nightly_at,
        SETTING_REBALANCE_AT: parameters.schedule.rebalance_at,
    }
    current = {
        SETTING_NIGHTLY_AT: effective.nightly_at,
        SETTING_REBALANCE_AT: effective.rebalance_at,
    }
    allowed = {
        SETTING_NIGHTLY_AT: (
            f"Any time OUTSIDE {hours.open.strftime('%H:%M')}-"
            f"{hours.close.strftime('%H:%M')} IST."
        ),
        SETTING_REBALANCE_AT: (
            f"Inside {hours.open.strftime('%H:%M')}-"
            f"{hours.close.strftime('%H:%M')} IST."
            + (
                f" After {auction.continuous_close.strftime('%H:%M')}, "
                f"F&O-eligible names cannot trade continuously and their orders "
                f"are refused."
                if auction is not None
                else ""
            )
        ),
    }

    rows: List[Dict[str, Any]] = []
    for setting, default in defaults.items():
        stored = registry.setting_override(definition.key, setting)
        rows.append(
            {
                "key": setting,
                "label": SETTING_LABELS.get(setting, setting),
                "description": SETTING_DESCRIPTIONS.get(setting, ""),
                "kind": "time",
                "default": default,
                "value": current[setting],
                # None means nobody has touched it, which is a different fact
                # from its being set to the same value as the default.
                "overridden": stored is not None,
                "allowed": allowed[setting],
            }
        )
    return {"settings": rows}


def setting_warnings(
    definition: StrategyDefinition, setting: str, value: str
) -> List[str]:
    """What moving this time does, said before it is moved."""
    warnings: List[str] = []
    hours = definition.market_hours
    auction = hours.closing_auction
    try:
        parsed = _parse(value, setting)
    except ScheduleSettingError:
        return warnings

    if setting == SETTING_NIGHTLY_AT:
        warnings.append(
            "The analysis places no order at any hour. What moves is when "
            "prices are refreshed, the ranking is recomputed and every trailing "
            "stop is ratcheted on to the session's close."
        )
        warnings.append(
            "A full refresh of 499 symbols takes about 12 minutes at Dhan's "
            "rate limits, so leave room before anything else needs the data."
        )
    elif setting == SETTING_REBALANCE_AT:
        warnings.append(
            "This is when it SPENDS MONEY. The rule buys at the next session's "
            "open, so the further this drifts from the open the further the "
            "fills drift from what the specification measured."
        )
        if auction is not None and parsed > auction.continuous_close:
            warnings.append(
                f"After {auction.continuous_close.strftime('%H:%M')} continuous "
                f"cash trading has ended for F&O-eligible names -- 210 of the "
                f"499 in this universe. Their orders will be refused and "
                f"journalled; the rest of the market still trades to "
                f"{hours.close.strftime('%H:%M')}."
            )

    warnings.append(
        "It applies to the NEXT run. A job already running keeps the schedule "
        "it started under, and a job whose time has already passed today does "
        "not fire retrospectively."
    )
    return warnings
