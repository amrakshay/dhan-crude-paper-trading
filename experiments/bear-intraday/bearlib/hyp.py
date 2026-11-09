"""B1, B2, B6 as signal generators (METHOD.md), and their controls.

`signals(U, days, names)`: `days` [D] bool says which days may trade (regime
on, inside the period); `names` [S, D] bool says which names (the shortlist,
or a random one for the shortlist test). Returns s, d, k, side, stop, trail.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from islib.features import OPENING_RANGE_BARS, Features
from islib.hypotheses import LAST_DECISION_BAR, first_true

from .universe import Universe

TRAIL_ATR = 3.0
B2_RALLY_LAST_BAR = 17      # the rally must touch by the close of 10:40-10:45


def _empty():
    return pd.DataFrame({c: pd.Series(dtype=float) for c in ("s", "d", "k", "side", "stop", "trail")})


class B1Breakdown:
    name = "B1_breakdown"

    def signals(self, U: Universe, days: np.ndarray, names: np.ndarray) -> pd.DataFrame:
        F = U.F
        orh, orl = F.opening_range
        c = F.p.close
        rs = F.ret_since_open - F.index_ret_since_open[None, :, :]
        ok = (names & days[None, :])[..., None]
        with np.errstate(invalid="ignore"):
            m = ok & (c < orl[..., None]) & (rs < 0)
        sig = first_true(m, OPENING_RANGE_BARS, LAST_DECISION_BAR)
        if sig.empty:
            return _empty()
        s, d = sig["s"].to_numpy(), sig["d"].to_numpy()
        return sig.assign(side=-1, stop=orh[s, d], trail=TRAIL_ATR)


class B2FailedRally:
    name = "B2_failed_rally"

    def signals(self, U: Universe, days: np.ndarray, names: np.ndarray) -> pd.DataFrame:
        F, p = U.F, U.p
        h, l, c, vw = p.high, p.low, p.close, F.vwap
        pc = F.prev_close
        rows = []
        for s, d in zip(*np.nonzero(names & days[None, :])):
            hs, ls, cs, vs = h[s, d], l[s, d], c[s, d], vw[s, d]
            if np.isnan(pc[s, d]):
                continue
            j0 = None
            for j in range(1, B2_RALLY_LAST_BAR + 1):
                if np.isnan(hs[j]) or np.isnan(vs[j - 1]) or np.isnan(cs[j - 1]):
                    continue
                t = min(pc[s, d], vs[j - 1])
                if cs[j - 1] < t <= hs[j]:
                    j0 = j
                    break
            if j0 is None:
                continue
            best, r = -np.inf, None
            for m in range(j0, LAST_DECISION_BAR + 1):
                if np.isnan(hs[m]):
                    continue
                if hs[m] > best:
                    best, r = hs[m], m
                if m >= max(j0 + 2, 2) and r <= m - 2 and cs[m] < ls[r] and cs[m] < vs[m]:
                    rows.append((s, d, m, -1, best, np.nan))
                    break
        if not rows:
            return _empty()
        return pd.DataFrame(rows, columns=["s", "d", "k", "side", "stop", "trail"])


class B6GapUpFade:
    """Contaminated: the gap-fade family has used the 2023+ holdout."""
    name = "B6_gap_up_fade"

    def signals(self, U: Universe, days: np.ndarray, names: np.ndarray) -> pd.DataFrame:
        F, p = U.F, U.p
        k = 2
        g = F.gap
        with np.errstate(invalid="ignore"):
            m = (names & days[None, :] & ~p.gap_flag & (np.nan_to_num(g) >= 0.01)
                 & ~np.isnan(p.close[:, :, k]) & (F.close_ff[:, :, k] < F.day_open))
        s, d = np.nonzero(m)
        orh, _ = F.opening_range
        return pd.DataFrame({"s": s, "d": d, "k": k, "side": -1, "stop": orh[s, d], "trail": np.nan})


HYPOTHESES = {h.name: h for h in (B1Breakdown(), B2FailedRally(), B6GapUpFade())}
# B6 is drawn from the liquid 100, not the shortlist (METHOD.md).
UNIVERSE_OF = {"B1_breakdown": "shortlist", "B2_failed_rally": "shortlist", "B6_gap_up_fade": "liquid"}


# ---------------------------------------------------------------------------
# Controls: keep the stop DISTANCE (fraction of the decision close) and the
# trailing rule; replace one ingredient. (HARNESS.md leaks 1 and 2.)
# ---------------------------------------------------------------------------

def _frac(F: Features, sig: pd.DataFrame) -> np.ndarray:
    ref = F.close_ff[sig["s"].to_numpy(), sig["d"].to_numpy(), sig["k"].to_numpy()]
    return np.abs(ref - sig["stop"].to_numpy()) / ref


def _place(F: Features, c: pd.DataFrame, frac: np.ndarray, trail) -> pd.DataFrame:
    ref = F.close_ff[c["s"].to_numpy(), c["d"].to_numpy(), c["k"].to_numpy()]
    return c.assign(stop=ref * (1 - c["side"].to_numpy() * frac), trail=trail)


def control_mirror_long(U, sig, rng):
    c = sig[["s", "d", "k"]].copy()
    c["side"] = -sig["side"].to_numpy()
    return _place(U.F, c, _frac(U.F, sig), sig["trail"].to_numpy())


def control_random_time(U, sig, rng):
    c = sig[["s", "d", "side"]].copy()
    k = sig["k"].to_numpy().astype(int)
    c["k"] = k + np.floor(rng.random(len(c)) * (LAST_DECISION_BAR + 1 - k)).astype(int)
    return _place(U.F, c, _frac(U.F, sig), sig["trail"].to_numpy())


def control_random_name(U, sig, rng):
    F = U.F
    has_bar = ~np.isnan(F.p.close)
    out = np.empty(len(sig), dtype=int)
    for i, (s, d, k) in enumerate(sig[["s", "d", "k"]].astype(int).itertuples(index=False, name=None)):
        pool = np.flatnonzero(F.liquid[:, d] & has_bar[:, d, k])
        pool = pool[pool != s]
        out[i] = rng.choice(pool) if pool.size else s
    c = sig[["d", "k", "side"]].copy()
    c["s"] = out
    return _place(F, c, _frac(F, sig), sig["trail"].to_numpy())


CONTROLS = {"random_name": control_random_name, "random_time": control_random_time,
            "mirror_long": control_mirror_long}
