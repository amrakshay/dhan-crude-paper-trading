"""Fetch Dhan Algos' public backtest data for Stratzy's Zen Credit Spread Overnight.

All endpoints are the unauthenticated ones algos.dhan.co calls for a logged-out
visitor (POST, body wrapped in the site's "OpenAlgo" envelope). Read-only.

Writes data/raw/*.json and data/raw/trades/YYYY-MM.json, one file per month.
"""
import datetime as dt
import json
import pathlib
import time
import urllib.request

BASE = "https://algo-api.dhan.co/algo/sub/"
STRATEGY_ID = "68596cd26aa2cba24bbb67da"
PARTNER = "STRATZY"
FIRST_MONTH = dt.date(2025, 1, 1)  # before the first P&L date; empty months are harmless
OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "raw"


def call(endpoint, **extra):
    body = {"entity_id": "OpenAlgo", "source": "W", "iv": "", "aes_key": "", "ip": "",
            "data": {"strategy_id": STRATEGY_ID, "partner_name": PARTNER, **extra}}
    req = urllib.request.Request(BASE + endpoint, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def months(start, end):
    d = start
    while d <= end:
        nxt = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        yield d, min(nxt - dt.timedelta(days=1), end)
        d = nxt


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for ep in ("GetHistoricalPnlBreakdown", "GetStrategyHistoricalData", "GetQuantStats"):
        (OUT / f"{ep}.json").write_text(json.dumps(call(ep), indent=1))
    tdir = OUT / "trades"
    tdir.mkdir(exist_ok=True)
    total = 0
    for a, b in months(FIRST_MONTH, dt.date.today()):
        r = call("GetHistoricalTrades", from_date=a.isoformat(), to_date=b.isoformat())
        n = len(r.get("data") or [])
        total += n
        (tdir / f"{a:%Y-%m}.json").write_text(json.dumps(r, indent=1))
        print(f"{a:%Y-%m}  {r.get('status')}  {n} signals")
        time.sleep(0.5)
    print("total signals", total)


if __name__ == "__main__":
    main()
