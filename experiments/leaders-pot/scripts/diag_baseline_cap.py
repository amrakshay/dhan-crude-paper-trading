"""DIAGNOSTIC (not part of the pass bar): how much does V0 owe to the original
engine's entry cap reading the gate at the FILL day's close (a one-day
lookahead that can only block entries)? Runs V0 with the cap reading the
signal close instead. One extra variant, counted in the README."""
from __future__ import annotations
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from lpot.data import load  # noqa: E402
from lpot.engine import Cfg, run, stats  # noqa: E402

syms, nifty = load()
r = run({k: v.copy() for k, v in syms.items()}, nifty, Cfg(name="V0 cap@signal", main_cap_prev_gate=True))
m = stats(r["trades"], r["curve"])
print({k: round(float(v), 3) for k, v in m.items()})
json.dump({k: float(v) for k, v in m.items()}, open(HERE / "out" / "diag_baseline_cap.json", "w"), indent=1)
