"""STEP 1 -- pull 5-minute bars for every F&O name, plus the Nifty 50 index.

Raw and untouched: one gzipped CSV per symbol under `data/raw/5m/`, exactly
as the vendor returned it (epoch seconds, OHLCV). Cleaning -- the jittered
2021 timestamps, Muhurat sessions, post-close bars -- is a separate step so
it can be changed without re-downloading. See FEASIBILITY.md for why each of
those exists.

Resumable: a symbol whose file exists is skipped, and a file is written only
once every window for that symbol has succeeded. An auth failure stops the
whole run (every later request would fail the same way); anything else is
retried, then recorded and skipped.

Liquidity is NOT filtered here. The whole F&O list is fetched so the universe
can be cut point-in-time from the bars themselves -- ranking on today's
turnover would pick names by what they became.

Read-only. Uses the application's own `DhanChartsClient`: no new host, no new
endpoint. Run from `backend/`:
    CONFIG_PATH=conf .venv/bin/python ../experiments/intraday-screener/scripts/download_bars.py
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import gzip
import json
import sqlite3
import sys
import time as wall
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXP = HERE.parent
BACKEND = EXP.parent.parent / "backend"
RAW = EXP / "data" / "raw" / "5m"
sys.path.insert(0, str(BACKEND))

from src.app_utils import load_config_properties  # noqa: E402

load_config_properties()

from src.database.session import session_scope  # noqa: E402
from src.market.services.dhan_charts_client import (  # noqa: E402
    ChartsRateLimited,
    DhanChartsClient,
)
from src.settings.database.db_operations.app_setting_repository import (  # noqa: E402
    AppSettingRepository,
)
from src.settings.services.settings_service import SettingsService  # noqa: E402

DB = BACKEND / "data" / "paper_trading.db"
# Measured in FEASIBILITY.md: HDFCBANK's first 5-minute bar is 2017-04-03 and
# the window before it is empty. Starting a little earlier costs one request.
HISTORY_START = datetime(2017, 3, 1)
# Dhan's security ids for the index. `IDX_I` / `INDEX` per the chart docs.
INDICES = [("NIFTY50", "13", "IDX_I", "INDEX")]


def universe() -> list[tuple[str, str, str, str]]:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT underlying_symbol, security_id FROM instruments "
        "WHERE exchange_segment='NSE_EQ' AND instrument_type='EQUITY' "
        "AND is_active=1 AND fno_eligible=1 ORDER BY underlying_symbol"
    ).fetchall()
    return INDICES + [(s, sid, "NSE_EQ", "EQUITY") for s, sid in rows]


def is_auth_failure(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(k in text for k in ("401", "403", "dh-901", "invalid token", "expired", "authenticat"))


async def fetch_symbol(client, sid, segment, instrument, delay, retries=3):
    end = datetime.now().replace(hour=23, minute=59, second=0, microsecond=0)
    bars: dict[int, tuple] = {}
    empties = 0
    while end > HISTORY_START:
        start = max(end - timedelta(days=89), HISTORY_START)
        for attempt in range(retries):
            try:
                candles = await client.fetch_intraday(
                    security_id=sid, exchange_segment=segment, instrument=instrument,
                    interval_minutes=5, from_date=start, to_date=end,
                )
                break
            except ChartsRateLimited:
                await asyncio.sleep(10 * (attempt + 1))
            except Exception as exc:  # noqa: BLE001
                if is_auth_failure(exc):
                    raise
                if attempt == retries - 1:
                    raise
                await asyncio.sleep(3 * (attempt + 1))
        for c in candles:
            bars[c.time] = (c.open, c.high, c.low, c.close, c.volume)
        # Two empty windows in a row = before the listing; stop walking back.
        empties = empties + 1 if not candles else 0
        if empties >= 2:
            break
        end = start - timedelta(minutes=1)
        await asyncio.sleep(delay)
    return bars


def write(path: Path, bars: dict) -> None:
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["time", "open", "high", "low", "close", "volume"])
        for t in sorted(bars):
            w.writerow([t, *bars[t]])
    tmp.rename(path)


async def reload_credentials() -> None:
    """Re-read the stored token. The running server renews it when under six
    hours remain, and renewal ROTATES it -- the copy this process loaded at
    start is dead a second later. One reload is the whole remedy."""
    async with session_scope() as session:
        await SettingsService(AppSettingRepository(session)).apply_to_config()


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--delay", type=float, default=0.6)
    ap.add_argument("--only", nargs="*", help="restrict to these symbols")
    args = ap.parse_args()

    await reload_credentials()
    client = DhanChartsClient()
    if not client.has_credentials():
        print("No Dhan credentials configured.")
        return 2

    RAW.mkdir(parents=True, exist_ok=True)
    names = universe()
    if args.only:
        names = [n for n in names if n[0] in set(args.only)]
    log_path = EXP / "out" / "download_log.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = json.loads(log_path.read_text()) if log_path.exists() else {}
    t0 = wall.time()
    for i, (sym, sid, seg, inst) in enumerate(names, 1):
        path = RAW / f"{sym}.csv.gz"
        if path.exists():
            continue
        try:
            try:
                bars = await fetch_symbol(client, sid, seg, inst, args.delay)
            except Exception as exc:  # noqa: BLE001
                if not is_auth_failure(exc):
                    raise
                print(f"{sym}: auth failure, reloading the stored token once", flush=True)
                await reload_credentials()
                bars = await fetch_symbol(client, sid, seg, inst, args.delay)
        except Exception as exc:  # noqa: BLE001
            if is_auth_failure(exc):
                print(f"AUTH FAILURE on {sym}: {exc}. Stopping; re-run to resume.", flush=True)
                return 3
            log[sym] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"[{i}/{len(names)}] {sym}: FAILED {exc}", flush=True)
            log_path.write_text(json.dumps(log, indent=1))
            continue
        write(path, bars)
        first = datetime.fromtimestamp(min(bars)).date().isoformat() if bars else None
        log[sym] = {"security_id": sid, "bars": len(bars), "first": first}
        log_path.write_text(json.dumps(log, indent=1))
        rate = client.request_count / max(wall.time() - t0, 1)
        print(f"[{i}/{len(names)}] {sym}: {len(bars)} bars from {first} "
              f"({client.request_count} req, {rate:.2f}/s)", flush=True)
    print(f"Done: {client.request_count} requests, {client.error_count} errors.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
