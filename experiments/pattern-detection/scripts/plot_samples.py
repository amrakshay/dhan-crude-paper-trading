"""Render the highest-scoring real detections so they can be eyeballed.

The point of the exercise is detection without looking at a chart. The point
of THIS script is that verification without looking at a chart would be
negligent: on real data there are no labels, so the only check available on
whether a "cup" is a cup is to draw it.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
from patlib.bars import load_sqlite, to_weekly
from patlib.detect import detect_all
from plot import plot_detection

DB = Path(__file__).resolve().parents[3] / "backend" / "data" / "paper_trading.db"
OUT = Path(__file__).resolve().parent.parent / "out" / "samples"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", required=True)
    ap.add_argument("--pattern", default=None)
    ap.add_argument("--profile", default="strict")
    ap.add_argument("--timeframe", default="D")
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--top", type=int, default=6)
    a = ap.parse_args()

    found = []
    for sym in a.symbols:
        b = load_sqlite(DB, sym)
        if a.timeframe == "W":
            b = to_weekly(b)
        seen = {}
        for t in range(260, len(b), a.step):
            which = [a.pattern] if a.pattern else None
            for d in detect_all(b, t, which=which, profile=a.profile):
                bi = d.metrics.get("breakout_idx")
                key = (d.pattern, bi if bi is not None else d.start_idx // 5)
                if key not in seen or d.score > seen[key][0].score:
                    seen[key] = (d, b)
        found.extend(seen.values())
    found.sort(key=lambda x: -x[0].score)
    OUT.mkdir(parents=True, exist_ok=True)
    for d, b in found[: a.top]:
        f = OUT / f"{d.symbol}_{d.pattern}_{d.metrics.get('breakout_date') or d.end_date}.png"
        plot_detection(b, d, f)
        print(f"{d}  -> {f.name}")


if __name__ == "__main__":
    main()
