"""Top `daily_bars` up from Dhan, on demand.

The nightly scheduler does this at 18:15 every session. This is the same call,
runnable by hand, for the two occasions that are not a nightly:

    * closing the gap after the ten-year panel is imported -- the panel ends
      2026-07-14 and the first live refresh fetches everything since; and
    * recovering after a run the scheduler missed, which it reports but never
      silently re-runs.

    cd backend
    CONFIG_PATH=conf .venv/bin/python scripts/refresh_daily_bars.py \\
        --strategy nse-swing-momentum

**It is slow on purpose.** `DhanChartsClient` throttles per security id, which
throttles a loop over five hundred different ids not at all, so
`daily_bars.request_delay_seconds` (0.6 s) is the real limiter -- about five
minutes for the universe on a small gap and longer on a large one. Lowering it
chooses a ban over a slow job. Do not run this minutes before the open; that is
the whole reason the table exists.

**It fetches; it never invents.** No credentials means no bars and an error.
There is no synthetic fallback on this path at all, unlike the price chart,
because a fabricated close would go straight into a trading decision.

**Do not run it during a session without `--as-of`.** It fetches up to today by
default, which is right at 18:15 and wrong at 13:00: Dhan's historical endpoint
will hand back today's FORMING bar, and storing that as a daily bar would have
the rotation decide a session that has not finished. `--as-of` bounds the fetch
to a date that is over. The nightly scheduler has no such problem -- it runs
after the close by construction.
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# `load_config_properties`, NOT `config_utils.load_config`: only the former
# reads `.env`, and without it APP_ENCRYPTION_KEY is absent -- so the Dhan
# token stored on the Settings page cannot be decrypted and this reports "no
# credentials" on a machine that has perfectly good ones. It also configures
# logging before anything builds a logger at module scope (backend/CLAUDE.md
# section 8), which is why it comes above the other `src.*` imports.
from src.app_utils import load_config_properties  # noqa: E402

load_config_properties()

from src.daily_bars.database.db_operations.daily_bar_repository import (  # noqa: E402
    DailyBarRepository,
)
from src.daily_bars.services.daily_bar_service import (  # noqa: E402
    DailyBarRefreshError,
    DailyBarRefreshService,
)
from src.database.session import session_scope  # noqa: E402
from src.instruments.database.db_operations.instrument_repository import (  # noqa: E402
    InstrumentRepository,
)
from src.settings.database.db_operations.app_setting_repository import (  # noqa: E402
    AppSettingRepository,
)
from src.settings.services.settings_service import SettingsService  # noqa: E402
from src.strategies.services.strategy_registry import get_strategy_registry  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strategy", required=True, help="strategy key, e.g. nse-swing-momentum"
    )
    parser.add_argument(
        "--symbols",
        default=None,
        help="comma-separated subset, for checking one name before pulling five hundred",
    )
    parser.add_argument(
        "--as-of",
        default=None,
        help="YYYY-MM-DD, the last session to fetch. Defaults to today in IST, "
        "which is only correct after the close -- see the module docstring.",
    )
    parser.add_argument(
        "--force-full",
        action="store_true",
        help="re-fetch every symbol's whole history rather than only the gap",
    )
    args = parser.parse_args()

    strategy = get_strategy_registry().get(args.strategy)
    if strategy is None:
        print(f"Unknown strategy {args.strategy!r}", file=sys.stderr)
        return 2

    # Settings saved in the UI beat `.env` (backend/CLAUDE.md section 7), and
    # the Dhan token usually lives there rather than in the environment. A
    # script that skipped this would report "no credentials" on a machine whose
    # Settings page shows a perfectly good one.
    async with session_scope() as session:
        await SettingsService(AppSettingRepository(session)).apply_to_config()

    as_of = None
    if args.as_of:
        from datetime import date as _date

        try:
            as_of = _date.fromisoformat(args.as_of)
        except ValueError:
            print(f"--as-of must be YYYY-MM-DD, got {args.as_of!r}", file=sys.stderr)
            return 2

    only = (
        [one.strip().upper() for one in args.symbols.split(",") if one.strip()]
        if args.symbols
        else None
    )

    async with session_scope() as session:
        service = DailyBarRefreshService(
            DailyBarRepository(session), InstrumentRepository(session)
        )
        try:
            result = await service.refresh_strategy(
                strategy,
                as_of=as_of,
                only_symbols=only,
                force_full=args.force_full,
            )
        except DailyBarRefreshError as error:
            print(f"Refresh could not run: {error}", file=sys.stderr)
            return 1
        await session.commit()

    payload = result.as_dict()
    for key in (
        "asOf", "symbolsConsidered", "symbolsRefreshed", "symbolsFailed",
        "symbolsUnresolved", "symbolsAlreadyCurrent", "barsInserted",
        "barsUpdated", "durationSeconds",
    ):
        print(f"{key}: {payload[key]}")
    for failure in payload["failures"]:
        print(f"FAILED {failure['symbol']}: {failure['error']}")
    for symbol in payload["unresolved"]:
        print(f"UNRESOLVED {symbol}: no active row in the instrument master")
    # One symbol failing does not fail the run -- 499 refreshed and one error is
    # a reportable state -- but it must not pass silently either.
    return 0 if not payload["symbolsFailed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
