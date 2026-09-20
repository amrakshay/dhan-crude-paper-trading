"""Download public daily OHLCV for the named examples and a wider universe.

Source: Yahoo Finance's public chart endpoint. Read-only, no key, no
account. Nothing here is written back anywhere.

The NAMED cases below are chart patterns that published write-ups point at
by name and approximate date. Their labels are WEAK -- secondary sources,
dates given to the month, no agreed bar-level boundaries -- so they are used
as a sanity check ("does the detector see what a human saw, at roughly the
right time"), never as a precision statistic. The statistic comes from the
synthetic benchmark, where the answer is known exactly.
"""
from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.request
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "public"

# symbol -> (pattern, human-reported window, where the claim comes from)
NAMED_CASES = {
    "AAPL":  ("cup_and_handle", "2019-01-01", "2019-12-31",
              "cup through H1 2019, ~2wk handle, breakout over ~$215"),
    "NVDA":  ("cup_and_handle", "2019-06-01", "2020-06-30",
              "multi-month cup into early 2020, short handle, breakout to new highs"),
    "POOL":  ("cup_and_handle", "2019-11-01", "2020-08-31",
              "Feb 2020 high, late-Mar low, late-Apr recovery, late-May breakout"),
    "TATAMOTORS.NS": ("cup_and_handle", "2020-01-01", "2021-06-30",
                      "cup Mar-Dec 2020, handle Dec 2020-Mar 2021, breakout Mar 2021"),
}

# a wider universe, for the forward-return study on data the detectors were
# not tuned against
UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMD", "AMZN", "GOOGL", "META", "NFLX", "TSLA",
    "AVGO", "CRM", "ADBE", "COST", "LLY", "UNH", "JPM", "V", "MA", "HD",
    "CAT", "DE", "BA", "GE", "XOM", "CVX", "PG", "KO", "PEP", "WMT", "MCD",
    "NKE", "SBUX", "ORCL", "CSCO", "INTC", "QCOM", "TXN", "MU", "AMAT",
    "LRCX", "PANW", "NOW", "SNPS", "CDNS", "ISRG", "REGN", "VRTX", "ABBV",
    "MRK", "PFE",
]


def fetch(symbol: str, start: str = "2010-01-01", end: str = "2026-09-19") -> Path:
    import datetime as dt
    p1 = int(dt.datetime.fromisoformat(start).timestamp())
    p2 = int(dt.datetime.fromisoformat(end).timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?period1={p1}&period2={p2}&interval=1d")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as fh:
        payload = json.load(fh)
    res = payload["chart"]["result"][0]
    ts = res["timestamp"]
    q = res["indicators"]["quote"][0]
    import datetime as _dt
    DATA.mkdir(parents=True, exist_ok=True)
    out = DATA / f"{symbol.replace('.', '_')}.csv"
    n = 0
    with out.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Date", "Open", "High", "Low", "Close", "Volume"])
        for i, t in enumerate(ts):
            row = [q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i]]
            if any(v is None for v in row):
                continue
            d = _dt.datetime.utcfromtimestamp(t).date().isoformat()
            w.writerow([d] + [f"{v:.6f}" if k < 4 else int(v)
                              for k, v in enumerate(row)])
            n += 1
    print(f"  {symbol:<16} {n:>5} bars -> {out.name}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--which", choices=["named", "universe", "all"], default="all")
    a = ap.parse_args()
    syms = []
    if a.which in ("named", "all"):
        syms += list(NAMED_CASES)
    if a.which in ("universe", "all"):
        syms += [s for s in UNIVERSE if s not in syms]
    print(f"fetching {len(syms)} symbols from Yahoo Finance (daily, read-only)")
    ok = 0
    for s in syms:
        try:
            fetch(s)
            ok += 1
        except Exception as e:                       # noqa: BLE001
            print(f"  {s:<16} FAILED {type(e).__name__}: {e}")
        time.sleep(0.4)
    print(f"{ok}/{len(syms)} fetched into {DATA}")


if __name__ == "__main__":
    main()
