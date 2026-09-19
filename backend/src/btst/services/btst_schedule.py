"""WHEN this strategy wakes up. The sibling of
`src/swing/services/schedule_settings.py`, and constrained in the OPPOSITE
direction.

That module refuses an analysis time INSIDE the session: the rotation's nightly
refreshes `daily_bars` from Dhan, and during trading hours Dhan hands back
today's forming bar, which would be stored as a finished daily bar and silently
change every ranking and stop computed from it afterwards.

**This module refuses a scan time OUTSIDE the session, for the mirror-image
reason.** BTST reads the forming session on purpose -- `hi_sofar`, `lo_sofar`
and `vol_sofar` from the live book are three of its seven filters -- and
outside trading hours there is no forming session to read. A scan at 18:15
would measure an empty book, qualify nothing, and journal a decision that meant
nothing.

That is why the validator is per module rather than one shared rule with a
branch in it. Two strategies genuinely want opposite things from the same kind
of setting, and a single function that asked which strategy it was looking at
would be the framework learning what a momentum rotation is.

Two settings, both `strategy_settings` rows overlaid on the YAML:

    schedule.scan_at   the afternoon scan; must be INSIDE continuous trading
    schedule.exit_at   the morning exit;   must be INSIDE continuous trading,
                       and early in it

Both are read when a RUN starts and a change applies to the next run, never to
one in flight.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional

from src.btst.services.btst_parameters import BtstParameters
from src.core.time_utils import parse_hhmm
from src.logging_config import get_logger
from src.strategies.services.strategy_definition import (
    SETTING_EXIT_AT,
    SETTING_SCAN_AT,
    StrategyDefinition,
)

logger = get_logger("btst.schedule")

# How long after the open the exit stops being "at the open" in any useful
# sense. Not a parameter of the rule and not editable: it is the boundary of
# what this module is willing to call an exit at the open, and the
# specification's own section 14 says "pre-open or market-on-open".
EXIT_MUST_BE_WITHIN = timedelta(minutes=45)


class BtstScheduleError(Exception):
    """A time that would break something, with the reason in the message."""


@dataclass(frozen=True)
class EffectiveSchedule:
    """The two clock times actually in force, as `HH:MM` strings."""

    scan_at: str
    exit_at: str
    exit_alarm_after_minutes: int

    @property
    def scan(self) -> time:
        return parse_hhmm(self.scan_at)

    @property
    def exit(self) -> time:
        return parse_hhmm(self.exit_at)

    def exit_alarm_at(self, on_day: date) -> datetime:
        """When a position still open stops being late and becomes an alarm."""
        return datetime.combine(on_day, self.exit) + timedelta(
            minutes=self.exit_alarm_after_minutes
        )


def _parse(value: str, label: str) -> time:
    text = str(value).strip()
    try:
        return parse_hhmm(text)
    except Exception as error:  # noqa: BLE001 - any parse failure is the same answer
        raise BtstScheduleError(
            f"{label}: {value!r} is not a time of day. Use 24-hour HH:MM, for "
            f"example 15:20."
        ) from error


def validate(definition: StrategyDefinition, setting: str, value: str) -> str:
    """Refuse a time that would break something. Returns the normalised value.

    Refused rather than warned about, the same way the rotation's are, and for
    the same reason: both failures are silent afterwards. A scan outside the
    session qualifies nothing and journals an empty decision every day; an exit
    outside the session is refused per instrument and leaves the book held
    through a second session, which is precisely the thing that takes this
    strategy's win rate from 71.4% to 49.0%.
    """
    hours = definition.market_hours
    auction = hours.closing_auction
    label = "Afternoon scan" if setting == SETTING_SCAN_AT else "Morning exit"
    parsed = _parse(value, label)
    normalised = parsed.strftime("%H:%M")
    open_text = hours.open.strftime("%H:%M")
    close_text = hours.close.strftime("%H:%M")

    if setting == SETTING_SCAN_AT:
        if not (hours.open <= parsed <= hours.close):
            raise BtstScheduleError(
                f"{normalised} is outside the trading session "
                f"({open_text}-{close_text} IST). This strategy decides on the "
                f"session SO FAR -- each candidate's running high, low and "
                f"cumulative volume from the live feed -- so outside the "
                f"session there is nothing to read: it would qualify nothing "
                f"and journal an empty decision every day. That is the "
                f"opposite of the rotation's constraint, and deliberately so. "
                f"The shipped default is 15:20."
            )
        if auction is not None and parsed <= auction.continuous_close:
            # Legal and quieter, so it is said rather than refused. Scanning
            # before 15:15 would let the F&O names back in as far as the clock
            # is concerned -- but `fno.exclude` still governs the universe, so
            # this is a note about what becomes POSSIBLE, not about what
            # happens.
            logger.info(
                "Scan time %s is at or before %s, when continuous trading ends "
                "for F&O-eligible names. Those names can execute continuously "
                "at that hour -- but whether they are scanned at all is the "
                "'fno.exclude' policy, not the clock.",
                normalised, auction.continuous_close.strftime("%H:%M"),
            )
        return normalised

    if setting == SETTING_EXIT_AT:
        if not (hours.open <= parsed <= hours.close):
            raise BtstScheduleError(
                f"{normalised} is outside continuous trading "
                f"({open_text}-{close_text} IST). An order sent outside the "
                f"session is refused per instrument, so this would leave every "
                f"position held through a second session -- which is the one "
                f"thing this strategy cannot do. The same signals held to the "
                f"next CLOSE instead of the next OPEN measure a 49.0% win rate "
                f"against 71.4%."
            )
        latest = (
            datetime.combine(date(2000, 1, 1), hours.open) + EXIT_MUST_BE_WITHIN
        ).time()
        if parsed > latest:
            raise BtstScheduleError(
                f"{normalised} is more than "
                f"{int(EXIT_MUST_BE_WITHIN.total_seconds() // 60)} minutes "
                f"after the {open_text} open, which is not an exit at the open "
                f"in any sense the specification would recognise. THE "
                f"OVERNIGHT GAP IS THE ENTIRE EDGE and it is given back during "
                f"the session: the same positions held to the next close "
                f"measure +0.428% at a 49.0% win rate against +0.617% at "
                f"71.4%. Pick a time at or shortly after the open; the shipped "
                f"default is 09:16."
            )
        return normalised

    raise BtstScheduleError(f"Unknown setting {setting!r}.")


def resolve_schedule(
    definition: StrategyDefinition,
    parameters: Optional[BtstParameters] = None,
    overrides: Optional[Dict[str, str]] = None,
) -> EffectiveSchedule:
    """The YAML's times with the operator's stored ones on top.

    A stored value that no longer validates is IGNORED with a loud log rather
    than applied or raised, exactly as the rotation's are: a strategy that
    stopped running because a stored string went stale would be a worse failure
    than one running on its shipped schedule.
    """
    parameters = parameters or BtstParameters.from_definition(definition)
    schedule = parameters.schedule
    values = {
        SETTING_SCAN_AT: schedule.scan_at,
        SETTING_EXIT_AT: schedule.exit_at,
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
        except BtstScheduleError as error:
            logger.error(
                "Ignoring the stored value %r for %s on %s: %s. Running on the "
                "configured default %r instead.",
                stored, setting, definition.key, error, values[setting],
            )

    return EffectiveSchedule(
        scan_at=values[SETTING_SCAN_AT],
        exit_at=values[SETTING_EXIT_AT],
        exit_alarm_after_minutes=schedule.exit_alarm_after_minutes,
    )


def describe_settings(
    definition: StrategyDefinition, parameters: Optional[BtstParameters] = None
) -> Dict[str, Any]:
    """Both the shipped value and the one in force, for the Configuration tab."""
    from src.strategies.services.strategy_definition import (
        SETTING_DESCRIPTIONS,
        SETTING_LABELS,
    )
    from src.strategies.services.strategy_registry import get_strategy_registry

    parameters = parameters or BtstParameters.from_definition(definition)
    registry = get_strategy_registry()
    effective = resolve_schedule(definition, parameters)
    hours = definition.market_hours
    auction = hours.closing_auction
    open_text = hours.open.strftime("%H:%M")
    close_text = hours.close.strftime("%H:%M")
    latest_exit = (
        datetime.combine(date(2000, 1, 1), hours.open) + EXIT_MUST_BE_WITHIN
    ).time().strftime("%H:%M")

    defaults = {
        SETTING_SCAN_AT: parameters.schedule.scan_at,
        SETTING_EXIT_AT: parameters.schedule.exit_at,
    }
    current = {
        SETTING_SCAN_AT: effective.scan_at,
        SETTING_EXIT_AT: effective.exit_at,
    }
    allowed = {
        SETTING_SCAN_AT: (
            f"Inside {open_text}-{close_text} IST -- the session so far is what "
            f"it measures."
            + (
                f" At or before {auction.continuous_close.strftime('%H:%M')}, "
                f"F&O-eligible names can still execute continuously."
                if auction is not None
                else ""
            )
        ),
        SETTING_EXIT_AT: (
            f"Between {open_text} and {latest_exit} IST. The overnight gap is "
            f"the whole edge and it is given back during the session."
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
    except BtstScheduleError:
        return warnings

    if setting == SETTING_SCAN_AT:
        warnings.append(
            "This is when it SPENDS MONEY. The scan decides and buys in the "
            "same pass -- unlike the rotation, which decides at night and "
            "trades the next morning -- because what it decides on is the "
            "state of the session at that moment and cannot be carried "
            "forward."
        )
        warnings.append(
            "Earlier measures less of the day: at 15:20 roughly 95% of the "
            "volume has traded, and the close-strength and volume tests are "
            "both computed on what has happened SO FAR. Section 10.3 validated "
            "the 15:20 snapshot specifically, at 83.4% precision against the "
            "closing signal; an earlier time is not covered by that."
        )
        if auction is not None and parsed > auction.continuous_close:
            warnings.append(
                f"After {auction.continuous_close.strftime('%H:%M')} the 210 "
                f"F&O-eligible names cannot execute continuously. That is "
                f"already why 'Exclude F&O-eligible names' ships on; with it "
                f"off, orders on those names will be refused and journalled."
            )
    elif setting == SETTING_EXIT_AT:
        warnings.append(
            "THIS IS THE EDGE. Every position is sold here, unconditionally, "
            "and the whole return is the gap between yesterday's close and "
            "this morning's open. The same signals held to the next close "
            "instead measure +0.428% at a 49.0% win rate against +0.617% at "
            "71.4%."
        )
        warnings.append(
            "Later is not safer. The specification's next-day low against the "
            "entry averages -1.90%, and that number is only relevant to "
            "somebody who failed to exit at the open."
        )
        warnings.append(
            f"The book is warmed a few minutes beforehand so the fill "
            f"simulator has depth to price against. Moving this to the open "
            f"itself ({hours.open.strftime('%H:%M')}) is allowed and will "
            f"usually fill worse, because at that second there is no depth "
            f"book yet."
        )

    warnings.append(
        "It applies to the NEXT run. A job already running keeps the schedule "
        "it started under, and a job whose time has already passed today does "
        "not fire retrospectively."
    )
    return warnings
