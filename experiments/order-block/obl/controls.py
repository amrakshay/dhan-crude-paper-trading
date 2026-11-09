"""Controls: each replaces ONE ingredient of an order and keeps everything else.

Kept from the real order: the gate it is live under (original name, original
side), its window (mark bar, replacement bar), the first-touch and cancel
rules, and its geometry as FRACTIONS of price --

    depth   = (close_k - Z) / |close_k|     how far below price the limit sits
    risk    = (Z - S1) / |Z|                stop distance
    reward  = (T - Z) / |Z|                 target distance
    brk     = (Z - block low) / |Z|         where a close breaks the zone

The screener's lesson applies (HARNESS.md): a control that re-derived its
levels from the strategy's RULE would smuggle the strategy back in. These
keep the DISTANCES, never the rule.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _geometry(o: pd.DataFrame, close_ls: np.ndarray):
    Z = o["Z"].to_numpy()
    aZ = np.abs(Z)
    return {
        "depth": (close_ls - Z) / np.abs(close_ls),
        "risk": (Z - o["S1"].to_numpy()) / aZ,
        "reward": (o["T"].to_numpy() - Z) / aZ,
        "brk": (Z - o["obl"].to_numpy()) / aZ,
    }


def _place(o: pd.DataFrame, s_exec, sign, close_ls_new, g, depth) -> pd.DataFrame:
    Z = close_ls_new - depth * np.abs(close_ls_new)
    aZ = np.abs(Z)
    return o.assign(s_exec=s_exec, sign=sign, Z=Z, S1=Z - g["risk"] * aZ,
                    T=Z + g["reward"] * aZ, obl=Z - g["brk"] * aZ)


def _close_ls(close_ff, s, d, k, sign):
    return sign * close_ff[s, d, k].astype(float)


def random_side(o: pd.DataFrame, close_ff, universe, rng) -> pd.DataFrame:
    """Same name, moment, gate and geometry; a coin for the direction."""
    c = _close_ls(close_ff, o["s_exec"].to_numpy(), o["d"].to_numpy(), o["k"].to_numpy(),
                  o["sign"].to_numpy())
    g = _geometry(o, c)
    flip = rng.choice([-1, 1], size=len(o))
    new_sign = o["sign"].to_numpy() * flip
    c2 = c * flip                              # the same price, seen from the other side
    return _place(o, o["s_exec"].to_numpy(), new_sign, c2, g, g["depth"])


def random_depth(o: pd.DataFrame, close_ff, universe, rng) -> pd.DataFrame:
    """Same name, side, moment, gate, risk and reward; the limit at a random
    depth, uniform on [0, 2x] the order block's. Is the block's level special,
    or is any pullback as good?"""
    c = _close_ls(close_ff, o["s_exec"].to_numpy(), o["d"].to_numpy(), o["k"].to_numpy(),
                  o["sign"].to_numpy())
    g = _geometry(o, c)
    depth = g["depth"] * rng.uniform(0, 2, size=len(o))
    return _place(o, o["s_exec"].to_numpy(), o["sign"].to_numpy(), c, g, depth)


def random_name(o: pd.DataFrame, close_ff, universe, rng) -> pd.DataFrame:
    """Same moment, side, gate and geometry, on a random OTHER universe name
    that had a price at that bar. Did the selection carry anything?"""
    s, d, k = o["s_exec"].to_numpy(), o["d"].to_numpy(), o["k"].to_numpy()
    sign = o["sign"].to_numpy()
    g = _geometry(o, _close_ls(close_ff, s, d, k, sign))
    new_s = s.copy()
    for (dd, kk), idx in pd.Series(np.arange(len(o))).groupby([d, k]).groups.items():
        pool = np.flatnonzero(universe[:, dd] & ~np.isnan(close_ff[:, dd, kk]))
        for i in idx:
            cand = pool[pool != s[i]]
            if cand.size:
                new_s[i] = rng.choice(cand)
    c2 = _close_ls(close_ff, new_s, d, k, sign)
    return _place(o, new_s, sign, c2, g, g["depth"])


CONTROLS = {"random_side": random_side, "random_depth": random_depth, "random_name": random_name}


def apply(name: str, orders: pd.DataFrame, close_ff, universe, seed: int) -> pd.DataFrame:
    """Transform only the tradeable orders; the rest still end windows."""
    rng = np.random.default_rng(seed)
    t = orders["trade"].to_numpy()
    new = CONTROLS[name](orders.loc[t], close_ff, universe, rng)
    out = orders.copy()
    for c in ("s_exec", "sign", "Z", "S1", "T", "obl"):
        out.loc[t, c] = new[c].to_numpy()
    return out
