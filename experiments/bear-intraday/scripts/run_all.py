"""Reproduce every number in this experiment's documents (development only).

    .venv/bin/python scripts/run_all.py

1. tests (synthetic null, planted edge, causality, locks)
2. REGIME.md            scripts/breadth_regime.py
3. INDEX_SHORT, B1, B2, B6 under regime B, with controls and portfolios
4. robustness: index_short, B1, B2 under regimes A and C
5. the day-decomposition diagnostic
6. out/bear_dashboard.html

Not run here: the 0d probe (needs a live Dhan token, run from backend/), and
any holdout run (none exists: nothing passed development).
Each run_dev call appends to out/variant_log.json; the README's variant table
is the authority on what counts as a variant.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

EXP = Path(__file__).resolve().parent.parent
PY = sys.executable


def run(*args):
    print("\n$", " ".join(args), flush=True)
    subprocess.run([PY, *args], cwd=EXP, check=True)


run("-m", "pytest", "tests", "-q", "-W", "ignore::RuntimeWarning")
run("scripts/breadth_regime.py")
run("scripts/run_dev.py", "all")
for rg in ("A", "C"):
    for h in ("index_short", "B1_breakdown", "B2_failed_rally"):
        run("scripts/run_dev.py", h, "--regime", rg)
run("scripts/decompose_day.py")
run("scripts/dashboard.py")
