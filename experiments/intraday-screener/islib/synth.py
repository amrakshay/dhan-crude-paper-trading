"""Synthetic panels with a known answer, for checking the harness itself.

`random_walk_panel` has NO edge by construction: any hypothesis should earn
about minus its slippage gross, and minus slippage-plus-costs net. `plant_orb_edge`
adds a drift in the direction of an opening-range break after it happens, so
H1 must find a positive edge and the random-side control must not.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from .panel import N_BARS, Panel


def random_walk_panel(S=40, D=300, seed=0, vol_bar=0.002, start=date(2018, 1, 1)) -> Panel:
    rng = np.random.default_rng(seed)
    dates, d = [], start
    while len(dates) < D:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    rets = rng.normal(0, vol_bar, size=(S, D, N_BARS)).astype(np.float32)
    gaps = rng.normal(0, 0.01, size=(S, D, 1)).astype(np.float32)
    rets[:, :, :1] += gaps
    level = 100 * np.exp(np.cumsum(rets.reshape(S, -1), axis=1)).reshape(S, D, N_BARS)
    close = level.astype(np.float32)
    open_ = np.empty_like(close)
    open_[:, :, 1:] = close[:, :, :-1]
    # Bar 0 OPENS at the previous close plus the overnight gap; only its
    # intrabar move is inside the bar. (The first version divided out the
    # whole of rets[0], which put the gap INSIDE bar 0 and made every open
    # equal the previous close -- so no gap hypothesis ever fired.)
    open_[:, :, 0] = close[:, :, 0] / np.exp(rets[:, :, 0] - gaps[:, :, 0])
    wick = np.abs(rng.normal(0, vol_bar / 2, size=close.shape)).astype(np.float32)
    high = np.maximum(open_, close) * (1 + wick)
    low = np.minimum(open_, close) * (1 - wick)
    # U-shaped intraday volume, with day-level and bar-level noise.
    tod = 1 + 2 * (np.linspace(-1, 1, N_BARS) ** 2)
    volume = (1e4 * tod[None, None, :] * rng.lognormal(0, 0.5, size=(S, D, 1))
              * rng.lognormal(0, 0.3, size=close.shape)).astype(np.float32)
    idx_close = close.mean(axis=0)
    idx_open = open_.mean(axis=0)
    turnover = (close * volume).sum(axis=2) / 1e7
    return Panel([f"S{i:03d}" for i in range(S)], dates, open_, high, low, close, volume,
                 idx_close, idx_open, turnover.astype(np.float32), np.zeros((S, D), bool))


def plant_orb_edge(p: Panel, drift_per_bar=0.0008, from_bar=3) -> Panel:
    """After the first close outside the opening range, move every later bar
    of that day by `drift_per_bar` in the break's direction (compounded)."""
    orh = p.high[:, :, :3].max(axis=2)
    orl = p.low[:, :, :3].min(axis=2)
    S, D, B = p.close.shape
    mult = np.ones((S, D, B), dtype=np.float64)
    for s in range(S):
        for d in range(D):
            c = p.close[s, d]
            brk = None
            for k in range(from_bar, B):
                if c[k] > orh[s, d]:
                    brk = (k, 1)
                    break
                if c[k] < orl[s, d]:
                    brk = (k, -1)
                    break
            if brk:
                k, side = brk
                n = np.arange(B) - k
                mult[s, d] = np.where(n > 0, (1 + side * drift_per_bar) ** n, 1.0)
    f = mult.astype(np.float32)
    return Panel(p.symbols, p.dates, p.open * f, p.high * f, p.low * f, p.close * f, p.volume,
                 p.index_close, p.index_open, p.turnover_cr, p.gap_flag)
