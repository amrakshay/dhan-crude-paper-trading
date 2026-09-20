"""Parameter sensitivity: move ONE threshold at a time, report what it buys.

The question a sweep answers is not "which value is best" -- that is how you
overfit a benchmark -- but "does this threshold do anything at all". A recall
curve that is flat across every value is a finding about the PATTERN: the
criterion is not load-bearing and could be dropped.

    python3 scripts/sweep.py
    python3 scripts/sweep.py --param max_depth_pct

Writes out/sweep.json and out/sweep.txt.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import _bootstrap  # noqa: F401

from ipolib import evaluate, ipo_base, synth

OUT = Path(__file__).resolve().parent.parent / "out"

GRID = {
    "left_high_window": [10, 15, 20, 25, 30, 40],
    "min_base_len": [3, 5, 7, 10, 14],
    "max_base_len": [15, 20, 25, 30, 40, 60],
    "max_depth_pct": [20.0, 30.0, 40.0, 50.0, 65.0],
    "normal_depth_pct": [10.0, 15.0, 20.0, 25.0, 30.0],
    "breakout_volume_ratio": [1.0, 1.2, 1.4, 1.6, 2.0],
    "dryup_ratio": [0.6, 0.75, 0.85, 1.0, 1.2],
    "volume_lookback": [10, 20, 30, 50],
    "pivot_buffer_pct": [0.0, 0.1, 0.5, 1.0, 2.0],
    "allow_listing_bar_pivot": [False, True],
    "max_age_sessions": [60, 125, 250, 500],
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--param", nargs="*", default=sorted(GRID))
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--step", type=int, default=3)
    args = parser.parse_args()

    samples = synth.build_benchmark(seed=args.seed)
    baseline = ipo_base.params("strict")

    lines: list[str] = []
    payload: dict = {}
    for name in args.param:
        values = GRID[name]
        header = (f"{name}\n" + "-" * len(name) + "\n"
                  f"{'value':>10s} {'recall':>8s} {'signal prec':>12s} "
                  f"{'FA/1k':>8s}")
        print(header)
        lines.append(header)
        payload[name] = []
        for value in values:
            params = replace(baseline, **{name: value})
            result = evaluate.evaluate(samples, params, step=args.step)
            row = (f"{str(value):>10s} {result.recall:8.1%} "
                   f"{result.signal_precision:12.1%} {result.fp_rate_per_1k:8.1f}")
            marker = "   <- default" if value == getattr(baseline, name) else ""
            print(row + marker)
            lines.append(row + marker)
            payload[name].append({
                "value": value, "recall": result.recall,
                "signal_precision": result.signal_precision,
                "fp_rate_per_1k": result.fp_rate_per_1k,
                "is_default": value == getattr(baseline, name),
            })

        recalls = [r["recall"] for r in payload[name]]
        precisions = [r["signal_precision"] for r in payload[name]]
        verdict = ("FLAT -- this threshold changes nothing on the benchmark"
                   if max(recalls) - min(recalls) < 0.02
                   and max(precisions) - min(precisions) < 0.02
                   else f"moves recall by {max(recalls) - min(recalls):.1%}, "
                        f"signal precision by {max(precisions) - min(precisions):.1%}")
        print(f"  {verdict}\n")
        lines += [f"  {verdict}", ""]

    OUT.mkdir(exist_ok=True)
    (OUT / "sweep.json").write_text(json.dumps(payload, indent=2))
    (OUT / "sweep.txt").write_text("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
