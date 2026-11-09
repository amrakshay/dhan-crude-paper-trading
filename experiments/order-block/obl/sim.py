"""Orders from setups, the fill simulation, and rupee P&L.

An ORDER is a setup made tradeable: a limit at Z with a bracket (stop S1,
target T), live in bars k+1 .. end, gated bar by bar. Orders are grouped by
(original name, day, original side); a group stops at its first fill, which
is "one trade per name per day" within a side. Across sides the earlier fill
wins (`resolve_sides`).

Every order carries two sides, kept apart on purpose:
  gside   the ORIGINAL side: which gate decides when it is live
  sign    the EXECUTION side: which way it actually trades
They differ only in the random-side control. Prices are handled in long space
(see setups.py): value = sign * price, high/low swapped for shorts, so the
simulation below is written once, for a long.

Fill rules (README.md):
  limit entry   only if the low trades THROUGH Z; at Z or a lower open; no slippage
  fill bar      stopped in it if its low also reaches S1; never a target in it
  stop          S1 or a worse open, minus slippage
  target        T or a better open, no slippage
  both in a bar stop
  time          close of bar 71, minus slippage
  cancel        before the retest: price reaches T (the move went without you),
                a newer setup replaces it, or the window ends
  first retest  the first VISIT to Z (consecutive bars whose low reaches it)
                is the only chance; a second visit is not taken
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from numba import njit

from islib.costs import RateCard

from .setups import LAST_MARK_BAR

LAST_LIVE_BAR = LAST_MARK_BAR + 1       # 57: an order may fill up to the 13:55-14:00 decision
LAST_EXIT_BAR = 71
ACCOUNT = 500_000.0
RISK_FRAC = 0.01
MAX_LEVERAGE = 5.0

R_TIME, R_STOP, R_STOP_GAP, R_TARGET, R_STOP_FILLBAR = 0, 1, 2, 3, 4
REASONS = {R_TIME: "time", R_STOP: "stop", R_STOP_GAP: "stop_gap", R_TARGET: "target",
           R_STOP_FILLBAR: "stop_fill_bar"}


@njit(cache=True)
def _px(A, Bneg, s, d, j, sign):
    """Long-space value of field A (or minus field Bneg for shorts)."""
    return A[s, d, j] if sign > 0 else -Bneg[s, d, j]


@njit(cache=True)
def _manage(O, H, L, C, s, d, sign, e, entry, stop, target, limit_fill, slip, last_exit,
            fill_bar_stop=True):
    """Run a long-space position from bar e. Returns exit value, bar, reason."""
    for j in range(e, last_exit + 1):
        o = _px(O, O, s, d, j, sign)
        if np.isnan(o):
            continue
        h = _px(H, L, s, d, j, sign)
        l = _px(L, H, s, d, j, sign)
        if j == e and limit_fill:
            # The fill bar: the order of events inside it is unknown, so a low
            # through S1 is a stop and a high through the target is NOT a win.
            if fill_bar_stop and l <= stop:
                px = o if o <= stop else stop
                return px - slip * abs(px), j, R_STOP_FILLBAR
            continue
        if j > e and o >= target:
            return o, j, R_TARGET
        if j > e and o <= stop:
            return o - slip * abs(o), j, R_STOP_GAP
        if l <= stop:
            return stop - slip * abs(stop), j, R_STOP
        if h >= target:
            return target, j, R_TARGET
    for j in range(last_exit, e - 1, -1):
        c = _px(C, C, s, d, j, sign)
        if not np.isnan(c):
            return c - slip * abs(c), j, R_TIME
    return np.nan, -1, -1


@njit(cache=True)
def _simulate(O, H, L, C, GL, GS, grp, s_exec, s_gate, d_, gside, sign_, k_, end_, Z_, S1_, T_,
              obl_, trade, slip, first_only, chase, gate_at_mark, last_live, last_exit, out,
              fill_bar_stop):
    n = grp.shape[0]
    row = 0
    i = 0
    while i < n:
        g = grp[i]
        a = i
        while i < n and grp[i] == g:
            i += 1
        for q in range(a, i):
            if not trade[q]:
                continue
            s, sg, d, sign, k = s_exec[q], s_gate[q], d_[q], sign_[q], k_[q]
            Z, S1, T, obl = Z_[q], S1_[q], T_[q], obl_[q]
            G = GL if gside[q] > 0 else GS
            last = min(end_[q], last_live)
            filled = False
            if chase:
                if not G[sg, d, k]:
                    continue
                for j in range(k + 1, last + 1):
                    o = _px(O, O, s, d, j, sign)
                    if np.isnan(o):
                        continue
                    if o <= S1 or o >= T:
                        break
                    entry = o + slip * abs(o)
                    x, xb, why = _manage(O, H, L, C, s, d, sign, j, entry, S1, T, False, slip, last_exit)
                    out[row, 0] = q; out[row, 1] = j; out[row, 2] = entry; out[row, 3] = x
                    out[row, 4] = xb; out[row, 5] = why; out[row, 6] = 0; out[row, 7] = entry - S1
                    row += 1
                    filled = True
                    break
            else:
                visits = 0
                touching = False
                for j in range(k + 1, last + 1):
                    o = _px(O, O, s, d, j, sign)
                    if np.isnan(o):
                        continue
                    h = _px(H, L, s, d, j, sign)
                    l = _px(L, H, s, d, j, sign)
                    c = _px(C, C, s, d, j, sign)
                    if o >= T and not touching:
                        break                               # ran to the target without us
                    if l <= Z:
                        if not touching:
                            visits += 1
                            touching = True
                        if first_only and visits > 1:
                            break
                        live = G[sg, d, k] if gate_at_mark else G[sg, d, j - 1]
                        if live and l < Z:
                            entry = o if o < Z else Z
                            x, xb, why = _manage(O, H, L, C, s, d, sign, j, entry, S1, T, True, slip,
                                                 last_exit, fill_bar_stop)
                            out[row, 0] = q; out[row, 1] = j; out[row, 2] = entry; out[row, 3] = x
                            out[row, 4] = xb; out[row, 5] = why; out[row, 6] = visits
                            out[row, 7] = Z - S1
                            row += 1
                            filled = True
                            break
                        if c < obl:
                            break                           # zone broken on a close
                    else:
                        touching = False
                        if h >= T:
                            break                           # target reached before the retest
            if filled:
                break
    return row


ORDER_COLS = ["grp", "s_exec", "s_gate", "d", "gside", "sign", "k", "end", "Z", "S1", "T", "obl",
              "trade"]


def orders_from_setups(st: np.ndarray, side: int, D: int, target: str = "R2",
                       rr_min: float | None = 1.5) -> pd.DataFrame:
    """Setups (long space, one side) -> orders. Every setup ends the previous
    one's window (replacement), whether or not it is itself tradeable."""
    df = pd.DataFrame(st, columns=["s", "d", "k", "ob", "Z", "S1", "R1", "R2", "obl", "impulse"])
    for c in ("s", "d", "k", "ob", "impulse"):
        df[c] = df[c].astype(np.int64)
    df = df.sort_values(["s", "d", "k"]).reset_index(drop=True)
    nxt = df.groupby(["s", "d"])["k"].shift(-1)
    df["end"] = nxt.fillna(LAST_LIVE_BAR).astype(np.int64).clip(upper=LAST_LIVE_BAR)
    df["rr"] = (df["R2"] - df["Z"]) / (df["Z"] - df["S1"])
    # target "time": no target at all, the position runs to the stop or 15:10
    T = df[target] if target in ("R1", "R2") else pd.Series(np.inf, index=df.index)
    trade = df["R2"].notna()            # no R2 -> no R:R -> the popup has no plan
    if rr_min is not None:
        trade &= df["rr"] >= rr_min
    return pd.DataFrame({
        "grp": (df["s"] * D + df["d"]) * 2 + (side > 0),
        "s_exec": df["s"], "s_gate": df["s"], "d": df["d"], "gside": side, "sign": side,
        "k": df["k"], "end": df["end"], "Z": df["Z"], "S1": df["S1"], "T": T.astype(float),
        "obl": df["obl"], "trade": trade,
        "rr": df["rr"], "R1": df["R1"], "R2": df["R2"], "impulse": df["impulse"], "ob": df["ob"],
    })


