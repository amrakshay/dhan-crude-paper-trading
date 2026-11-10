"""Regenerate the golden fixture that holds the VCP port to the experiment.

Runs the EXPERIMENT's detector (experiments/pattern-detection/patlib, its own
venv) over stored daily bars and writes, under tests/fixtures/vcp/:

  <SYMBOL>.csv   the bars it read, so the test does not need the database
  golden.json    every detection the experiment reports at each sampled bar

`tests/test_vcp_detector_port.py` then runs the SERVER's copy over the same
CSVs and asserts identical output. Run this only after a deliberate change to
the experiment's detector has been copied across:

    cd experiments/pattern-detection
    .venv/bin/python ../../backend/scripts/make_vcp_golden_fixture.py

It needs the experiment's venv (numpy, no server dependencies) and reads the
database read-only.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments" / "pattern-detection"))

from patlib.bars import load_sqlite  # noqa: E402
from patlib.vcp import detect_vcp  # noqa: E402

DB = ROOT / "backend" / "data" / "paper_trading.db"
OUT = ROOT / "backend" / "tests" / "fixtures" / "vcp"

# Four names with many historical signals between them, a stride that lands on
# both forming and breakout readings, and every bar on which the backtest first
# saw a base for these names.
SYMBOLS = ("APARINDS", "SRF", "TITAN", "BAJAJFINSV")
STRIDE = 7
SPAN = 700        # the most recent bars sampled per symbol


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    seen = {}
    bases = ROOT / "experiments" / "pattern-detection" / "out" / "vcp_entry_bases.json"
    if bases.exists():
        for row in json.loads(bases.read_text()):
            seen.setdefault(row["symbol"], set()).add(int(row["seen_idx"]))

    golden = []
    for symbol in SYMBOLS:
        b = load_sqlite(DB, symbol)
        with (OUT / f"{symbol}.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "open", "high", "low", "close", "volume"])
            for i in range(len(b)):
                w.writerow([str(b.date[i]), repr(float(b.open[i])), repr(float(b.high[i])),
                            repr(float(b.low[i])), repr(float(b.close[i])),
                            repr(float(b.volume[i]))])
        ts = set(range(max(260, len(b) - SPAN), len(b), STRIDE))
        ts |= {t for t in seen.get(symbol, ()) if t < len(b)}
        for t in sorted(ts):
            golden.append(dict(symbol=symbol, t=t,
                               detections=[d.to_dict() for d in detect_vcp(b, t)]))
    (OUT / "golden.json").write_text(json.dumps(golden, indent=0, sort_keys=True))
    n = sum(len(g["detections"]) for g in golden)
    print(f"{len(golden)} sampled bars, {n} detections, written to {OUT}")


if __name__ == "__main__":
    main()
