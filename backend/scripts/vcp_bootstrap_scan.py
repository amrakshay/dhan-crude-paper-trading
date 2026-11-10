"""One-off: give NSE VCP Swing its memory before the first nightly.

The nightly scans each symbol's bars AFTER its cursor. With no cursor at all, a
base that has been forming for weeks would look brand new on the first night
and be bought as if it had just appeared -- which the backtest never did,
because it had been scanning all along. This replays the most recent
`replay_sessions` bars (160) of every universe symbol, up to but NOT including
the newest bar, and records every base it accepts as REPLAYED: remembered,
never traded. The next nightly then scans the newest bar exactly as it will
every night after.

160 bars was measured, not guessed: replaying 160 or 400 bars gives the same
signals as a full ten-year scan at 88 of 88 simulated go-live dates.

Runs the detection on every core (about a minute; eleven single-threaded,
which is why the nightly refuses to do this itself). Safe to re-run: symbols
that already have a cursor are skipped.

    cd backend
    CONFIG_PATH=conf .venv/bin/python scripts/vcp_bootstrap_scan.py [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Above the other src.* imports, so logging is configured from the YAML
# (backend CLAUDE.md section 8).
from src.app_utils import load_config_properties  # noqa: E402

load_config_properties()

STRATEGY = "nse-vcp-swing"


def _scan(job):
    """Worker: (symbol, bars, first, last, params) -> accepted bases."""
    from src.vcp.services import signal_service as signals

    symbol, bars, first, last, params = job
    return symbol, signals.scan(bars, range(first, last), [], params)


async def main(dry_run: bool) -> None:
    import src.database.models  # noqa: F401 - register every table, as the app does
    from src.daily_bars.database.db_operations.daily_bar_repository import DailyBarRepository
    from src.database.session import session_scope
    from src.instruments.database.db_operations.instrument_repository import (
        InstrumentRepository,
    )
    from src.strategies.services.strategy_registry import get_strategy_registry
    from src.vcp.database.db_models.vcp_models import SIGNAL_REPLAYED
    from src.vcp.database.db_operations.vcp_repository import (
        VcpScanStateRepository,
        VcpSignalRepository,
    )
    from src.vcp.services import signal_service as signals
    from src.vcp.services.execution_service import _d
    from src.vcp.services.vcp_parameters import VcpParameters

    definition = get_strategy_registry().require(STRATEGY)
    params = VcpParameters.from_definition(definition)
    segment = definition.instrument_sets[0].exchange_segment
    universe = sorted(definition.universe.symbols)

    jobs = []
    async with session_scope() as session:
        cursors = await VcpScanStateRepository(session).all_for(STRATEGY)
        bars_repo = DailyBarRepository(session)
        ids = {}
        for one in await InstrumentRepository(session).list_by_symbols(universe, segment):
            if one.is_active and one.underlying_symbol not in ids:
                ids[one.underlying_symbol] = str(one.security_id)
        for symbol in universe:
            if symbol in cursors:
                continue
            rows = await bars_repo.history(symbol, segment, limit=params.history_sessions)
            if len(rows) < params.minimum_listed_sessions:
                continue   # too recently listed; the nightly skips it too
            bars = signals.bars_from_daily(symbol, rows)
            n = len(bars)
            jobs.append((symbol, bars, max(0, n - 1 - params.replay_sessions), n - 1, params))
    print(f"{len(jobs)} symbol(s) to replay ({len(cursors)} already have a cursor).")
    if not jobs:
        return

    results = {}
    with ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 4) - 2)) as pool:
        for i, (symbol, found) in enumerate(pool.map(_scan, jobs, chunksize=4), 1):
            results[symbol] = found
            if i % 100 == 0:
                print(f"  {i}/{len(jobs)}")

    total = sum(len(v) for v in results.values())
    print(f"{total} base(s) remembered across {len(results)} symbol(s).")
    if dry_run:
        print("--dry-run: nothing written.")
        return

    async with session_scope() as session:
        sig = VcpSignalRepository(session)
        cur = VcpScanStateRepository(session)
        for symbol, bars, first, last, _ in jobs:
            for base in results.get(symbol, []):
                await sig.create(
                    strategy_key=STRATEGY, session_id=None, symbol=symbol,
                    security_id=ids.get(symbol), seen_date=base.seen_date,
                    base_start_date=base.base_start_date, pivot_price=_d(base.pivot),
                    stop_price=_d(base.stop), close_price=_d(base.close), atr=_d(base.atr),
                    stop_atr=_d(base.stop_atr), contractions=base.contractions,
                    variant=base.variant, score=_d(base.score),
                    trend_template=base.trend_template, status=SIGNAL_REPLAYED,
                    reason=f"Bootstrap replay: remembered, never traded. ({base.reason})")
            # Cursor at the bar BEFORE the newest, so the next nightly scans
            # the newest bar as a normal, tradable session.
            await cur.advance(STRATEGY, symbol, signals.to_date(bars.date[last - 1]))
        await session.commit()
    print("Written. The next nightly scans each symbol's newest bar as a live session.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    asyncio.run(main(ap.parse_args().dry_run))
