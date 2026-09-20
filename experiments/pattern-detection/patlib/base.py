"""What every detector returns."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Detection:
    pattern: str                 # "cup_and_handle" | "vcp" | "triangle" | ...
    variant: str                 # "with_handle" | "ascending" | "3T" | ...
    symbol: str
    timeframe: str               # "D" | "W"
    start_idx: int               # first bar of the formation
    end_idx: int                 # last bar used (the "as of" bar)
    pivot_price: float           # the breakout trigger
    stop_price: float            # where the pattern is wrong
    state: str                   # "forming" | "breakout"
    score: float                 # 0..1, how textbook it is
    reasons: list[str] = field(default_factory=list)   # why it scored down
    metrics: dict[str, Any] = field(default_factory=dict)
    start_date: str = ""
    end_date: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def __repr__(self) -> str:
        return (f"<{self.pattern}/{self.variant} {self.symbol} {self.timeframe} "
                f"{self.start_date}..{self.end_date} score={self.score:.2f} "
                f"pivot={self.pivot_price:.2f} {self.state}>")


def overlap(a: Detection, b: Detection) -> float:
    """Jaccard overlap of two detections' bar spans."""
    lo = max(a.start_idx, b.start_idx)
    hi = min(a.end_idx, b.end_idx)
    inter = max(0, hi - lo)
    union = max(a.end_idx, b.end_idx) - min(a.start_idx, b.start_idx)
    return inter / union if union else 0.0


def dedupe(dets: list[Detection], threshold: float = 0.6) -> list[Detection]:
    """Keep the best-scoring detection out of each overlapping cluster.

    Enumerating rim pairs produces near-duplicates by construction -- shift
    the left rim one pivot and the same cup comes back slightly worse. The
    detector's job is to report a cup, not fourteen readings of it.
    """
    out: list[Detection] = []
    for d in sorted(dets, key=lambda x: (-x.score, x.start_idx)):
        if all(overlap(d, k) < threshold for k in out):
            out.append(d)
    return sorted(out, key=lambda x: x.start_idx)
