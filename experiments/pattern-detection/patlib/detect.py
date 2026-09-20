"""Run every detector over a series, causally."""
from __future__ import annotations

from .bars import Bars
from .base import Detection
from .cup_handle import CupParams, detect_cup_and_handle, detect_inverted_cup_and_handle
from .triangles import TriangleParams, detect_triangles
from .vcp import VCPParams, detect_vcp

DETECTORS = {
    "cup_and_handle": detect_cup_and_handle,
    "vcp": detect_vcp,
    "triangle": detect_triangles,
}


def detect_all(bars: Bars, t: int | None = None, which=None,
               inverted: bool = False, profile: str = "strict") -> list[Detection]:
    which = which or list(DETECTORS)
    out: list[Detection] = []
    for name in which:
        if profile == "strict":
            out.extend(DETECTORS[name](bars, t))
        else:
            from .profiles import params
            out.extend(DETECTORS[name](bars, t, params(name, bars.timeframe, profile)))
    if inverted:
        out.extend(detect_inverted_cup_and_handle(bars, t))
    return out


def scan(bars: Bars, start: int = 60, stop: int | None = None, step: int = 1,
         which=None, profile: str = "strict") -> list[tuple[int, Detection]]:
    """Walk the as-of bar forward and collect every detection, tagged with
    the bar at which it became visible.

    This is the only honest way to evaluate: calling a detector once on a
    completed chart tells you what it sees with hindsight, which is not the
    question anyone is asking.
    """
    stop = len(bars) - 1 if stop is None else stop
    out = []
    for t in range(start, stop + 1, step):
        for d in detect_all(bars, t, which, profile=profile):
            out.append((t, d))
    return out


def first_sightings(bars: Bars, start: int = 60, stop: int | None = None,
                    step: int = 1, which=None, profile: str = "strict") -> list[Detection]:
    """One row per distinct formation: the earliest bar it was reported."""
    seen: dict[tuple, Detection] = {}
    for t, d in scan(bars, start, stop, step, which, profile):
        key = (d.pattern, d.start_idx // 3)
        if key not in seen:
            d.metrics["first_seen_idx"] = t
            d.metrics["first_seen_date"] = str(bars.date[t])
            seen[key] = d
    return sorted(seen.values(), key=lambda d: d.metrics["first_seen_idx"])
