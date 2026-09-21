"""Rebuild a past session's 15:20 book from intraday bars and replay the scan.

    cd backend
    CONFIG_PATH=conf .venv/bin/python scripts/replay_btst_scan.py \\
        --strategy nse-btst-overnight --date 2026-09-21

**WHY THIS EXISTS.** BTST decides on the SESSION SO FAR. Three of its seven
filters read the live book -- the price against the 55-day high, cumulative
volume against a 20-day average, and the close location within the day's
running range -- and none of those numbers exists an hour later. The journal
stores them for every CANDIDATE and, since 2026-09-21, for every near-miss it
has room for. What it cannot store is every name: the cap is
`MAX_REJECTION_ROWS`, and on a day when hundreds fail at the breakout the rows
below the cap are gone for good.

This reconstructs them. `/charts/intraday` truncated at the scan time gives the
same three numbers the packet carried -- read-only market data, already
allowlisted, and the same endpoint `verify_feed_session_fields.py` compares
against.

**IT WRITES NOTHING.** `scan_now()` is the scan AS A READ: no orders, no
journal, no side effects. It is safe to run against a live database while the
application is running, and it does not touch the feed.

**IT VALIDATES ITSELF, AND THAT IS THE POINT.** A reconstruction that does not
reproduce the session's recorded funnel has not reproduced the session, and its
answer about which name got closest would be worth nothing. When the journal
holds a SCAN for the date, every stage count is printed beside the recorded one
and a mismatch is called out. Read that table before believing the rest.

**WHAT IT IS NOT.** It is not a backtest and it cannot tell you what the
strategy WOULD have made -- it replays one session's filter funnel, nothing
more. It is also not `verify_feed_session_fields.py`: that subscribes to the
live feed and compares the packet's own fields, which is what settles root
`CLAUDE.md` section 5. This only shows whether two independent sources agree on
the outcome, which is weaker (see --help on `--strategy`).

**A DAY THE VENDOR HAS NO INTRADAY BARS FOR CANNOT BE REPLAYED.** Dhan serves a
limited intraday history; an old session comes back with no candles and the
script says so rather than reporting an empty funnel as a quiet session.
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, time, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# `load_config_properties`, NOT `config_utils.load_config`: only the former
# reads `.env`, and without it APP_ENCRYPTION_KEY is absent -- so the Dhan
# token stored on the Connections page cannot be decrypted and this reports
# "no credentials" on a machine that has perfectly good ones. It also
# configures logging before anything builds a logger at module scope
# (backend/CLAUDE.md section 8), which is why it comes above the other
# `src.*` imports.
from src.app_utils import load_config_properties  # noqa: E402

load_config_properties()

from src.btst.database.db_operations.btst_repository import (  # noqa: E402
    BtstSessionRepository,
)
from src.btst.services import module_hooks  # noqa: E402
from src.btst.services.btst_parameters import BtstParameters  # noqa: E402
from src.btst.services.scan_service import FILTER_STAGES  # noqa: E402
from src.core.time_utils import IST, ist_today  # noqa: E402
from src.database.session import session_scope  # noqa: E402
from src.instruments.database.db_operations.instrument_repository import (  # noqa: E402
    InstrumentRepository,
)
from src.market.services.dhan_charts_client import DhanChartsClient  # noqa: E402
from src.settings.database.db_operations.app_setting_repository import (  # noqa: E402
    AppSettingRepository,
)
from src.settings.services.settings_service import SettingsService  # noqa: E402
from src.strategies.services.strategy_registry import get_strategy_registry  # noqa: E402

# The stages a name has to have REACHED to be worth printing individually.
# Everything above the breakout is the population, which the funnel counts.
DEEP_STAGES = ("breakout", "volume", "close_strength", "trend", "momentum")
_DEPTH = {key: depth for depth, (key, _) in enumerate(FILTER_STAGES)}
_LABEL = dict(FILTER_STAGES)


async def _rebuild_book(client, wanted, session_day, cutoff, segment, delay, quiet):
    """The book as it stood at `cutoff`, from that day's 1-minute bars.

    One fetch per instrument, paced the way `refresh_daily_bars` paces its
    own: `DhanChartsClient` throttles per security id, which throttles a loop
    over five hundred different ids not at all.
    """
    book, failures, empty = {}, [], []
    start = datetime.combine(session_day, time(0, 0))
    for index, (symbol, security_id) in enumerate(sorted(wanted.items()), start=1):
        try:
            candles = await client.fetch_intraday(
                security_id=security_id,
                exchange_segment=segment,
                instrument="EQUITY",
                interval_minutes=1,
                from_date=start,
                to_date=start + timedelta(days=1),
            )
        except Exception as error:  # noqa: BLE001 - one name must not end the run
            failures.append(f"{symbol}: {type(error).__name__}: {error}")
            await asyncio.sleep(delay)
            continue

        upto = [
            one for one in candles
            if datetime.fromtimestamp(one.time, IST).time() < cutoff
        ]
        if not upto:
            empty.append(symbol)
        else:
            book[security_id] = {
                "ltp": float(upto[-1].close),
                "high": max(float(one.high) for one in upto),
                "low": min(float(one.low) for one in upto),
                "volume": sum(float(one.volume or 0) for one in upto),
            }
        if not quiet and index % 50 == 0:
            print(f"  {index}/{len(wanted)} ...", flush=True)
        await asyncio.sleep(delay)
    return book, failures, empty


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strategy", default="nse-btst-overnight",
        help="strategy key. Only modules that decide on the session so far can "
             "be replayed this way; the rotation decides on finished bars and "
             "needs no reconstruction.",
    )
    parser.add_argument(
        "--date", default=None,
        help="YYYY-MM-DD, the session to replay. Defaults to today in IST.",
    )
    parser.add_argument(
        "--at", default=None,
        help="HH:MM IST to truncate the session at. Defaults to the strategy's "
             "own schedule.scan_at, which is what the live scan used.",
    )
    parser.add_argument(
        "--cache", default=None,
        help="path to hold the rebuilt book, so a re-run costs no Dhan "
             "requests. Written after a successful rebuild and read instead of "
             "fetching when it exists.",
    )
    parser.add_argument(
        "--delay", type=float, default=0.6,
        help="seconds between fetches (default 0.6, the interval "
             "`refresh_daily_bars` uses). Lowering it chooses a ban.",
    )
    parser.add_argument("--quiet", action="store_true", help="no progress lines")
    args = parser.parse_args()

    async with session_scope() as session:
        await SettingsService(AppSettingRepository(session)).apply_to_config()

    registry = get_strategy_registry()
    try:
        definition = registry.require(args.strategy)
    except Exception as error:  # noqa: BLE001
        print(f"Unknown strategy {args.strategy!r}: {error}")
        return 2

    hooks = None
    try:
        from src.strategies.services.strategy_modules import hooks_for

        hooks = hooks_for(definition)
    except Exception:  # noqa: BLE001
        pass
    if hooks is not module_hooks:
        print(
            f"{args.strategy} is not the overnight module. This script "
            f"reconstructs a LIVE-SESSION scan and only that module has one."
        )
        return 2

    parameters = BtstParameters.from_definition(definition)
    session_day = (
        datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else ist_today()
    )
    cutoff = datetime.strptime(
        args.at or parameters.schedule.scan_at, "%H:%M"
    ).time()
    segment = definition.exchange_segment
    universe = sorted(definition.universe.symbols) if definition.universe else []
    if not universe:
        print(f"{args.strategy} declares no universe, so there is nothing to scan.")
        return 2

    async with session_scope() as session:
        rows = await InstrumentRepository(session).list_by_symbols(universe, segment)
    wanted = {}
    for instrument in rows:
        if not instrument.is_active:
            continue
        # One EQUITY row per symbol is what the instrument set selects for, so
        # the first active row wins -- the same rule `_quotes` follows.
        wanted.setdefault(instrument.underlying_symbol, str(instrument.security_id))
    if not wanted:
        print("No active instruments for this universe; refresh the master first.")
        return 2

    cache = args.cache
    book = None
    if cache and os.path.exists(cache):
        with open(cache) as handle:
            book = json.load(handle)
        print(f"Loaded {len(book)} quote(s) from {cache}.")
    else:
        print(
            f"Rebuilding {len(wanted)} instrument(s) as at "
            f"{cutoff.strftime('%H:%M')} IST on {session_day} ..."
        )
        book, failures, empty = await _rebuild_book(
            DhanChartsClient(), wanted, session_day, cutoff, segment,
            args.delay, args.quiet,
        )
        if not book:
            print(
                f"No intraday bars for {session_day} on any of {len(wanted)} "
                f"instrument(s). Dhan's intraday history is limited, so an old "
                f"session cannot be replayed -- and an empty funnel here would "
                f"look exactly like a quiet session, which is why this refuses "
                f"instead of printing one."
            )
            return 1
        print(f"Rebuilt {len(book)} quote(s); {len(failures)} fetch failure(s), "
              f"{len(empty)} with no bars before the cutoff.")
        for line in failures[:10]:
            print(f"  FETCH FAILED {line}")
        if cache:
            with open(cache, "w") as handle:
                json.dump(book, handle)
            print(f"Cached to {cache}.")

    async with session_scope() as db:
        service = await module_hooks._build(db, definition)
        # The reconstructed book stands in for the live one. `scan_now()` reads
        # it exactly as it reads `MarketBook`, and writes nothing.
        service._book = book
        result, blocked = await service.scan_now()
        recorded = await _recorded_counts(db, definition.key, session_day)

    if result.error:
        print(f"\nThe scan could not run: {result.error}")
        return 1

    print(f"\nFunnel for {session_day} at {cutoff.strftime('%H:%M')} IST")
    mismatches = 0
    for key, label in FILTER_STAGES:
        got = result.counts.get(key, 0)
        was = recorded.get(key) if recorded else None
        if was is None:
            print(f"  {label:<34} {got:>6}")
            continue
        flag = "" if got == was else "   <-- DIFFERS from the journal"
        mismatches += 0 if got == was else 1
        print(f"  {label:<34} {got:>6}   journal {was:>6}{flag}")

    if recorded and mismatches:
        print(
            f"\n{mismatches} stage(s) differ from what the session recorded. "
            f"This reconstruction has NOT reproduced that scan, so treat the "
            f"names below as a different computation rather than as what "
            f"happened."
        )
    elif recorded:
        print("\nEvery stage matches the journal: this reproduces that scan.")
    else:
        print(
            "\nNo SCAN is journalled for this date, so there is nothing to "
            "check the reconstruction against."
        )

    deep = sorted(
        (one for one in result.rejections if one.stage in DEEP_STAGES),
        key=lambda one: (-_DEPTH.get(one.stage, 0), one.symbol),
    )
    print(f"\nReached the breakout test and failed ({len(deep)}), deepest first:")
    for one in deep[:40]:
        parts = [f"price={one.price}"]
        if one.breakout_high is not None:
            parts.append(f"hi55={one.breakout_high}")
        if one.vol_ratio is not None:
            parts.append(f"vol={one.vol_ratio:.2f}x")
        if one.clv is not None:
            parts.append(f"clv={one.clv:.3f}")
        if one.sma is not None:
            parts.append(f"sma200={one.sma:.2f}")
        if one.momentum is not None:
            parts.append(f"mom={one.momentum:.4f}")
        print(f"  {_LABEL.get(one.stage, one.stage):<34} {one.symbol:<12} "
              f"{'  '.join(parts)}")
    if len(deep) > 40:
        print(f"  ... and {len(deep) - 40} more that failed at the breakout.")

    print(f"\nQualified: {[one.symbol for one in result.candidates] or 'nothing'}")
    if blocked:
        print(f"Entries blocked: {blocked}")
    return 0


async def _recorded_counts(session, strategy_key, session_day):
    """The funnel the session actually journalled, if it is there."""
    from src.btst.database.db_models.btst_session_model import RUN_SCAN

    try:
        rows = await BtstSessionRepository(session).sessions_completed_on(
            strategy_key, session_day, RUN_SCAN
        )
    except Exception:  # noqa: BLE001 - a missing journal is not a failure here
        return None
    for row in rows or []:
        if row.filter_counts_json:
            return json.loads(row.filter_counts_json)
    return None


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
