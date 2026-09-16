"""Dhan option chain REST client -- the source of greeks and IV.

MARKET DATA ONLY. This client can reach exactly two endpoints, both read-only:

    POST /optionchain             greeks, IV, OI and touch per strike
    POST /optionchain/expirylist  available expiries

No trading, funds or holdings endpoint is reachable from here, and none may be
added (see tests/test_no_real_orders.py, which enforces this by scanning the
Dhan client modules specifically).

The live WebSocket feed carries no greeks, which is why this exists at all.

Rate limit, documented by Dhan: one unique request every 3 seconds. This client
enforces that per (underlying, expiry) pair so different expiries can be polled
concurrently, and backs off on HTTP 429.

Response schema verified against https://dhanhq.co/docs/v2/option-chain/ on
2026-09-16:

    {"data": {"last_price": float,
              "oc": {"<strike>": {"ce": {...}, "pe": {...}}}},
     "status": "success"}

Each ce/pe leg carries security_id, last_price, oi, previous_oi, volume,
implied_volatility, top_bid/ask price and quantity, previous_close_price and a
nested greeks object (delta/theta/gamma/vega).
"""
import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx

from src import config_utils
from src.logging_config import get_logger

logger = get_logger("market.optionchain")

# The only two endpoints this client is permitted to construct.
ENDPOINT_OPTION_CHAIN = "/optionchain"
ENDPOINT_EXPIRY_LIST = "/optionchain/expirylist"

MIN_REQUEST_INTERVAL_SECONDS = 3.0


class OptionChainError(Exception):
    pass


class OptionChainRateLimited(OptionChainError):
    pass


def configured_credentials_present() -> bool:
    """Whether the live config carries a usable Dhan credential pair."""
    return bool(
        config_utils.get_property_value("dhan.client_id", "")
        and config_utils.get_property_value("dhan.access_token", "")
    )


@dataclass
class OptionLeg:
    """One side (CE or PE) of one strike, as returned by the chain endpoint."""

    security_id: Optional[str]
    last_price: Optional[float] = None
    oi: Optional[int] = None
    previous_oi: Optional[int] = None
    volume: Optional[int] = None
    previous_volume: Optional[int] = None
    implied_volatility: Optional[float] = None
    delta: Optional[float] = None
    theta: Optional[float] = None
    gamma: Optional[float] = None
    vega: Optional[float] = None
    top_bid_price: Optional[float] = None
    top_bid_quantity: Optional[int] = None
    top_ask_price: Optional[float] = None
    top_ask_quantity: Optional[int] = None
    previous_close_price: Optional[float] = None
    average_price: Optional[float] = None

    @property
    def oi_change(self) -> Optional[int]:
        if self.oi is None or self.previous_oi is None:
            return None
        return self.oi - self.previous_oi


@dataclass
class OptionChainSnapshot:
    underlying_last_price: Optional[float] = None
    expiry: Optional[str] = None
    strikes: Dict[float, Dict[str, OptionLeg]] = field(default_factory=dict)
    fetched_at: float = 0.0

    def legs(self):
        """Yield (strike, option_type, leg) for every populated leg."""
        for strike, sides in self.strikes.items():
            for option_type, leg in sides.items():
                yield strike, option_type, leg


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> Optional[int]:
    number = _as_float(value)
    return int(number) if number is not None else None


def _parse_leg(payload: Optional[Dict[str, Any]]) -> Optional[OptionLeg]:
    if not payload:
        return None
    greeks = payload.get("greeks") or {}
    security_id = payload.get("security_id")
    return OptionLeg(
        security_id=str(security_id) if security_id is not None else None,
        last_price=_as_float(payload.get("last_price")),
        oi=_as_int(payload.get("oi")),
        previous_oi=_as_int(payload.get("previous_oi")),
        volume=_as_int(payload.get("volume")),
        previous_volume=_as_int(payload.get("previous_volume")),
        implied_volatility=_as_float(payload.get("implied_volatility")),
        delta=_as_float(greeks.get("delta")),
        theta=_as_float(greeks.get("theta")),
        gamma=_as_float(greeks.get("gamma")),
        vega=_as_float(greeks.get("vega")),
        top_bid_price=_as_float(payload.get("top_bid_price")),
        top_bid_quantity=_as_int(payload.get("top_bid_quantity")),
        top_ask_price=_as_float(payload.get("top_ask_price")),
        top_ask_quantity=_as_int(payload.get("top_ask_quantity")),
        previous_close_price=_as_float(payload.get("previous_close_price")),
        average_price=_as_float(payload.get("average_price")),
    )


def parse_option_chain(payload: Dict[str, Any], expiry: Optional[str] = None) -> OptionChainSnapshot:
    """Turn the documented response envelope into a snapshot."""
    data = payload.get("data") or {}
    chain = data.get("oc") or {}
    if not isinstance(chain, dict):
        raise OptionChainError(f"Unexpected option chain payload shape: {type(chain)}")

    snapshot = OptionChainSnapshot(
        underlying_last_price=_as_float(data.get("last_price")),
        expiry=expiry,
        fetched_at=time.time(),
    )

    for strike_key, sides in chain.items():
        strike = _as_float(strike_key)
        if strike is None or not isinstance(sides, dict):
            continue
        legs: Dict[str, OptionLeg] = {}
        call = _parse_leg(sides.get("ce"))
        put = _parse_leg(sides.get("pe"))
        if call is not None:
            legs["CE"] = call
        if put is not None:
            legs["PE"] = put
        if legs:
            snapshot.strikes[strike] = legs

    return snapshot


