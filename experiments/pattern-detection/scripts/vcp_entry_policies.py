"""Four ways to enter the SAME VCP bases, measured against each other.

The previous script showed that bases first spotted within 2% of their pivot
break out 70% of the time against 34% overall. That is a fact about WHERE A
BASE HAPPENED TO BE when the detector first saw it -- not a strategy, because
you cannot choose to be shown a base late.

The tradeable version is a WAITING RULE: see the base early, do nothing, and
enter only once price has coiled to within X% of the pivot. This script
simulates that properly. Every policy below is applied to the same set of
bases, with the same stop, so the only thing varying is when you commit.

  IMMEDIATE   enter at the close of the first bar the base is visible
  WITHIN 3%   wait for a close within 3% below the pivot, then enter
  WITHIN 1%   the same, tighter
  BREAKOUT    the textbook: enter on the first close above the pivot

A base that never reaches the entry condition is simply not traded by that
policy, and the policies are therefore compared on both expectancy per
trade AND how many trades they get. A rule that turns 0.9R on four trades a
year is not better than one that turns 0.5R on forty.

Causality: the pivot and stop are frozen at the first sighting. Nothing
below looks at a bar the detector had not already seen.
"""
from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
from patlib.bars import list_sqlite_symbols, load_sqlite
from patlib.base import overlap
from patlib.indicators import atr
from patlib.vcp import detect_vcp

DB = Path(__file__).resolve().parents[3] / "backend" / "data" / "paper_trading.db"
OUT = Path(__file__).resolve().parent.parent / "out"

POLICIES = ("immediate", "within3", "within1", "breakout")
STOPS = (("tight", 0.0), ("atr1", 1.0))


def _entry_bar(b, t0, pivot, policy, patience):
    """First bar this policy would buy on, or None if it never triggers."""
    hi = min(len(b) - 1, t0 + patience)
    if policy == "immediate":
        return t0
    for j in range(t0, hi + 1):
        c = float(b.close[j])
        if policy == "breakout":
            if c > pivot:
                return j
            continue
        if c > pivot:            # broke out before coiling: the waiting
            return None          # policies missed it, and must say so
        gap = pivot / c - 1.0
        if policy == "within3" and gap <= 0.03:
            return j
        if policy == "within1" and gap <= 0.01:
            return j
    return None


def _simulate(b, i, entry, stop_px, horizon):
    risk = entry - stop_px
    if risk <= 0:
        return float("nan"), "invalid", float("nan")
    hi = min(len(b) - 1, i + horizon)
    for j in range(i + 1, hi + 1):
        if float(b.low[j]) <= stop_px:
            return -1.0, "stopped", -risk / entry
    if hi <= i:
        return float("nan"), "truncated", float("nan")
    ex = float(b.close[hi])
    return (ex - entry) / risk, "held", ex / entry - 1.0


