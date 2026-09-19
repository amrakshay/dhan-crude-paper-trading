"""One source row -> one `SourceIpo`. Pure, and the piece the fixture pins.

Kept apart from `ipo_source_client` so the markup dependency can be tested
without the network, against a saved copy of the real response
(`tests/fixtures/ipo_gmp_report.json`, captured 2026-09-19). The client's
module docstring lists the exact markup depended on and why.

**Every parse is defensive.** A field that stops matching yields `None`, never
a wrong number: this feature's failure mode must be "no GMP, and it says so",
not a plausible figure nobody can trace. `Ipo.gmp` being `None` already means
"the source prints `--`", so the two are the same shape by design.
"""
import html
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

from src.ipo.database.db_models.ipo_model import (
    BOARD_MAINBOARD,
    BOARD_SME,
    STATUS_CLOSED,
    STATUS_LISTED,
    STATUS_OPEN,
    STATUS_UPCOMING,
)

# --- the source's own vocabulary -------------------------------------------
# `~IPO_Category`: "IPO" means mainboard. Anything else is not.
SOURCE_CATEGORY_MAINBOARD = "IPO"

# `~ipo_status1`. LP and LN are listed at a premium and listed at a discount --
# both are LISTED here, because the sign is already in the numbers.
_STATUS_MAP = {
    "U": STATUS_UPCOMING,
    "O": STATUS_OPEN,
    "C": STATUS_CLOSED,
    # "Closing today" is a display state of an OPEN subscription, not a closed
    # one -- an IPO closing today can still be applied for, which is the whole
    # point of this feature.
    "CT": STATUS_OPEN,
    "LP": STATUS_LISTED,
    "LN": STATUS_LISTED,
}

# `GMP`: `&#8377;<b>61</b> (3.42%)`. The bold number is the CURRENT premium.
_GMP_BOLD = re.compile(r"<b>\s*(-?[\d.,]+)\s*</b>")
# `Name`: `L@154.00 (10%)` -- the listing price, present only once listed.
_LISTING_PRICE = re.compile(r"L@\s*(-?[\d.,]+)")
# `Updated-On`: `<small ...><b>19-Sep 22:37</b></small>`, no year.
_UPDATED_ON = re.compile(
    r"<b>\s*(\d{1,2})-([A-Za-z]{3})\s+(\d{1,2}):(\d{2})\s*</b>"
)
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_TAGS = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class SourceIpo:
    """What the source says about one IPO, in this application's vocabulary."""

    source_id: int
    company_name: str
    board: str
    status: str

    issue_price: Optional[Decimal]
    issue_price_text: Optional[str]
    lot_size: Optional[int]

    open_date: Optional[date]
    close_date: Optional[date]
    listing_date: Optional[date]
    listing_price: Optional[Decimal]

    gmp: Optional[Decimal]
    gmp_percent: Optional[Decimal]
    # IST, naive, as the source prints it. Converted to UTC by the caller --
    # this module does no timezone arithmetic so the parse stays testable
    # against the literal text.
    gmp_updated_at_ist: Optional[datetime]

    source_path: Optional[str]

    @property
    def is_mainboard(self) -> bool:
        return self.board == BOARD_MAINBOARD


def _text(value: Any) -> str:
    """HTML fragment -> the words in it."""
    if value is None:
        return ""
    return html.unescape(_TAGS.sub(" ", str(value))).strip()


def _decimal(raw: Any) -> Optional[Decimal]:
    if raw is None:
        return None
    cleaned = str(raw).replace(",", "").strip()
    if not cleaned or cleaned in {"-", "--", "NA", "N/A"}:
        return None
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _int(raw: Any) -> Optional[int]:
    value = _decimal(raw)
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, OverflowError):
        return None


def _iso_date(raw: Any) -> Optional[date]:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw).strip()[:10])
    except ValueError:
        return None


def parse_updated_on(raw: Any, *, today: date) -> Optional[datetime]:
    """`19-Sep 22:37` -> a naive IST datetime.

    **THE SOURCE PUBLISHES NO YEAR**, so one has to be chosen, and choosing
    wrongly is how a fresh reading is labelled a year stale. The rule: assume
    the current year, and if that lands more than a few days in the FUTURE,
    assume the previous one -- which is the December-to-January case. A
    timestamp that is merely in the past needs no correction; GMP readings are
    never post-dated.
    """
    if not raw:
        return None
    match = _UPDATED_ON.search(str(raw))
    if match is None:
        return None
    day, month_name, hour, minute = match.groups()
    month = _MONTHS.get(month_name.lower())
    if month is None:
        return None
    try:
        candidate = datetime(today.year, month, int(day), int(hour), int(minute))
    except ValueError:
        return None
    if (candidate.date() - today).days > 2:
        try:
            candidate = candidate.replace(year=today.year - 1)
        except ValueError:  # 29 February in a non-leap previous year
            return None
    return candidate


def parse_row(row: Dict[str, Any], *, today: date) -> Optional[SourceIpo]:
    """One report row. `None` when it carries no usable identity.

    `today` is passed in rather than read from the clock so the parse is a pure
    function of its inputs and the fixture test means something in a year's
    time.
    """
    source_id = _int(row.get("~id"))
    if source_id is None:
        return None

    name = str(row.get("~ipo_name") or "").strip()
    if not name:
        # The plain field is missing; fall back to the words inside the link
        # rather than dropping a real IPO over a renamed column.
        name = _text(row.get("Name")).split("  ")[0].strip()
    if not name:
        return None

    category = str(row.get("~IPO_Category") or row.get("~ipo_category1") or "").strip()
    board = BOARD_MAINBOARD if category == SOURCE_CATEGORY_MAINBOARD else BOARD_SME

    status = _STATUS_MAP.get(
        str(row.get("~ipo_status1") or "").strip().upper(), STATUS_UPCOMING
    )

    price_text = _text(row.get("Price (₹)")) or None

    listing_price = None
    listing_match = _LISTING_PRICE.search(str(row.get("Name") or ""))
    if listing_match:
        listing_price = _decimal(listing_match.group(1))

    gmp = None
    gmp_match = _GMP_BOLD.search(str(row.get("GMP") or ""))
    if gmp_match:
        gmp = _decimal(gmp_match.group(1))

    return SourceIpo(
        source_id=source_id,
        company_name=name,
        board=board,
        status=status,
        issue_price=_decimal(price_text),
        issue_price_text=price_text,
        lot_size=_int(_text(row.get("Lot"))),
        open_date=_iso_date(row.get("~Srt_Open")),
        close_date=_iso_date(row.get("~Srt_Close")),
        # The source spells this key `~Str_Listing` -- Str, not Srt, unlike its
        # three siblings. Copied from the payload rather than corrected.
        listing_date=_iso_date(row.get("~Str_Listing")),
        listing_price=listing_price,
        gmp=gmp,
        gmp_percent=_decimal(row.get("~gmp_percent_calc")),
        gmp_updated_at_ist=parse_updated_on(row.get("Updated-On"), today=today),
        source_path=(str(row.get("~urlrewrite_folder_name") or "").strip() or None),
    )


def parse_rows(rows, *, today: date):
    """Every row that parses. Unparseable rows are dropped, not guessed at."""
    parsed = []
    for row in rows:
        item = parse_row(row, today=today)
        if item is not None:
            parsed.append(item)
    return parsed


def mainboard_only(items):
    """SME issues are filtered out here and never stored."""
    return [item for item in items if item.is_mainboard]
