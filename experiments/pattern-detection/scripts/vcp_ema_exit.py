"""Trailing the exit with a moving average instead of holding a fixed span.

The entry studies used a flat 40-bar exit, which nobody trades and which
almost certainly under-exploits the right tail these patterns live on. The
rule tested here is the common one:

    after the breakout, exit on the first CLOSE below the 9 EMA.

Applied to the same bases as `vcp_entry_policies.py`, two ways:

  BREAKOUT ENTRY   buy the first close above the pivot, trail from there.
  EARLY ENTRY      buy at the forming bar, hold the structural stop (the
                   final contraction's low, on an intraday touch) UNTIL the
                   breakout, then hand over to the moving average. This is
                   what "9 EMA as trailing SL after breakout" means for a
                   position opened before the breakout, and it is the
                   combination the previous discussion pointed at.

Everything is reported in R against the SAME risk as before -- entry minus
the contraction low -- so the numbers sit directly alongside the flat-exit
table and the only thing that changed is the exit.

Two honesty notes. Exits fire on a close, so they are filled at that close;
a next-open fill is reported as a sensitivity because an EOD system usually
cannot trade the close it just saw. And a pure moving-average trail has no
disaster stop, so the variant with the structural stop still armed is run
alongside -- the difference is what the hard floor is worth.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
from patlib.bars import load_sqlite

DB = Path(__file__).resolve().parents[3] / "backend" / "data" / "paper_trading.db"
OUT = Path(__file__).resolve().parent.parent / "out"


def ema(x: np.ndarray, period: int) -> np.ndarray:
    """Causal EMA, seeded with the first value."""
    a = 2.0 / (period + 1.0)
    out = np.empty(len(x))
    if not len(x):
        return out
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def sma_series(x: np.ndarray, period: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    cs = np.concatenate([[0.0], np.cumsum(x)])
    for i in range(len(x)):
        a = max(0, i - period + 1)
        out[i] = (cs[i + 1] - cs[a]) / (i + 1 - a)
    return out


def simulate(b, ma, entry_idx, entry, hard_stop, breakout_idx,
             use_hard_stop: bool, max_hold: int, next_open_fill: bool):
    """Return (exit_idx, exit_px, reason). `breakout_idx` may equal entry_idx."""
    hi = min(len(b) - 1, entry_idx + max_hold)
    trail_from = breakout_idx if breakout_idx is not None else entry_idx
    for j in range(entry_idx + 1, hi + 1):
        # pre-breakout: the structural stop is the only protection
        if j <= trail_from:
            if float(b.low[j]) <= hard_stop:
                op = float(b.open[j])
                return j, (op if op < hard_stop else hard_stop), "structural_stop"
            continue
        # post-breakout
        if use_hard_stop and float(b.low[j]) <= hard_stop:
            op = float(b.open[j])
            return j, (op if op < hard_stop else hard_stop), "structural_stop"
        if np.isfinite(ma[j]) and float(b.close[j]) < ma[j]:
            if next_open_fill and j + 1 <= len(b) - 1:
                return j + 1, float(b.open[j + 1]), "ma_exit_next_open"
            return j, float(b.close[j]), "ma_exit"
    return hi, float(b.close[hi]), "max_hold"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bases", default="out/vcp_entry_bases.json")
    ap.add_argument("--max-hold", type=int, default=250)
    ap.add_argument("--tag", default="vcp_ema_exit")
    a = ap.parse_args()

    rows = json.loads(Path(a.bases).read_text())
    by_sym = defaultdict(list)
    for r in rows:
        by_sym[r["symbol"]].append(r)

    VARIANTS = [
        ("ema9",  9, "ema", True),
        ("ema9_nofloor", 9, "ema", False),
        ("ema20", 20, "ema", True),
        ("sma9",  9, "sma", True),
        ("sma20", 20, "sma", True),
    ]
    results = defaultdict(list)

    for sym, group in by_sym.items():
        b = load_sqlite(DB, sym)
        mas = {}
        for _, per, kind, _ in VARIANTS:
            key = (per, kind)
            if key not in mas:
                mas[key] = ema(b.close, per) if kind == "ema" else sma_series(b.close, per)
        for r in group:
            stop = float(r["stop"])
            bo = r.get("breakout_idx")
            for entry_name, idx_key in (("breakout", "breakout_idx"),
                                        ("early", "immediate_idx")):
                i = r.get(idx_key)
                if i is None:
                    continue
                entry = float(r.get(f"{'breakout' if entry_name == 'breakout' else 'immediate'}_entry", np.nan))
                if not np.isfinite(entry) or entry <= stop:
                    continue
                risk = entry - stop
                trail_start = i if entry_name == "breakout" else bo
                for vname, per, kind, floor in VARIANTS:
                    for nof in (False, True):
                        if nof and vname != "ema9":
                            continue
                        ei, ex, why = simulate(
                            b, mas[(per, kind)], i, entry, stop, trail_start,
                            floor, a.max_hold, nof)
                        tag = f"{entry_name}/{vname}" + ("/nextopen" if nof else "")
                        results[tag].append(dict(
                            R=(ex - entry) / risk, ret=ex / entry - 1.0,
                            bars=ei - i, why=why, risk_pct=risk / entry,
                            year=r["seen_date"][:4]))

    txt = report(results, a.max_hold)
    print(txt)
    OUT.mkdir(exist_ok=True)
    (OUT / f"{a.tag}_report.txt").write_text(txt + "\n")
    (OUT / f"{a.tag}.json").write_text(json.dumps(
        {k: v for k, v in results.items()}, indent=1))


def report(results, max_hold) -> str:
    L = [f"9 EMA trailing exit, against the flat 40-bar hold "
         f"(max hold {max_hold} bars)", ""]
    L.append(f"{'entry / exit':<30}{'n':>6}{'mean R':>8}{'med R':>7}{'win%':>7}"
             f"{'mean ret':>10}{'med bars':>10}{'R/bar':>8}{'MA exit%':>10}")
    order = ["breakout/ema9", "breakout/ema9_nofloor", "breakout/ema20",
             "breakout/sma9", "breakout/sma20", "breakout/ema9/nextopen",
             "early/ema9", "early/ema9_nofloor", "early/ema20",
             "early/sma9", "early/sma20", "early/ema9/nextopen"]
    for k in order:
        v = results.get(k)
        if not v:
            continue
        R = np.array([x["R"] for x in v], float)
        ret = np.array([x["ret"] for x in v], float)
        bars = np.array([x["bars"] for x in v], float)
        ma = np.mean([x["why"].startswith("ma_exit") for x in v])
        rpb = R.sum() / max(bars.sum(), 1)
        L.append(f"{k:<30}{len(v):>6}{R.mean():>8.2f}{np.median(R):>7.2f}"
                 f"{(R > 0).mean():>7.0%}{ret.mean():>10.2%}"
                 f"{np.median(bars):>10.0f}{rpb:>8.3f}{ma:>10.0%}")
    L.append("")
    L.append("R/bar = total R divided by total bars held: the capital-efficiency")
    L.append("        number, and the one a fast exit is supposed to win on")
    L.append("MA exit% = share of trades that ended on the moving average rather")
    L.append("        than the structural stop or the holding cap")

    for k in ("breakout/ema9", "early/ema9"):
        v = results.get(k)
        if not v:
            continue
        R = np.array([x["R"] for x in v], float)
        L.append("")
        L.append(f"{k}: tail dependence and exit reasons")
        q = np.percentile(R, [10, 25, 50, 75, 90, 95, 99])
        L.append("   percentiles  " + "  ".join(
            f"p{p}={x:.2f}" for p, x in zip((10, 25, 50, 75, 90, 95, 99), q)))
        top = np.sort(R)[-max(1, len(R) // 20):]
        L.append(f"   top 5% of trades contribute {top.sum() / R.sum():.0%} of total R")
        why = defaultdict(int)
        for x in v:
            why[x["why"]] += 1
        L.append("   exits: " + ", ".join(f"{a_} {b_/len(v):.0%}"
                                          for a_, b_ in sorted(why.items())))
        by_year = defaultdict(list)
        for x in v:
            by_year[x["year"]].append(x["R"])
        yr = "  ".join(f"{y}:{np.mean(z):.2f}" for y, z in sorted(by_year.items())
                       if len(z) >= 20)
        L.append(f"   by year: {yr}")
    return "\n".join(L)


if __name__ == "__main__":
    main()
