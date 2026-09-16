"""Download and ingest Dhan's instrument master.

The master is a ~35 MB CSV that Dhan refreshes daily. Security IDs change every
expiry, so it is re-fetched at runtime rather than baked in.

Two fields in it cannot be taken at face value for MCX (both verified against
the live file on 2026-09-16):

  LOT_SIZE  -- reads 1.0 for *every* MCX row (158 FUTCOM, 15,844 OPTFUT) while
               NSE rows in the same file carry real values. Dhan does not
               publish MCX contract size here, so it comes from
               `contract_specs` in the YAML config instead.
  TICK_SIZE -- published in paise, not rupees (ALUMINIUM 5.0 = Rs 0.05,
               CARDAMOM 100.0 = Rs 1.00, CRUDEOIL options 10.0 = Rs 0.10).
               Divided by `contract_specs.tick_size_divisor`.
"""
import csv
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import httpx

from src import config_utils
from src.core.time_utils import utc_now
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.logging_config import get_logger

logger = get_logger("instruments.master")

MASTER_FILENAME = "api-scrip-master-detailed.csv"

# Column names as published. Verified against the live header on 2026-09-16.
COL_EXCH_ID = "EXCH_ID"
COL_SECURITY_ID = "SECURITY_ID"
COL_INSTRUMENT = "INSTRUMENT"
COL_UNDERLYING_SECURITY_ID = "UNDERLYING_SECURITY_ID"
COL_UNDERLYING_SYMBOL = "UNDERLYING_SYMBOL"
COL_SYMBOL_NAME = "SYMBOL_NAME"
COL_DISPLAY_NAME = "DISPLAY_NAME"
COL_LOT_SIZE = "LOT_SIZE"
COL_EXPIRY = "SM_EXPIRY_DATE"
COL_STRIKE = "STRIKE_PRICE"
COL_OPTION_TYPE = "OPTION_TYPE"
COL_TICK_SIZE = "TICK_SIZE"

REQUIRED_COLUMNS = {
    COL_EXCH_ID, COL_SECURITY_ID, COL_INSTRUMENT, COL_UNDERLYING_SYMBOL,
    COL_EXPIRY, COL_STRIKE, COL_OPTION_TYPE, COL_TICK_SIZE,
}

VALID_OPTION_TYPES = {"CE", "PE"}


class InstrumentMasterError(Exception):
    pass


@dataclass
class RefreshResult:
    downloaded: bool
    source_path: str
    rows_scanned: int = 0
    rows_matched: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    deactivated: int = 0
    expiries: List[date] = field(default_factory=list)
    duration_seconds: float = 0.0
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "downloaded": self.downloaded,
            "sourcePath": self.source_path,
            "rowsScanned": self.rows_scanned,
            "rowsMatched": self.rows_matched,
            "inserted": self.inserted,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "deactivated": self.deactivated,
            "expiries": [value.isoformat() for value in self.expiries],
            "durationSeconds": round(self.duration_seconds, 3),
            "warnings": self.warnings,
        }


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    text = value.strip()
    if not text or text.upper() in {"NA", "XX", "0"}:
        return None
    # Observed format is YYYY-MM-DD; the timestamped variant is accepted in
    # case Dhan changes it for some segments.
    for pattern in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _parse_decimal(value: Optional[str]) -> Optional[Decimal]:
    if value is None:
        return None
    text = value.strip()
    if not text or text.upper() in {"NA", "XX"}:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


