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
"""
from datetime import date
from typing import Optional

import httpx

from src import config_utils
from src.logging_config import get_logger

logger = get_logger("options.span_client")

SPAN_URL_TEMPLATE = (
    "https://nsearchives.nseindia.com/archives/nsccl/span/nsccl.{yyyymmdd}.s.zip"
)

# NSE drops requests without one. A generic browser string, naming nobody.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

MINIMUM_PLAUSIBLE_BYTES = 1_000_000


class SpanDownloadError(Exception):
    pass


class SpanNotPublished(SpanDownloadError):
    """404: that session's file is not up yet, or it was not a trading day."""


def url_for(day: date) -> str:
    return SPAN_URL_TEMPLATE.format(yyyymmdd=day.strftime("%Y%m%d"))


async def download(day: date, destination: str, timeout: Optional[int] = None) -> int:
    """Fetch one day's zip to `destination`. Returns its size in bytes."""
    timeout = timeout or config_utils.get_property_value_int("dhan.http_timeout_seconds", 30)
    url = url_for(day)
    headers = {"User-Agent": BROWSER_USER_AGENT, "Accept": "*/*"}
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
            response = await client.get(url)
    except httpx.HTTPError as error:
        # The exception TYPE only: nothing secret is in play here, but the
        # codebase's rule for outbound errors is the same everywhere.
        raise SpanDownloadError(f"NSE SPAN download failed: {type(error).__name__}") from error
    if response.status_code == 404:
        raise SpanNotPublished(f"No SPAN file published for {day.isoformat()} yet")
    if response.status_code != 200:
        raise SpanDownloadError(f"NSE SPAN download returned HTTP {response.status_code}")
    body = response.content
    if len(body) < MINIMUM_PLAUSIBLE_BYTES:
        raise SpanDownloadError(
            f"NSE SPAN file for {day.isoformat()} looks truncated ({len(body)} bytes)"
        )
    with open(destination, "wb") as handle:
        handle.write(body)
    logger.info("Downloaded NSE SPAN file for %s (%.1f MB)", day, len(body) / (1 << 20))
    return len(body)
