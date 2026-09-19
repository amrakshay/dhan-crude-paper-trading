"""The IPO GMP source, and the ONLY module allowed to name its host.

Same containment `telegram_client.py` gives `api.telegram.org` and
`dhan_token_client.py` gives `/RenewToken`, applied to the third party this
feature reads: one module names the host, one closed set of URLs, and
`tests/test_outbound_hosts.py` asserts nothing else names it. Root `CLAUDE.md`
section 1's scanner (`test_no_real_orders.py`) is deliberately untouched -- it
guards Dhan's hosts, and widening it would blur what it is for.

**WHAT THE PAGE ACTUALLY SERVES.** Established by fetching it on 2026-09-19,
not assumed. `https://www.investorgain.com/report/ipo-gmp-live/331/` is a
Next.js application that ships an EMPTY table (`<tbody>` says "No data
available") and fills it client-side. The table is built from a JSON endpoint
on a DIFFERENT host:

    https://webnodejs.investorgain.com/cloud/v2/report/data-read/331/1/9/2026/2026-27/0/all?search=

* `200`, `application/json`, no authentication, no cookie, no Referer check.
* The `/1/9/2026/2026-27/0/` segments are page / month / year / financial-year.
  They do NOT filter the payload -- requesting month 1 returns the same 50
  rows as month 9 -- so they are sent as the live site sends them and nothing
  is inferred from them.
* The `v=22-49` query parameter is the site's own cache-buster and is
  optional; it is omitted here rather than pinned to a version that will move.

So this is the JSON path, preferred over parsing the page. It is NOT, however,
clean JSON: several fields carry HTML fragments, and three things this feature
needs are only available inside them.

**THE MARKUP THIS MODULE DEPENDS ON**, so a break is diagnosable when the site
changes (each is parsed defensively -- a field that stops matching becomes
`None`, never a wrong number):

1. `GMP` -- the CURRENT premium, as `&#8377;<b>61</b> (3.42%)`. The `<b>` is
   the number. NOTE: the sibling field `~max_gmp1` is the HIGHEST premium this
   IPO has ever shown, not the current one. They agree often enough to look
   interchangeable and are not.
2. `Name` -- for a listed IPO, the listing price as `L@154.00 (10%)` inside a
   `text-success` / `text-danger` span. Absent before listing.
3. `Updated-On` -- when the source last moved the GMP, as `19-Sep 22:37`
   inside a `<small><b>`. NO YEAR; see `ipo_parser` for how one is chosen.

Everything else is read from the plain `~`-prefixed fields, which the site uses
for sorting and which carry ISO dates (`~Srt_Close`, `~Str_Listing`), the
category (`~IPO_Category`), the status (`~ipo_status1`) and the unescaped name
(`~ipo_name`).
"""
from typing import Any, Dict, List

import httpx

from src import config_utils
from src.logging_config import get_logger

logger = get_logger("ipo.source")

# The ONE place this host is named. Asserted by
# tests/test_outbound_hosts.py.
IPO_SOURCE_HOST = "webnodejs.investorgain.com"

# A closed set of one, read in one glance -- the same shape the Dhan clients'
# endpoint lists have, and for the same reason: a regex that accepts a family
# of URLs is not the guarantee a set is. Report 331 is "IPO GMP Live".
GMP_REPORT_URL = (
    "https://webnodejs.investorgain.com/cloud/v2/report/data-read/331/1/9/2026/2026-27/0/all"
)

# The human page a reader might want to open lives on `www.investorgain.com`,
# a DIFFERENT host, and is deliberately NOT named anywhere in Python. This
# process never fetches it, and `OUTBOUND_HOSTS` answers "what can this process
# talk to" -- a host listed there but never called would weaken that answer.
# The link is built in the browser, where it is clicked
# (`frontend/src/pages/IpoDashboardPage.jsx`), from each IPO's stored relative
# `source_path`.

DEFAULT_TIMEOUT_SECONDS = 20

# The site is a browser application and answers a default httpx UA perfectly
# well, but a request that identifies itself is the polite form and makes this
# application recognisable in somebody else's access log.
USER_AGENT = "dhan-crude-paper-trading/1.0 (personal IPO dashboard)"


class IpoSourceError(Exception):
    """The source could not be read.

    One exception rather than a taxonomy: unlike Telegram, where five failures
    have five different fixes, every failure here has the same fix -- try again
    later, and meanwhile say the GMP is stale. What it must never do is look
    like success.
    """


class IpoSourceClient:
    """Fetches the GMP report. Parses nothing; see `ipo_parser`."""

    def __init__(self, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS):
        self._timeout = timeout_seconds

    @staticmethod
    def _timeout_seconds() -> int:
        return config_utils.get_property_value_int(
            "ipo.source_timeout_seconds", DEFAULT_TIMEOUT_SECONDS
        )

    async def fetch_rows(self) -> List[Dict[str, Any]]:
        """Every row the report publishes, mainboard and SME alike.

        Filtering is the service's job, not the client's: a client that
        silently dropped rows would make "how many did the source return"
        unanswerable from its own logs.
        """
        timeout = self._timeout_seconds()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(
                    GMP_REPORT_URL,
                    params={"search": ""},
                    headers={
                        "Accept": "application/json",
                        "User-Agent": USER_AGENT,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise IpoSourceError(
                f"The IPO source answered {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            # The TYPE, not the message: the same rule the Dhan and Telegram
            # clients follow. Nothing secret is in this URL, but the habit is
            # worth more than the exception text.
            raise IpoSourceError(
                f"The IPO source could not be reached ({type(exc).__name__})"
            ) from exc
        except ValueError as exc:  # json() on a non-JSON body
            raise IpoSourceError("The IPO source did not answer with JSON") from exc

        if not isinstance(payload, dict):
            raise IpoSourceError("The IPO source's payload was not an object")

        rows = payload.get("reportTableData")
        if not isinstance(rows, list):
            raise IpoSourceError(
                "The IPO source's payload had no `reportTableData` list; the "
                "endpoint's shape has changed"
            )

        logger.debug("Fetched %d row(s) from the IPO GMP report", len(rows))
        return [row for row in rows if isinstance(row, dict)]
