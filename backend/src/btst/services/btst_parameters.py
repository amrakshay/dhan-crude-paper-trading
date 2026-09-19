"""B1-B16, read from the strategy module's YAML.

Root `CLAUDE.md` section 7: no number that affects a trade is hardcoded in
Python. Every parameter the specification names is a key in
`conf/strategies/nse-btst-overnight.yaml`, and this is the only place that
reads them.

Every field is **required**, for the same reason `SwingParameters` requires
its own: a default is a silently different strategy. The whole point of the
YAML is that its numbers can be checked line by line against the
specification's own table, and a value that quietly fell back to something
reasonable defeats that. A missing key names the file and the key.

This is a separate class from `SwingParameters` rather than a generalisation of
it, deliberately. The two strategies share three numbers by coincidence (a
20-session liquidity window, a Rs 50 price floor, a 200-session trend SMA) and
disagree about almost everything else: this one has a breakout lookback and a
close-location floor and no ATR, no breadth ramp, no rotation rank and NO STOP.
A shared parameters class would have to carry every field either might want and
make each of them optional, which is exactly the defaulting this file exists to
forbid.
"""
from dataclasses import dataclass
from typing import Any, Dict


class BtstConfigError(Exception):
    """The strategy module's configuration cannot be used, and says why."""


RANK_VOL_RATIO = "vol_ratio"
RANK_MOM126 = "mom126"
RANKINGS = (RANK_VOL_RATIO, RANK_MOM126)

ENFORCEMENT_ENFORCE = "enforce"
ENFORCEMENT_OBSERVE = "observe"
ENFORCEMENTS = (ENFORCEMENT_ENFORCE, ENFORCEMENT_OBSERVE)

# B15. Spelled as a word in the YAML so the absence of a stop is something a
# reader FINDS rather than something they fail to find, and so that a value
# other than "none" is an error rather than a silently ignored key.
STOP_NONE = "none"


def _require(section: Dict[str, Any], key: str, where: str) -> Any:
    if key not in section or section[key] is None:
        raise BtstConfigError(f"{where}: missing required key '{key}'")
    return section[key]


def _number(section: Dict[str, Any], key: str, where: str) -> float:
    value = _require(section, key, where)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise BtstConfigError(
            f"{where}: '{key}' must be a number, got {value!r}"
        ) from exc


def _integer(section: Dict[str, Any], key: str, where: str) -> int:
    value = _require(section, key, where)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise BtstConfigError(
            f"{where}: '{key}' must be an integer, got {value!r}"
        ) from exc


def _positive(value: float, key: str, where: str) -> float:
    if value <= 0:
        raise BtstConfigError(f"{where}: '{key}' must be positive, got {value!r}")
    return value


@dataclass(frozen=True)
class RegimePolicy:
    """B9, and whether it is obeyed.

    No entry-return filter: the rotation's P9 has no counterpart in this
    specification, so there is no `enforce_entry_return` here and the switch is
    not offered for this module. An absent field rather than a field set False,
    because "this strategy does not have that rule" and "this strategy has it
    switched off" are different facts.
    """

    index_role: str
    sma_sessions: int
    enforce_by_default: bool = True


@dataclass(frozen=True)
class SchedulePolicy:
    scan_at: str                    # IST HH:MM, inside the session
    exit_at: str                    # IST HH:MM, at the open
    exit_alarm_after_minutes: int


