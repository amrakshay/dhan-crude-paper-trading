"""Measure what crossing the spread actually costs, from the live order book.

The backtest ASSUMED 5 bps of slippage per side, and the holdout verdict on
H3c turned on exactly that number (+12 bps at 2 bps, -4 at 10). This measures
it instead. It subscribes the most liquid F&O names in Full mode, snapshots
five levels of depth every few seconds, and for each snapshot computes the
cost, in bps of mid, of an immediate market order of Rs 1L and Rs 5L:
walking the ask side for a buy and the bid side for a sell, level by level.
If five levels do not fill the order, that is recorded, not extrapolated.

Read-only market data. It opens its own feed connection (one of Dhan's five
per user) through the application's own DhanFeedClient, like
backend/scripts/verify_feed_session_fields.py, and places nothing.

Run from backend/ during a session:
    CONFIG_PATH=conf .venv/bin/python ../experiments/intraday-screener/scripts/sample_spreads.py --seconds 180

One run measures ONE time of day. 09:30 is when H3c trades and spreads are
widest after the open, so a run at 14:00 is a LOWER bound for H3c's cost.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXP = HERE.parent
BACKEND = EXP.parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from src.app_utils import load_config_properties  # noqa: E402

load_config_properties()

from src.core.time_utils import ist_now  # noqa: E402
from src.database.session import session_scope  # noqa: E402
from src.instruments.database.db_operations.instrument_repository import (  # noqa: E402
    InstrumentRepository,
)
from src.market.services.dhan_feed_client import DhanFeedClient  # noqa: E402
from src.market.services.market_book import MarketBook  # noqa: E402
from src.settings.database.db_operations.app_setting_repository import (  # noqa: E402
    AppSettingRepository,
)
from src.settings.services.settings_service import SettingsService  # noqa: E402

NOTIONALS = (100_000, 500_000)


def walk(levels, side: str, notional: float, mid: float):
    """bps of mid paid by an immediate order of `notional` rupees, or None if
    five levels are not enough. side='buy' walks asks, 'sell' walks bids."""
    remaining, cost_qty, spent = notional, 0.0, 0.0
    for bid_qty, ask_qty, _bo, _ao, bid_px, ask_px in levels:
        px, qty = (ask_px, ask_qty) if side == "buy" else (bid_px, bid_qty)
        if not px or px <= 0 or not qty:
            continue
        take = min(qty, remaining / px)
        spent += take * px
        cost_qty += take
        remaining -= take * px
        if remaining <= 1e-6:
            avg = spent / cost_qty
            return 1e4 * ((avg / mid - 1) if side == "buy" else (1 - avg / mid))
    return None


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--top", type=int, default=100)
    ap.add_argument("--seconds", type=int, default=180)
    ap.add_argument("--every", type=float, default=5.0)
    args = ap.parse_args()

    async with session_scope() as session:
        await SettingsService(AppSettingRepository(session)).apply_to_config()

    ranking = json.loads((EXP / "out" / "liquidity_ranking.json").read_text())[: args.top]
    rank = {r["symbol"]: i + 1 for i, r in enumerate(ranking)}
    async with session_scope() as session:
        rows = await InstrumentRepository(session).list_by_symbols(list(rank), "NSE_EQ")
    inst = {str(r.security_id): r.underlying_symbol for r in rows if r.is_active}

    book = MarketBook()
    client = DhanFeedClient(book)
    started = ist_now()
    print(f"Subscribing {len(inst)} names at {started:%H:%M:%S} IST for {args.seconds}s "
          f"(own feed connection; read-only).", flush=True)
    await client.start()
    await client.subscribe([("NSE_EQ", sid) for sid in inst])

    samples = []
    loop = asyncio.get_event_loop()
    deadline = loop.time() + args.seconds
    await asyncio.sleep(min(10.0, args.seconds / 4))    # let the first packets arrive
    while loop.time() < deadline:
        at = ist_now().strftime("%H:%M:%S")
        for sid, sym in inst.items():
            row = book.get(sid) or {}
            depth, bid, ask = row.get("depth"), row.get("bid"), row.get("ask")
            if not depth or not bid or not ask or ask <= bid:
                continue
            mid = (bid + ask) / 2
            rec = {"at": at, "symbol": sym, "rank": rank.get(sym), "bid": bid, "ask": ask,
                   "half_spread_bps": round(1e4 * (ask - bid) / mid / 2, 3)}
            for n in NOTIONALS:
                b, s = walk(depth, "buy", n, mid), walk(depth, "sell", n, mid)
                rec[f"buy_{n // 1000}k_bps"] = None if b is None else round(b, 3)
                rec[f"sell_{n // 1000}k_bps"] = None if s is None else round(s, 3)
            samples.append(rec)
        await asyncio.sleep(args.every)
    await client.stop()

    if not samples:
        print("No depth arrived. Is the market open and the token valid?")
        return 2
    out = EXP / "out" / f"spreads_{started:%Y%m%d_%H%M}.csv"
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(samples[0]))
        w.writeheader()
        w.writerows(samples)
    print(f"{len(samples)} snapshots of {len({s['symbol'] for s in samples})} names -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
