"""The server's VCP detector is the backtested one, to the last float.

`src/vcp/services/detector/` is a copy of experiments/pattern-detection/patlib.
The fixture under tests/fixtures/vcp/ was produced by running the EXPERIMENT's
detector over stored bars (`scripts/make_vcp_golden_fixture.py`); this replays
the same bars through the server's copy and asserts every detection matches
field for field -- state, footprint, pivot, stop, score, every metric.

Exact equality, not a tolerance: the code is the same code on the same inputs,
so any difference at all means the port has drifted from what was measured.
"""
import csv
import json
from pathlib import Path

import pytest

from src.vcp.services.detector.bars import from_rows
from src.vcp.services.detector.vcp import detect_vcp

FIXTURES = Path(__file__).parent / "fixtures" / "vcp"


def _bars(symbol):
    with (FIXTURES / f"{symbol}.csv").open() as fh:
        rows = [(r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"])
                for r in csv.DictReader(fh)]
    return from_rows(symbol, rows)


GOLDEN = json.loads((FIXTURES / "golden.json").read_text())


def test_the_fixture_exercises_both_states():
    states = {d["state"] for g in GOLDEN for d in g["detections"]}
    assert {"forming", "breakout"} <= states
    assert sum(len(g["detections"]) for g in GOLDEN) >= 50


@pytest.mark.parametrize("symbol", sorted({g["symbol"] for g in GOLDEN}))
def test_the_port_reproduces_every_experiment_detection(symbol):
    bars = _bars(symbol)
    cases = [g for g in GOLDEN if g["symbol"] == symbol]
    mismatches = []
    for case in cases:
        got = [d.to_dict() for d in detect_vcp(bars, case["t"])]
        # JSON round trip so tuples and ints compare the way the fixture stores them
        got = json.loads(json.dumps(got, sort_keys=True))
        if got != case["detections"]:
            mismatches.append(case["t"])
    assert not mismatches, f"{symbol}: port differs from the experiment at t={mismatches[:10]}"
