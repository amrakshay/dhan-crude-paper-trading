"""STEP 0 -- what the repository's data can and cannot support.

Five arbitrage families were on the table. This script establishes, from the
data rather than from assumption, which of them can be measured here. It
writes `out/feasibility.json` and prints the table that opens `FEASIBILITY.md`.

The decisive question is whether a futures history can be obtained. Dhan's
chart endpoint takes a `security_id`; a security_id for an EXPIRED contract
can only come from the instrument master. So the test is: how many distinct
expiries does the master carry?

`--refresh-master` downloads the public detailed scrip master
(images.dhan.co, already in the application's allowlist -- inbound market
data, no credentials). Without it a cached copy is used.
"""
from __future__ import annotations

import argparse
import csv
import collections
import json
import sqlite3
import urllib.request
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
from arblib import data

OUT = Path(__file__).resolve().parent.parent / "out"
CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"
MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
MASTER = CACHE / "api-scrip-master-detailed.csv"


def fetch_master(refresh: bool) -> Path | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    if MASTER.exists() and not refresh:
        return MASTER
    try:
        urllib.request.urlretrieve(MASTER_URL, MASTER)     # noqa: S310
    except Exception as exc:                               # pragma: no cover
        print(f"  ! could not fetch the instrument master: {exc}")
        return MASTER if MASTER.exists() else None
    return MASTER


def survey_master(path: Path) -> dict:
    """Distinct expiries per (exchange, instrument). The whole Step 0 answer."""
    exp = collections.defaultdict(set)
    count = collections.Counter()
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh):
            key = f"{row.get('EXCH_ID')}:{row.get('INSTRUMENT')}"
            count[key] += 1
            e = (row.get("SM_EXPIRY_DATE") or "")[:10]
            # 0001-01-01 is the master's null expiry, used for cash and indices.
            # The NSETEST symbols carry a synthetic 2036 expiry and are not
            # tradeable instruments.
            if e and e != "0001-01-01" and not str(row.get("UNDERLYING_SYMBOL", "")).endswith("NSETEST"):
                exp[key].add(e)
    return {
        k: {"rows": count[k], "expiries": sorted(exp[k])}
        for k in sorted(count)
        if k.split(":")[1] in {"FUTSTK", "FUTIDX", "FUTCOM", "OPTIDX", "OPTSTK", "EQUITY", "INDEX"}
    }


