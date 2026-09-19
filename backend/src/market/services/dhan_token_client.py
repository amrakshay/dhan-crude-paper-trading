"""Renews the Dhan access token, and does nothing else.

ONE ENDPOINT, and the narrowest one Dhan offers:

    GET /v2/RenewToken    exchange an ACTIVE token for a fresh 24-hour one

This is the first module in the project to name a Dhan URL that is not market
data, so it is worth being exact about what it can and cannot do.

WHAT IT DOES. It sends the access token this application already holds, plus
the client id, and receives another token with another 24 hours on it. The old
one is expired by Dhan in the same call. Nothing else is sent: no PIN, no TOTP,
no password, no credential of any kind that the application did not already
have. It cannot obtain access it was not already given -- it can only extend a
session the user started by pasting a token in.

WHAT IT DELIBERATELY CANNOT DO. `auth.dhan.co` is absent from here and from
`tests/test_no_real_orders.py`'s allowlist, on purpose. That host has
`/app/generateAccessToken`, which MINTS a token from a client id, a six-digit
PIN and a TOTP code, and `/app/generate-consent`, which starts an OAuth login.
Either would let this application authenticate AS the user rather than borrow a
session from them, and would mean storing the PIN. Considered on 2026-09-18 and
declined: the cost of the narrower choice is that a token allowed to lapse
entirely must be pasted in again by hand, and that was judged the right trade.

WHY IT EXISTS AT ALL. A Dhan access token is valid for 24 hours. When it
expires the live feed stops, the chart stops, the option chain stops and the
rotation's overnight bar refresh fails -- and the failure is silent until
something asks for a price. Renewing on a timer is the difference between an
unattended strategy and one that needs a human every morning.

Request/response per https://dhanhq.co/docs/v2/authentication/, read
2026-09-18:

    request   GET, headers only: access-token: {JWT}, dhanClientId: {client id}
    response  {"dhanClientId": str, "dhanClientName": str, "dhanClientUcc": str,
               "givenPowerOfAttorney": bool, "accessToken": str,
               "expiryTime": "YYYY-MM-DDTHH:MM:SS"}

Documented limits: it renews only a token that is still ACTIVE -- renewing an
expired one is an error -- and only tokens generated from Dhan Web. Both are
reported rather than worked around.
"""
import time
from typing import Any, Dict, Optional

import httpx

from src import config_utils
from src.logging_config import get_logger

logger = get_logger("market.token")

# The only endpoint this client is permitted to construct. Named once, here,
# and asserted by `test_only_the_token_client_may_name_the_renewal_url`.
ENDPOINT_RENEW = "/RenewToken"


class TokenRenewalError(Exception):
    """The token could not be renewed. Never fatal to the application."""


class TokenRenewalRefused(TokenRenewalError):
    """Dhan will not renew this token, and a retry will not change that.

    Distinct from `TokenRenewalError` -- which means "something went wrong,
    try later" -- because the remedy is a HUMAN one and the window to apply it
    is finite. Renewal first runs six hours before the token dies; a refusal
    that is quietly retried every fifteen minutes burns all six of those hours
    and then the token expires anyway. That is exactly what happened on
    2026-09-19: nineteen identical 400s, no escalation, and the only alert
    arrived after the token was already dead and unrecoverable.
    """


class TokenExpiredError(TokenRenewalRefused):
    """The stored token has already lapsed, so there is nothing to renew.

    A distinct type because the remedy is different and is a HUMAN one: Dhan
    will not renew a dead token, so somebody has to paste a new one in. The
    caller stops retrying on this rather than asking every fifteen minutes for
    something that cannot succeed.
    """


