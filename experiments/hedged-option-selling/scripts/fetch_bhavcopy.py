"""Fetch NSE F&O bhavcopy, index closes and NSE's daily volatility into the cache.

Index underlyings only.  Single-stock options are deliberately out of scope --
they are PHYSICALLY settled, so a short leg left in the money at expiry becomes
a delivery obligation rather than a cash difference, and this study does not
model delivery.  See RESEARCH.md.

  python scripts/fetch_bhavcopy.py --from 2016-01-01 --to 2026-09-17
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hedgelib import bhavcopy as b  # noqa: E402

KEEP = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}
KEEP_INDEX = {"Nifty 50", "Nifty Bank", "India VIX", "Nifty Financial Services",
              "Nifty Midcap Select", "Nifty Next 50"}


def logged(conn, d: date, feed: str) -> str | None:
    r = conn.execute(
        "SELECT status FROM fetch_log WHERE trade_date=? AND feed=?",
        (d.isoformat(), feed)).fetchone()
    return r[0] if r else None


def mark(conn, d: date, feed: str, status: str, rows: int = 0, note: str = "") -> None:
    conn.execute(
        "INSERT OR REPLACE INTO fetch_log(trade_date,feed,status,rows,note) VALUES (?,?,?,?,?)",
        (d.isoformat(), feed, status, rows, note))


def do_bhav(conn, d: date) -> str:
    rows, src = b.fetch_day(d)
    # Legacy rows carry no board lot quantity; derive it from the exchange's own
    # turnover BEFORE filtering, so every contract of an expiry contributes to
    # the median rather than only the handful this study keeps.
    if src == "legacy":
        b.derive_lot_sizes(rows)
    keep = [r for r in rows if r.symbol in KEEP]
    # Columns named explicitly: `turnover` was added by ALTER TABLE and so
    # sits AFTER `source` in the physical order, which a positional VALUES
    # list would silently transpose.
    conn.executemany(
        "INSERT OR REPLACE INTO fo_bhav "
        "(trade_date,symbol,kind,expiry,strike,opt_type,open,high,low,close,"
        " settle,underlying,oi,volume,num_trades,lot_size,turnover,source) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(r.trade_date.isoformat(), r.symbol, r.kind, r.expiry.isoformat(), r.strike,
          r.opt_type, r.open, r.high, r.low, r.close, r.settle, r.underlying,
          r.oi, r.volume, r.num_trades, r.lot_size, r.turnover, r.source)
         for r in keep])
    mark(conn, d, "bhav", "ok", len(keep), src)
    nolot = sum(1 for r in keep if not r.lot_size)
    return f"{len(keep):6d} rows ({src}, {nolot} without a lot size)"


def do_index(conn, d: date) -> str:
    idx = b.fetch_index_closes(d)
    keep = {k: v for k, v in idx.items() if k in KEEP_INDEX}
    conn.executemany(
        "INSERT OR REPLACE INTO index_close VALUES (?,?,?,?,?,?)",
        [(d.isoformat(), k, v["open"], v["high"], v["low"], v["close"])
         for k, v in keep.items()])
    mark(conn, d, "index", "ok", len(keep))
    return f"{len(keep)} indices"


def do_volt(conn, d: date) -> str:
    vol = b.fetch_fovolt(d)
    keep = {k: v for k, v in vol.items() if k in KEEP}
    conn.executemany(
        "INSERT OR REPLACE INTO fo_volt VALUES (?,?,?)",
        [(d.isoformat(), k, v) for k, v in keep.items()])
    mark(conn, d, "volt", "ok", len(keep))
    return f"{len(keep)} vols"


FEEDS = {"bhav": do_bhav, "index": do_index, "volt": do_volt}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", required=True)
    ap.add_argument("--to", dest="end", required=True)
    ap.add_argument("--db", default="data/cache/options.db")
    ap.add_argument("--feeds", default="bhav,index,volt")
    ap.add_argument("--retry-failed", action="store_true")
    a = ap.parse_args()

    conn = b.open_cache(a.db)
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    feeds = [f.strip() for f in a.feeds.split(",")]

    done = skipped = holiday = blocked = failed = 0
    for i, d in enumerate(b.sessions(start, end)):
        for feed in feeds:
            prev = logged(conn, d, feed)
            if prev == "ok" or (prev in ("holiday", "blocked") and not a.retry_failed):
                skipped += 1
                continue
            try:
                note = FEEDS[feed](conn, d)
                done += 1
                if done % 100 == 0:
                    print(f"  {d} {feed:5s} {note}", flush=True)
            except b.NotPublished:
                mark(conn, d, feed, "holiday"); holiday += 1
            except b.Blocked:
                mark(conn, d, feed, "blocked"); blocked += 1
            except Exception as e:  # noqa: BLE001
                mark(conn, d, feed, "failed", note=str(e)[:200]); failed += 1
                print(f"  !! {d} {feed}: {e}", flush=True)
        if i % 200 == 0:
            conn.commit()
    conn.commit()
    print(f"\nfetched={done} skipped={skipped} holiday={holiday} "
          f"blocked={blocked} failed={failed}")
    for t in ("fo_bhav", "index_close", "fo_volt"):
        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:12s} {n:>10,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
