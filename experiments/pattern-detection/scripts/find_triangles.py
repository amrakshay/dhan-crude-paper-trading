"""Find real triangles in the NSE data, so the TradingView indicator can be
verified against charts that are known to contain one.

Applies the same drift gate the Pine version now uses, so the shortlist is
what the indicator should actually agree with.
"""
from __future__ import annotations
import argparse, os
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import _bootstrap  # noqa: F401
from patlib.bars import list_sqlite_symbols, load_sqlite
from patlib.triangles import TriangleParams, detect_triangles

DB = str(Path(__file__).resolve().parents[3] / "backend" / "data" / "paper_trading.db")


def scan(sym):
    try:
        b = load_sqlite(DB, sym)
    except Exception:
        return []
    if len(b) < 400:
        return []
    out, seen = [], set()
    for t in range(max(300, len(b) - 620), len(b), 4):
        for d in detect_triangles(b, t, TriangleParams("D")):
            key = (d.variant, d.start_idx // 15)
            if key in seen:
                continue
            seen.add(key)
            m = d.metrics
            mS = (m["upper_start"] + m["lower_start"]) / 2
            mE = (m["upper_now"] + m["lower_now"]) / 2
            drift = abs(mE - mS) / max(m["width_start"], 1e-9)
            out.append(dict(sym=sym, var=d.variant, start=d.start_date,
                            end=str(b.date[t]), score=d.score, drift=drift,
                            bars=m["length"], conv=m["convergence"],
                            touch=m["touches_upper"] + m["touches_lower"]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-drift", type=float, default=1.5)
    ap.add_argument("--top", type=int, default=4)
    a = ap.parse_args()
    syms = list_sqlite_symbols(DB)
    rows = []
    with ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 4) - 2)) as ex:
        for r in ex.map(scan, syms, chunksize=8):
            rows.extend(r)
    ok = [r for r in rows if r["drift"] <= a.max_drift]
    print(f"{len(rows)} triangles in the last ~2.5 years across {len(syms)} symbols; "
          f"{len(ok)} survive the {a.max_drift}x drift gate\n")
    by = defaultdict(list)
    for r in ok:
        by[r["var"]].append(r)
    for v in sorted(by, key=lambda k: -len(by[k])):
        g = sorted(by[v], key=lambda r: (-r["score"], -r["touch"]))
        print(f"--- {v}  ({len(by[v])} found)")
        for r in g[: a.top]:
            print(f"      {r['sym']:<12} {r['start']} -> {r['end']}  {r['bars']:>3}d  "
                  f"score {r['score']:.2f}  touches {r['touch']}  "
                  f"conv {r['conv']:.2f}  drift {r['drift']:.2f}x")
        print()


if __name__ == "__main__":
    main()