class DhanTokenClient:
    """Read-only with respect to the trading account; it touches only the session."""

    def __init__(self) -> None:
        self.renewals = 0
        self.failures = 0
        self.rate_limited = 0
        self.last_error: Optional[str] = None

    # --- configuration ------------------------------------------------------
    @staticmethod
    def _base_url() -> str:
        return config_utils.get_property_value(
            "dhan.api_base_url", "https://api.dhan.co/v2"
        )

    @staticmethod
    def _timeout() -> int:
        return config_utils.get_property_value_int("dhan.http_timeout_seconds", 30)

    @staticmethod
    def _credentials() -> tuple:
        return (
            config_utils.get_property_value("dhan.client_id", "") or "",
            config_utils.get_property_value("dhan.access_token", "") or "",
        )

    def has_credentials(self) -> bool:
        client_id, access_token = self._credentials()
        return bool(client_id and access_token)

    # --- the one call -------------------------------------------------------
    async def renew(self) -> Dict[str, Any]:
        """Swap the stored token for a fresh one. Returns the parsed response.

        The token is read from the live configuration rather than passed in, so
        that a caller cannot accidentally hand this a token from somewhere
        else -- and so that no caller has to hold one to use it.
        """
        client_id, access_token = self._credentials()
        if not (client_id and access_token):
            raise TokenRenewalError(
                "Dhan credentials are not configured, so there is no token to "
                "renew. Nothing is generated in their place."
            )

        url = self._base_url() + ENDPOINT_RENEW
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout()) as client:
                # GET, not POST. Dhan's documented call is
                #   curl --location 'https://api.dhan.co/v2/RenewToken' \
                #     --header 'access-token: ...' --header 'dhanClientId: ...'
                # with no --request and no --data, which curl sends as a GET.
                # The generateAccessToken example directly above it in the same
                # document DOES say --request POST, so the docs distinguish and
                # this was simply misread. Shipped as a POST on 2026-09-18 and
                # every one of the first nineteen renewals came back 400.
                response = await client.get(
                    url,
                    headers={
                        # The token renews ITSELF: it is the credential and the
                        # subject of the call at the same time.
                        "access-token": access_token,
                        "dhanClientId": client_id,
                        "Accept": "application/json",
                    },
                )
        except Exception as error:  # noqa: BLE001
            self.failures += 1
            # The exception is NOT re-raised with its message attached: an
            # httpx error can carry the request URL, and the token has been a
            # query parameter in other Dhan flows. Only the type is reported.
            self.last_error = f"{type(error).__name__} contacting Dhan"
            logger.warning("Dhan token renewal could not be completed: %s", type(error).__name__)
            raise TokenRenewalError("The renewal request could not be completed")

        elapsed_ms = (time.monotonic() - started) * 1000

        if response.status_code >= 400:
            self.failures += 1
            # WHY it failed, in Dhan's own words. Not logging this was the
            # reason nineteen consecutive failures said nothing but "400" and
            # the cause had to be found by re-reading the documentation. The
            # body of an error is an errorType/errorCode/errorMessage object,
            # not a credential -- and it goes through RedactingFormatter like
            # every other line regardless. Truncated, because an HTML error
            # page from a proxy would otherwise land whole in the log.
            detail = self._error_detail(response)
            self.last_error = f"Dhan returned {response.status_code}: {detail}"

            # 429 is the only 4xx worth retrying. Every other one is Dhan
            # telling us the REQUEST is wrong -- a dead token, a token it will
            # not renew, a malformed call -- and repeating it every fifteen
            # minutes neither fixes it nor tells anybody. It is reported as
            # REFUSED so the caller escalates to a human immediately, which
            # matters because the first attempt happens six hours before the
            # token dies and those six hours are the whole point.
            if response.status_code == 429:
                self.rate_limited += 1
                logger.warning(
                    "Dhan rate limited the token renewal in %.0f ms: %s",
                    elapsed_ms, detail,
                )
                raise TokenRenewalError(
                    "Dhan rate limited the renewal; it will be tried again."
                )

            logger.error(
                "Dhan REFUSED the token renewal: %s %s (in %.0f ms). This will "
                "not succeed on a retry -- it needs a person.",
                response.status_code, detail, elapsed_ms,
            )
            raise TokenRenewalRefused(
                f"Dhan refused to renew the stored token ({response.status_code}: "
                f"{detail}). Retrying will not help. Dhan renews only an active "
                f"token that was generated on Dhan Web -- if this token came "
                f"from the API-key OAuth flow it cannot be renewed at all. "
                f"Generate a new token on Dhan Web and save it on the "
                f"Connections page."
            )

        try:
            payload = response.json()
        except Exception as error:  # noqa: BLE001
            self.failures += 1
            self.last_error = "Dhan returned a body that is not JSON"
            raise TokenRenewalError("Dhan returned a body that is not JSON") from error

        token = (payload or {}).get("accessToken")
        if not token:
            self.failures += 1
            keys = ", ".join(sorted((payload or {}).keys())) or "nothing"
            self.last_error = f"Dhan returned no accessToken (keys: {keys})"
            # REFUSED, not "try later". A 200 carrying no token will not start
            # carrying one on the next attempt, and classifying it as transient
            # is the same mistake the 400 made on 2026-09-19 -- it retries
            # until the token dies and nobody is told in time. Observed for
            # real against a live, working token on 2026-09-19: one 200 with no
            # accessToken, then DH-906 "Invalid Token" on every call after,
            # while the same token went on serving market data perfectly.
            raise TokenRenewalRefused(
                f"Dhan accepted the renewal request but returned no access "
                f"token (the response carried: {keys}). Dhan documents "
                f"RenewToken as working only for tokens generated from Dhan "
                f"Web -- a token created under an APPLICATION on the "
                f"\"Generate Access Token / API Key\" screen appears not to "
                f"qualify, even though it authenticates for market data. "
                f"Renewal cannot recover this; the token has to be replaced by "
                f"hand before it expires."
            )

        self.renewals += 1
        self.last_error = None
        # The token itself is NEVER logged, here or anywhere. Its length and
        # the expiry are enough to tell a renewal from a truncated response.
        logger.info(
            "Dhan access token renewed in %.0f ms; new token is %s characters, "
            "valid until %s",
            elapsed_ms, len(token), payload.get("expiryTime") or "an unstated time",
        )
        return payload

    @staticmethod
    def _error_detail(response) -> str:
        """Dhan's own description of the failure, safe to log and truncated."""
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001 - not JSON; fall back to the text
            return (response.text or "").strip()[:200] or "no detail"
        if isinstance(payload, dict):
            for key in ("errorMessage", "message", "error", "errorType"):
                value = payload.get(key)
                if value:
                    return str(value)[:200]
        return str(payload)[:200]

    def status(self) -> Dict[str, Any]:
        """Counters for the health page. No token, no client id, no secret."""
        return {
            "renewals": self.renewals,
            "failures": self.failures,
            "rateLimited": self.rate_limited,
            "lastError": self.last_error,
        }


_client: Optional[DhanTokenClient] = None


def get_token_client() -> DhanTokenClient:
    global _client
    if _client is None:
        _client = DhanTokenClient()
    return _client
