"""Survivorship indicator, in the manner of spec section 12: compare an
equal-weight index of today's 485 survivors (the backtest universe) with the
real Nifty 500 index (^CRSLDX, yfinance, read-only market data).

Confound fixed in advance: equal weight vs cap weight. The gap is an upper-bound
indicator of the bias, not a clean measurement.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from lpot.data import load  # noqa: E402

syms, nifty = load()
rets = pd.DataFrame({s: d.set_index("date").close.pct_change() for s, d in syms.items()})
rets = rets.sort_index()
ew = (1 + rets.mean(axis=1, skipna=True).fillna(0)).cumprod()

import yfinance as yf  # noqa: E402
h = yf.Ticker("^CRSLDX").history(start="2015-07-01", end="2026-09-18", auto_adjust=False)
h.index = [x.date().isoformat() for x in h.index]
idx = h.Close
print(f"^CRSLDX rows {len(idx)}: {idx.index[0]} -> {idx.index[-1]}")

START = "2016-07-20"
common = [d for d in ew.index if d >= START and d in idx.index]
e = ew[common]; i = idx[common]
yrs = (pd.Timestamp(common[-1]) - pd.Timestamp(common[0])).days / 365.25
out = dict(start=common[0], end=common[-1],
           ew_total=100 * (e.iloc[-1] / e.iloc[0] - 1), idx_total=100 * (i.iloc[-1] / i.iloc[0] - 1),
           ew_cagr=100 * ((e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1),
           idx_cagr=100 * ((i.iloc[-1] / i.iloc[0]) ** (1 / yrs) - 1))
wins = [("2019-07-29", "2019-09-23"), ("2020-02-25", "2020-07-20"), ("2022-04-28", "2022-08-01"),
        ("2025-01-03", "2025-04-22"), ("2026-02-26", "2026-09-17")]
out["windows"] = []
for a, b in wins:
    ea = e[(e.index >= a) & (e.index <= b)]; ia = i[(i.index >= a) & (i.index <= b)]
    out["windows"].append(dict(a=a, b=b, ew=100 * (ea.iloc[-1] / ea.iloc[0] - 1), idx=100 * (ia.iloc[-1] / ia.iloc[0] - 1)))
print(json.dumps(out, indent=1))
json.dump(out, open(HERE / "out" / "survivorship.json", "w"), indent=1)
