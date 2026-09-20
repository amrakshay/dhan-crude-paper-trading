"""Reproduce every number in this experiment, in the order it was produced.

    .venv/bin/python scripts/run_all.py              # everything, ~60-75 min
    .venv/bin/python scripts/run_all.py --quick      # smaller samples, ~12 min
    .venv/bin/python scripts/run_all.py --only costs stats

Intermediate results are cached under `data/cache/` (simulated nulls, and one
screen per formation date), so a second run is much faster than the first and
a single stage can be re-run on its own.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "bin" / "python")

STAGES = [
    # (name, full args, quick args, one-line description)
    ("feasibility", ["feasibility.py"], ["feasibility.py"],
     "STEP 0 -- what the data supports, and why futures arbitrage is out"),
    ("costs", ["charges_check.py"], ["charges_check.py"],
     "the charges engine, validated three ways, and what a pair round trip costs"),
    ("nulls", ["build_nulls.py", "--sims", "200000", "--windows", "504", "756", "1008"],
     ["build_nulls.py", "--sims", "40000", "--windows", "756"],
     "simulated null distributions for the exact test procedure"),
    ("stats", ["validate_stats.py", "--trials", "400"],
     ["validate_stats.py", "--trials", "120"],
     "size, power, negative controls, and recovery of the traded quantities"),
    ("engine", ["validate_backtest.py"], ["validate_backtest.py"],
     "can the simulator find an edge that is definitely there?"),

    # --- the three screens. These dominate the runtime and are cached, so a
    # --- second run skips straight past them.
    ("select", ["select_pairs.py", "--tag", "base"],
     ["select_pairs.py", "--tag", "base", "--sims", "40000", "--limit", "4"],
     "walk-forward screen on price LEVELS, and the multiple-comparison audit"),
    ("logpx", ["select_pairs.py", "--log-prices", "--tag", "logpx"],
     ["select_pairs.py", "--log-prices", "--tag", "logpx", "--sims", "40000",
      "--limit", "4"],
     "the same screen on LOG prices -- the specification that found more"),
    ("placebo", ["select_pairs.py", "--placebo-seed", "11", "--tag", "placebo"],
     ["select_pairs.py", "--placebo-seed", "11", "--tag", "placebo",
      "--sims", "40000", "--limit", "4"],
     "the same screen on scrambled real prices -- the bar the others must beat"),
    ("compare", ["compare_screens.py"], ["compare_screens.py"],
     "real against placebo, paired by formation date"),

    # --- the books. Four, because the correction and the specification are
    # --- both load-bearing and the table in README section 4 needs all of them.
    ("backtest_log", ["backtest_pairs.py", "--log-prices", "--correction", "none",
                      "--tag", "lognominal"],
     ["backtest_pairs.py", "--log-prices", "--correction", "none",
      "--tag", "lognominal", "--no-baseline"],
     "THE headline book: log prices, uncorrected, with its random-pair baseline"),
    ("backtest_logfdr", ["backtest_pairs.py", "--log-prices", "--correction", "bh",
                         "--tag", "logfdr", "--no-baseline"],
     ["backtest_pairs.py", "--log-prices", "--correction", "bh", "--tag", "logfdr",
      "--no-baseline"],
     "the same book once the multiple comparisons are corrected for"),
    ("backtest_lvl", ["backtest_pairs.py", "--correction", "none", "--tag", "nominal"],
     ["backtest_pairs.py", "--correction", "none", "--tag", "nominal", "--no-baseline"],
     "price levels, uncorrected -- the specification that failed"),
    ("backtest_lvlfdr", ["backtest_pairs.py", "--correction", "bh", "--tag", "fdr",
                         "--no-baseline"],
     ["backtest_pairs.py", "--correction", "bh", "--tag", "fdr", "--no-baseline"],
     "price levels, corrected"),

    # --- sweeps
    ("sweep_lvl", ["sweep.py"], ["sweep.py", "--axis", "z_in", "z_stop", "card"],
     "nine axes on price levels -- the shape, not the best cell"),
    ("sweep_log", ["sweep.py", "--log-base", "--tag", "sweep_log", "--axis",
                   "z_in", "z_out", "z_stop", "hold_hl", "top_k", "correction",
                   "card", "collateral_yield", "max_half_life"],
     ["sweep.py", "--log-base", "--tag", "sweep_log", "--axis", "z_in", "top_k"],
     "the same nine on log prices, where the shape reverses"),
    ("sweep_size", ["sweep.py", "--log-base", "--tag", "sweep_size", "--axis",
                    "gross_per_pair"],
     ["sweep.py", "--log-base", "--tag", "sweep_size", "--axis", "gross_per_pair"],
     "position size: does amortising the flat brokerage rescue it?"),

    # --- dashboards
    ("dashboard", ["dashboard.py", "--backtest", "backtest_lognominal.json",
                   "--sweep", "sweep_log.json"],
     ["dashboard.py", "--backtest", "backtest_lognominal.json",
      "--sweep", "sweep_log.json"],
     "out/arbitrage_dashboard.html -- the log-price book"),
    ("dashboard_lvl", ["dashboard.py", "--backtest", "backtest_nominal.json",
                       "--sweep", "sweep.json", "--out",
                       "arbitrage_dashboard_levels.html"],
     ["dashboard.py", "--backtest", "backtest_nominal.json", "--sweep", "sweep.json",
      "--out", "arbitrage_dashboard_levels.html"],
     "out/arbitrage_dashboard_levels.html -- the level-price book, for comparison"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", nargs="+", default=None)
    ap.add_argument("--from-stage", default=None)
    args = ap.parse_args()

    names = [s[0] for s in STAGES]
    stages = STAGES
    if args.from_stage:
        stages = STAGES[names.index(args.from_stage):]
    if args.only:
        stages = [s for s in stages if s[0] in set(args.only)]

    t0 = time.time()
    for name, full, quick, why in stages:
        cmd = [PY, "-W", "ignore", str(ROOT / "scripts" / (quick if args.quick else full)[0])]
        cmd += (quick if args.quick else full)[1:]
        print(f"\n{'='*72}\n== {name}  --  {why}\n{'='*72}", flush=True)
        t = time.time()
        r = subprocess.run(cmd, cwd=ROOT)
        if r.returncode != 0:
            print(f"\n!! stage {name} failed with code {r.returncode}")
            sys.exit(r.returncode)
        print(f"-- {name} done in {time.time()-t:.0f}s", flush=True)
    print(f"\nall stages complete in {(time.time()-t0)/60:.1f} min")
    print(f"dashboard: {ROOT / 'out' / 'arbitrage_dashboard.html'}")
    print(f"           {ROOT / 'out' / 'arbitrage_dashboard_levels.html'}")


if __name__ == "__main__":
    main()
