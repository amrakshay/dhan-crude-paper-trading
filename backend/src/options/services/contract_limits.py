"""NSE's per-contract order limits: freeze quantity and the day's price range.

OPTIONS_TRADER_PLAN.md D6, approved 2026-11-25. Dhan rejects an F&O order the
exchange would reject: one above the contract's FREEZE QUANTITY, or a limit
price outside the day's OPERATING RANGE. Both come from NSE's daily F&O
contract file (see nse_span_client.py), not from configuration:

  * `MaxTradQty` -- NSE lists 1801 for NIFTY and 20001 for RELIANCE. An order
    must be BELOW it, so the most one order may carry is that minus one
    (1,800 for NIFTY: NSE's published NIFTY freeze limit).
  * `MinPric` / `MaxPric` -- the range in paise, for THAT session.

Two deliberate softenings, both because a refusal the exchange would not make
is worse than an acceptance it would:

  * The price range is applied only from the file that SETS today's range.
    NSE publishes the file named for session D in D's evening (20-Nov at
    17:47 IST, 23-Nov at 19:02, read from Last-Modified on 2026-11-25); its
    base prices are D's closes, so its range is for the NEXT session. Today's
    range therefore comes from the newest file dated BEFORE today, and only
    if that file is recent (`MAX_FILE_AGE_DAYS`, which spans a weekend plus a
    holiday). A file dated today describes tomorrow. (Found live on
    2026-11-25: the first version waited for a file dated today, which NSE
    never publishes during the session, so the range never applied.)
  * NSE widens an option's range during the day. Where the live last price
    is already outside the file's range, the range has plainly moved, so it
    is SCALED by last price / base price rather than applied as published.
    (Found live on 2026-11-25: after a 2.3% gap-up the NIFTY 26300 CE traded
    at 110 against a file range of 0.05-70.15. The first version stood aside
    entirely there, and a buy limit at 5,000 went through.) The scaled range
    is an approximation of NSE's flexing, which it does not publish.

The freeze quantity is stable from session to session, so any recent file
serves for it.

Like the SPAN store, `get()` never touches the network: the `span-refresher`
task refreshes this file on the same pass.
"""
import asyncio
import csv
import glob
import gzip
import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, Optional

from src.core.time_utils import ist_now
from src.logging_config import get_logger
from src.options.services import nse_span_client

logger = get_logger("options.contract_limits")

FILE_PATTERN = re.compile(r"NSE_FO_contract_(\d{8})\.csv\.gz$")
LOOKBACK_DAYS = 7
MAX_FILE_AGE_DAYS = 4


def sets_range_for(file_date: date, today: date) -> bool:
    """Whether the file published in `file_date`'s evening sets today's range."""
    return file_date < today and (today - file_date).days <= MAX_FILE_AGE_DAYS


@dataclass(frozen=True)
class ContractLimits:
    max_order_quantity: int
    min_price: Decimal
    max_price: Decimal
    session: date
    base_price: Optional[Decimal] = None


def parse(path: str, session: date) -> Dict[str, ContractLimits]:
    """Option rows only, keyed by security id (NSE's FinInstrmId)."""
    found: Dict[str, ContractLimits] = {}
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("OptnTp") not in ("CE", "PE"):
                continue
            try:
                freeze = int(float(row["MaxTradQty"]))
                low = Decimal(row["MinPric"]) / 100
                high = Decimal(row["MaxPric"]) / 100
                base = Decimal(row["BasePric"]) / 100 if row.get("BasePric") else None
            except (KeyError, ValueError, ArithmeticError):
                continue
            if freeze <= 1:
                continue
            found[row["FinInstrmId"]] = ContractLimits(freeze - 1, low, high, session, base)
    return found


def _cache_dir() -> str:
    from src.options.services.span_store import _cache_dir as span_cache

    return span_cache()


