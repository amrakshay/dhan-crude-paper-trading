"""Daily series, regime rule B and the shortlist, all derived from the PANEL.

Deriving them from the panel (not from daily_bars) means the synthetic tests
run through exactly this code. Everything for day d uses days < d only:
`tests/test_causality.py` corrupts day d onward and asserts nothing moves.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

import numpy as np
import pandas as pd

from islib.features import Features, ffill_bars
from islib.panel import Panel

from . import ORDER_BLOCK

N_SHORTLIST = 5
BAND = 0.09            # |prev-day move| >= 9% -> at or near a price band
BREADTH_THRESHOLD = 0.60


def _shift_days(x: np.ndarray, n: int = 1) -> np.ndarray:
    """[S, D] -> value as of n days earlier (NaN for the first n)."""
    out = np.full_like(x, np.nan, dtype=np.float64)
    out[:, n:] = x[:, :-n]
    return out


def sectors(symbols: list[str]) -> np.ndarray:
    m = pd.read_csv(ORDER_BLOCK / "data" / "sectors.csv").set_index("symbol")["sector"]
    return np.array([m.get(s, f"_solo_{s}") for s in symbols])


@dataclass
class Daily:
    close: np.ndarray      # [S, D]
    high: np.ndarray
    low: np.ndarray
    nifty: np.ndarray      # [D]


def daily(p: Panel) -> Daily:
    with np.errstate(all="ignore"):
        c = ffill_bars(p.close)[:, :, -1].astype(np.float64)
        h = np.nanmax(p.high, axis=2).astype(np.float64)
        l = np.nanmin(p.low, axis=2).astype(np.float64)
    kept = ~np.isnan(p.turnover_cr)
    c[~kept] = np.nan
    h[~kept] = np.nan
    l[~kept] = np.nan
    n = ffill_bars(p.index_close)[:, -1].astype(np.float64)
    return Daily(c, h, l, n)


def _roll(x: np.ndarray, n: int, minp: int, how: str) -> np.ndarray:
    df = pd.DataFrame(x.T)
    r = getattr(df.rolling(n, min_periods=minp), how)()
    return r.to_numpy().T


class Universe:
    """Per-day eligibility, features, shortlist and regime, for trading day d
    (inputs: closes up to d-1, plus day d's 09:15 open for the gap flag)."""

    def __init__(self, p: Panel, F: Features | None = None, n: int = N_SHORTLIST):
        self.p = p
        self.F = F or Features(p)
        self.n = n
        self.D = daily(p)
        self.sector = sectors(p.symbols)

    # --- regime B, as traded --------------------------------------------
    @cached_property
    def regime_b(self) -> np.ndarray:
        """[D] bool: more than 60% of names with a defined SMA50 were below it
        at the close of d-1. Matches bearlib.regime rule B by construction."""
        c = self.D.close
        sma = _roll(c, 50, 40, "mean")
        defined = ~np.isnan(sma) & ~np.isnan(c)
        with np.errstate(invalid="ignore"):
            below = (c < sma) & defined
        share = below.sum(0) / np.maximum(defined.sum(0), 1)
        at_close = (share > BREADTH_THRESHOLD) & (defined.sum(0) > 0)
        out = np.zeros_like(at_close)
        out[1:] = at_close[:-1]
        return out

    # --- features at the close of d-1, stamped on d -----------------------
    @cached_property
    def features(self) -> dict[str, np.ndarray]:
        c, h, l, n = self.D.close, self.D.high, self.D.low, self.D.nifty
        with np.errstate(all="ignore"):
            r20 = c / _shift_days(c, 20) - 1.0
            nr20 = n / np.concatenate([np.full(20, np.nan), n[:-20]]) - 1.0
            sma50 = _roll(c, 50, 40, "mean")
            loc = np.where(h > l, (c - l) / (h - l), 0.5)
            ret = c / _shift_days(c, 1) - 1.0
            nret = n / np.concatenate([[np.nan], n[:-1]]) - 1.0
        # 60-session beta, from returns that exist (both legs present).
        nr = np.broadcast_to(nret, ret.shape)
        ok = ~np.isnan(ret) & ~np.isnan(nr)
        x = np.where(ok, nr, np.nan)
        y = np.where(ok, ret, np.nan)
        mx, my = _roll(x, 60, 50, "mean"), _roll(y, 60, 50, "mean")
        mxy, mxx = _roll(x * y, 60, 50, "mean"), _roll(x * x, 60, 50, "mean")
        with np.errstate(all="ignore"):
            beta = (mxy - mx * my) / (mxx - mx * mx)
        feats = {
            "rs_nifty": r20 - nr20[None, :],
            "trend": c / sma50 - 1.0,
            "close_loc": loc,
            "beta": beta,
            "r20": r20,
            "ret": ret,
        }
        # Everything above is AT the close of day t; shift to the trading day.
        return {k: _shift_days(v, 1) for k, v in feats.items()}

    @cached_property
    def eligible(self) -> np.ndarray:
        f = self.features
        with np.errstate(invalid="ignore"):
            ok = (self.F.liquid & ~self.p.gap_flag & (np.abs(f["ret"]) < BAND))
        for k in ("rs_nifty", "trend", "close_loc", "beta", "r20"):
            ok &= ~np.isnan(f[k])
        return ok

    @cached_property
    def score(self) -> np.ndarray:
        """[S, D] mean percentile rank of the six oriented features; NaN if
        not eligible. Higher = better short."""
        f, el = self.features, self.eligible
        S, D = el.shape
        r20 = np.where(el, f["r20"], np.nan)
        sec_med_all = np.full((S, D), np.nan)
        sec_med_other = np.full((S, D), np.nan)
        for sec in np.unique(self.sector):
            rows = np.flatnonzero(self.sector == sec)
            block = r20[rows]                                   # [k, D]
            with np.errstate(all="ignore"):
                med = np.nanmedian(block, axis=0) if len(rows) else np.full(D, np.nan)
            sec_med_all[rows] = med
            for i, s in enumerate(rows):
                others = np.delete(block, i, axis=0)
                if others.shape[0] == 0:
                    sec_med_other[s] = np.nan
                else:
                    with np.errstate(all="ignore"):
                        sec_med_other[s] = np.nanmedian(others, axis=0)
        rs_sector = np.where(np.isnan(sec_med_other), 0.0, r20 - sec_med_other)
        cols = {
            "rs_nifty": -f["rs_nifty"],
            "rs_sector": -rs_sector,
            "trend": -f["trend"],
            "close_loc": -f["close_loc"],
            "sector_weak": -sec_med_all,
            "beta": f["beta"],
        }
        ranks = []
        for v in cols.values():
            v = np.where(el, v, np.nan)
            ranks.append(pd.DataFrame(v.T).rank(axis=1, pct=True).to_numpy().T)
        with np.errstate(all="ignore"):
            sc = np.nanmean(np.stack(ranks), axis=0)
        return np.where(el, sc, np.nan)

    @cached_property
    def shortlist(self) -> np.ndarray:
        """[S, D] bool: top N by score (ties by symbol order)."""
        sc = self.score
        S, D = sc.shape
        out = np.zeros((S, D), bool)
        for d in range(D):
            v = sc[:, d]
            idx = np.flatnonzero(~np.isnan(v))
            if idx.size == 0:
                continue
            order = idx[np.lexsort((idx, -v[idx]))]
            out[order[: self.n], d] = True
        return out

    def random_shortlist(self, rng) -> np.ndarray:
        el = self.eligible
        S, D = el.shape
        out = np.zeros((S, D), bool)
        for d in range(D):
            idx = np.flatnonzero(el[:, d])
            if idx.size:
                out[rng.choice(idx, size=min(self.n, idx.size), replace=False), d] = True
        return out
