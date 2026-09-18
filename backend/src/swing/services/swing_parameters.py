"""P1-P19, read from the strategy module's YAML.

Root `CLAUDE.md` section 7: no number that affects a trade is hardcoded in
Python. Every parameter the specification names is a key in
`conf/strategies/nse-swing-momentum.yaml`, and this is the only place that
reads them.

Every field is **required**. There is no default for a momentum floor or a
trail multiple, because a default is a silently different strategy: the whole
point of the YAML is that the numbers in it can be checked against the
specification's own table, and a value that quietly fell back to something
reasonable would defeat that. A missing key names the file and the key.
"""
from dataclasses import dataclass
from typing import Any, Dict, Optional


class SwingConfigError(Exception):
    """The strategy module's configuration cannot be used, and says why."""


def _require(section: Dict[str, Any], key: str, where: str) -> Any:
    if key not in section or section[key] is None:
        raise SwingConfigError(f"{where}: missing required key '{key}'")
    return section[key]


def _number(section: Dict[str, Any], key: str, where: str) -> float:
    value = _require(section, key, where)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise SwingConfigError(f"{where}: '{key}' must be a number, got {value!r}") from exc


def _integer(section: Dict[str, Any], key: str, where: str) -> int:
    value = _require(section, key, where)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise SwingConfigError(f"{where}: '{key}' must be an integer, got {value!r}") from exc


CADENCE_DAILY = "daily"
CADENCE_WEEKLY = "weekly"
CADENCES = (CADENCE_DAILY, CADENCE_WEEKLY)


@dataclass(frozen=True)
class OffGatePolicy:
    """V3b: what to do while the index is below its 200-day SMA.

    Disabled by default, and the owner's own research is the reason: thirteen
    variants were tested and not one beat holding cash. V3b's trades are good
    in isolation -- 61% win rate, mean +7.81% -- and it still ends lower than
    the baseline, because capital committed to a bear rally is not available at
    the regime flip. Per-trade edge is not portfolio edge.
    """

    enabled: bool
    slots: int
    momentum_floor: float
    require_entry_return: bool


@dataclass(frozen=True)
class RegimePolicy:
    index_role: str
    sma_sessions: int              # P8
    entry_return_sessions: int     # P9
    entry_return_minimum: float


@dataclass(frozen=True)
class SchedulePolicy:
    rebalance_cadence: str         # P18
    nightly_at: str                # IST HH:MM
    rebalance_at: str              # IST HH:MM

    @property
    def is_daily(self) -> bool:
        return self.rebalance_cadence == CADENCE_DAILY


