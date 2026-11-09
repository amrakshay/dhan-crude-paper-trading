"""Step 1: reproduce spec section 9.3 (V0) with the ORIGINAL engine, untouched.

Imports `gate_run/engine.py` and `gate_run/run_gates.load` from the pullback
repo read-only (run with PYTHONDONTWRITEBYTECODE=1 so no __pycache__ lands
there). Writes nothing outside experiments/leaders-pot/out/.

Expected (spec 9.3): final Rs 70,93,012 | CAGR 19.1% | max DD -17.8% | MAR 1.07 | 669 trades.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
GATE_RUN = Path("/Users/akshay/Workarea/local/pullback/backend/intrday_test_strategy/research2/gate_run")
sys.path.insert(0, str(GATE_RUN))
sys.path.insert(0, str(GATE_RUN.parent))

from engine import G, run, stats  # noqa: E402  (pullback, read-only)
from run_gates import load  # noqa: E402  (pullback, read-only)


def main() -> None:
    t0 = time.time()
    syms, nifty = load()
    print(f"loaded {len(syms)} symbols; NIFTY {nifty.date.min()} -> {nifty.date.max()} ({time.time()-t0:.1f}s)")
    t, c = run({k: v.copy() for k, v in syms.items()}, nifty, G("V0 C4 baseline (as-is)"))
    m = stats(t, c)
    print(f"V0 original engine: final Rs {m['final']:,.0f} | CAGR {m['cagr']:.2f}% | DD {m['dd']:.2f}% | "
          f"MAR {m['mar']:.3f} | n {m['n']} | win {m['win']:.1f}%  ({time.time()-t0:.1f}s)")
    out = HERE / "out"
    out.mkdir(exist_ok=True)
    json.dump({k: float(v) for k, v in m.items()}, open(out / "baseline_original_engine.json", "w"), indent=1)
    t.to_csv(out / "baseline_original_trades.csv", index=False)


if __name__ == "__main__":
    main()