def simulate(p, gates: tuple[np.ndarray, np.ndarray], orders: pd.DataFrame, slip: float = 0.0005,
             first_only: bool = True, chase: bool = False, gate_at_mark: bool = False,
             fill_bar_stop: bool = True) -> pd.DataFrame:
    """Run orders; return one row per FILLED trade (long-space prices).

    fill_bar_stop=False is a SENSITIVITY bound only, never the rule: it assumes
    the low of the fill bar always came BEFORE the fill, so a stop there never
    counts. It is the most generous reading of a 5-minute bar."""
    orders = orders.sort_values(["grp", "k"], kind="stable").reset_index(drop=True)
    a = {c: orders[c].to_numpy() for c in ORDER_COLS}
    out = np.empty((len(orders), 8))
    n = _simulate(p.open, p.high, p.low, p.close, gates[0], gates[1],
                  a["grp"].astype(np.int64), a["s_exec"].astype(np.int64), a["s_gate"].astype(np.int64),
                  a["d"].astype(np.int64), a["gside"].astype(np.int64), a["sign"].astype(np.int64),
                  a["k"].astype(np.int64), a["end"].astype(np.int64), a["Z"].astype(float),
                  a["S1"].astype(float), a["T"].astype(float), a["obl"].astype(float),
                  a["trade"].astype(np.bool_), float(slip), first_only, chase, gate_at_mark,
                  LAST_LIVE_BAR, LAST_EXIT_BAR, out, fill_bar_stop)
    t = pd.DataFrame(out[:n], columns=["q", "fill_bar", "entry_ls", "exit_ls", "exit_bar", "reason",
                                       "retest", "risk_ps"])
    for c in ("q", "fill_bar", "exit_bar", "reason", "retest"):
        t[c] = t[c].astype(np.int64)
    o = orders.iloc[t["q"].to_numpy()].reset_index(drop=True)
    keep = [c for c in orders.columns if c not in ("trade",)]
    t = pd.concat([o[keep], t.drop(columns="q")], axis=1)
    t["reason"] = t["reason"].map(REASONS)
    return t