def _scan(args):
    db, sym, start, step, horizon, patience = args
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
            pivot, stop = float(d.pivot_price), float(d.stop_price)
            if stop <= 0 or pivot <= float(b.close[t]) or stop >= float(b.close[t]):
                continue
            row = dict(symbol=sym, seen_date=str(b.date[t]), seen_idx=t,
                       pivot=pivot, stop=stop, variant=d.variant,
                       score=d.score, tt=d.metrics.get("trend_template_passed"),
                       first_gap=pivot / float(b.close[t]) - 1.0)
            av = float(a14[t]) if np.isfinite(a14[t]) else 0.0
            for pol in POLICIES:
                i = _entry_bar(b, t, pivot, pol, patience)
                row[f"{pol}_idx"] = i
                if i is None:
                    continue
                e = float(b.close[i])
                row[f"{pol}_entry"] = e
                row[f"{pol}_wait"] = i - t
                for sname, mult in STOPS:
                    sp = stop - mult * av
                    r, oc, ret = _simulate(b, i, e, sp, horizon)
                    row[f"{pol}_{sname}_R"] = r
                    row[f"{pol}_{sname}_oc"] = oc
                    row[f"{pol}_{sname}_ret"] = ret
                    row[f"{pol}_{sname}_risk"] = (e - sp) / e
            rows.append(row)
    return sym, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--step", type=int, default=3)
    ap.add_argument("--start", type=int, default=260)
    ap.add_argument("--horizon", type=int, default=40)
    ap.add_argument("--patience", type=int, default=25,
                    help="bars a waiting policy will wait for its entry")
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--tag", default="vcp_entry")
    a = ap.parse_args()

    syms = list_sqlite_symbols(a.db)
    if a.limit:
        syms = syms[: a.limit]
    jobs = [(a.db, s, a.start, a.step, a.horizon, a.patience) for s in syms]
    rows = []
    w = a.workers or max(1, (os.cpu_count() or 4) - 2)
    print(f"{len(syms)} symbols on {w} processes; horizon {a.horizon}, "
          f"patience {a.patience}")
    with ProcessPoolExecutor(max_workers=w) as ex:
        for i, (sym, r) in enumerate(ex.map(_scan, jobs, chunksize=4), 1):
            rows.extend(r)
            if i % 150 == 0:
                print(f"  {i}/{len(syms)}, {len(rows)} bases")
    OUT.mkdir(exist_ok=True)
    (OUT / f"{a.tag}_bases.json").write_text(json.dumps(rows, indent=1))
    txt = report(rows, a)
    print(txt)
    (OUT / f"{a.tag}_report.txt").write_text(txt + "\n")


def report(rows, a) -> str:
    n = len(rows)
    L = [f"{n} distinct VCP bases, each offered to four entry policies",
         f"(hold {a.horizon} bars or stop out; waiting policies give up after "
         f"{a.patience} bars)", ""]
    for sname, _ in STOPS:
        L.append(f"stop = final contraction low"
                 + (" minus 1 ATR" if sname == "atr1" else " exactly"))
        L.append(f"   {'policy':<12}{'traded':>8}{'% of bases':>12}{'med wait':>10}"
                 f"{'med risk':>10}{'stopped':>9}{'mean R':>9}{'mean ret':>10}"
                 f"{'R/base':>9}")
        for pol in POLICIES:
            R = np.array([x.get(f"{pol}_{sname}_R", np.nan) for x in rows], float)
            ret = np.array([x.get(f"{pol}_{sname}_ret", np.nan) for x in rows], float)
            rk = np.array([x.get(f"{pol}_{sname}_risk", np.nan) for x in rows], float)
            wait = np.array([x.get(f"{pol}_wait", np.nan) for x in rows], float)
            m = np.isfinite(R)
            if m.sum() < 10:
                continue
            st = np.mean([x.get(f"{pol}_{sname}_oc") == "stopped" for x in rows
                          if np.isfinite(x.get(f"{pol}_{sname}_R", np.nan))])
            # R per BASE, not per trade: a policy that skips most bases has to
            # earn its selectivity back
            per_base = np.nansum(R[m]) / n
            L.append(f"   {pol:<12}{m.sum():>8}{m.sum() / n:>12.0%}"
                     f"{np.nanmedian(wait):>10.0f}{np.nanmedian(rk):>10.2%}"
                     f"{st:>9.0%}{np.nanmean(R[m]):>9.2f}"
                     f"{np.nanmean(ret[m]):>10.2%}{per_base:>9.2f}")
        L.append("")
    L.append("mean R   = expectancy per trade taken, in units of the risk taken")
    L.append("mean ret = the same in percent of position, which is what a fixed-")
    L.append("           capital trader feels; the two rank differently and both")
    L.append("           are reported for that reason")
    L.append("R/base   = total R earned divided by ALL bases seen, so a policy")
    L.append("           that trades rarely is charged for the ones it skipped")
    return "\n".join(L)


if __name__ == "__main__":
    main()