@dataclass(frozen=True)
class BtstParameters:
    # B2
    liquidity_floor_rupees: float
    liquidity_window_sessions: int
    # B3
    price_floor: float
    # B4
    breakout_lookback_sessions: int
    # B5
    volume_window_sessions: int
    volume_multiple: float
    # B6
    close_location_minimum: float
    # B7
    trend_sma_sessions: int
    # B8
    momentum_lookback_sessions: int
    momentum_skip_sessions: int
    momentum_floor: float
    # B10
    ranking: str
    # B11
    slots: int
    # B12
    position_size_divisor: int
    # warm-up
    minimum_sessions: int

    regime: RegimePolicy
    schedule: SchedulePolicy
    # Whether F&O-eligible names are excluded BY DEFAULT. The live value is a
    # `feature_toggles` row; see `btst_policy.py`.
    exclude_fno_by_default: bool
    armed_by_default: bool

    @property
    def history_needed(self) -> int:
        """How many completed sessions a symbol needs before it can qualify.

        The longest lookback any filter reads, which is B8's 126 plus its
        5-session skip, or B7's 200, whichever is larger -- and `minimum_sessions`
        on top of that is the specification's own panel requirement, not this.
        Reported so the Signals tab can say why a recently-listed name is
        absent instead of leaving it unexplained.
        """
        return max(
            self.trend_sma_sessions,
            self.breakout_lookback_sessions + 1,
            self.momentum_lookback_sessions + 1,
            self.liquidity_window_sessions,
            self.volume_window_sessions,
        )

    @classmethod
    def from_definition(cls, definition) -> "BtstParameters":
        source = f"conf/strategies/{definition.key}.yaml"
        parameters = definition.module_section("parameters")
        if not parameters:
            raise BtstConfigError(
                f"{source}: no 'parameters' block. Every number that affects a "
                f"trade lives in the YAML; there are no defaults here."
            )
        regime = definition.module_section("regime")
        schedule = definition.module_section("schedule")
        fno = definition.module_section("fno")

        where = f"{source}: parameters"

        ranking = str(_require(parameters, "ranking", where)).strip().lower()
        if ranking not in RANKINGS:
            raise BtstConfigError(
                f"{where}: ranking {ranking!r} is not one of {RANKINGS}. B10 "
                f"specifies {RANK_VOL_RATIO!r}; section 16 recommends "
                f"{RANK_MOM126!r} and section 9.3 measures the difference at "
                f"under one point of CAGR."
            )

        # B15. Refused rather than ignored: somebody adding a stop to this
        # strategy has to do it deliberately and in code, not by editing a
        # string, because a stop cannot be applied to the risk this strategy
        # actually carries -- the position is only open while the market is
        # shut.
        stop = str(_require(parameters, "stop", where)).strip().lower()
        if stop != STOP_NONE:
            raise BtstConfigError(
                f"{where}: stop {stop!r} is not supported. B15 is 'none' and "
                f"none is possible: every position in this strategy is opened "
                f"near the close and sold at the next open, so its only risk "
                f"window is overnight, when no order can execute at any price."
            )

        enforcement = str(
            regime.get("enforcement", ENFORCEMENT_ENFORCE)
        ).strip().lower()
        if enforcement not in ENFORCEMENTS:
            raise BtstConfigError(
                f"{source}: regime.enforcement {enforcement!r} is not one of "
                f"{ENFORCEMENTS}."
            )

        slots = _integer(parameters, "slots", where)
        if slots <= 0:
            raise BtstConfigError(f"{where}: slots must be positive, got {slots}.")
        divisor = _integer(parameters, "position_size_divisor", where)
        if divisor <= 0:
            raise BtstConfigError(
                f"{where}: position_size_divisor must be positive, got {divisor}."
            )

        close_location_minimum = _number(parameters, "close_location_minimum", where)
        if not 0.0 <= close_location_minimum < 1.0:
            # 1.0 excluded: CLV is bounded above BY ONE, so a floor of 1.0
            # qualifies nothing at all and a floor above it is a strategy that
            # can never trade. Both are configurations nobody meant.
            raise BtstConfigError(
                f"{where}: close_location_minimum must be at least 0 and below "
                f"1 (CLV is a fraction of the day's range), got "
                f"{close_location_minimum}."
            )

        return cls(
            liquidity_floor_rupees=_positive(
                _number(parameters, "liquidity_floor_rupees", where),
                "liquidity_floor_rupees", where,
            ),
            liquidity_window_sessions=_integer(
                parameters, "liquidity_window_sessions", where
            ),
            price_floor=_number(parameters, "price_floor", where),
            breakout_lookback_sessions=_integer(
                parameters, "breakout_lookback_sessions", where
            ),
            volume_window_sessions=_integer(
                parameters, "volume_window_sessions", where
            ),
            volume_multiple=_positive(
                _number(parameters, "volume_multiple", where),
                "volume_multiple", where,
            ),
            close_location_minimum=close_location_minimum,
            trend_sma_sessions=_integer(parameters, "trend_sma_sessions", where),
            momentum_lookback_sessions=_integer(
                parameters, "momentum_lookback_sessions", where
            ),
            momentum_skip_sessions=_integer(
                parameters, "momentum_skip_sessions", where
            ),
            momentum_floor=_number(parameters, "momentum_floor", where),
            ranking=ranking,
            slots=slots,
            position_size_divisor=divisor,
            minimum_sessions=_integer(parameters, "minimum_sessions", where),
            regime=RegimePolicy(
                index_role=str(_require(regime, "index_role", f"{source}: regime")),
                sma_sessions=_integer(regime, "sma_sessions", f"{source}: regime"),
                # Defaulted rather than required, unlike every parameter above:
                # a module that says nothing about enforcement obeys its own
                # regime gate, which is the specification's behaviour and the
                # only safe silence.
                enforce_by_default=enforcement == ENFORCEMENT_ENFORCE,
            ),
            schedule=SchedulePolicy(
                scan_at=str(_require(schedule, "scan_at", f"{source}: schedule")),
                exit_at=str(_require(schedule, "exit_at", f"{source}: schedule")),
                exit_alarm_after_minutes=_integer(
                    schedule, "exit_alarm_after_minutes", f"{source}: schedule"
                ),
            ),
            # Defaulted TRUE when the block is absent. The safe silence here is
            # the specification's own recommendation (section 5a): excluding
            # F&O names removes the closing auction from the problem and takes
            # the better half of the edge.
            exclude_fno_by_default=bool(fno.get("exclude", True)),
            armed_by_default=bool(definition.automation.armed_by_default),
        )

    def position_value(self, equity):
        """B12: total equity divided by the divisor. Money stays `Decimal`.

        Whole shares and the floor happen at the call site, against a live
        price -- this is only the rupee allocation.
        """
        from decimal import Decimal

        return Decimal(equity) / Decimal(self.position_size_divisor)
