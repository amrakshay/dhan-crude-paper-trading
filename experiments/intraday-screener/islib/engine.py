"""Turn signals into trades, on 5-minute bars, pessimistically.

A SIGNAL is decided on the CLOSE of bar k (so everything it used is known)
and says: symbol s, day d, side (+1 long / -1 short), a stop price and
optionally a target price. Everything below follows from that.

Fills, in the order a real day would produce them:

  entry   the OPEN of the first bar after k that has one, plus adverse
          slippage. Never the close of bar k -- that price is gone by the time
          the order exists.
  skip    if that open is already through the stop (or beyond the target),
          nobody would take the trade live, so it is recorded as not taken.
  stop    a stop-market order: fills at the stop, or at the bar's OPEN if the
          bar opened through it (a gap does not honour your price), then
          adverse slippage.
  target  a limit order: fills at the target, or at a better open; no
          slippage, since a limit does not pay the spread.
  both    a bar that touches stop AND target is assumed to have hit the STOP
          first. 5-minute bars cannot say which came first; assuming the good
          one is how a backtest invents an edge.
  time    at the close of bar 71 (15:10-15:15) -- the closing auction starts
          at 15:15 -- with adverse slippage.

Costs are the Dhan intraday rate card at a fixed notional per trade.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .costs import RateCard
from .panel import LAST_EXIT_BAR, Panel


@dataclass(frozen=True)
class ExecConfig:
    slippage: float = 0.0005          # per side, fraction of price (5 bps)
    notional: float = 100_000.0       # rupees per trade; costs depend on it
    exit_bar: int = LAST_EXIT_BAR


TRADE_COLUMNS = [
    "s", "d", "k", "side", "entry_bar", "entry_px", "stop", "target", "exit_bar",
    "exit_px", "exit_reason", "gross_ret", "cost_frac", "net_ret", "r_multiple",
    "mfe", "mae",
]


def simulate(panel: Panel, signals: pd.DataFrame, cfg: ExecConfig = ExecConfig(),
             card: RateCard | None = None) -> pd.DataFrame:
    """signals: columns s, d, k, side, stop, [target]. Returns one row per
    signal; untaken signals carry exit_reason 'not_taken:<why>' and NaN P&L."""
    card = card or RateCard.load()
    O, H, L, C = panel.open, panel.high, panel.low, panel.close
    slip = cfg.slippage
    last = cfg.exit_bar
    tgt_col = signals["target"].to_numpy() if "target" in signals else np.full(len(signals), np.nan)
    rows = []
    for (s, d, k, side, stop), target in zip(
            signals[["s", "d", "k", "side", "stop"]].itertuples(index=False, name=None), tgt_col):
        s, d, k, side = int(s), int(d), int(k), int(side)
        o, h, l, c = O[s, d], H[s, d], L[s, d], C[s, d]
        base = dict(s=s, d=d, k=k, side=side, stop=stop, target=target)
        after = np.flatnonzero(~np.isnan(o[k + 1:last + 1]))
        if after.size == 0:
            rows.append(base | {"exit_reason": "not_taken:no_bar_before_exit"})
            continue
        e = k + 1 + int(after[0])
        raw = float(o[e])
        if side * (raw - stop) <= 0:
            rows.append(base | {"exit_reason": "not_taken:stop_already_breached", "entry_bar": e})
            continue
        if not np.isnan(target) and side * (target - raw) <= 0:
            rows.append(base | {"exit_reason": "not_taken:target_already_reached", "entry_bar": e})
            continue
        entry = raw * (1 + side * slip)

        exit_px, reason, xb = None, None, None
        for j in range(e, last + 1):
            if np.isnan(o[j]):
                continue
            oj, hj, lj = float(o[j]), float(h[j]), float(l[j])
            if side > 0:
                if not np.isnan(target) and j > e and oj >= target:
                    exit_px, reason = oj, "target"
                elif j > e and oj <= stop:
                    exit_px, reason = oj * (1 - slip), "stop_gap"
                elif lj <= stop:
                    exit_px, reason = stop * (1 - slip), "stop"
                elif not np.isnan(target) and hj >= target:
                    exit_px, reason = target, "target"
            else:
                if not np.isnan(target) and j > e and oj <= target:
                    exit_px, reason = oj, "target"
                elif j > e and oj >= stop:
                    exit_px, reason = oj * (1 + slip), "stop_gap"
                elif hj >= stop:
                    exit_px, reason = stop * (1 + slip), "stop"
                elif not np.isnan(target) and lj <= target:
                    exit_px, reason = target, "target"
            if reason:
                xb = j
                break
        if reason is None:
            present = np.flatnonzero(~np.isnan(c[e:last + 1]))
            xb = e + int(present[-1])
            exit_px, reason = float(c[xb]) * (1 - side * slip), "time"

        seg_h, seg_l = h[e:xb + 1], l[e:xb + 1]
        with np.errstate(all="ignore"):
            fav = (np.nanmax(seg_h) / entry - 1) if side > 0 else (1 - np.nanmin(seg_l) / entry)
            adv = (np.nanmin(seg_l) / entry - 1) if side > 0 else (1 - np.nanmax(seg_h) / entry)
        rows.append(base | dict(
            entry_bar=e, entry_px=entry, exit_bar=xb, exit_px=exit_px, exit_reason=reason,
            gross_ret=side * (exit_px / entry - 1),
            r_multiple=side * (exit_px - entry) / abs(entry - stop),
            mfe=fav, mae=adv,
        ))

    t = pd.DataFrame(rows).reindex(columns=TRADE_COLUMNS)
    taken = t["entry_px"].notna() & t["exit_px"].notna()
    qty = cfg.notional / t.loc[taken, "entry_px"]
    entry_val = qty * t.loc[taken, "entry_px"]
    exit_val = qty * t.loc[taken, "exit_px"]
    long_ = t.loc[taken, "side"] > 0
    buy = np.where(long_, entry_val, exit_val)
    sell = np.where(long_, exit_val, entry_val)
    t.loc[taken, "cost_frac"] = card.round_trip(buy, sell) / cfg.notional
    t["net_ret"] = t["gross_ret"] - t["cost_frac"]
    t["date"] = [panel.dates[i] for i in t["d"]]
    t["symbol"] = [panel.symbols[i] for i in t["s"]]
    return t
