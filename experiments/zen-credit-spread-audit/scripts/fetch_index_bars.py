"""Pull 5-minute NIFTY 50 and India VIX bars covering the trade window.

Read-only market data through the application's own DhanChartsClient -- no new
host, no new endpoint. Run from `backend/`:
    CONFIG_PATH=conf .venv/bin/python ../experiments/zen-credit-spread-audit/scripts/fetch_index_bars.py
Writes data/index/<NAME>_5m.csv (IST timestamps).
"""
from __future__ import annotations

import asyncio
import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

EXP = Path(__file__).resolve().parent.parent
BACKEND = EXP.parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from src.app_utils import load_config_properties  # noqa: E402

load_config_properties()

from src.database.session import session_scope  # noqa: E402
from src.market.services.dhan_charts_client import DhanChartsClient  # noqa: E402
from src.settings.database.db_operations.app_setting_repository import AppSettingRepository  # noqa: E402
from src.settings.services.settings_service import SettingsService  # noqa: E402

START = datetime(2025, 6, 1)
INDICES = [("NIFTY50", "13"), ("INDIAVIX", "21")]
IST = timezone(timedelta(hours=5, minutes=30))


async def main() -> int:
    async with session_scope() as session:
        await SettingsService(AppSettingRepository(session)).apply_to_config()
    client = DhanChartsClient()
    if not client.has_credentials():
        print("No Dhan credentials configured.")
        return 2
    out = EXP / "data" / "index"
    out.mkdir(parents=True, exist_ok=True)
    for name, sid in INDICES:
        bars = {}
        end = datetime.now().replace(hour=23, minute=59, second=0, microsecond=0)
        while end > START:
            start = max(end - timedelta(days=89), START)
            candles = await client.fetch_intraday(
                security_id=sid, exchange_segment="IDX_I", instrument="INDEX",
                interval_minutes=5, from_date=start, to_date=end)
            for c in candles:
                bars[c.time] = (c.open, c.high, c.low, c.close)
            end = start - timedelta(minutes=1)
            await asyncio.sleep(0.6)
        with open(out / f"{name}_5m.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["ts", "open", "high", "low", "close"])
            for t in sorted(bars):
                w.writerow([datetime.fromtimestamp(t, IST).strftime("%Y-%m-%d %H:%M"), *bars[t]])
        print(name, len(bars), "bars")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
