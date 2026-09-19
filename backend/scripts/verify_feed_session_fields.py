"""Settle whether Dhan's Quote/Full packet maps high and low the way we think.

    cd backend
    CONFIG_PATH=conf .venv/bin/python scripts/verify_feed_session_fields.py \\
        --symbols RELIANCE,TCS,INFY,SBIN,ITC --seconds 90

**WHY THIS EXISTS.** Root `CLAUDE.md` section 5 ends with an unverified fact:
the `dhanhq` SDK maps the Quote and Full packets' four price fields in the
order open, close, high, low -- not the conventional OHLC -- and that ordering
is reproduced in `feed_protocol.py` because the SDK is the best available
source. It has never been checked against a live feed.

For the price chart that was a cosmetic risk. For BTST it is not. B6 is

    CLV = (price - low) / (high - low) > 0.8

so if high and low are transposed the close-location filter does not merely
drift, it INVERTS: the strategy buys the weakest closes in the market instead
of the strongest, and every number it produces looks plausible. This script is
the standing answer, and the finding belongs in root `CLAUDE.md` section 5's
table.

**IT ALSO SETTLES THE SECOND UNVERIFIED CLAIM**, the one BTST's subscription
window rests on: that the packet carries the session's AGGREGATE high, low and
cumulative volume rather than a delta since subscription. If it does, a
subscription opened at 14:45 still reports the whole session and the universe
costs the feed forty minutes a day instead of six hours. `--late-subscribe`
tests exactly that -- it subscribes now, mid-session, and checks whether the
first packets already describe the session from 09:15.

**HOW IT VERIFIES.** It subscribes to a handful of instruments, waits for
packets, and compares each one's reported session high, low and cumulative
volume against `/charts/intraday` for the same day -- the same read-only
market-data endpoint the price chart uses, and a source this application
already trusts. Three outcomes:

    MATCH        high >= low, and both agree with the intraday bars.
    TRANSPOSED   the packet's "high" matches the intraday LOW and vice versa.
                 `feed_protocol.FULL_IDX_HIGH` and `FULL_IDX_LOW` are wrong and
                 must be swapped.
    UNCLEAR      neither -- a thin book, a halted name, or something else.
                 Say so rather than concluding either way.

**IT OPENS ITS OWN CONNECTION**, which is the second of Dhan's five per user
and is why this is a script rather than something the application does. Run it
while the app is running and you are using two slots; that is fine and
deliberate, and it is over in ninety seconds.

**IT MUST RUN DURING A SESSION.** Outside market hours every packet's session
fields describe the last session or nothing at all, and the comparison means
nothing. It refuses rather than reporting a result nobody should act on.
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# `load_config_properties`, NOT `config_utils.load_config`: only the former
# bootstraps logging before anything builds a logger at module scope.
from src.app_utils import load_config_properties  # noqa: E402

load_config_properties()

from src.core.time_utils import ist_now  # noqa: E402
from src.database.session import session_scope  # noqa: E402
from src.logging_config import get_logger  # noqa: E402
from src.market.services.dhan_feed_client import DhanFeedClient  # noqa: E402
from src.market.services.market_book import MarketBook  # noqa: E402

logger = get_logger("btst.verify")

# How far apart two prices may be and still count as the same number. The feed
# is a live last-trade and the intraday bars are a one-minute aggregation, so
# they are never bit-identical: the session's high can be set by a trade that
# lands between bar boundaries. Ten basis points is far tighter than the gap a
# TRANSPOSITION would produce (high against low is the day's whole range) and
# loose enough not to cry wolf on a fast tape.
TOLERANCE = 0.001


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--symbols",
        default="RELIANCE,TCS,INFY,SBIN,ITC",
        help="Comma-separated NSE underlyings to check.",
    )
    parser.add_argument(
        "--seconds", type=int, default=90,
        help="How long to collect packets before comparing.",
    )
    parser.add_argument(
        "--late-subscribe", action="store_true",
        help=(
            "Also report whether the FIRST packet after subscribing already "
            "describes the whole session -- the claim BTST's subscription "
            "window rests on."
        ),
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Run outside market hours anyway. The result means nothing.",
    )
    return parser.parse_args()


async def _instruments(symbols):
    from src.instruments.database.db_operations.instrument_repository import (
        InstrumentRepository,
    )

    async with session_scope() as session:
        rows = await InstrumentRepository(session).list_by_symbols(symbols, "NSE_EQ")
        return [
            {
                "symbol": row.underlying_symbol,
                "security_id": str(row.security_id),
                "segment": row.exchange_segment,
            }
            for row in rows
            if row.is_active
        ]


async def _intraday_session(instrument):
    """The session's high, low and volume from `/charts/intraday`.

    The same read-only market-data client the price chart uses, and the same
    closed endpoint set. Nothing new is reached.
    """
    from src.market.services.dhan_charts_client import DhanChartsClient

    now = ist_now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    candles = await DhanChartsClient().fetch_intraday(
        security_id=instrument["security_id"],
        exchange_segment=instrument["segment"],
        instrument="EQUITY",
        interval_minutes=1,
        from_date=start.replace(tzinfo=None),
        to_date=(start + timedelta(days=1)).replace(tzinfo=None),
    )
    if not candles:
        return None
    return {
        "high": max(float(one.high) for one in candles),
        "low": min(float(one.low) for one in candles),
        "volume": sum(int(one.volume or 0) for one in candles),
        "bars": len(candles),
    }


def _close(left, right) -> bool:
    if left is None or right is None or right == 0:
        return False
    return abs(float(left) - float(right)) / abs(float(right)) <= TOLERANCE


def _verdict(packet, intraday):
    """MATCH, TRANSPOSED or UNCLEAR, per symbol."""
    if packet.get("high") is None or packet.get("low") is None:
        return "UNCLEAR", "the packet carried no session high/low"
    if intraday is None:
        return "UNCLEAR", "no intraday bars were returned for today"

    if packet["high"] < packet["low"]:
        return "TRANSPOSED", (
            f"the packet's high ({packet['high']:,.2f}) is BELOW its low "
            f"({packet['low']:,.2f}), which cannot be true of a session"
        )

    straight = _close(packet["high"], intraday["high"]) and _close(
        packet["low"], intraday["low"]
    )
    crossed = _close(packet["high"], intraday["low"]) and _close(
        packet["low"], intraday["high"]
    )
    if straight and not crossed:
        return "MATCH", (
            f"high {packet['high']:,.2f} ~ {intraday['high']:,.2f}, "
            f"low {packet['low']:,.2f} ~ {intraday['low']:,.2f}"
        )
    if crossed and not straight:
        return "TRANSPOSED", (
            f"the packet's high {packet['high']:,.2f} matches the intraday LOW "
            f"{intraday['low']:,.2f}, and its low {packet['low']:,.2f} matches "
            f"the intraday HIGH {intraday['high']:,.2f}"
        )
    if straight and crossed:
        # The session has barely moved, so high and low are the same number
        # and the test cannot distinguish them. Not a pass.
        return "UNCLEAR", (
            "the session's high and low are within tolerance of each other, so "
            "a transposition would be invisible -- try again later in the day "
            "or on a name that has moved"
        )
    return "UNCLEAR", (
        f"packet high/low {packet['high']:,.2f}/{packet['low']:,.2f} against "
        f"intraday {intraday['high']:,.2f}/{intraday['low']:,.2f} -- neither "
        f"straight nor transposed"
    )


async def main() -> int:
    args = _parse_args()
    now = ist_now()

    symbols = [one.strip().upper() for one in args.symbols.split(",") if one.strip()]
    instruments = await _instruments(symbols)
    if not instruments:
        print(
            f"None of {symbols} resolved against the instrument master. Refresh "
            f"it from the Instruments page first."
        )
        return 2

    from src.strategies.services.strategy_registry import get_strategy_registry

    definition = get_strategy_registry().get("nse-btst-overnight")
    if definition is not None and not args.force:
        from src.strategies.services import market_clock

        if not market_clock.is_market_open(definition, now=now):
            print(
                f"The market is shut ({now.strftime('%a %H:%M')} IST). Every "
                f"packet's session fields would describe the last session or "
                f"nothing, and the comparison would mean nothing. Run this "
                f"between {definition.market_hours.open.strftime('%H:%M')} and "
                f"{definition.market_hours.close.strftime('%H:%M')} IST on a "
                f"trading day, or pass --force and ignore the result."
            )
            return 3

    book = MarketBook()
    client = DhanFeedClient(book)
    print(
        f"Subscribing {len(instruments)} instrument(s) at "
        f"{now.strftime('%H:%M:%S')} IST and collecting for {args.seconds}s. "
        f"This opens a SECOND upstream connection (Dhan allows five per user)."
    )

    await client.start()
    await client.subscribe(
        [(one["segment"], one["security_id"]) for one in instruments]
    )

    first_seen = {}
    deadline = asyncio.get_event_loop().time() + args.seconds
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(1.0)
        for instrument in instruments:
            row = book.get(instrument["security_id"])
            if row and row.get("high") is not None:
                first_seen.setdefault(instrument["security_id"], dict(row))

    await client.stop()

    print()
    print(f"{'symbol':12} {'verdict':12} why")
    print("-" * 100)
    verdicts = {}
    for instrument in instruments:
        row = book.get(instrument["security_id"]) or {}
        intraday = await _intraday_session(instrument)
        verdict, why = _verdict(row, intraday)
        verdicts[instrument["symbol"]] = verdict
        print(f"{instrument['symbol']:12} {verdict:12} {why}")

        if args.late_subscribe:
            first = first_seen.get(instrument["security_id"])
            if first and intraday:
                whole = _close(first.get("volume"), intraday["volume"])
                print(
                    f"{'':12} {'':12} late-subscribe: the FIRST packet reported "
                    f"volume {first.get('volume'):,} against the session's "
                    f"{intraday['volume']:,} -- "
                    + (
                        "AGGREGATE, so a window subscription is safe"
                        if whole
                        else "NOT the whole session; widen "
                        "subscription.window_opens_at in the strategy YAML"
                    )
                )

    print()
    counts = {}
    for verdict in verdicts.values():
        counts[verdict] = counts.get(verdict, 0) + 1
    print(f"Result: {counts}")

    if counts.get("TRANSPOSED"):
        print(
            "\nTRANSPOSED. `feed_protocol.FULL_IDX_HIGH` and `FULL_IDX_LOW` "
            "are the wrong way round, and so are the Quote packet's `high` and "
            "`low`. Swap them, record the finding in root CLAUDE.md section 5, "
            "and note that every CLV computed before this was INVERTED."
        )
        return 1
    if counts.get("MATCH") and not counts.get("TRANSPOSED"):
        print(
            "\nMATCH. The SDK's field order is correct as implemented. Record "
            "it in root CLAUDE.md section 5's table, with today's date and the "
            "symbols checked, and delete the 'unverified' note."
        )
        return 0
    print(
        "\nUNCLEAR on every symbol -- nothing is settled. Try again later in "
        "the session, or on names that have moved further."
    )
    return 4


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
