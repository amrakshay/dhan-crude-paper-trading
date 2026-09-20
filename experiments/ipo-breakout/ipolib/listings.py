"""The study population: which symbols are IPOs, when they listed, and the
bars that follow.

Three sources are joined here and each answers a different question:

    data/listings.db      Dhan. Every NSE equity's whole price history. Says
                          WHEN a symbol first traded. Adjusted for corporate
                          actions (RESEARCH.md 4.4).
    data/master.csv       Dhan's public instrument master. Says what a ticker
                          is CALLED, which is how a company name is matched.
    data/ipo_calendar.csv ipocentral. Says which listings were IPOs, and the
                          issue price -- neither of which is in price data.

WHY THE JOIN IS NOT OPTIONAL. A first trading day is not an IPO. The same field
captures demergers (JIOFIN, ADANIGREEN, TIINDIA), which have no anchor
investors and no statutory lock-in, and data gaps (FORCEMOT first trades in
2019 in this data and has been listed for decades). A lock-in study run over
the unfiltered set would measure supply events that never happened.

HOW THE MATCH IS MADE, AND WHY IT IS SAFE. Company name similarity alone is
not trustworthy -- "Sona BLW Precision Forgings" is SONACOMS and "One 97
Communications" is PAYTM. The LISTING DATE is, though: a company that listed on
18 Nov 2021 has its first Dhan bar on 18 Nov 2021. So the date is the key and
the name is the tiebreaker, never the other way round. A candidate is accepted
only when the dates agree within `DATE_TOLERANCE_DAYS` AND the names are
similar enough; everything else is reported unmatched rather than guessed.
"""
from __future__ import annotations

import csv
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

from patlib.bars import Bars, from_rows

HERE = Path(__file__).resolve().parent.parent
LISTINGS_DB = HERE / "data" / "listings.db"
MASTER_CSV = HERE / "data" / "master.csv"
CALENDAR_CSV = HERE / "data" / "ipo_calendar.csv"

# NSE and the calendar can disagree by a day or two: BSE-only listings, a
# calendar entry keyed on the BSE date, an off-by-one around a holiday. Three
# calendar days is wide enough for those and far too narrow to pair the wrong
# company, because two IPOs listing within three days of each other with
# similar names does not happen.
DATE_TOLERANCE_DAYS = 3

# Below this the pair is reported for a human to look at, never auto-accepted.
NAME_SIMILARITY_FLOOR = 0.55

# NSE's exchange test scrips, which sort first and are not instruments.
TEST_SCRIP = re.compile(r"NSETEST|^TEST\d|DUMMY", re.I)

_NOISE = re.compile(
    r"\b(limited|ltd|private|pvt|india|indian|company|co|corporation|corp|"
    r"industries|enterprises|technologies|technology|holdings|services|"
    r"solutions|systems|the|and|of|ipo)\b", re.I)


def _normalise(name: str) -> str:
    name = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    name = _NOISE.sub(" ", name)
    return " ".join(name.split())


def _similarity(a: str, b: str) -> float:
    a, b = _normalise(a), _normalise(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # A short name fully contained in a longer one is a strong signal that
    # SequenceMatcher undersells: "sona blw precision forgings" vs "sona".
    if a in b or b in a:
        return max(0.90, SequenceMatcher(None, a, b).ratio())
    return SequenceMatcher(None, a, b).ratio()


@dataclass(frozen=True)
class Listing:
    """One IPO, matched to a tradable symbol and its bars."""

    symbol: str
    security_id: str
    company: str                 # as the calendar names it
    display_name: str            # as Dhan names it
    listing_date: date           # the first bar Dhan serves
    calendar_date: date          # what the IPO calendar says
    issue_price: float | None    # UNADJUSTED, from the calendar
    listing_return_pct: float | None  # UNADJUSTED, from the calendar
    n_bars: int
    name_similarity: float

    @property
    def year(self) -> int:
        return self.listing_date.year


def _connect(db_path: Path = LISTINGS_DB) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(
            f"{db_path} not found -- run scripts/build_universe.py first")
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def load_bars(symbol: str, db_path: Path = LISTINGS_DB) -> Bars:
    """One symbol's daily bars, oldest first. Bar 0 IS the listing bar."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT bar_date, open, high, low, close, volume FROM daily_bars "
            "WHERE symbol = ? ORDER BY bar_date", (symbol,)).fetchall()
    finally:
        conn.close()
    return from_rows(symbol, rows)


def first_bars(db_path: Path = LISTINGS_DB) -> dict[str, tuple[date, int, str]]:
    """symbol -> (first bar date, bar count, security id), successes only."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT symbol, first_bar, n_bars, security_id FROM securities "
            "WHERE status = 'ok' AND first_bar IS NOT NULL").fetchall()
    finally:
        conn.close()
    return {s: (datetime.strptime(f, "%Y-%m-%d").date(), n, sid)
            for s, f, n, sid in rows if not TEST_SCRIP.search(s)}


def _display_names(db_path: Path = LISTINGS_DB) -> dict[str, str]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT symbol, COALESCE(display_name, symbol_name, symbol) "
            "FROM securities").fetchall()
    finally:
        conn.close()
    return dict(rows)


