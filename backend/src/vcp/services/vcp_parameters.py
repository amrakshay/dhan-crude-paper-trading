"""The VCP rule set's numbers, read from `conf/strategies/nse-vcp-swing.yaml`.

Every field is REQUIRED, for the reason `SwingParameters` and `BtstParameters`
give: a default is a silently different strategy, and the YAML is meant to be
checked line by line against experiments/pattern-detection/FINAL_LOGIC.md. A
missing key names the file and the key.

The detector's own thresholds are not here. They are the experiment's
`VCPParams` defaults, copied with the detector and pinned to it by
`tests/test_vcp_detector_port.py`; repeating them in YAML would create a
second place to change them that the golden test does not cover.
"""
from dataclasses import asdict, dataclass
from typing import Any, Dict

from src.core.time_utils import parse_hhmm


class VcpConfigError(Exception):
    """The strategy module's configuration cannot be used, and says why."""


def _require(section: Dict[str, Any], key: str, where: str) -> Any:
    if key not in section or section[key] is None:
        raise VcpConfigError(f"{where}: missing required key '{key}'")
    return section[key]


def _number(section: Dict[str, Any], key: str, where: str, positive: bool = True) -> float:
    value = _require(section, key, where)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise VcpConfigError(f"{where}: '{key}' must be a number, got {value!r}") from exc
    if positive and number <= 0:
        raise VcpConfigError(f"{where}: '{key}' must be positive, got {value!r}")
    return number


def _integer(section: Dict[str, Any], key: str, where: str) -> int:
    value = _require(section, key, where)
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise VcpConfigError(f"{where}: '{key}' must be an integer, got {value!r}") from exc
    if number <= 0:
        raise VcpConfigError(f"{where}: '{key}' must be positive, got {value!r}")
    return number


@dataclass(frozen=True)
class VcpSchedule:
    nightly_at: str      # IST HH:MM, OUTSIDE the session
    rebalance_at: str    # IST HH:MM, inside continuous trading


@dataclass(frozen=True)
class VcpParameters:
    min_contractions: int
    min_stop_atr: float
    atr_sessions: int
    overlap_threshold: float
    minimum_sessions: int
    minimum_listed_sessions: int
    replay_sessions: int
    history_sessions: int
    trail_ema_sessions: int
    breakout_window_sessions: int
    max_hold_sessions: int
    position_rupees: float
    schedule: VcpSchedule

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_definition(cls, definition) -> "VcpParameters":
        source = f"conf/strategies/{definition.key}.yaml"
        parameters = definition.module_section("parameters")
        if not parameters:
            raise VcpConfigError(
                f"{source}: no 'parameters' block. Every number that affects a "
                f"trade lives in the YAML; there are no defaults here."
            )
        where = f"{source}: parameters"
        schedule = definition.module_section("schedule") or {}
        swhere = f"{source}: schedule"
        nightly = str(_require(schedule, "nightly_at", swhere)).strip()
        rebalance = str(_require(schedule, "rebalance_at", swhere)).strip()
        for key, value in (("nightly_at", nightly), ("rebalance_at", rebalance)):
            try:
                parse_hhmm(value)
            except Exception as exc:  # noqa: BLE001 - any parse failure is the same answer
                raise VcpConfigError(f"{swhere}: '{key}' {value!r} is not HH:MM") from exc

        overlap = _number(parameters, "overlap_threshold", where)
        if overlap > 1:
            raise VcpConfigError(f"{where}: 'overlap_threshold' is a fraction, got {overlap!r}")
        return cls(
            min_contractions=_integer(parameters, "min_contractions", where),
            min_stop_atr=_number(parameters, "min_stop_atr", where, positive=False),
            atr_sessions=_integer(parameters, "atr_sessions", where),
            overlap_threshold=overlap,
            minimum_sessions=_integer(parameters, "minimum_sessions", where),
            minimum_listed_sessions=_integer(parameters, "minimum_listed_sessions", where),
            replay_sessions=_integer(parameters, "replay_sessions", where),
            history_sessions=_integer(parameters, "history_sessions", where),
            trail_ema_sessions=_integer(parameters, "trail_ema_sessions", where),
            breakout_window_sessions=_integer(parameters, "breakout_window_sessions", where),
            max_hold_sessions=_integer(parameters, "max_hold_sessions", where),
            position_rupees=_number(parameters, "position_rupees", where),
            schedule=VcpSchedule(nightly_at=nightly, rebalance_at=rebalance),
        )
