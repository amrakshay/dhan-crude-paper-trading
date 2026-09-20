"""Fetch the mainboard IPO calendar: which listings were IPOs, and at what price.

WHY THIS EXISTS. `build_universe.py` gives every NSE equity's first trading day,
but a first trading day is not an IPO. The same field also captures demergers
(JIOFIN, ADANIGREEN, TIINDIA, MSUMI...), which have no anchor investors and no
statutory lock-in, and plain data gaps (FORCEMOT has traded for decades and
still shows a 2019 first bar). Mixing those into a lock-in study would measure
supply events that never existed.

This is the SECOND external host in the experiment, named here and nowhere
else -- the same containment discipline `test_outbound_hosts.py` applies to the
application. It is inbound data: fetched, never sent to.

WHAT IT ADDS BEYOND CLASSIFICATION. The issue price, which Dhan cannot supply.
Dhan's history is corporate-action adjusted, so an adjusted rupee level cannot
be compared with an unadjusted issue price; the LISTING RETURN published here
is computed from unadjusted prices at the time and is therefore the only sound
"listed at a premium of X%" figure available.

    python3 scripts/fetch_ipo_calendar.py                # 2016..current year
    python3 scripts/fetch_ipo_calendar.py --years 2021 2022

Output: data/ipo_calendar.csv (year, company, listing_date, issue_price,
listing_return_pct).
"""
from __future__ import annotations

import argparse
import csv
import html
import re
import sys
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "data" / "ipo_calendar.csv"

# The only host this module names.
IPO_CALENDAR_HOST = "ipocentral.in"
YEAR_URL = "https://ipocentral.in/ipo-{year}/"

FIRST_YEAR = 2016


def _cells(row_html: str) -> list[str]:
    return [html.unescape(re.sub("<[^>]+>", "", c)).strip()
            for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.S)]


def _parse_date(text: str) -> date | None:
    """The pages use both MM/DD/YYYY and MM-DD-YYYY, zero-padded or not."""
    text = text.strip().replace("-", "/")
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_number(text: str) -> float | None:
    """`(6.24)` is how these pages write -6.24."""
    text = text.strip().replace(",", "").replace("%", "")
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    try:
        value = float(text)
    except ValueError:
        return None
    return -value if negative else value


def fetch_year(year: int) -> list[dict]:
    request = urllib.request.Request(
        YEAR_URL.format(year=year), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        page = response.read().decode("utf-8", errors="replace")

    rows = []
    for match in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        cells = _cells(match)
        if len(cells) < 4:
            continue
        listing_date = _parse_date(cells[1])
        if listing_date is None:            # header, or a stray table
            continue
        # The page's own year pages occasionally carry a stray row from the
        # neighbouring year; keep the page's year authoritative.
        if listing_date.year != year:
            continue
        rows.append({
            "year": year,
            "company": cells[0],
            "listing_date": listing_date.isoformat(),
            "issue_price": _parse_number(cells[2]),
            "listing_return_pct": _parse_number(cells[3]),
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, nargs="*")
    args = parser.parse_args()

    years = args.years or list(range(FIRST_YEAR, date.today().year + 1))
    all_rows: list[dict] = []
    for year in years:
        try:
            rows = fetch_year(year)
        except Exception as exc:  # noqa: BLE001 -- one bad year must not end the fetch
            print(f"{year}: FAILED {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        print(f"{year}: {len(rows)} IPOs", flush=True)
        all_rows.extend(rows)
        time.sleep(1.0)

    if not all_rows:
        sys.exit("nothing fetched")

    # De-duplicate on (company, listing_date): a company can appear on two
    # year pages when its issue straddles a year end.
    seen: dict[tuple, dict] = {}
    for row in all_rows:
        seen[(row["company"].lower(), row["listing_date"])] = row

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["year", "company", "listing_date", "issue_price",
                        "listing_return_pct"])
        writer.writeheader()
        for row in sorted(seen.values(), key=lambda r: r["listing_date"]):
            writer.writerow(row)
    print(f"\n{OUT.name}: {len(seen)} IPOs, "
          f"{min(r['listing_date'] for r in seen.values())} .. "
          f"{max(r['listing_date'] for r in seen.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
