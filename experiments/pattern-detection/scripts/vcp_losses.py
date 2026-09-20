"""Anatomy of the losing trades, and what (if anything) predicts them.

THE TRAP THIS IS BUILT TO AVOID. Cutting losers is trivially easy -- take
fewer trades -- and in a strategy whose profit is entirely in the right tail,
almost every filter that removes losers removes winners faster. Two of these
have already been tested and failed exactly that way: waiting for price to
coil nearer the pivot, and the index regime filter.

So every candidate filter here is judged on THREE numbers together:

  win rate      does it actually remove losers
  total R kept  what fraction of the strategy's total profit survives
  CAGR          the only one that pays for anything

A filter that lifts the win rate and lowers total R has made the equity curve
prettier and the account smaller.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
from patlib.bars import load_sqlite
from patlib.indicators import atr, sma
from vcp_backtest import simulate, summarise
from vcp_final import DB, build_trades

OUT = Path(__file__).resolve().parent.parent / "out"


def enrich(trades, rows):
    """Attach everything knowable at the entry bar."""
    meta = {(r["symbol"], r["seen_idx"]): r for r in rows}
    pre = {}
    f = OUT / "vcp_pre_signals.json"
    if f.exists():
        for r in json.loads(f.read_text()):
            pre[(r["symbol"], r["entry_idx"])] = r
    by_sym = defaultdict(list)
    for t in trades:
        by_sym[t["symbol"]].append(t)
    for sym, group in by_sym.items():
        b = load_sqlite(DB, sym)
        a14 = atr(b, 14)
        turn = sma(b.close * b.volume, 20)
        v20 = sma(b.volume, 20)
        for t in group:
            i = t["entry_idx"]
            m = meta.get((sym, i), {})
            p = pre.get((sym, i), {})
            t["score"] = m.get("score")
            t["t_count"] = p.get("t_count")
            t["squeeze"] = p.get("squeeze")
            t["dry"] = p.get("dry")
            t["tt"] = m.get("tt", p.get("tt"))
            t["to_pivot"] = m.get("first_gap", p.get("to_pivot"))
            t["rr"] = p.get("rr")
            t["price"] = float(b.close[i])
            t["turnover_cr"] = float(turn[i]) / 1e7        # rupees crore/day
            t["atr_pct"] = float(a14[i]) / max(float(b.close[i]), 1e-9)
            t["vol20"] = float(v20[i])
            # did the entry bar itself close up?
            t["up_bar"] = bool(b.close[i] > b.open[i])
            # where in its own 52w range
            lo = float(b.low[max(0, i - 251):i + 1].min())
            hi = float(b.high[max(0, i - 251):i + 1].max())
            t["pct_52w"] = (float(b.close[i]) - lo) / max(hi - lo, 1e-9)
            t["broke_out"] = t["reason"] != "stop" or False
            # distinguish a base that never broke out from a breakout that failed
            piv = m.get("pivot") or p.get("pivot")
            t["confirmed"] = False
            if piv:
                hiJ = min(len(b) - 1, t["exit_idx"])
                t["confirmed"] = bool((b.close[i + 1:hiJ + 1] > piv).any())
    return trades


def anatomy(trades):
    L = ["ANATOMY OF THE LOSSES", ""]
    n = len(trades)
    R = np.array([t["R"] for t in trades], float)
    losers = [t for t in trades if t["R"] <= 0]
    winners = [t for t in trades if t["R"] > 0]
    L.append(f"{n} signals   {len(winners)} winners ({len(winners)/n:.0%})   "
             f"{len(losers)} losers ({len(losers)/n:.0%})   total {R.sum():.0f}R")
    L.append("")
    L.append(f"{'bucket':<34}{'n':>7}{'share':>8}{'mean R':>9}{'total R':>10}{'med bars':>10}")
    groups = {
        "never broke out (base failed)": [t for t in trades if not t["confirmed"]],
        "broke out, then failed": [t for t in trades if t["confirmed"] and t["R"] <= 0],
        "broke out, worked": [t for t in trades if t["confirmed"] and t["R"] > 0],
    }
    for k, v in groups.items():
        if not v:
            continue
        r = np.array([x["R"] for x in v], float)
        L.append(f"{k:<34}{len(v):>7}{len(v)/n:>8.0%}{r.mean():>9.2f}"
                 f"{r.sum():>10.0f}{np.median([x['bars'] for x in v]):>10.0f}")
    L.append("")
    lr = np.array([t["R"] for t in losers], float)
    L.append(f"losers cost {lr.sum():.0f}R in total; winners make "
             f"{np.array([t['R'] for t in winners]).sum():.0f}R")
    L.append(f"a loser costs {lr.mean():.2f}R on average -- the tight stop means "
             f"they are cheap, which is the whole design")
    top = np.sort(R)[-max(1, n // 20):]
    L.append(f"the best 5% of trades produce {top.sum():.0f}R, "
             f"{top.sum()/R.sum():.0%} of everything")
    return L


FEATURES = [
    ("turnover_cr", "20-day turnover, Rs crore/day", [0, 1, 5, 20, 1e9]),
    ("price", "share price, Rs", [0, 100, 300, 1000, 1e9]),
    ("atr_pct", "ATR14 as % of price", [0, .02, .03, .045, 1]),
    ("risk_pct", "distance to the stop", [0, .015, .03, .06, 1]),
    ("to_pivot", "distance still to the pivot", [0, .03, .06, .10, 1]),
    ("squeeze", "range contraction inside the base", [0, .45, .60, .75, 2]),
    ("dry", "final volume vs base average", [0, .6, .8, 1.0, 5]),
    ("t_count", "contractions", [2, 3, 4, 9]),
    ("tt", "Trend Template criteria passed", [6, 7, 8]),
    ("pct_52w", "position in its own 52-week range", [0, .6, .8, .93, 1.01]),
    ("score", "detector score", [0, .70, .78, .85, 1.01]),
]


def feature_table(trades):
    L = ["", "WHAT SEPARATES A WINNER FROM A LOSER", "",
         "Each feature is bucketed and every bucket reports the win rate, the",
         "mean R, and -- the column that matters -- the SHARE OF TOTAL PROFIT",
         "that lives in it. A bucket with a poor win rate holding 40% of the",
         "profit must not be filtered out.", ""]
    total_R = sum(t["R"] for t in trades)
    for key, label, edges in FEATURES:
        vals = np.array([t.get(key) if t.get(key) is not None else np.nan
                         for t in trades], float)
        if not np.isfinite(vals).any():
            continue
        L.append(f"{label}")
        L.append(f"   {'bucket':<18}{'n':>7}{'win%':>7}{'mean R':>9}"
                 f"{'total R':>10}{'% of profit':>13}")
        for lo, hi in zip(edges, edges[1:]):
            m = (vals >= lo) & (vals < hi)
            if m.sum() < 30:
                continue
            g = [t for t, k in zip(trades, m) if k]
            r = np.array([x["R"] for x in g], float)
            L.append(f"   [{lo:g}, {hi:g})".ljust(21)
                     + f"{len(g):>4}{np.mean(r > 0):>7.0%}{r.mean():>9.2f}"
                     + f"{r.sum():>10.0f}{r.sum()/total_R:>13.0%}")
        L.append("")
    return L


CANDIDATE_FILTERS = {
    "none (baseline)": lambda t: True,
    "turnover >= Rs 1cr/day": lambda t: t["turnover_cr"] >= 1,
    "turnover >= Rs 5cr/day": lambda t: t["turnover_cr"] >= 5,
    "price >= Rs 100": lambda t: t["price"] >= 100,
    "ATR14 <= 4.5% of price": lambda t: t["atr_pct"] <= 0.045,
    "ATR14 <= 3% of price": lambda t: t["atr_pct"] <= 0.03,
    "stop at least 1.5% away": lambda t: t["risk_pct"] >= 0.015,
    "stop no wider than 6%": lambda t: t["risk_pct"] <= 0.06,
    "entry bar closed up": lambda t: t["up_bar"],
    "all 7 Trend Template": lambda t: (t["tt"] or 0) >= 7,
    "top 20% of 52w range": lambda t: t["pct_52w"] >= 0.80,
    "3+ contractions": lambda t: (t["t_count"] or 0) >= 3,
    "turnover>=1cr AND stop>=1.5%": lambda t: t["turnover_cr"] >= 1 and t["risk_pct"] >= 0.015,
    "turnover>=5cr AND ATR<=4.5%": lambda t: t["turnover_cr"] >= 5 and t["atr_pct"] <= 0.045,
}


def filter_table(trades, capital, per_trade, cost):
    L = ["", "CANDIDATE FILTERS -- judged on all three numbers at once", ""]
    base_R = sum(t["R"] for t in trades)
    L.append(f"{'filter':<32}{'kept':>7}{'win%':>7}{'mean R':>8}"
             f"{'% profit kept':>15}{'CAGR':>8}{'maxDD':>8}{'final':>9}")
    for name, fn in CANDIDATE_FILTERS.items():
        kept = [t for t in trades if fn(t)]
        if len(kept) < 100:
            continue
        r = np.array([t["R"] for t in kept], float)
        ser, led, sk = simulate(kept, capital, per_trade, cost)
        if not led:
            continue
        s, _, _ = summarise(ser, led, capital, sk, len(kept))
        L.append(f"{name:<32}{len(kept)/len(trades):>7.0%}{np.mean(r > 0):>7.0%}"
                 f"{r.mean():>8.2f}{r.sum()/base_R:>15.0%}"
                 f"{s['cagr']:>8.1f}{s['maxdd']:>8.1f}{s['final']/1e5:>8.1f}L")
    L.append("")
    L.append("kept          = share of signals that survive the filter")
    L.append("% profit kept = share of the unfiltered strategy's total R that survives")
    L.append("A filter is only an improvement if CAGR goes UP. Win rate on its own")
    L.append("is a vanity metric here.")
    return L


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=1_000_000)
    ap.add_argument("--per-trade", type=float, default=100_000)
    ap.add_argument("--cost", type=float, default=0.003)
    a = ap.parse_args()
    rows = json.loads((OUT / "vcp_entry_bases.json").read_text())
    trades = enrich(build_trades(rows)["early + EMA50"], rows)
    L = anatomy(trades) + feature_table(trades) + \
        filter_table(trades, a.capital, a.per_trade, a.cost)
    txt = "\n".join(L)
    print(txt)
    (OUT / "vcp_losses_report.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