@dataclass(frozen=True)
class SwingParameters:
    # P2
    liquidity_floor_rupees: float
    liquidity_window_sessions: int
    # P3
    price_floor: float
    # P4 / P10
    trend_sma_sessions: int
    # P5
    momentum_lookback_sessions: int
    momentum_skip_sessions: int
    # P6
    momentum_floor: float
    # P7
    volatility_adjusted_score: bool
    atr_sessions: int
    # P11
    breadth_lower: float
    breadth_span: float
    # P12
    max_positions: int
    # P13
    position_size_divisor: int
    # P14 / P15
    trail_atr_multiple: float
    # P16
    rotation_exit_rank: int
    # warm-up
    minimum_sessions: int

    regime: RegimePolicy
    schedule: SchedulePolicy
    off_gate: OffGatePolicy
    armed_by_default: bool

    @classmethod
    def from_definition(cls, definition) -> "SwingParameters":
        source = f"conf/strategies/{definition.key}.yaml"
        parameters = definition.module_section("parameters")
        if not parameters:
            raise SwingConfigError(
                f"{source}: no 'parameters' block. Every number that affects a "
                f"trade lives in the YAML; there are no defaults here."
            )
        regime = definition.module_section("regime")
        schedule = definition.module_section("schedule")
        off_gate = definition.module_section("off_gate")

        where = f"{source}: parameters"
        breadth_lower = _number(parameters, "breadth_lower", where)
        breadth_span = _number(parameters, "breadth_span", where)
        if breadth_span <= 0:
            raise SwingConfigError(
                f"{where}: breadth_span ({breadth_span}) must be positive; the "
                f"slot ramp divides by it."
            )

        cadence = str(
            _require(schedule, "rebalance_cadence", f"{source}: schedule")
        ).strip().lower()
        if cadence not in CADENCES:
            raise SwingConfigError(
                f"{source}: schedule.rebalance_cadence {cadence!r} is not one of "
                f"{CADENCES}."
            )

        return cls(
            liquidity_floor_rupees=_number(parameters, "liquidity_floor_rupees", where),
            liquidity_window_sessions=_integer(
                parameters, "liquidity_window_sessions", where
            ),
            price_floor=_number(parameters, "price_floor", where),
            trend_sma_sessions=_integer(parameters, "trend_sma_sessions", where),
            momentum_lookback_sessions=_integer(
                parameters, "momentum_lookback_sessions", where
            ),
            momentum_skip_sessions=_integer(parameters, "momentum_skip_sessions", where),
            momentum_floor=_number(parameters, "momentum_floor", where),
            volatility_adjusted_score=bool(
                _require(parameters, "volatility_adjusted_score", where)
            ),
            atr_sessions=_integer(parameters, "atr_sessions", where),
            breadth_lower=breadth_lower,
            breadth_span=breadth_span,
            max_positions=_integer(parameters, "max_positions", where),
            position_size_divisor=_integer(parameters, "position_size_divisor", where),
            trail_atr_multiple=_number(parameters, "trail_atr_multiple", where),
            rotation_exit_rank=_integer(parameters, "rotation_exit_rank", where),
            minimum_sessions=_integer(parameters, "minimum_sessions", where),
            regime=RegimePolicy(
                index_role=str(
                    _require(regime, "index_role", f"{source}: regime")
                ),
                sma_sessions=_integer(regime, "sma_sessions", f"{source}: regime"),
                entry_return_sessions=_integer(
                    regime, "entry_return_sessions", f"{source}: regime"
                ),
                entry_return_minimum=_number(
                    regime, "entry_return_minimum", f"{source}: regime"
                ),
            ),
            schedule=SchedulePolicy(
                rebalance_cadence=cadence,
                nightly_at=str(_require(schedule, "nightly_at", f"{source}: schedule")),
                rebalance_at=str(
                    _require(schedule, "rebalance_at", f"{source}: schedule")
                ),
            ),
            off_gate=OffGatePolicy(
                enabled=bool(off_gate.get("enabled", False)),
                slots=_integer(off_gate, "slots", f"{source}: off_gate"),
                momentum_floor=_number(
                    off_gate, "momentum_floor", f"{source}: off_gate"
                ),
                require_entry_return=bool(off_gate.get("require_entry_return", False)),
            ),
            # Read off the DEFINITION, not out of `module_config`: whether a
            # strategy trades unattended became a framework concern on
            # 2026-09-18, because the Strategies page, the health page and the
            # scheduler all have to know it without understanding momentum.
            # `automation` is a framework key now, so `module_section` would
            # return an empty mapping and this would silently read False.
            armed_by_default=bool(definition.automation.armed_by_default),
        )

    def slots_for_breadth(self, breadth: Optional[float]) -> Optional[int]:
        """P11. `int(round(max_positions * clamp((b - lo) / (hi - lo), 0, 1)))`.

        Reproduces the engine's arithmetic exactly, including Python's
        round-half-to-even: at a breadth of 0.365 the ramp lands on 0.5 slots,
        and `round(0.5)` is 0, not 1. A different rounding rule there would buy
        a position the backtest did not.

        `None` breadth returns `None`, never zero: "we could not measure
        breadth" and "breadth says buy nothing" are different answers, and only
        one of them is a reason to hold cash.
        """
        if breadth is None:
            return None
        fraction = min(
            max((breadth - self.breadth_lower) / self.breadth_span, 0.0), 1.0
        )
        return int(round(self.max_positions * fraction))