def survey_db() -> dict:
    conn = sqlite3.connect(f"file:{data.DB.resolve()}?mode=ro", uri=True)
    q = lambda s, *a: conn.execute(s, a).fetchall()       # noqa: E731
    try:
        segs = q("SELECT exchange_segment, COUNT(DISTINCT symbol), COUNT(*), "
                 "MIN(bar_date), MAX(bar_date) FROM daily_bars GROUP BY 1")
        instr = q("SELECT exchange_segment, instrument_type, COUNT(*) FROM instruments GROUP BY 1,2")
        depth = q("SELECT symbol, COUNT(*) c FROM daily_bars WHERE exchange_segment='NSE_EQ' "
                  "GROUP BY 1 ORDER BY c")
        # CAST to REAL matters: SQLite gives INTEGER affinity to whole-number
        # NUMERIC values, so `close/prev` silently integer-divides and every
        # unremarkable day reads as a 100% crash. This cost a wrong answer once.
        breaks = q("""
            WITH s AS (SELECT symbol, bar_date, CAST(close AS REAL) c,
                              LAG(CAST(close AS REAL)) OVER (PARTITION BY symbol ORDER BY bar_date) p
                       FROM daily_bars WHERE exchange_segment='NSE_EQ')
            SELECT symbol, bar_date, p, c, c/p FROM s
            WHERE p IS NOT NULL AND p > 0 AND (c/p < 0.6 OR c/p > 1.75)
            ORDER BY symbol, bar_date""")
    finally:
        conn.close()
    ns = sorted(r[1] for r in depth)
    return {
        "segments": [dict(zip(("segment", "symbols", "bars", "first", "last"), r)) for r in segs],
        "instruments": [dict(zip(("segment", "type", "rows"), r)) for r in instr],
        "bars_per_symbol": {
            "min": ns[0], "p10": ns[len(ns) // 10], "median": ns[len(ns) // 2], "max": ns[-1],
            "full_history": sum(1 for n in ns if n >= 2700),
        },
        "unadjusted_breaks": [
            dict(zip(("symbol", "date", "prev_close", "close", "ratio"), r)) for r in breaks
        ],
        "fno_eligible": len(data.fno_eligible()),
    }


def check_split_adjustment() -> list[dict]:
    """Three known capitalisation changes. If the series is continuous across
    the ex-date the vendor has back-adjusted it."""
    cases = [
        ("INFY", "2018-09-11", "1:1 bonus"),
        ("IRCTC", "2021-10-29", "1:5 split"),
        ("RELIANCE", "2024-10-28", "1:1 bonus"),
    ]
    out = []
    for sym, ex, what in cases:
        s = data.load(sym)
        i = int(np.searchsorted(s.date, np.datetime64(ex)))
        if i <= 0 or i >= len(s):
            continue
        out.append({"symbol": sym, "ex_date": ex, "event": what,
                    "prev_close": s.close[i - 1], "close": s.close[i],
                    "ratio": round(float(s.close[i] / s.close[i - 1]), 4)})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-master", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    db = survey_db()
    adj = check_split_adjustment()
    path = fetch_master(args.refresh_master)
    master = survey_master(path) if path else {}

    print("\n=== daily_bars ===")
    for s in db["segments"]:
        print(f"  {s['segment']:<9} {s['symbols']:>4} symbols  {s['bars']:>9,} bars  "
              f"{s['first']} .. {s['last']}")
    b = db["bars_per_symbol"]
    print(f"  bars/symbol: min {b['min']} p10 {b['p10']} median {b['median']} max {b['max']}; "
          f"{b['full_history']} have the full panel")
    print(f"  F&O-eligible underlyings (shortable overnight): {db['fno_eligible']}")

    print("\n=== is the price series adjusted? ===")
    for c in adj:
        verdict = "adjusted" if 0.85 < c["ratio"] < 1.18 else "NOT adjusted"
        print(f"  {c['symbol']:<10} {c['event']:<10} ex {c['ex_date']}  "
              f"{c['prev_close']:>9.2f} -> {c['close']:>9.2f}  x{c['ratio']:.3f}  {verdict}")
    print(f"  unadjusted breaks over the whole panel: {len(db['unadjusted_breaks'])}")
    for r in db["unadjusted_breaks"]:
        print(f"     {r['symbol']:<11} {r['date']}  x{r['ratio']:.3f}")

    print("\n=== instrument master: how many expiries does it carry? ===")
    for k, v in master.items():
        n = len(v["expiries"])
        span = f"{v['expiries'][0]} .. {v['expiries'][-1]}" if n else "-"
        print(f"  {k:<14} {v['rows']:>7,} rows   {n:>3} expiries   {span}")

    futstk = master.get("NSE:FUTSTK", {}).get("expiries", [])
    futidx = master.get("NSE:FUTIDX", {}).get("expiries", [])
    verdict = {
        "pairs_statistical": "FEASIBLE",
        "index_arbitrage": "NOT FEASIBLE" if len(futidx) <= 4 else "check",
        "cash_futures_basis": "NOT FEASIBLE" if len(futstk) <= 4 else "check",
        "calendar_spreads": "NOT FEASIBLE" if len(futstk) <= 4 else "check",
        "etf_nav": "NOT FEASIBLE",
    }
    print("\n=== verdict ===")
    for k, v in verdict.items():
        print(f"  {k:<22} {v}")

    (OUT / "feasibility.json").write_text(json.dumps(
        {"db": db, "split_adjustment": adj, "master": master, "verdict": verdict},
        indent=2, default=str))
    print(f"\nwrote {OUT / 'feasibility.json'}")


if __name__ == "__main__":
    main()
