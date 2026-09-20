"""Can a VCP be entered BEFORE the breakout, and is it worth it?

The detector reports a VCP as `forming` once every structural condition is
met but no close has cleared the pivot. That is a real, causal, pre-breakout
signal -- the zigzag's provisional pivot makes the final contraction's low
visible the day it is made, rather than after price has rallied far enough to
confirm it.

Whether the signal is TRADEABLE is a different question, and this script is
the only thing here that answers it. Three outcomes are tracked from the
first bar a base is visible as forming:

  BREAKOUT   a close above the pivot, before the stop is touched
  STOPPED    an intraday low at or below the final contraction's low, first
  NEITHER    still in the base when the horizon runs out

The stop uses the intraday LOW, not the close, because a resting stop order
does. The breakout uses the close, because that is how the detector defines
it. Being inconsistent about that in the optimistic direction is the easiest
way to manufacture an edge that is not there.

The comparison that matters is not "does early entry make money" -- in a bull
market most things do. It is early entry against LATE entry on THE SAME
BASES: same patterns, same dates, one entered at the forming bar and one at
the breakout close. That isolates the timing decision from the pattern.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
from patlib.bars import load_sqlite
from patlib.indicators import atr
from patlib.base import overlap
from patlib.vcp import detect_vcp

DB = Path(__file__).resolve().parents[3] / "backend" / "data" / "paper_trading.db"
OUT = Path(__file__).resolve().parent.parent / "out"
HORIZONS = (5, 10, 20, 60)


# Stop policies. The bare contraction low is the textbook early stop and it
# is brutally tight; the ATR buffers ask how much of the damage is genuine
# failure and how much is just being shaken out of a correct read.
STOP_POLICIES = (("tight", 0.0), ("atr0.5", 0.5), ("atr1.0", 1.0))


def _simulate(b, entry_idx, entry, stop_px, horizon):
    """Enter at close, stop on an intraday touch, else mark out at the horizon.

    Returns (R multiple, outcome). R is in units of the risk taken, which is
    the only scale on which a tight stop and a wide one can be compared: a
    strategy that loses 2% sixty times out of a hundred is not comparable to
    one that loses 8%, however similar the hit rates look.
    """
    risk = entry - stop_px
    if risk <= 0:
        return float("nan"), "invalid"
    hi = min(len(b) - 1, entry_idx + horizon)
    for j in range(entry_idx + 1, hi + 1):
        if float(b.low[j]) <= stop_px:
            return -1.0, "stopped"
    if hi <= entry_idx:
        return float("nan"), "truncated"
    return (float(b.close[hi]) - entry) / risk, "held"


def _scan(args):
    db, sym, start, step, horizon = args
    b = load_sqlite(db, sym)
    if len(b) < start + 80:
        return sym, []
    a14 = atr(b, 14)
    accepted, rows = [], []
    for t in range(start, len(b), step):
        for d in detect_vcp(b, t):
            if d.state != "forming":
                continue
            if any(overlap(d, k) >= 0.5 for k in accepted):
                continue
            accepted.append(d)

            entry = float(b.close[t])
            pivot, stop = float(d.pivot_price), float(d.stop_price)
            if entry <= 0 or stop <= 0 or stop >= entry or pivot <= entry:
                continue      # already through the pivot, or already broken

            hi = min(len(b) - 1, t + horizon)
            bo_idx = stop_idx = None
            for j in range(t + 1, hi + 1):
                if stop_idx is None and float(b.low[j]) <= stop:
                    stop_idx = j
                if bo_idx is None and float(b.close[j]) > pivot:
                    bo_idx = j
                if bo_idx is not None or stop_idx is not None:
                    break
            if bo_idx is not None and (stop_idx is None or bo_idx <= stop_idx):
                outcome = "breakout"
            elif stop_idx is not None:
                outcome = "stopped"
            else:
                outcome = "neither"

            row = dict(
                symbol=sym, entry_date=str(b.date[t]), entry_idx=t,
                entry=entry, pivot=pivot, stop=stop,
                to_pivot=pivot / entry - 1.0, to_stop=entry / stop - 1.0,
                rr=(pivot / entry - 1.0) / max(entry / stop - 1.0, 1e-9),
                outcome=outcome,
                bars_to_breakout=(bo_idx - t) if outcome == "breakout" else None,
                breakout_date=str(b.date[bo_idx]) if outcome == "breakout" else None,
                breakout_px=float(b.close[bo_idx]) if outcome == "breakout" else None,
                variant=d.variant, score=d.score,
                t_count=d.metrics.get("t_count"),
                squeeze=d.metrics.get("atr_squeeze_in_base"),
                dry=d.metrics.get("final_volume_ratio"),
                tt=d.metrics.get("trend_template_passed"),
            )
            # --- expectancy in R, early vs late, under three stop policies
            av = float(a14[t]) if np.isfinite(a14[t]) else 0.0
            for name, mult in STOP_POLICIES:
                sp = stop - mult * av
                r, oc2 = _simulate(b, t, entry, sp, horizon)
                row[f"R_early_{name}"] = r
                row[f"oc_early_{name}"] = oc2
                row[f"risk_early_{name}"] = (entry - sp) / entry if entry else float("nan")
                if outcome == "breakout":
                    bpx = float(b.close[bo_idx])
                    r2, oc3 = _simulate(b, bo_idx, bpx, sp, horizon)
                    row[f"R_late_{name}"] = r2
                    row[f"oc_late_{name}"] = oc3
                    row[f"risk_late_{name}"] = (bpx - sp) / bpx if bpx else float("nan")

            # forward returns from the EARLY entry, and from the breakout
            for h in HORIZONS:
                j = t + h
                row[f"early{h}"] = (float(b.close[j] / entry - 1.0)
                                    if j < len(b) else float("nan"))
            if outcome == "breakout":
                row["edge_vs_late"] = row["breakout_px"] / entry - 1.0
                for h in HORIZONS:
                    j = bo_idx + h
                    row[f"late{h}"] = (float(b.close[j] / row["breakout_px"] - 1.0)
                                       if j < len(b) else float("nan"))
            rows.append(row)
    return sym, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--step", type=int, default=3)
    ap.add_argument("--start", type=int, default=260)
    ap.add_argument("--horizon", type=int, default=40,
                    help="bars allowed for the base to resolve")
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--tag", default="vcp_pre")
    a = ap.parse_args()

    from patlib.bars import list_sqlite_symbols
    syms = list_sqlite_symbols(a.db)
    if a.limit:
        syms = syms[: a.limit]
    jobs = [(a.db, s, a.start, a.step, a.horizon) for s in syms]
    rows = []
    w = a.workers or max(1, (os.cpu_count() or 4) - 2)
    print(f"{len(syms)} symbols on {w} processes, resolve horizon {a.horizon} bars")
    with ProcessPoolExecutor(max_workers=w) as ex:
        for i, (sym, r) in enumerate(ex.map(_scan, jobs, chunksize=4), 1):
            rows.extend(r)
            if i % 150 == 0:
                print(f"  {i}/{len(syms)}, {len(rows)} forming signals")

    OUT.mkdir(exist_ok=True)
    (OUT / f"{a.tag}_signals.json").write_text(json.dumps(rows, indent=1))
    txt = report(rows, a.horizon)
    print(txt)
    (OUT / f"{a.tag}_report.txt").write_text(txt + "\n")


def _pct(x):
    return f"{x:.2%}" if np.isfinite(x) else "   n/a"


def report(rows, horizon) -> str:
    L = [f"{len(rows)} distinct VCPs first seen while FORMING "
         f"(resolve horizon {horizon} bars)", ""]
    oc = defaultdict(int)
    for r in rows:
        oc[r["outcome"]] += 1
    n = max(len(rows), 1)
    L.append("what happened to the base after the forming signal")
    for k in ("breakout", "stopped", "neither"):
        L.append(f"   {k:<10}{oc[k]:>6}{oc[k] / n:>8.1%}")
    L.append("")

    bo = [r for r in rows if r["outcome"] == "breakout"]
    tp = np.array([r["to_pivot"] for r in rows], float)
    ts = np.array([r["to_stop"] for r in rows], float)
    rr = np.array([r["rr"] for r in rows], float)
    L.append("geometry at the forming signal")
    L.append(f"   distance up to the pivot   median {_pct(np.median(tp))}"
             f"   p25 {_pct(np.percentile(tp, 25))}  p75 {_pct(np.percentile(tp, 75))}")
    L.append(f"   distance down to the stop  median {_pct(np.median(ts))}"
             f"   p25 {_pct(np.percentile(ts, 25))}  p75 {_pct(np.percentile(ts, 75))}")
    L.append(f"   reward:risk to the pivot   median {np.median(rr):>6.2f}"
             f"   p25 {np.percentile(rr, 25):.2f}  p75 {np.percentile(rr, 75):.2f}")
    if bo:
        ev = np.array([r["edge_vs_late"] for r in bo], float)
        bb = np.array([r["bars_to_breakout"] for r in bo], float)
        L.append("")
        L.append(f"of the {len(bo)} that broke out:")
        L.append(f"   bars from forming signal to breakout   median {np.median(bb):.0f}"
                 f"   p90 {np.percentile(bb, 90):.0f}")
        L.append(f"   entry price advantage over the breakout close   "
                 f"median {_pct(np.median(ev))}  mean {_pct(np.nanmean(ev))}")

    L.append("")
    L.append("EARLY entry (forming bar) vs LATE entry (breakout close), "
             "on the bases that broke out")
    L.append(f"   {'horizon':<10}{'early':>10}{'late':>10}{'early win%':>12}")
    for h in HORIZONS:
        e = np.array([r[f"early{h}"] for r in bo], float)
        l = np.array([r.get(f"late{h}", np.nan) for r in bo], float)
        if not np.isfinite(e).any():
            continue
        L.append(f"   {f'{h} bars':<10}{np.nanmean(e):>10.2%}{np.nanmean(l):>10.2%}"
                 f"{np.nanmean(e > 0):>12.0%}")

    L.append("")
    L.append("ALL forming signals, held blind for N bars "
             "(no stop, no breakout requirement)")
    for h in HORIZONS:
        e = np.array([r[f"early{h}"] for r in rows], float)
        L.append(f"   {f'{h} bars':<10}mean {_pct(np.nanmean(e)):>8}   "
                 f"median {_pct(np.nanmedian(e)):>8}   win {np.nanmean(e > 0):.0%}")

    # --- expectancy
    L.append("")
    L.append(f"EXPECTANCY IN R, holding {horizon} bars or until the stop is hit")
    L.append(f"   {'stop policy':<14}{'median risk':>12}{'n':>7}"
             f"{'stopped':>9}{'mean R':>9}{'  early / late'}")
    for name, _ in STOP_POLICIES:
        for side, pool in (("early", rows), ("late", bo)):
            key, ok = f"R_{side}_{name}", f"oc_{side}_{name}"
            r = np.array([x.get(key, np.nan) for x in pool], float)
            rk = np.array([x.get(f"risk_{side}_{name}", np.nan) for x in pool], float)
            m = np.isfinite(r)
            if m.sum() < 20:
                continue
            st = np.mean([x.get(ok) == "stopped" for x in pool])
            L.append(f"   {name:<14}{np.nanmedian(rk):>12.2%}{m.sum():>7}"
                     f"{st:>9.0%}{np.nanmean(r[m]):>9.2f}   {side}")
    L.append("")
    L.append("   Note: `late` is measured only on the bases that actually broke out,")
    L.append("   which is the comparison that isolates timing -- but it also means")
    L.append("   late entry never pays for the bases that failed, and early entry")
    L.append("   does. The all-signals early column is the one that includes them.")

    # --- what raises the conversion rate?
    L.append("")
    L.append("breakout rate, conditioned")
    def bucket(label, fn, edges):
        vals = np.array([fn(r) if fn(r) is not None else np.nan for r in rows], float)
        if not np.isfinite(vals).any():
            return
        L.append(f"   {label}")
        for lo, hi in edges:
            m = (vals >= lo) & (vals < hi)
            if m.sum() < 25:
                continue
            got = sum(1 for r, k in zip(rows, m) if k and r["outcome"] == "breakout")
            stp = sum(1 for r, k in zip(rows, m) if k and r["outcome"] == "stopped")
            L.append(f"      [{lo:g}, {hi:g}){'':<4}n={m.sum():<6}"
                     f"breakout {got / m.sum():>6.1%}   stopped {stp / m.sum():>6.1%}")
    bucket("range squeeze inside the base (last contraction / first)",
           lambda r: r["squeeze"], [(0, .4), (.4, .55), (.55, .7), (.7, 1.01)])
    bucket("final-contraction volume vs base average",
           lambda r: r["dry"], [(0, .6), (.6, .75), (.75, .9), (.9, 2)])
    bucket("contraction count", lambda r: r["t_count"], [(2, 3), (3, 4), (4, 7)])
    bucket("Trend Template criteria passed (of 7)",
           lambda r: r["tt"], [(0, 6), (6, 7), (7, 8)])
    bucket("distance still to run to the pivot",
           lambda r: r["to_pivot"], [(0, .02), (.02, .05), (.05, .10), (.10, 1)])
    return "\n".join(L)


if __name__ == "__main__":
    main()