class ContractLimitsStore:
    def __init__(self) -> None:
        self.data: Dict[str, ContractLimits] = {}
        self.session: Optional[date] = None
        self.loaded_from: Optional[str] = None
        self.last_error: Optional[str] = None
        self._lock = asyncio.Lock()

    def get(self, security_id) -> Optional[ContractLimits]:
        return self.data.get(str(security_id))

    def status(self) -> Dict[str, Any]:
        return {
            "session": self.session.isoformat() if self.session else None,
            "contracts": len(self.data),
            "file": os.path.basename(self.loaded_from) if self.loaded_from else None,
            "error": self.last_error,
        }

    def _local(self) -> Dict[date, str]:
        found = {}
        for path in glob.glob(os.path.join(_cache_dir(), "NSE_FO_contract_*.csv.gz")):
            match = FILE_PATTERN.search(path)
            if match:
                raw = match.group(1)
                found[date(int(raw[4:]), int(raw[2:4]), int(raw[:2]))] = path
        return found

    async def refresh(self, download: bool = True) -> Dict[str, Any]:
        async with self._lock:
            os.makedirs(_cache_dir(), exist_ok=True)
            today = ist_now().date()
            local = self._local()
            if download:
                for offset in range(LOOKBACK_DAYS):
                    day = today - timedelta(days=offset)
                    if day.weekday() >= 5:
                        continue
                    if day in local:
                        break
                    destination = os.path.join(_cache_dir(), f"NSE_FO_contract_{day:%d%m%Y}.csv.gz")
                    try:
                        await nse_span_client.download_contract_file(day, destination)
                        local[day] = destination
                        break
                    except nse_span_client.SpanNotPublished:
                        continue
                    except nse_span_client.SpanDownloadError as error:
                        self.last_error = str(error)
                        logger.warning("%s", error)
                        break
            usable = [day for day in local if day <= today]
            if not usable:
                if self.last_error is None:
                    self.last_error = "No NSE F&O contract file is available yet."
                return self.status()
            newest = max(usable)
            if newest != self.session:
                try:
                    self.data = await asyncio.to_thread(parse, local[newest], newest)
                    self.session = newest
                    self.loaded_from = local[newest]
                    self.last_error = None
                    logger.info("NSE contract limits for %s in force: %s option contracts", newest, len(self.data))
                except Exception as error:  # noqa: BLE001 - keep the old file in force
                    self.last_error = f"Could not parse the {newest} contract file: {type(error).__name__}"
                    logger.exception("Parsing the NSE contract file for %s failed", newest)
            return self.status()


_store: Optional[ContractLimitsStore] = None


def get_contract_limits() -> ContractLimitsStore:
    global _store
    if _store is None:
        _store = ContractLimitsStore()
    return _store


def set_contract_limits_for_tests(data: Optional[Dict[str, ContractLimits]], session: Optional[date] = None) -> None:
    store = get_contract_limits()
    store.data = data or {}
    store.session = session


# --- the rule ----------------------------------------------------------------------
def largest_order(instrument, limits: Optional[ContractLimits]) -> Optional[int]:
    """The largest whole-lot quantity one order may carry, or None if unknown."""
    if limits is None:
        return None
    lot = max(int(instrument.lot_size or 1), 1)
    return (limits.max_order_quantity // lot) * lot or None


def current_range(limits: ContractLimits, book, security_id):
    """(low, high, note): the file's range, or that range scaled to the live
    price when the market has already traded outside it."""
    low, high = limits.min_price, limits.max_price
    ltp = None
    if book is not None:
        row = book.get(str(security_id)) or {}
        ltp = row.get("ltp")
    if not ltp or low <= Decimal(str(ltp)) <= high:
        return low, high, ""
    if not limits.base_price or limits.base_price <= 0:
        return None, None, ""   # moved, and nothing to scale by: do not guess
    scale = Decimal(str(ltp)) / limits.base_price
    tick = Decimal("0.05")
    scaled_low = max(tick, (low * scale / tick).quantize(Decimal(1)) * tick)
    scaled_high = (high * scale / tick).quantize(Decimal(1)) * tick
    return scaled_low, scaled_high, " (the published range, scaled to today's price: NSE has widened it)"


def refusal(instrument, quantity: int, order_type: str, limit_price, today: date, book=None,
            check_freeze: bool = True, check_band: bool = True) -> Optional[str]:
    """Why the exchange (and so Dhan) would reject this order, or None."""
    limits = get_contract_limits().get(instrument.security_id)
    if limits is None:
        return None
    if check_freeze and int(quantity) > limits.max_order_quantity:
        biggest = largest_order(instrument, limits)
        lots = biggest // max(int(instrument.lot_size or 1), 1) if biggest else 0
        return (
            f"{instrument.trading_symbol}: {int(quantity):,} is above NSE's freeze limit of "
            f"{limits.max_order_quantity:,} per order. Dhan rejects it; split it into orders "
            f"of at most {lots} lots ({biggest:,})."
        )
    if (
        check_band
        and str(order_type).upper() == "LIMIT"
        and limit_price is not None
        and sets_range_for(limits.session, today)
    ):
        price = Decimal(str(limit_price))
        low, high, note = current_range(limits, book, instrument.security_id)
        if low is None or low <= price <= high:
            return None
        return (
            f"{instrument.trading_symbol}: limit {price} is outside today's NSE price range "
            f"{low}-{high}{note}. Dhan rejects it."
        )
    return None
