"""Renews the Dhan access token, and does nothing else.

ONE ENDPOINT, and the narrowest one Dhan offers:

    POST /v2/RenewToken    exchange an ACTIVE token for a fresh 24-hour one

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

    request   headers: access-token: {JWT}, dhanClientId: {client id}
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


class TokenExpiredError(TokenRenewalError):
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
                response = await client.post(
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

        if response.status_code in (401, 403):
            self.failures += 1
            self.last_error = f"Dhan refused the renewal ({response.status_code})"
            raise TokenExpiredError(
                "Dhan refused to renew the stored token. It renews only a token "
                "that is still active, so this one has most likely already "
                "expired -- generate a new one on Dhan Web and save it on the "
                "Settings page."
            )
        if response.status_code >= 400:
            self.failures += 1
            self.last_error = f"Dhan returned {response.status_code}"
            logger.warning(
                "Dhan token renewal returned %s in %.0f ms",
                response.status_code, elapsed_ms,
            )
            raise TokenRenewalError(f"Dhan returned {response.status_code}")

        try:
            payload = response.json()
        except Exception as error:  # noqa: BLE001
            self.failures += 1
            self.last_error = "Dhan returned a body that is not JSON"
            raise TokenRenewalError("Dhan returned a body that is not JSON") from error

        token = (payload or {}).get("accessToken")
        if not token:
            self.failures += 1
            self.last_error = "Dhan returned no accessToken"
            raise TokenRenewalError(
                "Dhan accepted the renewal but returned no accessToken."
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

    def status(self) -> Dict[str, Any]:
        """Counters for the health page. No token, no client id, no secret."""
        return {
            "renewals": self.renewals,
            "failures": self.failures,
            "lastError": self.last_error,
        }


_client: Optional[DhanTokenClient] = None


def get_token_client() -> DhanTokenClient:
    global _client
    if _client is None:
        _client = DhanTokenClient()
    return _client
