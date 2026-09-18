"""Bootstrap the daily-bar table from a directory of CSVs.

This exists once, for one job: loading the ten-year panel the strategy was
researched against, so the first nightly refresh has only a two-month gap to
fill rather than ten years at 0.6 s per symbol.

Everything about it is deliberately conservative:

* **It reads a path it is given.** The panel is 49 MB of CSV that belongs to
  the research project; copying it into this repository would double it for no
  benefit, and the importer is run once.
* **It never invents a bar.** A file that is missing, empty, wrongly shaped or
  carries an unparseable row is reported, not patched.
* **It marks what it wrote.** Rows land with `source='import'`, so a series
  that came from a file can always be told from one Dhan served.

The panel it was written for is a single-vendor Dhan export
(`research2/data_daily_10y/`, 2015-07-01 to 2026-07-14). The research project
also has an *extended* panel that splices a second vendor's rows after
2026-07-14; that one is used as a TEST FIXTURE and must not be imported as
production data, because two vendors' corporate-action policies can diverge on
a future action and the join would be invisible afterwards.
"""
import csv
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.daily_bars.database.db_models.daily_bar_model import SOURCE_IMPORT
from src.daily_bars.database.db_operations.daily_bar_repository import (
    DailyBarRepository,
)
from src.logging_config import get_logger

logger = get_logger("daily_bars.import")

REQUIRED_COLUMNS = ("date", "open", "high", "low", "close")


class BarImportError(Exception):
    """The import cannot proceed, and says why."""


@dataclass
class SymbolImport:
    symbol: str
    file: str
    rows_read: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    first_date: Optional[date] = None
    last_date: Optional[date] = None
    warnings: List[str] = field(default_factory=list)


@dataclass
class ImportResult:
    directory: str
    symbols: List[SymbolImport] = field(default_factory=list)
    missing_files: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def inserted(self) -> int:
        return sum(one.inserted for one in self.symbols)

    @property
    def rows_read(self) -> int:
        return sum(one.rows_read for one in self.symbols)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "directory": self.directory,
            "symbolsImported": len(self.symbols),
            "symbolsMissing": len(self.missing_files),
            "missingFiles": self.missing_files[:50],
            "rowsRead": self.rows_read,
            "inserted": self.inserted,
            "updated": sum(one.updated for one in self.symbols),
            "unchanged": sum(one.unchanged for one in self.symbols),
            "skipped": sum(one.skipped for one in self.symbols),
            "durationSeconds": round(self.duration_seconds, 2),
        }


def _parse_date(value: str) -> Optional[date]:
    text = (value or "").strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _parse_price(value: str) -> Optional[Decimal]:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed > 0 else None


def _parse_volume(value: str) -> Optional[int]:
    text = (value or "").strip()
    if not text:
        return None
    try:
        # The panel writes volume as a float ("6124279.0").
        return int(float(text))
    except (TypeError, ValueError):
        return None


def read_bar_file(
    path: str, symbol: str, exchange_segment: str, security_id: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], List[str], int]:
    """Parse one CSV into upsertable rows. Returns (rows, warnings, read).

    A row is dropped only when it cannot be trusted -- an unparseable date, a
    non-positive price, or a high below its own low. Each drop is reported.
    """
    warnings: List[str] = []
    rows: List[Dict[str, Any]] = []
    read = 0

    with open(path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [
            column for column in REQUIRED_COLUMNS if column not in (reader.fieldnames or [])
        ]
        if missing:
            raise BarImportError(
                f"{path}: missing column(s) {missing}; got {reader.fieldnames}"
            )

        for record in reader:
            read += 1
            bar_date = _parse_date(record.get("date", ""))
            if bar_date is None:
                warnings.append(f"{symbol}: unparseable date {record.get('date')!r}")
                continue

            prices = {
                field: _parse_price(record.get(field, ""))
                for field in ("open", "high", "low", "close")
            }
            unusable = [name for name, value in prices.items() if value is None]
            if unusable:
                warnings.append(
                    f"{symbol} {bar_date}: unusable {', '.join(sorted(unusable))}"
                )
                continue
            if prices["high"] < prices["low"]:
                warnings.append(
                    f"{symbol} {bar_date}: high {prices['high']} below low "
                    f"{prices['low']}"
                )
                continue

            rows.append(
                {
                    "symbol": symbol,
                    "exchange_segment": exchange_segment,
                    "security_id": security_id,
                    "bar_date": bar_date,
                    "open": prices["open"],
                    "high": prices["high"],
                    "low": prices["low"],
                    "close": prices["close"],
                    "volume": _parse_volume(record.get("volume", "")),
                    "source": SOURCE_IMPORT,
                }
            )

    return rows, warnings, read


class BarImportService:
    """Loads a directory of per-symbol CSVs into `daily_bars`."""

    def __init__(self, repository: DailyBarRepository):
        self.repository = repository

    async def import_symbols(
        self,
        directory: str,
        wanted: Iterable[Tuple[str, str, Optional[str], Optional[str]]],
        commit_each_symbol: bool = True,
    ) -> ImportResult:
        """Import one file per wanted symbol.

        `wanted` is (symbol, exchange_segment, security_id, filename). The
        filename is explicit because the panel does not always name a file
        after the symbol -- the NIFTY 50 series is `NIFTY50.csv` while the
        symbol is `NIFTY`.

        `commit_each_symbol` commits and expunges after every file. The ten-year
        panel is 1.09 MILLION rows across 501 files; held in one session's
        identity map that is enough pending ORM objects to exhaust memory and
        take the process down. Committing per symbol keeps the footprint flat
        and makes a failure half-way through leave the symbols already done
        rather than nothing. Pass False only when the caller owns the
        transaction and knows the batch is small.
        """
        import time as _time

        started = _time.perf_counter()
        if not os.path.isdir(directory):
            raise BarImportError(f"No such directory: {directory}")

        result = ImportResult(directory=directory)

        for symbol, segment, security_id, filename in wanted:
            name = filename or f"{symbol}.csv"
            path = os.path.join(directory, name)
            if not os.path.isfile(path):
                result.missing_files.append(name)
                continue

            rows, warnings, read = read_bar_file(path, symbol, segment, security_id)
            counts = await self.repository.upsert_many(rows)
            entry = SymbolImport(
                symbol=symbol,
                file=name,
                rows_read=read,
                inserted=counts["inserted"],
                updated=counts["updated"],
                unchanged=counts["unchanged"],
                skipped=read - len(rows),
                first_date=rows[0]["bar_date"] if rows else None,
                last_date=rows[-1]["bar_date"] if rows else None,
                warnings=warnings,
            )
            result.symbols.append(entry)

            if commit_each_symbol:
                await self.repository.session.commit()
                self.repository.session.expunge_all()

        result.duration_seconds = _time.perf_counter() - started

        if result.missing_files:
            logger.warning(
                "Bar import: %s of %s wanted symbols had no file in %s: %s",
                len(result.missing_files),
                len(result.missing_files) + len(result.symbols),
                directory,
                ", ".join(result.missing_files[:10]),
            )
        logger.info(
            "Bar import from %s: %s symbols, %s rows read, %s inserted, %s updated "
            "in %.1fs",
            directory, len(result.symbols), result.rows_read, result.inserted,
            sum(one.updated for one in result.symbols), result.duration_seconds,
        )
        return result
