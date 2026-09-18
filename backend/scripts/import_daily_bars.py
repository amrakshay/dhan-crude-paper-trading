"""Bootstrap `daily_bars` from a directory of per-symbol CSVs.

Run once, to load the ten-year panel the strategy was researched against, so
that the first nightly refresh has a two-month gap to fill rather than ten
years at 0.6 s per symbol.

    cd backend
    CONFIG_PATH=conf .venv/bin/python scripts/import_daily_bars.py \\
        --strategy nse-swing-momentum \\
        --directory ~/Workarea/local/pullback/backend/intrday_test_strategy/research2/data_daily_10y

The panel is read from wherever it lives; 49 MB of research CSV is not copied
into this repository. Files are named `<SYMBOL>.csv`, except the regime index,
whose file is named after the index rather than the symbol -- hence
`--index-file`.

**Do not point this at `research2/gate_run/data_ext/`.** Those files splice a
second vendor's rows after 2026-07-14. They are the parity-test fixture; as
production data the join would be invisible afterwards, and two vendors'
corporate-action policies can diverge on a future action.

The import is idempotent: rows are upserted on (segment, symbol, date), so
running it twice changes nothing and re-running it after the panel is extended
picks up only what is new.
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import config_utils  # noqa: E402

config_utils.load_config()

from src.daily_bars.database.db_operations.daily_bar_repository import (  # noqa: E402
    DailyBarRepository,
)
from src.daily_bars.services.bar_import_service import (  # noqa: E402
    BarImportError,
    BarImportService,
)
from src.database.session import session_scope  # noqa: E402
from src.strategies.services.strategy_registry import get_strategy_registry  # noqa: E402


def _targets(strategy, index_file):
    """(symbol, segment, security_id, filename) for everything to import.

    Security ids are left as None: the panel is historical price data and the
    id that fetched it years ago is not the id the next refresh will use. The
    nightly refresh fills the id in when it writes.
    """
    wanted = []
    for instrument_set in strategy.instrument_sets:
        symbols = (
            strategy.universe.symbols
            if instrument_set.from_universe and strategy.universe
            else instrument_set.symbols
        )
        for symbol in sorted(symbols):
            wanted.append((symbol, instrument_set.exchange_segment, None, None))

    for reference in strategy.reference_instruments.values():
        filename = index_file if reference.role == "regime_index" else None
        wanted.append(
            (reference.symbol, reference.exchange_segment, reference.security_id, filename)
        )
    return wanted


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strategy", required=True, help="strategy key, e.g. nse-swing-momentum"
    )
    parser.add_argument(
        "--directory", required=True, help="directory of <SYMBOL>.csv daily bar files"
    )
    parser.add_argument(
        "--index-file",
        default="NIFTY50.csv",
        help="filename for the regime index, whose symbol is NIFTY but whose "
        "file in the research panel is NIFTY50.csv",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be imported without writing",
    )
    args = parser.parse_args()

    strategy = get_strategy_registry().get(args.strategy)
    if strategy is None:
        print(f"Unknown strategy {args.strategy!r}", file=sys.stderr)
        return 2

    directory = os.path.expanduser(args.directory)
    wanted = _targets(strategy, args.index_file)

    if args.dry_run:
        present = sum(
            1
            for symbol, _, _, filename in wanted
            if os.path.isfile(os.path.join(directory, filename or f"{symbol}.csv"))
        )
        print(f"{present} of {len(wanted)} wanted files present in {directory}")
        return 0

    async with session_scope() as session:
        service = BarImportService(DailyBarRepository(session))
        try:
            result = await service.import_symbols(directory, wanted)
        except BarImportError as error:
            print(f"Import failed: {error}", file=sys.stderr)
            return 1
        await session.commit()

    summary = result.as_dict()
    for key, value in summary.items():
        print(f"{key}: {value}")

    warned = [one for one in result.symbols if one.warnings]
    if warned:
        print(f"\n{len(warned)} symbol(s) had per-row warnings; first few:")
        for entry in warned[:5]:
            print(f"  {entry.symbol}: {entry.warnings[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
