"""STEP 2 -- daily bars for every Mainboard symbol the channel listed.

One CSV per instrument under `data/bars/<EXCH>_<SYMBOL>.csv` (date, OHLCV),
exactly as Dhan returned it. History starts well before the first sheet so the
50 EMA, ATR14 and the 52-week high are warm on the first list date.

Read-only. Uses the application's own `DhanChartsClient` and the token stored
from the Settings page: no new host, no new endpoint. Resumable -- an existing
file is skipped unless --force. Run from `backend/`:
    CONFIG_PATH=conf .venv/bin/python ../experiments/vcp-channel-audit/scripts/download_bars.py
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from datetime import datetime
from pathlib import Path

EXP = Path(__file__).resolve().parent.parent
BACKEND = EXP.parent.parent / "backend"
BARS = EXP / "data" / "bars"
MASTER = BACKEND / "data" / "instrument_master" / "api-scrip-master-detailed.csv"
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

HISTORY_START = datetime(2025, 3, 1)
SEGMENT = {"NSE": "NSE_EQ", "BSE": "BSE_EQ"}
# The client spaces requests per SYMBOL, not across symbols, and Dhan's limit
# is per account -- so pace the loop here and back off when it still trips.
DELAY = 0.5
RETRIES = 5


async def fetch(client, sid, ex, end):
    for attempt in range(RETRIES):
        try:
            return await client.fetch_daily(sid, SEGMENT[ex], "EQUITY", HISTORY_START, end)
        except ChartsRateLimited:
            await asyncio.sleep(2 * (attempt + 1))
    raise ChartsRateLimited(f"still rate limited after {RETRIES} attempts")


def bse_twin(isin: str) -> str | None:
    """The BSE security id for the same ISIN. NSE trade-to-trade (BE) ids are
    refused by the charts endpoint with DH-905; the BSE listing is the same
    company and the same shares, so its bars stand in and the swap is logged."""
    with open(MASTER, newline="") as fh:
        for r in csv.DictReader(fh):
            if r["SEGMENT"] == "E" and r["EXCH_ID"] == "BSE" and r["ISIN"] == isin:
                return r["SECURITY_ID"]
    return None


def instruments() -> list[tuple[str, str, str, str]]:
    seen = {}
    with open(EXP / "data" / "setups_mainboard.csv", newline="") as fh:
        for r in csv.DictReader(fh):
            seen[(r["exchange"], r["dhan_symbol"])] = (r["security_id"], r["isin"])
    return [(ex, sym, sid, isin) for (ex, sym), (sid, isin) in sorted(seen.items())]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    async with session_scope() as session:
        await SettingsService(AppSettingRepository(session)).apply_to_config()
    client = DhanChartsClient()
    if not client.has_credentials():
        sys.exit("no Dhan credentials configured")
    BARS.mkdir(parents=True, exist_ok=True)
    end = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    failed, swapped = [], []
    todo = instruments()
    for i, (ex, sym, sid, isin) in enumerate(todo, 1):
        out = BARS / f"{ex}_{sym}.csv"
        if out.exists() and not args.force:
            continue
        try:
            await asyncio.sleep(DELAY)
            try:
                candles = await fetch(client, sid, ex, end)
            except Exception as exc:
                twin = bse_twin(isin) if ex == "NSE" and "DH-905" in str(exc) else None
                if twin is None:
                    raise
                candles = await fetch(client, twin, "BSE", end)
                swapped.append((sym, sid, twin, isin))
                print(f"[{i}/{len(todo)}] {ex}:{sym} NSE id refused; using BSE {twin}")
        except Exception as exc:  # recorded and skipped; analysis reports the gap
            failed.append((ex, sym, sid, str(exc)[:120]))
            print(f"[{i}/{len(todo)}] {ex}:{sym} FAILED {str(exc)[:80]}")
            continue
        with open(out, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "open", "high", "low", "close", "volume"])
            for c in candles:
                w.writerow([datetime.fromtimestamp(c.time).date().isoformat(),
                            c.open, c.high, c.low, c.close, c.volume])
        print(f"[{i}/{len(todo)}] {ex}:{sym} {len(candles)} bars")
    with open(EXP / "data" / "download_failures.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["exchange", "symbol", "security_id", "error"])
        w.writerows(failed)
    if swapped:
        with open(EXP / "data" / "bse_substitutes.csv", "a", newline="") as fh:
            csv.writer(fh).writerows(swapped)
    print(f"done; {len(failed)} failed, {len(swapped)} served from BSE")


if __name__ == "__main__":
    asyncio.run(main())
