"""The server's scan finds the backtest's bases, on the same days.

Golden: `scan_golden.json`, the rows `vcp_entry_policies.py --step 1` produced
for four names (daily scan, the cadence the server runs at). The server scans
the last 860 bars of each from an EMPTY memory -- a 160-bar replay followed by
700 compared bars, the same shape the bootstrap and the nightly have -- and
must find exactly the backtest's bases in those 700 bars. A 160-bar replay was
measured to match a full-history scan at 88 of 88 go-live dates; this keeps
that property under test without scanning ten years per run.
"""
import csv
import json
from pathlib import Path

import pytest

from src.strategies.services.strategy_registry import get_strategy_registry
from src.vcp.database.db_models.vcp_models import SIGNAL_IGNORED
from src.vcp.services import signal_service as ss
from src.vcp.services.detector.bars import from_rows
from src.vcp.services.vcp_parameters import VcpParameters

FIXTURES = Path(__file__).parent / "fixtures" / "vcp"
REPLAY, COMPARED = 160, 700


@pytest.mark.parametrize("symbol", ["APARINDS", "SRF", "TITAN", "BAJAJFINSV"])
def test_the_scan_reproduces_the_backtests_daily_signals(symbol):
    params = VcpParameters.from_definition(get_strategy_registry().require("nse-vcp-swing"))
    with (FIXTURES / f"{symbol}.csv").open() as fh:
        rows = [(r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"])
                for r in csv.DictReader(fh)]
    bars = from_rows(symbol, rows)
    n = len(bars)
    first_compared = bars.date[n - COMPARED].astype(str)
    got = [b for b in ss.scan(bars, range(n - COMPARED - REPLAY, n), [], params)
           if b.status != SIGNAL_IGNORED and b.t >= n - COMPARED]
    expected = [e for e in json.loads((FIXTURES / "scan_golden.json").read_text())[symbol]
                if e["seen_date"] >= first_compared]
    assert expected, "the compared window must contain backtest signals"
    assert [(b.seen_date.isoformat(), round(b.pivot, 4), round(b.stop, 4)) for b in got] == [
        (e["seen_date"], round(e["pivot"], 4), round(e["stop"], 4)) for e in expected
    ]