class InstrumentMasterService:
    """Fetches the master CSV and upserts the CRUDEOIL universe from it."""

    def __init__(self, repository: InstrumentRepository):
        self.repository = repository

    # --- configuration -----------------------------------------------------
    @staticmethod
    def _master_url() -> str:
        return config_utils.get_property_value(
            "dhan.instrument_master_url",
            "https://images.dhan.co/api-data/api-scrip-master-detailed.csv",
        )

    @staticmethod
    def _cache_dir() -> str:
        return config_utils.get_property_value(
            "dhan.instrument_master_cache_dir", "./data/instrument_master"
        )

    @classmethod
    def _cache_path(cls) -> str:
        return os.path.join(cls._cache_dir(), MASTER_FILENAME)

    @staticmethod
    def _max_age_seconds() -> float:
        return config_utils.get_property_value_int(
            "dhan.instrument_master_max_age_hours", 12
        ) * 3600

    @staticmethod
    def _configured_lot_size(underlying_symbol: str) -> Optional[int]:
        specs = config_utils.get_property_dict(f"contract_specs.{underlying_symbol}", {})
        lot_size = specs.get("lot_size")
        return int(lot_size) if lot_size else None

    @staticmethod
    def _tick_divisor() -> Decimal:
        return Decimal(
            str(config_utils.get_property_value_int("contract_specs.tick_size_divisor", 100))
        )

    # --- download ----------------------------------------------------------
    def is_cache_fresh(self) -> bool:
        path = self._cache_path()
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            return False
        return (time.time() - os.path.getmtime(path)) < self._max_age_seconds()

    async def download(self, force: bool = False) -> bool:
        """Fetch the master to the cache. Returns True if it was downloaded.

        The CSV is public and needs no credentials, so this works whether or
        not Dhan market-data credentials are configured.
        """
        if not force and self.is_cache_fresh():
            logger.info("Instrument master cache is fresh; skipping download")
            return False

        url = self._master_url()
        cache_dir = self._cache_dir()
        os.makedirs(cache_dir, exist_ok=True)
        timeout = config_utils.get_property_value_int("dhan.http_timeout_seconds", 30)

        logger.info("Downloading instrument master from %s", url)
        started = time.perf_counter()
        total_bytes = 0

        # Stream to a temp file in the same directory, then atomically replace,
        # so a failed download can never leave a truncated master in place.
        handle, temp_path = tempfile.mkstemp(dir=cache_dir, suffix=".partial")
        try:
            with os.fdopen(handle, "wb") as sink:
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                    async with client.stream("GET", url) as response:
                        response.raise_for_status()
                        async for chunk in response.aiter_bytes(chunk_size=1 << 20):
                            sink.write(chunk)
                            total_bytes += len(chunk)

            if total_bytes < 1_000_000:
                raise InstrumentMasterError(
                    f"Instrument master download looks truncated ({total_bytes} bytes)"
                )

            os.replace(temp_path, self._cache_path())
        except Exception:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

        logger.info(
            "Instrument master downloaded: %.1f MB in %.1fs",
            total_bytes / (1 << 20),
            time.perf_counter() - started,
        )
        return True

    # --- parse + ingest ----------------------------------------------------
    def parse(self, path: str) -> tuple[List[Dict[str, Any]], int, List[str]]:
        """Read the CSV and return the rows for the configured underlying."""
        exchange_id = config_utils.get_property_value("underlying.exchange_id", "MCX")
        exchange_segment = config_utils.get_property_value(
            "underlying.exchange_segment", "MCX_COMM"
        )
        segment_code = config_utils.get_property_value_int(
            "underlying.exchange_segment_code", 5
        )
        underlying_symbol = config_utils.get_property_value("underlying.symbol", "CRUDEOIL")
        option_type_name = config_utils.get_property_value(
            "underlying.option_instrument_type", "OPTFUT"
        )
        futures_type_name = config_utils.get_property_value(
            "underlying.futures_instrument_type", "FUTCOM"
        )
        wanted_instruments = {option_type_name, futures_type_name}

        configured_lot_size = self._configured_lot_size(underlying_symbol)
        if not configured_lot_size:
            raise InstrumentMasterError(
                f"No contract_specs.{underlying_symbol}.lot_size configured. "
                "Dhan's master reports LOT_SIZE=1 for every MCX contract, so the "
                "real lot size must be supplied in conf/default-config.yaml."
            )
        tick_divisor = self._tick_divisor()

        warnings: List[str] = []
        rows: List[Dict[str, Any]] = []
        scanned = 0
        refreshed_at = utc_now()
        suspicious_lot_sizes = 0

        with open(path, "r", encoding="utf-8", errors="replace", newline="") as handle:
            reader = csv.DictReader(handle)
            missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
            if missing:
                raise InstrumentMasterError(
                    f"Instrument master is missing expected columns: {sorted(missing)}. "
                    f"Dhan may have changed the schema; got {reader.fieldnames}"
                )

            for record in reader:
                scanned += 1
                if record.get(COL_EXCH_ID, "").strip() != exchange_id:
                    continue
                if record.get(COL_UNDERLYING_SYMBOL, "").strip() != underlying_symbol:
                    continue
                instrument = record.get(COL_INSTRUMENT, "").strip()
                if instrument not in wanted_instruments:
                    continue

                security_id = record.get(COL_SECURITY_ID, "").strip()
                if not security_id:
                    continue

                option_type = (record.get(COL_OPTION_TYPE) or "").strip().upper()
                if option_type not in VALID_OPTION_TYPES:
                    option_type = None

                strike = _parse_decimal(record.get(COL_STRIKE))
                # Futures rows carry a placeholder strike (0 or -0.01).
                if option_type is None or (strike is not None and strike <= 0):
                    strike = None if option_type is None else strike
                if option_type is not None and (strike is None or strike <= 0):
                    warnings.append(f"Skipped option {security_id}: unusable strike")
                    continue

                master_lot_size = _parse_decimal(record.get(COL_LOT_SIZE))
                if master_lot_size is not None and master_lot_size > 1:
                    # If Dhan ever starts publishing a real MCX lot size, use it
                    # and say so rather than silently preferring the config.
                    lot_size = int(master_lot_size)
                    warnings.append(
                        f"Master now publishes LOT_SIZE={lot_size} for {security_id}; "
                        f"using it in preference to the configured {configured_lot_size}"
                    )
                else:
                    lot_size = configured_lot_size
                    suspicious_lot_sizes += 1

                tick_size = _parse_decimal(record.get(COL_TICK_SIZE))
                if tick_size is not None and tick_size > 0:
                    tick_size = tick_size / tick_divisor
                else:
                    tick_size = None

                underlying_scrip = _parse_decimal(record.get(COL_UNDERLYING_SECURITY_ID))

                rows.append(
                    {
                        "security_id": security_id,
                        "exchange_id": exchange_id,
                        "exchange_segment": exchange_segment,
                        "segment_code": segment_code,
                        "instrument_type": instrument,
                        "underlying_symbol": underlying_symbol,
                        "underlying_scrip": int(underlying_scrip) if underlying_scrip else None,
                        # DISPLAY_NAME is the descriptive, per-contract name
                        # ("CRUDEOIL 17 SEP 8350 CALL"); SYMBOL_NAME is just the
                        # underlying ("CRUDEOIL") and is the fallback only.
                        "trading_symbol": (
                            record.get(COL_DISPLAY_NAME) or record.get(COL_SYMBOL_NAME) or ""
                        ).strip()[:128],
                        "display_name": (record.get(COL_DISPLAY_NAME) or "").strip()[:160],
                        "expiry_date": _parse_date(record.get(COL_EXPIRY)),
                        "strike_price": strike,
                        "option_type": option_type,
                        "lot_size": lot_size,
                        "tick_size": tick_size,
                        "is_active": True,
                        "refreshed_at": refreshed_at,
                    }
                )

        if suspicious_lot_sizes:
            warnings.append(
                f"{suspicious_lot_sizes} rows had LOT_SIZE<=1 in the master (expected "
                f"for MCX); used contract_specs.{underlying_symbol}.lot_size="
                f"{configured_lot_size}"
            )
        if not rows:
            raise InstrumentMasterError(
                f"No {underlying_symbol} contracts found in the instrument master. "
                f"Scanned {scanned} rows."
            )

        return rows, scanned, warnings

    async def refresh(self, force: bool = False) -> RefreshResult:
        """Download if needed, parse, and upsert. Idempotent."""
        started = time.perf_counter()
        downloaded = await self.download(force=force)
        path = self._cache_path()
        if not os.path.exists(path):
            raise InstrumentMasterError(f"Instrument master not found at {path}")

        rows, scanned, warnings = self.parse(path)
        counts = await self.repository.upsert_many(rows)

        underlying_symbol = config_utils.get_property_value("underlying.symbol", "CRUDEOIL")
        deactivated = await self.repository.deactivate_missing(
            underlying_symbol, [row["security_id"] for row in rows]
        )

        expiries = sorted({row["expiry_date"] for row in rows if row["expiry_date"]})

        result = RefreshResult(
            downloaded=downloaded,
            source_path=path,
            rows_scanned=scanned,
            rows_matched=len(rows),
            inserted=counts["inserted"],
            updated=counts["updated"],
            unchanged=counts["unchanged"],
            deactivated=deactivated,
            expiries=expiries,
            duration_seconds=time.perf_counter() - started,
            warnings=warnings,
        )
        logger.info(
            "Instrument master refresh: matched=%s inserted=%s updated=%s "
            "unchanged=%s deactivated=%s in %.2fs",
            result.rows_matched, result.inserted, result.updated,
            result.unchanged, result.deactivated, result.duration_seconds,
        )
        return result
