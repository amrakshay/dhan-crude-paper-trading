"""Reproduce every number in the write-ups, in order.

    python3 scripts/run_all.py              # everything except the data pull
    python3 scripts/run_all.py --with-data  # including it (~50 min, needs a
                                            # live Dhan token)

The data pull is opt-in because it is the only step that talks to a broker API
and the only one that cannot be repeated offline. Everything after it reads
`data/listings.db` and `data/ipo_calendar.csv`.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PYTHON = sys.executable

DATA_STEPS = [
    ("build the listing universe from Dhan", ["build_universe.py", "--resume"]),
    ("fetch the mainboard IPO calendar", ["fetch_ipo_calendar.py"]),
]

STEPS = [
    ("isolation: no broker surface, read-only, imported by nothing",
     ["check_isolation.py"]),
    ("synthetic benchmark -- detector accuracy", ["run_synthetic_eval.py"]),
    ("parameter sweep -- which thresholds matter", ["sweep.py"]),
    ("the listing bar, measured rather than assumed",
     ["listing_bar_stats.py"]),
    ("scan the real listings, causally", ["scan_real.py"]),
    ("forward returns vs two baselines, block bootstrap",
     ["analyse_signals.py"]),
    ("are the three setups one setup?", ["compare_setups.py"]),
    ("lock-in expiry study -- the causal feature", ["lockin_study.py"]),
    ("exit rules, in both currencies", ["exit_grid.py"]),
    # Both rows of README.md section 5, and in that order, because the
    # comparison is the point: the filtered run is sized UP so the two are
    # compared at matched exposure rather than matched position size.
    ("portfolio: every signal (the baseline row)",
     ["ipo_backtest.py", "--out", "ipo_dashboard_all_signals.html"]),
    ("portfolio: the decided rule set, and the dashboard",
     ["ipo_backtest.py", "--skip-before-unlock", "--per-trade", "133000",
      "--out", "ipo_dashboard.html"]),
]


def run(label: str, argv: list[str]) -> bool:
    print(f"\n{'=' * 72}\n== {label}\n== {' '.join(argv)}\n{'=' * 72}", flush=True)
    started = time.monotonic()
    result = subprocess.run([PYTHON, str(HERE / argv[0]), *argv[1:]],
                            cwd=HERE.parent)
    elapsed = time.monotonic() - started
    status = "ok" if result.returncode == 0 else f"FAILED ({result.returncode})"
    print(f"-- {status} in {elapsed:.0f}s", flush=True)
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-data", action="store_true",
                        help="also re-pull from Dhan and re-fetch the calendar")
    args = parser.parse_args()

    steps = (DATA_STEPS if args.with_data else []) + STEPS
    failures = [label for label, argv in steps if not run(label, argv)]

    print(f"\n{'=' * 72}")
    if failures:
        print(f"{len(failures)} step(s) FAILED:")
        for label in failures:
            print(f"  - {label}")
        return 1
    print(f"all {len(steps)} steps ok -- see out/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
