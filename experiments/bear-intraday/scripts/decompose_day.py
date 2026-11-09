"""Diagnostic (exploratory, not a pass-bar test): where does a regime-on day's
NIFTY move happen -- overnight, in the first 15 minutes, or in the tradeable
session 09:30 -> 15:10? Spot index, no costs.

    .venv/bin/python scripts/decompose_day.py      -> out/decompose_day.json
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import json
from datetime import date

import numpy as np
import pandas as pd

from islib import panel as PN

from bearlib import OUT
from bearlib.engine import index_bars, market_halt
from bearlib.universe import Universe

p = PN.load()
U = Universe(p)
ib = index_bars(p)
halt = market_halt(ib)
c = np.where(halt, np.nan, ib["close"])
o = np.where(halt, np.nan, ib["open"])


def at(a, b):
    """Value at bar b, or the next tradeable bar after it."""
    out = np.full(a.shape[0], np.nan)
    for d in range(a.shape[0]):
        nz = np.flatnonzero(~np.isnan(a[d, b:72]))
        if nz.size:
            out[d] = a[d, b + nz[0]]
    return out


prev_close = np.concatenate([[np.nan], ib["close"][:-1, :][np.arange(len(p.dates) - 1),
                              np.array([np.flatnonzero(~np.isnan(r))[-1] for r in ib["close"][:-1]])]])
day_open = ib["open"][:, 0]
c0930 = at(c, 2)
o0930 = at(o, 3)
c1510 = np.array([r[np.flatnonzero(~np.isnan(r[:72]))[-1]] for r in np.where(np.isnan(c), np.nan, c)])
last = np.array([r[np.flatnonzero(~np.isnan(r))[-1]] for r in ib["close"]])
legs = pd.DataFrame({
    "overnight": day_open / prev_close - 1,
    "open_to_0930": c0930 / day_open - 1,
    "session_0930_1510": c1510 / o0930 - 1,
    "close_auction_tail": last / c1510 - 1,
}, index=pd.to_datetime(p.dates))
dev = legs.index < "2023-01-01"
out = {}
for name, on in (("regime_on", U.regime_b), ("regime_off", ~U.regime_b)):
    # Development only. (A first version also printed 2023+ rows; see
    # out/holdout_log.json for what that exposed.)
    for per, m in (("dev", dev),):
        sub = legs[on & m]
        out[f"{name}_{per}"] = {"days": int(len(sub)),
                                **{k: round(1e4 * float(v), 2) for k, v in sub.mean().items()},
                                "session_share_up": round(float((sub["session_0930_1510"] > 0).mean()), 3)}
(OUT / "decompose_day.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))
