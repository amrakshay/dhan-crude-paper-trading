"""STEP 0 -- how much 5-minute history can Dhan actually give us?

Everything else in this experiment is sized by the answer. The intraday
endpoint serves at most 90 days per request, and how FAR BACK it goes is
undocumented and unverified (`backend/scripts/replay_btst_scan.py` notes that
old sessions come back empty). So: walk backwards in 90-day windows for a few
names until the vendor returns nothing, and inspect what came back.

Also answers the liquidity question from data we already hold: rank the F&O
names by median daily turnover over the last 60 sessions of `daily_bars`.

Read-only. Uses the application's own `DhanChartsClient` -- no new host, no
new endpoint -- and writes only under `experiments/intraday-screener/out/`.

Run from `backend/`:
    CONFIG_PATH=conf .venv/bin/python ../experiments/intraday-screener/scripts/feasibility.py
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import sqlite3
import statistics
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXP = HERE.parent
BACKEND = EXP.parent.parent / "backend"
OUT = EXP / "out"
sys.path.insert(0, str(BACKEND))

# Same reason as replay_btst_scan.py: only this loader reads `.env`, which the
# stored (encrypted) Dhan token needs to be decrypted.
from src.app_utils import load_config_properties  # noqa: E402

load_config_properties()

from src.core.time_utils import IST  # noqa: E402
from src.database.session import session_scope  # noqa: E402
from src.market.services.dhan_charts_client import DhanChartsClient  # noqa: E402
from src.settings.database.db_operations.app_setting_repository import (  # noqa: E402
    AppSettingRepository,
)
from src.settings.services.settings_service import SettingsService  # noqa: E402

DB = BACKEND / "data" / "paper_trading.db"
SESSION_OPEN, SESSION_CLOSE = time(9, 15), time(15, 30)
FULL_DAY_BARS = 75  # 09:15..15:25 at 5 minutes


def liquidity_ranking(lookback: int = 60) -> list[dict]:
    """F&O names ranked by median daily turnover (close x volume), in crore."""
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    fno = {
        sym: sid for sym, sid in conn.execute(
            "SELECT underlying_symbol, security_id FROM instruments "
            "WHERE exchange_segment='NSE_EQ' AND instrument_type='EQUITY' "
            "AND is_active=1 AND fno_eligible=1"
        )
    }
    rows = conn.execute(
        "SELECT symbol, bar_date, CAST(close AS REAL), CAST(volume AS REAL) "
        "FROM daily_bars WHERE exchange_segment='NSE_EQ' AND bar_date >= "
        "(SELECT date(MAX(bar_date), ?) FROM daily_bars WHERE exchange_segment='NSE_EQ')",
        (f"-{int(lookback * 1.6)} days",),
    ).fetchall()
    per = collections.defaultdict(list)
    for sym, _d, close, vol in rows:
        if sym in fno and close and vol:
            per[sym].append(close * vol / 1e7)
    ranked = [
        {"symbol": s, "security_id": fno[s],
         "median_turnover_cr": round(statistics.median(v[-lookback:]), 1),
         "sessions": len(v[-lookback:])}
        for s, v in per.items()
    ]
    ranked.sort(key=lambda r: -r["median_turnover_cr"])
    return ranked


async def probe(client, symbol, security_id, max_windows, delay):
    """Walk back in 90-day windows until two consecutive windows are empty."""
    end = datetime.now(IST).replace(tzinfo=None, hour=23, minute=59, second=0, microsecond=0)
    windows, empties = [], 0
    days: dict = {}
    for _ in range(max_windows):
        start = end - timedelta(days=89)
        try:
            candles = await client.fetch_intraday(
                security_id=security_id, exchange_segment="NSE_EQ",
                instrument="EQUITY", interval_minutes=5,
                from_date=start, to_date=end,
            )
            err = None
        except Exception as exc:  # noqa: BLE001 - record and keep walking
            candles, err = [], f"{type(exc).__name__}: {exc}"
        windows.append({"from": start.date().isoformat(), "to": end.date().isoformat(),
                        "bars": len(candles), "error": err})
        for c in candles:
            ts = datetime.fromtimestamp(c.time, IST)
            d = days.setdefault(ts.date().isoformat(), {"bars": 0, "zero_vol": 0,
                                                         "outside": 0, "first": None})
            d["bars"] += 1
            d["zero_vol"] += int(not c.volume)
            d["outside"] += int(not (SESSION_OPEN <= ts.time() < SESSION_CLOSE))
            d["first"] = min(d["first"] or ts.time().isoformat(), ts.time().isoformat())
        empties = empties + 1 if not candles else 0
        if empties >= 2:
            break
        end = start - timedelta(minutes=1)
        await asyncio.sleep(delay)

    ordered = sorted(days)
    bars = [days[d]["bars"] for d in ordered]
    return {
        "symbol": symbol, "security_id": security_id,
        "requests": len(windows), "windows": windows,
        "earliest_day": ordered[0] if ordered else None,
        "latest_day": ordered[-1] if ordered else None,
        "sessions": len(ordered),
        "full_sessions": sum(b == FULL_DAY_BARS for b in bars),
        "short_sessions": [(d, days[d]["bars"]) for d in ordered if days[d]["bars"] < FULL_DAY_BARS][:15],
        "over_sessions": [(d, days[d]["bars"]) for d in ordered if days[d]["bars"] > FULL_DAY_BARS][:15],
        "zero_volume_bars": sum(days[d]["zero_vol"] for d in ordered),
        "bars_outside_session": sum(days[d]["outside"] for d in ordered),
        "first_bar_times": dict(collections.Counter(days[d]["first"] for d in ordered)),
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--names", type=int, default=6, help="how many of the ranked names to probe")
    ap.add_argument("--max-windows", type=int, default=30, help="90-day windows per name (30 = ~7.4y)")
    ap.add_argument("--delay", type=float, default=0.6)
    args = ap.parse_args()

    async with session_scope() as session:
        await SettingsService(AppSettingRepository(session)).apply_to_config()
    client = DhanChartsClient()
    if not client.has_credentials():
        print("No Dhan credentials configured; cannot probe.")
        return 2

    ranked = liquidity_ranking()
    print(f"{len(ranked)} F&O names ranked by median 60-session turnover.")
    # Probe across the liquidity range, not just the top: the tail is where
    # history might be thinner, and the tail decides where the universe cuts.
    n = len(ranked)
    picks = sorted({0, 1, n // 4, n // 2, (3 * n) // 4, n - 1})[: args.names]
    results = []
    for i in picks:
        r = ranked[i]
        print(f"  probing #{i + 1} {r['symbol']} ({r['median_turnover_cr']} cr/day) ...", flush=True)
        res = await probe(client, r["symbol"], r["security_id"], args.max_windows, args.delay)
        res["liquidity_rank"] = i + 1
        res["median_turnover_cr"] = r["median_turnover_cr"]
        print(f"    {res['earliest_day']} .. {res['latest_day']}: {res['sessions']} sessions, "
              f"{res['full_sessions']} full, {res['requests']} requests", flush=True)
        results.append(res)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "liquidity_ranking.json").write_text(json.dumps(ranked, indent=1))
    (OUT / "feasibility.json").write_text(json.dumps(
        {"run_at": datetime.now(IST).isoformat(), "requests": client.request_count,
         "errors": client.error_count, "rate_limited": client.rate_limited_count,
         "probes": results}, indent=1, default=str))
    print(f"\nWrote {OUT / 'feasibility.json'} and liquidity_ranking.json "
          f"({client.request_count} requests, {client.error_count} errors).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
