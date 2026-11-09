"""Reproduce every number in this experiment, in order.

    python scripts/run_all.py              # everything, assuming the cache exists
    python scripts/run_all.py --fetch      # fetch the data first (~50 min, once)
    python scripts/run_all.py --only charges,controls

The order is the order the work was done in, and it matters: data, then charges
and margin, then ground truth, then anything that looks like a result.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv/bin/python")
DB = ROOT / "data/cache/options.db"

STEPS = [
    ("fetch", "Fetch the bhavcopy, index closes and NSE's daily volatility",
     [PY, "scripts/fetch_bhavcopy.py", "--from", "2016-01-01", "--to", "2026-09-17"]),
    ("validate_data", "Validate the legacy/UDiFF splice and the derived lot size",
     [PY, "scripts/validate_bhavcopy.py"]),
    ("charges", "Assert the charge card against hand-computed worked examples",
     [PY, "scripts/validate_charges.py"]),
    ("margin", "Margin model: calibration, and the sweep of what is uncertain",
     [PY, "scripts/margin_report.py"]),
    ("controls", "Synthetic ground truth and the negative controls",
     [PY, "scripts/run_negative_controls.py"]),
    ("backtest", "The real-data backtest, its baselines and the bootstrap",
     [PY, "scripts/run_backtest.py"]),
    ("sweep", "Parameter sweeps, one dimension at a time",
     [PY, "scripts/sweep.py"]),
    ("bull_put", "Interrogate the one structure that made money",
     [PY, "scripts/bull_put_robustness.py"]),
    ("tails", "The left tail: what the sample contained and ruin-risk sizing",
     [PY, "scripts/tail_report.py"]),
    ("weekly", "The '+2% a week or -2% and out' target, tested",
     [PY, "scripts/weekly_target.py"]),
    ("diagonals", "Two-expiry diagonals: sell near, buy further OTM at expiry+N",
     [PY, "scripts/diagonals.py"]),
    ("why_losses", "Why a -2% stop realises -10%: gap, gamma and the mark",
     [PY, "scripts/why_big_losses.py"]),
    ("backspreads", "Buy more wings than you sell: does a ratio cut the losses?",
     [PY, "scripts/backspreads.py"]),
    ("dashboard", "Build out/option_selling_dashboard.html",
     [PY, "scripts/dashboard.py"]),
]

DEFAULT = [s for s in STEPS if s[0] != "fetch"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true",
                    help="include the data fetch (slow, and only needed once)")
    ap.add_argument("--only", default="",
                    help="comma-separated step names to run")
    a = ap.parse_args()

    if a.only:
        want = {s.strip() for s in a.only.split(",")}
        steps = [s for s in STEPS if s[0] in want]
    else:
        steps = STEPS if a.fetch else DEFAULT

    if not a.fetch and not DB.exists():
        print("The cache does not exist yet. Run with --fetch first:")
        print("    python scripts/run_all.py --fetch")
        return 1

    failures = []
    for name, desc, cmd in steps:
        print("\n" + "=" * 78)
        print(f"STEP {name} -- {desc}")
        print("=" * 78, flush=True)
        t0 = time.time()
        r = subprocess.run(cmd, cwd=ROOT)
        dt = time.time() - t0
        status = "ok" if r.returncode == 0 else f"EXIT {r.returncode}"
        print(f"\n[{name}] {status} in {dt:.1f}s", flush=True)
        if r.returncode != 0:
            failures.append(name)

    print("\n" + "=" * 78)
    if failures:
        print(f"FAILED STEPS: {', '.join(failures)}")
        print("A failing step is reported, not hidden. Read its output above.")
    else:
        print("ALL STEPS OK")
    print("=" * 78)
    print(f"\nOutputs in {ROOT / 'out'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