def resolve_sides(t: pd.DataFrame, D: int) -> pd.DataFrame:
    """One trade per name per day across sides: the earlier fill wins (ties:
    the long, arbitrarily -- a same-bar fill on both sides needs a bar that
    reached both zones, and is rare)."""
    t = t.assign(_nd=t["grp"] // 2)
    t = t.sort_values(["_nd", "fill_bar", "gside"], ascending=[True, True, False], kind="stable")
    return t.drop_duplicates("_nd", keep="first").drop(columns="_nd").reset_index(drop=True)


def price_trades(t: pd.DataFrame, p, card: RateCard | None = None, account: float = ACCOUNT,
                 risk_frac: float = RISK_FRAC, max_leverage: float = MAX_LEVERAGE,
                 fixed_notionals=(100_000.0, 500_000.0)) -> pd.DataFrame:
    """Rupee P&L. Size = risk budget / risk per share, capped at max_leverage x
    account. net_R is P&L after charges, in units of the risk budget."""
    card = card or RateCard.load()
    t = t.copy()
    sign = t["sign"].to_numpy()
    entry_px = np.abs(t["entry_ls"].to_numpy())          # real price (always > 0)
    exit_px = np.abs(t["exit_ls"].to_numpy())
    pnl_ps = (t["exit_ls"] - t["entry_ls"]).to_numpy()   # per share, already signed
    budget = account * risk_frac
    qty = np.minimum(budget / t["risk_ps"].to_numpy(), max_leverage * account / entry_px)
    long_ = sign > 0
    buy = np.where(long_, qty * entry_px, qty * exit_px)
    sell = np.where(long_, qty * exit_px, qty * entry_px)
    charges = card.round_trip(buy, sell)
    t["qty"] = qty
    t["notional"] = qty * entry_px
    t["gross_R"] = qty * pnl_ps / budget
    t["charges_R"] = charges / budget
    t["net_R"] = t["gross_R"] - t["charges_R"]
    t["risk_frac_px"] = t["risk_ps"].to_numpy() / entry_px
    t["gross_ret"] = pnl_ps / entry_px
    for n in fixed_notionals:
        q = n / entry_px
        c = card.round_trip(np.where(long_, n, q * exit_px), np.where(long_, q * exit_px, n))
        t[f"net_bps_{int(n // 1000)}k"] = 1e4 * (t["gross_ret"] - c / n)
    t["date"] = [p.dates[i] for i in t["d"]]
    t["symbol"] = [p.symbols[i] for i in t["s_exec"]]
    t["side"] = sign
    return t
