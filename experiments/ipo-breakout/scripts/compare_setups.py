"""Are the three setups three strategies, or one strategy described three ways?

SETUPS.md makes the point that if `day1`, `week1` and `base` fire on largely the
same listings at largely the same times, they are not three setups to compare --
they are one effect with three descriptions, and a difference in their average
returns is noise between overlapping samples rather than evidence for a rule.

So the overlap is measured before the returns are compared.

    python3 scripts/compare_setups.py

Writes out/setup_comparison.txt and .json.
"""
from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path

import numpy as np

import _bootstrap  # noqa: F401

OUT = Path(__file__).resolve().parent.parent / "out"
SETUPS = ("base", "day1", "week1")

# Two signals on the same listing count as "the same trade" when their entry
# bars are this close. Five sessions: a week apart is the same move.
SAME_TRADE_SESSIONS = 5


def main() -> int:
    profile = "strict"
    signals = {}
    for setup in SETUPS:
        path = OUT / f"signals_{setup}_{profile}.json"
        if path.exists():
            signals[setup] = json.loads(path.read_text())
    if len(signals) < 2:
        return print("need at least two setups scanned -- run scan_real.py") or 1

    lines = ["Are the three setups one setup?", "=" * 31, ""]
    for setup, rows in signals.items():
        lines.append(f"  {setup:<6s} {len(rows):4d} signals on "
                     f"{len({r['symbol'] for r in rows}):4d} listings, "
                     f"median entry at session "
                     f"{int(np.median([r['sessions_after_listing'] for r in rows]))}"
                     f" after listing")
    lines.append("")

    payload = {"counts": {s: len(r) for s, r in signals.items()}, "overlap": {}}

    # How early is the left-side high? When it lands on session 0-4 the base
    # setup's pivot IS the first week's high, and the `base` and `week1` setups
    # are looking at the same level from two directions. DMART is the worked
    # example: its pivot is session 1.
    base_rows = signals.get("base") or []
    pivots = [r["metrics"].get("pivot_session") for r in base_rows
              if r["metrics"].get("pivot_session") is not None]
    if pivots:
        pivots = np.array(pivots, dtype=float)
        lines += [
            "Where the left-side high sits, for the `base` setup:",
            f"  median session {np.median(pivots):.0f}   "
            f"25th {np.quantile(pivots, .25):.0f}   "
            f"75th {np.quantile(pivots, .75):.0f}",
            f"  on session 0-4 (i.e. the pivot IS the first week's high): "
            f"{np.mean(pivots <= 4):.0%} of signals",
            "",
        ]

    lines += ["Overlap. 'same listing' shares a symbol; 'same trade' also enters",
              f"within {SAME_TRADE_SESSIONS} sessions of the other setup's entry.",
              ""]
    header = (f"{'pair':<16s} {'same listing':>13s} {'same trade':>11s} "
              f"{'of the smaller set':>20s}")
    lines += [header, "-" * len(header)]

    for left, right in combinations(sorted(signals), 2):
        a, b = signals[left], signals[right]
        by_symbol_b = {}
        for row in b:
            by_symbol_b.setdefault(row["symbol"], []).append(
                row["sessions_after_listing"])
        same_listing = sum(1 for row in a if row["symbol"] in by_symbol_b)
        same_trade = sum(
            1 for row in a
            if any(abs(row["sessions_after_listing"] - other) <= SAME_TRADE_SESSIONS
                   for other in by_symbol_b.get(row["symbol"], [])))
        smaller = min(len(a), len(b))
        lines.append(f"{left + ' vs ' + right:<16s} {same_listing:>13d} "
                     f"{same_trade:>11d} {same_trade / smaller:>19.0%}")
        payload["overlap"][f"{left} vs {right}"] = {
            "same_listing": same_listing, "same_trade": same_trade,
            "smaller_set": smaller,
            "share_of_smaller": same_trade / smaller if smaller else 0.0}

    lines += ["", "Forward returns, from out/analysis_<setup>_strict.json:", ""]
    header = (f"{'setup':<8s} {'n':>5s} {'r20':>8s} {'excess vs mkt':>15s} "
              f"{'95% CI':>20s} {'r60':>8s} {'excess vs mkt':>15s}")
    lines += [header, "-" * len(header)]
    payload["returns"] = {}
    for setup in SETUPS:
        path = OUT / f"analysis_{setup}_{profile}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        h20 = data["horizons"].get("20") or data["horizons"].get(20)
        h60 = data["horizons"].get("60") or data["horizons"].get(60)
        if not (h20 and h60):
            continue
        lines.append(
            f"{setup:<8s} {h20['n']:>5d} {h20['signal_mean']:>7.2%} "
            f"{h20['excess_vs_market']['mean']:>14.2%} "
            f"[{h20['excess_vs_market']['lo']:>+6.2%},"
            f"{h20['excess_vs_market']['hi']:>+6.2%}] "
            f"{h60['signal_mean']:>7.2%} "
            f"{h60['excess_vs_market']['mean']:>14.2%}")
        payload["returns"][setup] = {"h20": h20, "h60": h60}

    text = "\n".join(lines)
    print(text)
    (OUT / "setup_comparison.txt").write_text(text + "\n")
    (OUT / "setup_comparison.json").write_text(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
