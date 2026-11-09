"""Round 2 (ROUND2.md): price action, technicals and OHLCV order-flow proxies.

Importing this module registers S1-S4 and S6 into `bearlib.hyp.HYPOTHESES`
(shortlist universe), so `bearlib.run.evaluate` runs them with the same
controls, stats and portfolio as round 1. S5 trades the index and has its own
evaluation in `scripts/run_round2.py`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from islib.features import OPENING_RANGE_BARS, Features
from islib.hypotheses import LAST_DECISION_BAR, first_true

from . import hyp as H
from .universe import Universe


# --- order-flow proxies (causal: bar k uses bars 0..k) -----------------------

def clv(F: Features) -> np.ndarray:
    h, l, c = F.p.high, F.p.low, F.p.close
    with np.errstate(invalid="ignore", divide="ignore"):
        x = (2 * c - h - l) / (h - l)
    return np.where(h > l, x, 0.0).astype(np.float32)


_FLOW: dict = {}     # money flow per Features instance (it is [S, D, B])


def money_flow(F: Features) -> np.ndarray:
    """Volume-weighted CLV since the open, in [-1, 1]. NaN before any volume."""
    k = id(F)
    if k not in _FLOW:
        v = np.nan_to_num(F.p.volume)
        num = np.cumsum(np.nan_to_num(clv(F)) * v, axis=2)
        den = np.cumsum(v, axis=2)
        with np.errstate(invalid="ignore", divide="ignore"):
            _FLOW[k] = np.where(den > 0, num / den, np.nan).astype(np.float32)
    return _FLOW[k]


def high_so_far(F: Features) -> np.ndarray:
    h = np.where(np.isnan(F.p.high), -np.inf, F.p.high)
    out = np.maximum.accumulate(h, axis=2)
    return np.where(np.isinf(out), np.nan, out)


def prev_day(x: np.ndarray) -> np.ndarray:
    out = np.full_like(x, np.nan, dtype=np.float64)
    out[:, 1:] = x[:, :-1]
    return out


def _empty():
    return H._empty()


# --- S1 VWAP rejection --------------------------------------------------------

class S1VwapRejection:
    name = "S1_vwap_rejection"

    def signals(self, U: Universe, days, names):
        F = U.F
        h, c, vw, a = F.p.high, F.p.close, F.vwap, F.atr5
        cl, mf = clv(F), money_flow(F)
        ok = (names & days[None, :])[..., None]
        with np.errstate(invalid="ignore"):
            m = ok & (h >= vw) & (c < vw) & (cl <= -0.33) & (mf < 0)
        sig = first_true(m, 6, LAST_DECISION_BAR)
        if sig.empty:
            return _empty()
        s, d, k = (sig[x].to_numpy() for x in ("s", "d", "k"))
        stop = np.maximum(h[s, d, k], vw[s, d, k]) + 0.5 * a[s, d, k]
        return sig.assign(side=-1, stop=stop, trail=np.nan)


# --- S2 prior-day-low breakdown on volume ------------------------------------

class S2PdlBreakdown:
    name = "S2_pdl_breakdown"

    def signals(self, U: Universe, days, names):
        F = U.F
        h, c, a = F.p.high, F.p.close, F.atr5
        pdl = prev_day(U.D.low)
        cl = clv(F)
        idx = F.index_ret_since_open
        ok = (names & days[None, :])[..., None]
        with np.errstate(invalid="ignore"):
            m = (ok & (c < pdl[..., None]) & (F.relvol >= 1.5) & (cl <= -0.4)
                 & (idx[None, :, :] < 0))
        sig = first_true(m, OPENING_RANGE_BARS, LAST_DECISION_BAR)
        if sig.empty:
            return _empty()
        s, d, k = (sig[x].to_numpy() for x in ("s", "d", "k"))
        stop = np.maximum(pdl[s, d], h[s, d, k]) + 1.0 * a[s, d, k]
        return sig.assign(side=-1, stop=stop, trail=3.0)


# --- S3 failed breakout (bull trap) ------------------------------------------

class S3FailedBreakout:
    name = "S3_failed_breakout"

    def signals(self, U: Universe, days, names):
        F = U.F
        c, vw, a = F.p.close, F.vwap, F.atr5
        orh, _ = F.opening_range
        hsf = high_so_far(F)
        rows = []
        for s, d in zip(*np.nonzero(names & days[None, :])):
            cs, vs, o = c[s, d], vw[s, d], orh[s, d]
            if np.isnan(o):
                continue
            j = None
            for b in range(OPENING_RANGE_BARS, 41):
                if not np.isnan(cs[b]) and cs[b] > o:
                    j = b
                    break
            if j is None:
                continue
            for k in range(j + 1, min(j + 6, LAST_DECISION_BAR) + 1):
                if np.isnan(cs[k]) or np.isnan(vs[k]):
                    continue
                if cs[k] < o and cs[k] < vs[k]:
                    rows.append((s, d, k, -1, hsf[s, d, k] + 0.25 * a[s, d, k], np.nan))
                    break
        if not rows:
            return _empty()
        return pd.DataFrame(rows, columns=["s", "d", "k", "side", "stop", "trail"])


# --- S4 afternoon weakness continuation --------------------------------------

class S4AfternoonWeakness:
    name = "S4_afternoon_weakness"
    bar = 45

    def signals(self, U: Universe, days, names):
        F = U.F
        k = self.bar
        c = F.close_ff[:, :, k]
        rs = F.ret_since_open[:, :, k]
        rel = rs - F.index_ret_since_open[None, :, k]
        mf = money_flow(F)[:, :, k]
        with np.errstate(invalid="ignore"):
            m = (names & days[None, :] & ~np.isnan(F.p.close[:, :, k]) & (rs < 0)
                 & (c < F.vwap[:, :, k]) & (rel < 0) & (mf < -0.1))
        s, d = np.nonzero(m)
        if s.size == 0:
            return _empty()
        stop = c[s, d] + 3.0 * F.atr5[s, d, k]
        return pd.DataFrame({"s": s, "d": d, "k": k, "side": -1, "stop": stop, "trail": np.nan})


# --- S6 opening drive down ----------------------------------------------------

class S6OpeningDrive:
    name = "S6_opening_drive"
    bar = 2

    def signals(self, U: Universe, days, names):
        F = U.F
        k = self.bar
        h, l = F.p.high[:, :, : k + 1], F.p.low[:, :, : k + 1]
        with np.errstate(invalid="ignore"):
            hi, lo = np.nanmax(h, axis=2), np.nanmin(l, axis=2)
            c = F.close_ff[:, :, k]
            loc = np.where(hi > lo, (c - lo) / (hi - lo), 0.5)
            m = (names & days[None, :] & ~np.isnan(F.p.close[:, :, k])
                 & (F.ret_since_open[:, :, k] <= -0.0075) & (loc <= 0.25)
                 & (money_flow(F)[:, :, k] < -0.3) & (F.index_ret_since_open[None, :, k] < 0))
        s, d = np.nonzero(m)
        if s.size == 0:
            return _empty()
        stop = hi[s, d] + 0.25 * F.atr5[s, d, k]
        return pd.DataFrame({"s": s, "d": d, "k": k, "side": -1, "stop": stop, "trail": 3.0})


ROUND2 = (S1VwapRejection(), S2PdlBreakdown(), S3FailedBreakout(), S4AfternoonWeakness(),
          S6OpeningDrive())
for _h in ROUND2:
    H.HYPOTHESES[_h.name] = _h
    H.UNIVERSE_OF[_h.name] = "shortlist"