class DhanOptionChainClient:
    """Read-only client for the two option chain endpoints."""

    def __init__(
        self,
        client_id: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> None:
        """Optionally pin explicit credentials.

        Used by the Settings page to validate a token the operator has typed
        but not yet saved. When omitted, the configured credentials are read
        from the live config as usual.
        """
        self._client_id_override = client_id
        self._access_token_override = access_token
        self._last_request_at: Dict[Tuple[int, str], float] = {}
        self._locks: Dict[Tuple[int, str], asyncio.Lock] = {}
        self.request_count = 0
        self.error_count = 0
        self.rate_limited_count = 0

    # --- configuration -----------------------------------------------------
    @staticmethod
    def _base_url() -> str:
        return config_utils.get_property_value("dhan.api_base_url", "https://api.dhan.co/v2")

    @staticmethod
    def _timeout() -> int:
        return config_utils.get_property_value_int("dhan.http_timeout_seconds", 30)

    def _credentials(self) -> Tuple[str, str]:
        client_id = self._client_id_override or (
            config_utils.get_property_value("dhan.client_id", "") or ""
        )
        access_token = self._access_token_override or (
            config_utils.get_property_value("dhan.access_token", "") or ""
        )
        return client_id, access_token

    def _headers(self) -> Dict[str, str]:
        client_id, access_token = self._credentials()
        return {
            "access-token": access_token,
            "client-id": client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def has_credentials(self) -> bool:
        client_id, access_token = self._credentials()
        return bool(client_id and access_token)

    # --- rate limiting -----------------------------------------------------
    def _lock_for(self, key: Tuple[int, str]) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def _respect_rate_limit(self, key: Tuple[int, str]) -> None:
        last = self._last_request_at.get(key)
        if last is not None:
            elapsed = time.monotonic() - last
            if elapsed < MIN_REQUEST_INTERVAL_SECONDS:
                await asyncio.sleep(MIN_REQUEST_INTERVAL_SECONDS - elapsed)
        self._last_request_at[key] = time.monotonic()

    # --- requests ----------------------------------------------------------
    async def _post(self, endpoint: str, body: Dict[str, Any]) -> Dict[str, Any]:
        if endpoint not in (ENDPOINT_OPTION_CHAIN, ENDPOINT_EXPIRY_LIST):
            # Belt and braces: this client is market-data only by construction.
            raise OptionChainError(f"Endpoint not permitted by this client: {endpoint}")
        if not self.has_credentials():
            raise OptionChainError(
                "Dhan market-data credentials are not configured "
                "(DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN)."
            )

        url = self._base_url() + endpoint
        self.request_count += 1
        async with httpx.AsyncClient(timeout=self._timeout()) as client:
            response = await client.post(url, json=body, headers=self._headers())

        if response.status_code == 429:
            self.rate_limited_count += 1
            raise OptionChainRateLimited(
                "Dhan rate limited the option chain request (one per 3s per expiry)"
            )
        if response.status_code >= 400:
            self.error_count += 1
            raise OptionChainError(
                f"Option chain request failed: HTTP {response.status_code} {response.text[:300]}"
            )

        payload = response.json()
        if isinstance(payload, dict) and payload.get("status") not in (None, "success"):
            self.error_count += 1
            raise OptionChainError(f"Option chain request rejected: {payload}")
        return payload

    async def fetch_expiry_list(self, underlying_scrip: int, underlying_segment: str) -> List[str]:
        key = (underlying_scrip, "expirylist")
        async with self._lock_for(key):
            await self._respect_rate_limit(key)
            payload = await self._post(
                ENDPOINT_EXPIRY_LIST,
                {"UnderlyingScrip": underlying_scrip, "UnderlyingSeg": underlying_segment},
            )
        data = payload.get("data") or []
        return [str(value) for value in data]

    async def fetch_option_chain(
        self, underlying_scrip: int, underlying_segment: str, expiry: str
    ) -> OptionChainSnapshot:
        """One chain snapshot. Serialised per (underlying, expiry)."""
        key = (underlying_scrip, expiry)
        async with self._lock_for(key):
            await self._respect_rate_limit(key)
            payload = await self._post(
                ENDPOINT_OPTION_CHAIN,
                {
                    "UnderlyingScrip": underlying_scrip,
                    "UnderlyingSeg": underlying_segment,
                    "Expiry": expiry,
                },
            )
        return parse_option_chain(payload, expiry=expiry)

    def stats(self) -> Dict[str, Any]:
        return {
            "requests": self.request_count,
            "errors": self.error_count,
            "rateLimited": self.rate_limited_count,
        }
