"""Signals -> trades for the short book. The screener's `islib.engine` fill
rules, unchanged, plus two things it does not have:

  trailing  a stop that ratchets toward price after every bar CLOSE
            (B1: close + 3 x ATR5 for a short) and applies from the next bar;
  locks     no short into a locked-down bar, no cover in a locked-up one
            (METHOD.md "Locked limits"). A lock that lasts past the exit bar
            is priced at the exit close + 20% and flagged `exit_locked`.

Index trades (`simulate_index`) use the NIFTY bars, the futures card and real
lot sizes, and are never blocked by the lock rule.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from islib.features import Features
from islib.panel import LAST_EXIT_BAR, Panel

from .costs import FuturesCard, RateCard, nifty_lot

LOCK_MOVE = 0.095
LOCKED_EXIT_PENALTY = 0.20


@dataclass(frozen=True)
class Exec:
    slippage: float = 0.0005
    notional: float = 500_000.0
    exit_bar: int = LAST_EXIT_BAR


def lock_masks(p: Panel, F: Features) -> tuple[np.ndarray, np.ndarray]:
    pc = F.prev_close[..., None]
    with np.errstate(invalid="ignore"):
        flat = p.high == p.low
        up = flat & (p.close / pc - 1.0 >= LOCK_MOVE)
        dn = flat & (p.close / pc - 1.0 <= -LOCK_MOVE)
    return up, dn


def market_halt(ib: dict, preopen_bars: int = 3, min_run: int = 3) -> np.ndarray:
    """[D, B] bool: bars in which the WHOLE market could not trade continuously.

    The vendor does not leave a halt empty: it prints the frozen index level as
    flat bars (high == low). Found on the first real run (2020-03-13, lower
    circuit at 09:20: the index short 'entered' at 8,630.75 during the halt).
    Six days carry them: 2017-07-10 and 2021-02-24 (NSE outages), 2020-03-13
    and 2020-03-23 (market-wide circuit), 2021-11-13 and 2022-03-07. After a
    run of >= `min_run` flat bars the next `preopen_bars` bars are the 15-minute
    pre-open call auction (they move, but they are indicative, not continuous)."""
    o, h, l = ib["open"], ib["high"], ib["low"]
    with np.errstate(invalid="ignore"):
        flat = (h == l) & ~np.isnan(h)
    out = flat.copy()
    D, B = flat.shape
    for d in np.flatnonzero(flat.any(axis=1)):
        run = 0
        for b in range(B):
            if flat[d, b]:
                run += 1
            else:
                if run >= min_run:
                    out[d, b:b + preopen_bars] = True
                run = 0
    return out


TRADE_COLUMNS = ["s", "d", "k", "side", "entry_bar", "entry_px", "stop", "stop0", "exit_bar",
                 "exit_px", "exit_reason", "gross_ret", "cost_frac", "net_ret", "r_multiple",
                 "risk_frac", "mfe", "mae", "exit_locked"]


def simulate(p: Panel, F: Features, sig: pd.DataFrame, cfg: Exec = Exec(),
             card: RateCard | None = None, locks=None, halt: np.ndarray | None = None) -> pd.DataFrame:
    """sig: s, d, k, side, stop, [trail] (ATR multiple, NaN = fixed stop).
    `halt` [D, B]: market-wide halt bars -- nothing fills in them, either way."""
    card = card or RateCard.load()
    up, dn = locks if locks is not None else lock_masks(p, F)
    if halt is not None:
        up, dn = up | halt[None], dn | halt[None]
        both = halt[None]
    else:
        both = None
    O, H, L, C, A = p.open, p.high, p.low, p.close, F.atr5
    slip, last = cfg.slippage, cfg.exit_bar
    trail = sig["trail"].to_numpy() if "trail" in sig else np.full(len(sig), np.nan)
    rows = []
    for (s, d, k, side, stop), tm in zip(
            sig[["s", "d", "k", "side", "stop"]].itertuples(index=False, name=None), trail):
        s, d, k, side = int(s), int(d), int(k), int(side)
        o, h, l, c, a = O[s, d], H[s, d], L[s, d], C[s, d], A[s, d]
        blocked_entry = dn[s, d] if side < 0 else up[s, d]
        blocked_exit = up[s, d] if side < 0 else dn[s, d]
        if both is not None:
            blocked_entry = blocked_entry | both[0, d]
            blocked_exit = blocked_exit | both[0, d]
        base = dict(s=s, d=d, k=k, side=side, stop0=stop, exit_locked=False)
        cand = [j for j in range(k + 1, last + 1) if not np.isnan(o[j]) and not blocked_entry[j]]
        if not cand:
            rows.append(base | {"exit_reason": "not_taken:no_bar_before_exit"})
            continue
        e = cand[0]
        raw = float(o[e])
        if side * (raw - stop) <= 0:
            rows.append(base | {"exit_reason": "not_taken:stop_already_breached", "entry_bar": e})
            continue
        entry = raw * (1 + side * slip)
        risk = abs(raw - stop) / raw
        cur = float(stop)
        exit_px = reason = xb = None
        for j in range(e, last + 1):
            if np.isnan(o[j]):
                continue
            if not blocked_exit[j]:
                oj, hj, lj = float(o[j]), float(h[j]), float(l[j])
                if side > 0:
                    if j > e and oj <= cur:
                        exit_px, reason = oj * (1 - slip), "stop_gap"
                    elif lj <= cur:
                        exit_px, reason = cur * (1 - slip), "stop"
                else:
                    if j > e and oj >= cur:
                        exit_px, reason = oj * (1 + slip), "stop_gap"
                    elif hj >= cur:
                        exit_px, reason = cur * (1 + slip), "stop"
                if reason:
                    xb = j
                    break
            if not np.isnan(tm) and not np.isnan(a[j]) and not np.isnan(c[j]):
                lvl = float(c[j]) - side * tm * float(a[j])
                cur = max(cur, lvl) if side > 0 else min(cur, lvl)
        locked = False
        if reason is None:
            present = np.flatnonzero(~np.isnan(c[e:last + 1]))
            xb = e + int(present[-1])
            px = float(c[xb])
            stock_locked = (up[s, d] if side < 0 else dn[s, d])[xb] and not (
                both is not None and both[0, d, xb])
            if both is not None and both[0, d, xb] and not stock_locked:
                # A market-wide halt through the exit (2021-02-24): NSE extended
                # the session and positions were closed later at prices this
                # data does not have. Priced at the frozen close, flagged.
                exit_px, reason = px * (1 - side * slip), "time_market_halt"
            elif blocked_exit[xb]:
                locked = True
                exit_px, reason = px * (1 - side * LOCKED_EXIT_PENALTY), "time_locked"
            else:
                exit_px, reason = px * (1 - side * slip), "time"
        seg_h, seg_l = h[e:xb + 1], l[e:xb + 1]
        with np.errstate(all="ignore"):
            fav = (np.nanmax(seg_h) / entry - 1) if side > 0 else (1 - np.nanmin(seg_l) / entry)
            adv = (np.nanmin(seg_l) / entry - 1) if side > 0 else (1 - np.nanmax(seg_h) / entry)
        rows.append(base | dict(
            entry_bar=e, entry_px=entry, stop=cur, exit_bar=xb, exit_px=exit_px,
            exit_reason=reason, gross_ret=side * (exit_px / entry - 1),
            r_multiple=side * (exit_px - entry) / abs(entry - stop), risk_frac=risk,
            mfe=fav, mae=adv, exit_locked=locked))
    t = pd.DataFrame(rows).reindex(columns=TRADE_COLUMNS)
    tk = t["entry_px"].notna() & t["exit_px"].notna()
    qty = cfg.notional / t.loc[tk, "entry_px"]
    ev, xv = qty * t.loc[tk, "entry_px"], qty * t.loc[tk, "exit_px"]
    long_ = t.loc[tk, "side"] > 0
    t.loc[tk, "cost_frac"] = card.round_trip(np.where(long_, ev, xv), np.where(long_, xv, ev)) / cfg.notional
    t["net_ret"] = t["gross_ret"] - t["cost_frac"]
    t["date"] = [p.dates[i] for i in t["d"]]
    t["symbol"] = [p.symbols[i] for i in t["s"]]
    return t


def index_bars(p: Panel, path=None) -> dict[str, np.ndarray]:
    """NIFTY OHLC [D, B] aligned to the panel calendar. The panel stores only
    the index open and close; high/low come from the cleaned parquet."""
    from islib.panel import CLEAN, INDEX, N_BARS
    b = pd.read_parquet(path or CLEAN / "5m" / f"{INDEX}.parquet")
    pos = {d: i for i, d in enumerate(p.dates)}
    di = b["date"].map(pos)
    ok = di.notna() & (b["bar"] < N_BARS)
    out = {}
    for f in ("open", "high", "low", "close"):
        a = np.full((len(p.dates), N_BARS), np.nan)
        a[di[ok].astype(int), b.loc[ok, "bar"].astype(int)] = b.loc[ok, f]
        out[f] = a
    return out


def simulate_index(p: Panel, ib: dict, sig: pd.DataFrame, slippage: float = 0.0001,
                   card: FuturesCard | None = None, lots: int = 1,
                   exit_bar: int = LAST_EXIT_BAR) -> pd.DataFrame:
    """sig: d, k, side, [stop]. NIFTY futures proxied by the spot index.
    Halted bars (`market_halt`) are treated as absent; if the halt runs
    through the exit bar, the exit is at the frozen close, flagged."""
    card = card or FuturesCard()
    halt = market_halt(ib)
    ib = {f: np.where(halt, np.nan, ib[f]) for f in ("open", "high", "low", "close")}
    rows = []
    stops = sig["stop"].to_numpy() if "stop" in sig else np.full(len(sig), np.nan)
    for (d, k, side), stop in zip(sig[["d", "k", "side"]].itertuples(index=False, name=None), stops):
        d, k, side = int(d), int(k), int(side)
        o, h, l, c = (ib[f][d] for f in ("open", "high", "low", "close"))
        nxt = np.flatnonzero(~np.isnan(o[k + 1:exit_bar + 1]))
        if nxt.size == 0:
            rows.append(dict(d=d, k=k, side=side, exit_reason="not_taken:no_bar"))
            continue
        e = k + 1 + int(nxt[0])
        raw = float(o[e])
        if not np.isnan(stop) and side * (raw - stop) <= 0:
            rows.append(dict(d=d, k=k, side=side, exit_reason="not_taken:stop_already_breached"))
            continue
        entry = raw * (1 + side * slippage)
        exit_px = reason = xb = None
        if not np.isnan(stop):
            for j in range(e, exit_bar + 1):
                if np.isnan(o[j]):
                    continue
                if side < 0 and j > e and o[j] >= stop:
                    exit_px, reason, xb = o[j] * (1 + slippage), "stop_gap", j
                elif side < 0 and h[j] >= stop:
                    exit_px, reason, xb = stop * (1 + slippage), "stop", j
                elif side > 0 and j > e and o[j] <= stop:
                    exit_px, reason, xb = o[j] * (1 - slippage), "stop_gap", j
                elif side > 0 and l[j] <= stop:
                    exit_px, reason, xb = stop * (1 - slippage), "stop", j
                if reason:
                    break
        if reason is None:
            present = np.flatnonzero(~np.isnan(c[e:exit_bar + 1]))
            xb = e + int(present[-1])
            exit_px, reason = float(c[xb]) * (1 - side * slippage), "time"
        day = p.dates[d]
        lot = nifty_lot(day)
        qty = lot * lots
        ev, xv = qty * entry, qty * exit_px
        buy, sell = (ev, xv) if side > 0 else (xv, ev)
        cost = card.round_trip(buy, sell, day)
        g = side * (exit_px / entry - 1)
        rows.append(dict(d=d, k=k, side=side, entry_bar=e, entry_px=entry, exit_bar=xb,
                         exit_px=exit_px, exit_reason=reason, gross_ret=g,
                         cost_frac=cost / ev, net_ret=g - cost / ev, lot=lot, lots=lots,
                         notional=ev, pnl_rs=side * qty * (exit_px - entry) - cost))
    t = pd.DataFrame(rows)
    t["date"] = [p.dates[i] for i in t["d"]]
    t["s"] = -1
    t["symbol"] = "NIFTYFUT"
    return t
