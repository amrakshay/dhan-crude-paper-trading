"""The two checks that have to pass before any of this is a 'strategy'.

Everything so far is PER-TRADE EXPECTANCY from separate studies. Two things
can still sink it:

  1. REGIME. A slow trailing stop flatters itself in a bull market, and NSE
     2015-2026 was one. If EMA50's edge lives in 2017, 2020, 2021 and 2023
     and vanishes in 2018, 2022 and 2024, it is a bull-market artefact and
     should not be the recommendation.

  2. CAPACITY AND COSTS. A 0.9R expectancy on 3,188 signals means nothing if
     the signals cluster so that a real account could only take a fifth of
     them, or if costs eat the edge once every trade is paid for. Per-trade
     expectancy is not a return.

So: year-by-year for the candidate rules, then a portfolio simulation with a
finite number of slots, fixed-fractional risk and round-trip costs, against
an equal-weighted buy-and-hold of the same universe over the same dates.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
from patlib.bars import load_sqlite
from patlib.indicators import atr

DB = Path(__file__).resolve().parents[3] / "backend" / "data" / "paper_trading.db"
OUT = Path(__file__).resolve().parent.parent / "out"


def ema(x, p):
    a = 2.0 / (p + 1.0)
    o = np.empty(len(x)); o[0] = x[0]
    for i in range(1, len(x)):
        o[i] = a * x[i] + (1 - a) * o[i - 1]
    return o


CANDIDATES = [
    # name,                entry,      ma,  confirm, arm_R, flat
    ("early + flat40",     "early",   None, 1, 0.0, 40),
    ("early + EMA20x2",    "early",     20, 2, 0.0, None),
    ("early + EMA50",      "early",     50, 1, 0.0, None),
    ("breakout + flat40",  "breakout", None, 1, 0.0, 40),
    ("breakout + EMA9@1R", "breakout",    9, 1, 1.0, None),
    ("breakout + EMA20x2", "breakout",   20, 2, 0.0, None),
    ("breakout + EMA50@1R", "breakout",  50, 1, 1.0, None),
]


def run_trade(b, ma, a14, i, entry, stop, trail_from, confirm, arm_R, flat, max_hold):
    risk = entry - stop
    hi = min(len(b) - 1, i + (flat or max_hold))
    below, armed = 0, arm_R <= 0
    for j in range(i + 1, hi + 1):
        if float(b.low[j]) <= stop:
            op = float(b.open[j])
            return j, (op if op < stop else stop), "stop"
        if flat is not None:
            continue
        if trail_from is None or j <= trail_from:
            continue
        if not armed:
            if (float(b.high[j]) - entry) / risk >= arm_R:
                armed = True
            continue
        m = ma[j]
        if float(b.close[j]) < m:
            below += 1
            if below >= confirm:
                return j, float(b.close[j]), "trail"
        else:
            below = 0
    return hi, float(b.close[hi]), "cap"


def build_trades(rows, max_hold=250):
    by_sym = defaultdict(list)
    for r in rows:
        by_sym[r["symbol"]].append(r)
    out = defaultdict(list)
    periods = sorted({c[2] for c in CANDIDATES if c[2]})
    for sym, group in by_sym.items():
        b = load_sqlite(DB, sym)
        mas = {p: ema(b.close, p) for p in periods}
        a14 = atr(b, 14)
        for r in group:
            stop, bo = float(r["stop"]), r.get("breakout_idx")
            for name, ent, per, conf, armR, flat in CANDIDATES:
                ikey = "breakout_idx" if ent == "breakout" else "immediate_idx"
                ekey = "breakout_entry" if ent == "breakout" else "immediate_entry"
                i, e = r.get(ikey), r.get(ekey)
                if i is None or e is None or float(e) <= stop:
                    continue
                e = float(e)
                trail = i if ent == "breakout" else bo
                ei, ex, why = run_trade(b, mas.get(per), a14, i, e, stop, trail,
                                        conf, armR, flat, max_hold)
                out[name].append(dict(
                    symbol=sym, entry_idx=i, exit_idx=ei,
                    entry_date=str(b.date[i]), exit_date=str(b.date[ei]),
                    entry=e, exit=ex, stop=stop,
                    R=(ex - e) / (e - stop), ret=ex / e - 1.0,
                    risk_pct=(e - stop) / e, bars=ei - i, reason=why,
                    year=str(b.date[i])[:4]))
    return out


def portfolio(trades, slots=8, risk_frac=0.01, cost=0.003, start=1.0):
    """Take signals in date order into a finite number of slots.

    Position size is chosen so a stop-out costs `risk_frac` of equity, capped
    at 1/slots so a fully-loaded book is 100% invested and never levered.
    That cap binds constantly for the early entry, whose 2.55% stop would
    otherwise ask for a 39% position to risk 1% -- which is how a tight stop
    quietly turns into leverage.

    Events are processed strictly in date order with EXITS BOOKED BEFORE
    ENTRIES, so capital freed by a close is available the same day and, more
    importantly, so realised profit is never dropped on the floor.
    """
    max_weight = 1.0 / slots
    ev = []
    for t in trades:
        ev.append((t["entry_date"], 1, t))     # 1 sorts after 0: exits first
    for t in trades:
        ev.append((t["exit_date"], 0, t))
    ev.sort(key=lambda x: (x[0], x[1]))

    equity, peak, maxdd = start, start, 0.0
    live, live_ids = {}, 0
    taken = skipped = 0
    curve, exposure = [], []
    for date, kind, t in ev:
        if kind == 0:                                   # an exit
            key = id(t)
            if key in live:
                equity += live.pop(key)
                peak = max(peak, equity)
                maxdd = max(maxdd, (peak - equity) / peak if peak > 0 else 0.0)
                curve.append((date, equity))
            continue
        if len(live) >= slots:                          # an entry, no room
            skipped += 1
            continue
        w = min(risk_frac / max(t["risk_pct"], 1e-6), max_weight)
        live[id(t)] = equity * w * (t["ret"] - cost)
        taken += 1
        exposure.append(len(live) / slots)
        curve.append((date, equity))
    for v in live.values():
        equity += v
    if not curve or taken == 0:
        return None
    d0, d1 = curve[0][0], curve[-1][0]
    yrs = (np.datetime64(d1) - np.datetime64(d0)).astype(int) / 365.25
    cagr = (equity / start) ** (1 / yrs) - 1 if yrs > 0.5 and equity > 0 else float("nan")
    return dict(final=equity, cagr=cagr, maxdd=maxdd, taken=taken,
                skipped=skipped, years=yrs, curve=curve,
                exposure=float(np.mean(exposure)) if exposure else 0.0)


def benchmark(symbols, d0, d1):
    """Equal-weighted buy-and-hold of the same universe over the same dates."""
    rets = []
    for s in symbols:
        b = load_sqlite(DB, s)
        m = (b.date >= np.datetime64(d0)) & (b.date <= np.datetime64(d1))
        i = np.flatnonzero(m)
        if len(i) < 100:
            continue
        rets.append(float(b.close[i[-1]] / b.close[i[0]]))
    if not rets:
        return float("nan")
    yrs = (np.datetime64(d1) - np.datetime64(d0)).astype(int) / 365.25
    return float(np.mean(rets)) ** (1 / yrs) - 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bases", default="out/vcp_entry_bases.json")
    ap.add_argument("--slots", type=int, default=8)
    ap.add_argument("--cost", type=float, default=0.003)
    a = ap.parse_args()
    rows = json.loads(Path(a.bases).read_text())
    trades = build_trades(rows)

    L = ["1. YEAR BY YEAR (mean R; a rule whose edge is one bull run is not a rule)", ""]
    years = sorted({t["year"] for v in trades.values() for t in v})
    L.append(f"{'rule':<24}" + "".join(f"{y[2:]:>6}" for y in years) + f"{'all':>7}{'neg yrs':>9}")
    for name, *_ in CANDIDATES:
        v = trades[name]
        by = defaultdict(list)
        for t in v:
            by[t["year"]].append(t["R"])
        cells, neg = "", 0
        for y in years:
            z = by.get(y, [])
            if len(z) < 15:
                cells += f"{'.':>6}"
            else:
                m = np.mean(z)
                cells += f"{m:>6.2f}"
                neg += m < 0
        allR = np.mean([t["R"] for t in v])
        L.append(f"{name:<24}{cells}{allR:>7.2f}{neg:>9}")

    L += ["", "2. PORTFOLIO (8 slots, 1% risk per trade, 25% position cap, "
          f"{a.cost:.1%} round-trip cost)", ""]
    L.append(f"{'rule':<24}{'signals':>9}{'taken':>7}{'skipped':>9}"
             f"{'avg expo':>10}{'CAGR':>8}{'maxDD':>8}{'final x':>9}")
    best = None
    for name, *_ in CANDIDATES:
        p = portfolio(trades[name], slots=a.slots, cost=a.cost)
        if not p:
            continue
        L.append(f"{name:<24}{len(trades[name]):>9}{p['taken']:>7}{p['skipped']:>9}"
                 f"{p['exposure']:>10.0%}{p['cagr']:>8.1%}{p['maxdd']:>8.1%}"
                 f"{p['final']:>9.2f}")
        if best is None or p["cagr"] > best[1]["cagr"]:
            best = (name, p)
    syms = sorted({t["symbol"] for t in trades[CANDIDATES[0][0]]})
    d0 = min(t["entry_date"] for t in trades[CANDIDATES[0][0]])
    d1 = max(t["exit_date"] for t in trades[CANDIDATES[0][0]])
    L.append("")
    L.append(f"equal-weighted buy-and-hold of the same {len(syms)} symbols, "
             f"{d0}..{d1}: {benchmark(syms, d0, d1):.1%} CAGR")
    if best:
        L.append(f"\nbest by CAGR: {best[0]}")
    txt = "\n".join(L)
    print(txt)
    OUT.mkdir(exist_ok=True)
    (OUT / "vcp_final_report.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
