"""Scan the paper-trading app's own daily_bars for the three patterns,
causally, and measure what happened next.

WHAT THIS CAN AND CANNOT MEASURE. There are no labels here: nobody has
marked which of 499 NSE symbols were in a cup in March 2021, so precision
and recall are not available and are not reported. What IS available is the
question a trader actually asks -- when the detector said `breakout`, what
did the next N sessions do? -- and that is answerable, PROVIDED it is
compared against a baseline rather than quoted on its own. A 6% average
20-day return means nothing in a market that returned 6% to everybody over
the same stretch.

The baseline used is DATE-MATCHED: for every signal on date D, the same
forward return is computed for every other symbol that had data on D, and
averaged. So the comparison is against "buying a random NSE name that day",
which removes the market's own drift and the bull-run bias that makes every
long signal look clever in a sample that starts in 2015.
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
from patlib.bars import Bars, list_sqlite_symbols, load_sqlite, to_weekly
from patlib.detect import detect_all

DB = Path(__file__).resolve().parents[3] / "backend" / "data" / "paper_trading.db"
OUT = Path(__file__).resolve().parent.parent / "out"
HORIZONS = (5, 10, 20, 60)


def forward_returns(bars: Bars, i: int) -> dict[int, float]:
    out = {}
    for h in HORIZONS:
        j = i + h
        out[h] = (float(bars.close[j] / bars.close[i] - 1.0)
                  if j < len(bars) and bars.close[i] > 0 else float("nan"))
    return out


def _scan_symbol(args):
    """One symbol, in its own process. Returns (symbol, signals, n_scans).

    Each worker opens the database itself, read-only. 474 symbols at 12ms a
    scan is fifty minutes on one core and about five on twelve, which is the
    difference between running this once and running it for each profile.
    """
    db, sym, start, step, profile, timeframe = args
    b = load_sqlite(db, sym)
    if len(b) < start + 80:
        return sym, None, [], 0
    if timeframe == "W":
        b = to_weekly(b)
        if len(b) < 120:
            return sym, None, [], 0
    sigs, seen, n = [], set(), 0
    for t in range(min(start, len(b) - 1), len(b), step):
        n += 1
        for d in detect_all(b, t, profile=profile):
            if d.state != "breakout":
                continue
            bi = d.metrics.get("breakout_idx")
            if bi is None or (d.pattern, bi) in seen:
                continue
            seen.add((d.pattern, bi))
            r = forward_returns(b, bi)
            sigs.append(dict(
                symbol=sym, pattern=d.pattern, variant=d.variant,
                timeframe=d.timeframe, score=d.score,
                signal_date=str(b.date[bi]), signal_idx=bi,
                detected_date=str(b.date[t]), lag_bars=t - bi,
                pivot=d.pivot_price, stop=d.stop_price, start_date=d.start_date,
                direction=d.metrics.get("breakout_direction", "up"),
                **{f"fwd{h}": r[h] for h in HORIZONS}))
    return sym, b, sigs, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--limit", type=int, default=0, help="symbols, 0 = all")
    ap.add_argument("--step", type=int, default=5, help="scan every Nth bar")
    ap.add_argument("--start", type=int, default=260)
    ap.add_argument("--profile", default="strict", choices=["strict", "relaxed"])
    ap.add_argument("--timeframe", default="D", choices=["D", "W"])
    ap.add_argument("--tag", default=None)
    ap.add_argument("--workers", type=int, default=0)
    a = ap.parse_args()
    tag = a.tag or f"real_{a.profile}_{a.timeframe}"

    symbols = list_sqlite_symbols(a.db)
    if a.limit:
        symbols = symbols[: a.limit]
    print(f"{len(symbols)} symbols, profile={a.profile}, timeframe={a.timeframe}, "
          f"scanning every {a.step} bars")

    jobs = [(a.db, sym, a.start, a.step, a.profile, a.timeframe) for sym in symbols]
    series: dict[str, Bars] = {}
    signals: list[dict] = []
    n_scans = 0
    workers = a.workers or max(1, (os.cpu_count() or 4) - 2)
    print(f"scanning on {workers} processes ...")
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, (sym, b, sigs, n) in enumerate(ex.map(_scan_symbol, jobs, chunksize=4), 1):
            if b is not None:
                series[sym] = b
            signals.extend(sigs)
            n_scans += n
            if i % 100 == 0:
                print(f"  {i}/{len(symbols)} symbols, {len(signals)} signals")

    # --- date-matched baseline
    print("building date-matched baseline ...")
    by_date: dict[str, list[float]] = defaultdict(list)
    dset = {np.datetime64(d) for d in {x["signal_date"] for x in signals}}
    for b in series.values():
        for i, dt in enumerate(b.date):
            if dt not in dset:
                continue
            r = forward_returns(b, i)
            for h in HORIZONS:
                if np.isfinite(r[h]):
                    by_date[f"{h}|{dt}"].append(r[h])
    baseline = {k: float(np.mean(v)) for k, v in by_date.items() if v}

    for s in signals:
        # A descending triangle or a rising wedge that breaks DOWN is a short.
        # Scoring it as a long -- which the first version of this script did --
        # makes the bearish variants look like losing longs when what they
        # actually predicted was a fall. `dir_exc` is the excess return TO THE
        # TRADE THE PATTERN IMPLIES; `exc` stays long-only for comparison.
        sign = -1.0 if s.get("direction") == "down" else 1.0
        for h in HORIZONS:
            b0 = baseline.get(f"{h}|{s['signal_date']}", float("nan"))
            s[f"base{h}"] = b0
            s[f"exc{h}"] = s[f"fwd{h}"] - b0
            s[f"dexc{h}"] = sign * s[f"exc{h}"]

    OUT.mkdir(exist_ok=True)
    (OUT / f"{tag}_signals.json").write_text(json.dumps(signals, indent=1))
    print(summarise(signals, n_scans, len(series)))
    (OUT / f"{tag}_summary.txt").write_text(summarise(signals, n_scans, len(series)) + "\n")


def summarise(signals, n_scans, n_symbols) -> str:
    L = [f"{len(signals)} distinct breakout signals from {n_scans:,} bar-scans "
         f"across {n_symbols} symbols",
         f"  = {1000 * len(signals) / max(n_scans, 1):.1f} signals per 1,000 bar-scans",
         ""]
    L.append(f"{'pattern':<26}{'n':>6}" +
             "".join(f"{f'fwd{h}':>9}{f'exc{h}':>9}{'win%':>7}" for h in (10, 20)))
    groups = defaultdict(list)
    for s in signals:
        groups[s["pattern"]].append(s)
        groups[f"  {s['pattern']}/{_fam(s['variant'])}"].append(s)
    for k in sorted(groups):
        g = groups[k]
        cells = ""
        for h in (10, 20):
            f = np.array([x[f"fwd{h}"] for x in g], float)
            e = np.array([x[f"dexc{h}"] for x in g], float)
            win = np.nanmean(e > 0) if np.isfinite(e).any() else float("nan")
            cells += f"{np.nanmean(f):>9.2%}{np.nanmean(e):>9.2%}{win:>7.0%}"
        L.append(f"{k:<26}{len(g):>6}{cells}")
    L.append("")
    L.append("fwd = raw forward return (always long); exc = excess over the "
             "date-matched average of every symbol, SIGNED to the direction the "
             "pattern implies; win% = share of those beating the average")
    return "\n".join(L)


def _fam(variant: str) -> str:
    if variant and variant[0].isdigit():
        return variant.split()[-1]          # VCP footprint -> "3T"
    return variant.split(":")[0]


if __name__ == "__main__":
    main()
