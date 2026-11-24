"""Downloads NSE Clearing's daily SPAN risk-parameter file. INBOUND data only.

The ONLY module in this codebase that names `nsearchives.nseindia.com`
(asserted by tests/test_outbound_hosts.py). Approved as an outbound host on
2026-11-24 for OPTIONS_TRADER_PLAN.md decision D1. It fetches one public file
a day and sends nothing: no credential, no account, no order. It is not Dhan,
so tests/test_no_real_orders.py is deliberately unchanged by it.

**It must send a browser-like User-Agent.** Verified 2026-11-24: without one
NSE's archive drops the connection (curl reports status 000); with one,
`nsccl.20261123.s.zip` came back 200, 8.0 MB. NSE does not document this.

The file for a session is published in the evening (the 2026-11-24 file was
still 404 at 21:21 IST; the 2026-11-23 one carries `<created>202611231709`),
so the newest file is usually the previous session's until late evening.

It also fetches NSE's daily F&O CONTRACT file (`NSE_FO_contract_DDMMYYYY.csv.gz`)
from the same host, for the per-order freeze quantity and the day's price
range (OPTIONS_TRADER_PLAN.md D6, approved 2026-11-25). NSE stopped publishing
`qtyfreeze` separately on 2024-04-15 (circular NSE/FAOP/61157, which the old
URL now redirects to) and named this file as its replacement. Verified
2026-11-25: the 23-Nov file is 2.1 MB, 123,615 rows, and its `FinInstrmId` is
Dhan's security id (NIFTY 01 DEC 25500 CE = 47810 in both).
"""
from datetime import date
from typing import Optional

import httpx

from src import config_utils
from src.logging_config import get_logger

logger = get_logger("options.span_client")

SPAN_URL_TEMPLATE = (
    "https://nsearchives.nseindia.com/archives/nsccl/span/nsccl.{yyyymmdd}.{variant}.zip"
)
# `s` end of day; `i1` beginning of day (published the evening before);
# `i2`..`i5` intraday snapshots. Verified against the archive 2026-11-24.
VARIANTS = ("s", "i1", "i2", "i3", "i4", "i5")

# NSE drops requests without one. A generic browser string, naming nobody.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

CONTRACT_URL_TEMPLATE = (
    "https://nsearchives.nseindia.com/content/fo/NSE_FO_contract_{ddmmyyyy}.csv.gz"
)

MINIMUM_PLAUSIBLE_BYTES = 1_000_000
MINIMUM_CONTRACT_FILE_BYTES = 200_000


class SpanDownloadError(Exception):
    pass


class SpanNotPublished(SpanDownloadError):
    """404: that session's file is not up yet, or it was not a trading day."""


def url_for(day: date, variant: str = "s") -> str:
    if variant not in VARIANTS:
        raise ValueError(f"Unknown SPAN file variant {variant!r}")
    return SPAN_URL_TEMPLATE.format(yyyymmdd=day.strftime("%Y%m%d"), variant=variant)


async def download(
    day: date, destination: str, timeout: Optional[int] = None, variant: str = "s"
) -> int:
    """Fetch one session's zip to `destination`. Returns its size in bytes."""
    timeout = timeout or config_utils.get_property_value_int("dhan.http_timeout_seconds", 30)
    url = url_for(day, variant)
    headers = {"User-Agent": BROWSER_USER_AGENT, "Accept": "*/*"}
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
            response = await client.get(url)
    except httpx.HTTPError as error:
        # The exception TYPE only: nothing secret is in play here, but the
        # codebase's rule for outbound errors is the same everywhere.
        raise SpanDownloadError(f"NSE SPAN download failed: {type(error).__name__}") from error
    if response.status_code == 404:
        raise SpanNotPublished(f"No {variant} SPAN file published for {day.isoformat()} yet")
    if response.status_code != 200:
        raise SpanDownloadError(f"NSE SPAN download returned HTTP {response.status_code}")
    body = response.content
    if len(body) < MINIMUM_PLAUSIBLE_BYTES:
        raise SpanDownloadError(
            f"NSE SPAN file for {day.isoformat()} looks truncated ({len(body)} bytes)"
        )
    with open(destination, "wb") as handle:
        handle.write(body)
    logger.info("Downloaded NSE SPAN %s file for %s (%.1f MB)", variant, day, len(body) / (1 << 20))
    return len(body)


def contract_url_for(day: date) -> str:
    return CONTRACT_URL_TEMPLATE.format(ddmmyyyy=day.strftime("%d%m%Y"))


async def download_contract_file(day: date, destination: str, timeout: Optional[int] = None) -> int:
    """Fetch one session's F&O contract file to `destination`."""
    timeout = timeout or config_utils.get_property_value_int("dhan.http_timeout_seconds", 30)
    headers = {"User-Agent": BROWSER_USER_AGENT, "Accept": "*/*"}
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, headers=headers) as client:
            response = await client.get(contract_url_for(day))
    except httpx.HTTPError as error:
        raise SpanDownloadError(f"NSE contract file download failed: {type(error).__name__}") from error
    if response.status_code == 404:
        raise SpanNotPublished(f"No F&O contract file published for {day.isoformat()} yet")
    if response.status_code != 200:
        raise SpanDownloadError(f"NSE contract file download returned HTTP {response.status_code}")
    body = response.content
    if len(body) < MINIMUM_CONTRACT_FILE_BYTES:
        raise SpanDownloadError(
            f"NSE contract file for {day.isoformat()} looks truncated ({len(body)} bytes)"
        )
    with open(destination, "wb") as handle:
        handle.write(body)
    logger.info("Downloaded NSE F&O contract file for %s (%.1f MB)", day, len(body) / (1 << 20))
    return len(body)