def read_calendar(path: Path = CALENDAR_CSV) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found -- run scripts/fetch_ipo_calendar.py first")
    out = []
    with path.open() as handle:
        for row in csv.DictReader(handle):
            out.append({
                "company": row["company"],
                "listing_date": datetime.strptime(
                    row["listing_date"], "%Y-%m-%d").date(),
                "issue_price": float(row["issue_price"]) if row["issue_price"] else None,
                "listing_return_pct": (float(row["listing_return_pct"])
                                       if row["listing_return_pct"] else None),
            })
    return out


def build(min_bars: int = 0, db_path: Path = LISTINGS_DB,
          calendar_path: Path = CALENDAR_CSV) -> tuple[list[Listing], list[dict]]:
    """Match the IPO calendar to tradable symbols.

    Returns (matched listings, unmatched calendar entries). The unmatched list
    is returned rather than logged because its SIZE is a result: it is the
    residual survivorship hole -- IPOs that listed and are no longer in the
    instrument master because they were delisted, merged away or renamed
    beyond recognition.
    """
    firsts = first_bars(db_path)
    names = _display_names(db_path)

    # Index candidate symbols by listing date so the date does the work and
    # the name only breaks ties.
    by_date: dict[date, list[str]] = {}
    for symbol, (first, _n, _sid) in firsts.items():
        by_date.setdefault(first, []).append(symbol)

    conn = _connect(db_path)
    try:
        security_ids = dict(conn.execute(
            "SELECT symbol, security_id FROM securities").fetchall())
    finally:
        conn.close()

    listings: list[Listing] = []
    unmatched: list[dict] = []
    claimed: set[str] = set()

    for entry in read_calendar(calendar_path):
        candidates: list[str] = []
        for offset in range(-DATE_TOLERANCE_DAYS, DATE_TOLERANCE_DAYS + 1):
            candidates += by_date.get(entry["listing_date"] + timedelta(days=offset), [])
        candidates = [c for c in candidates if c not in claimed]

        if not candidates:
            unmatched.append({**entry, "reason": "no symbol first traded near that date"})
            continue

        scored = sorted(
            ((_similarity(entry["company"], names.get(c, c)), c) for c in candidates),
            reverse=True)
        # A symbol name often beats the display name ("PAYTM" vs "One 97"), so
        # score against both and keep the better.
        scored = sorted(
            ((max(score, _similarity(entry["company"], symbol)), symbol)
             for score, symbol in scored), reverse=True)
        best_score, best = scored[0]

        if best_score < NAME_SIMILARITY_FLOOR:
            unmatched.append({
                **entry,
                "reason": f"date matched {len(candidates)} symbol(s) but names "
                          f"disagree (best {scored[0][1]} @ {best_score:.2f})"})
            continue

        claimed.add(best)
        first, n_bars, _sid = firsts[best]
        listings.append(Listing(
            symbol=best,
            security_id=security_ids.get(best, ""),
            company=entry["company"],
            display_name=names.get(best, best),
            listing_date=first,
            calendar_date=entry["listing_date"],
            issue_price=entry["issue_price"],
            listing_return_pct=entry["listing_return_pct"],
            n_bars=n_bars,
            name_similarity=best_score,
        ))

    if min_bars:
        listings = [listing for listing in listings if listing.n_bars >= min_bars]
    return sorted(listings, key=lambda listing: listing.listing_date), unmatched


# --------------------------------------------------------------------------
# the listing bar, which is not a normal bar (RESEARCH.md 4.3)
# --------------------------------------------------------------------------
def listing_bar_stats(bars: Bars) -> dict:
    """What the first bar looks like relative to the ones after it.

    Used to MEASURE the claims in RESEARCH.md 4.2-4.3 rather than assume them:
    how often the listing bar closes exactly on a +-5% or +-20% circuit, and
    how far its volume sits above everything that follows.
    """
    if len(bars) < 2:
        return {}
    open_, high, low, close = (bars.open[0], bars.high[0], bars.low[0], bars.close[0])
    later_volume = bars.volume[1:21]
    stats = {
        "range_pct": (high - low) / open_ * 100 if open_ else np.nan,
        "close_vs_open_pct": (close / open_ - 1) * 100 if open_ else np.nan,
        "high_vs_open_pct": (high / open_ - 1) * 100 if open_ else np.nan,
        "low_vs_open_pct": (low / open_ - 1) * 100 if open_ else np.nan,
        "volume_vs_next20_median": (
            bars.volume[0] / np.median(later_volume)
            if len(later_volume) and np.median(later_volume) > 0 else np.nan),
        "closed_at_high": bool(np.isclose(close, high, rtol=1e-6)),
        "closed_at_low": bool(np.isclose(close, low, rtol=1e-6)),
    }
    # A band is "hit" when the extreme sits on it to within a tick's worth of
    # rounding. 0.2 percentage points is generous enough for paise rounding on
    # a three-figure price and far tighter than any real move would land.
    for band in (5.0, 20.0):
        stats[f"hit_up_{int(band)}"] = abs(stats["high_vs_open_pct"] - band) < 0.2
        stats[f"hit_down_{int(band)}"] = abs(stats["low_vs_open_pct"] + band) < 0.2
    return stats
