"""Check the detectors against publicly-described chart patterns.

A SANITY CHECK, NOT A STATISTIC. The labels are the dates a write-up gives,
usually to the month, from secondary sources; there is no agreed first bar
of anybody's cup. What this can establish is that the detector fires on the
charts humans point at, at roughly the time they point at, on data it was
never tuned against. What it cannot establish is precision or recall, and
it is not used for either.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
from fetch_public import DATA, NAMED_CASES
from patlib.bars import load_csv, to_weekly
from patlib.detect import first_sightings
from patlib.cup_handle import CupParams, detect_cup_and_handle

OUT = Path(__file__).resolve().parent.parent / "out"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plot", action="store_true")
    a = ap.parse_args()
    lines = []
    for sym, (pattern, lo, hi, note) in NAMED_CASES.items():
        f = DATA / f"{sym.replace('.', '_')}.csv"
        if not f.exists():
            lines.append(f"{sym}: no data"); continue
        bars = load_csv(f, sym)
        m = (bars.date >= np.datetime64(lo)) & (bars.date <= np.datetime64(hi))
        idx = np.flatnonzero(m)
        if len(idx) < 120:
            lines.append(f"{sym}: only {len(idx)} bars in window"); continue
        # give the detector 300 bars of run-up before the window so its
        # moving averages and prior-advance lookbacks are real
        a0 = max(0, idx[0] - 300)
        sub = bars.slice(a0, idx[-1] + 1)
        seen = first_sightings(sub, start=300, step=1, which=[pattern])
        lines.append(f"\n=== {sym}  claimed: {pattern}  {lo}..{hi}\n    {note}")
        if not seen:
            lines.append("    NO DETECTION in the claimed window")
        for d in seen:
            lines.append(f"    {d.variant:<22} start={d.start_date} "
                         f"first_seen={d.metrics['first_seen_date']} "
                         f"score={d.score:.2f} pivot={d.pivot_price:,.2f} "
                         f"state={d.state} breakout={d.metrics.get('breakout_date')}")
            if a.plot:
                from plot import plot_detection
                plot_detection(sub, d, OUT / "public" / f"{sym}_{d.start_date}.png")
    txt = "\n".join(lines)
    print(txt)
    OUT.mkdir(exist_ok=True)
    (OUT / "public_named_cases.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
