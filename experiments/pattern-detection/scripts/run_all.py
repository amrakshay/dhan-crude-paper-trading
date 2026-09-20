"""Reproduce every number in README.md, in order.

Roughly 20 minutes on 12 cores. Each step writes to out/ and can be run
alone; this exists so the results are reproducible rather than described.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = str(Path(sys.executable))

STEPS = [
    ("synthetic benchmark, strict",
     [PY, "scripts/run_synthetic_eval.py", "--tag", "FINAL_strict"]),
    ("synthetic benchmark, relaxed",
     [PY, "scripts/run_synthetic_eval.py", "--tag", "FINAL_relaxed", "--profile", "relaxed"]),
    ("parameter sweep: cup", [PY, "scripts/sweep.py", "--pattern", "cup_and_handle"]),
    ("parameter sweep: vcp", [PY, "scripts/sweep.py", "--pattern", "vcp"]),
    ("parameter sweep: triangle", [PY, "scripts/sweep.py", "--pattern", "triangle"]),
    ("fetch public examples", [PY, "scripts/fetch_public.py", "--which", "named"]),
    ("public named cases", [PY, "scripts/run_public_eval.py"]),
    ("NSE scan, strict, daily",
     [PY, "scripts/scan_real.py", "--step", "5", "--tag", "real_strict_D"]),
    ("NSE scan, relaxed, daily",
     [PY, "scripts/scan_real.py", "--step", "5", "--profile", "relaxed",
      "--tag", "real_relaxed_D"]),
    ("NSE scan, strict, weekly",
     [PY, "scripts/scan_real.py", "--step", "2", "--timeframe", "W",
      "--tag", "real_strict_W"]),
    ("significance, strict daily",
     [PY, "scripts/analyse_signals.py", "--tag", "real_strict_D", "--horizon", "20"]),
]


def main() -> None:
    root = HERE.parent
    for i, (label, cmd) in enumerate(STEPS, 1):
        print(f"\n{'=' * 72}\n[{i}/{len(STEPS)}] {label}\n{'=' * 72}")
        r = subprocess.run(cmd, cwd=root)
        if r.returncode != 0:
            print(f"FAILED: {label}")
            sys.exit(r.returncode)
    print("\nall steps complete; results in out/")


if __name__ == "__main__":
    main()
