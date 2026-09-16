"""Dhan charts REST client -- the source of candle history.

MARKET DATA ONLY. This client can reach exactly two endpoints, both read-only:

    POST /charts/historical   daily candles, back to instrument inception
    POST /charts/intraday     minute candles, max 90 days per request

No trading, funds or holdings endpoint is reachable from here, and none may be
added (see tests/test_no_real_orders.py, which scans the Dhan client modules
specifically, and whose ALLOWED_DHAN_URLS gained these two URLs -- and only
these two -- when charting was added).

The live WebSocket feed carries no history at all: MarketBook holds one current
row per instrument and nothing is persisted, which is why this exists.

Request/response schema per https://dhanhq.co/docs/v2/historical-data/, read
2026-09-16. UNVERIFIED against the live API -- this project had no Dhan token
when charting was written, so every claim below comes from the documentation
and not from a response we have seen:

    daily     {"securityId": str, "exchangeSegment": str, "instrument": str,
               "expiryCode": int, "oi": bool,
               "fromDate": "YYYY-MM-DD", "toDate": "YYYY-MM-DD"}
    intraday  {..., "interval": "1"|"5"|"15"|"25"|"60",
               "fromDate": "YYYY-MM-DD HH:MM:SS", "toDate": same}

    response  {"open": [float], "high": [float], "low": [float],
               "close": [float], "volume": [int], "timestamp": [epoch int],
               "open_interest": [int]}

`toDate` is documented as non-inclusive. The response arrays are parallel and
are rejected here if their lengths disagree.
"""
import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

import httpx

from src import config_utils
from src.logging_config import get_logger

logger = get_logger("market.charts")

# The only two endpoints this client is permitted to construct.
ENDPOINT_DAILY = "/charts/historical"
ENDPOINT_INTRADAY = "/charts/intraday"

# Dhan's intraday endpoint accepts exactly these interval values (minutes).
# Note 25, not 30 -- unusual, and unverified against the live API. Everything
# the UI offers that is not in this set is aggregated from one that is; see
# candle_service.TIMEFRAMES.
NATIVE_INTRADAY_INTERVALS = (1, 5, 15, 25, 60)

# Documented: "Only 90 days of data can be polled at once" (intraday).
MAX_INTRADAY_DAYS = 90

# Dhan does not publish a per-endpoint limit for charts the way it does for the
# option chain. This is a self-imposed floor so a UI that flips timeframes fast
# cannot machine-gun the API; the response cache in candle_service does the rest.
MIN_REQUEST_INTERVAL_SECONDS = 1.0

DAILY_DATE_FORMAT = "%Y-%m-%d"
INTRADAY_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class ChartsError(Exception):
    """Any failure to obtain candles from Dhan."""


class ChartsRateLimited(ChartsError):
    pass


@dataclass(frozen=True)
class Candle:
    """One OHLC bar. `time` is epoch SECONDS -- lightweight-charts' unit."""

    time: int
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None
    open_interest: Optional[float] = None

    def as_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "time": self.time,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
        }
        if self.volume is not None:
            payload["volume"] = self.volume
        if self.open_interest is not None:
            payload["openInterest"] = self.open_interest
        return payload


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_candles(payload: Dict[str, Any]) -> List[Candle]:
    """Turn the documented parallel-array envelope into sorted candles.

    Rows with a missing timestamp or a missing OHLC value are dropped rather
    than defaulted -- a candle with an invented price is worse than no candle.
    Duplicate timestamps are collapsed to the last occurrence, because
    lightweight-charts rejects a series that is not strictly ascending.
    """
    if not isinstance(payload, dict):
        raise ChartsError(f"Unexpected candle payload shape: {type(payload)}")

    timestamps = payload.get("timestamp") or []
    opens = payload.get("open") or []
    highs = payload.get("high") or []
    lows = payload.get("low") or []
    closes = payload.get("close") or []
    volumes = payload.get("volume") or []
    open_interest = payload.get("open_interest") or []

    if not isinstance(timestamps, Sequence) or isinstance(timestamps, (str, bytes)):
        raise ChartsError("Candle payload has no timestamp array")

    count = len(timestamps)
    for name, series in (
        ("open", opens), ("high", highs), ("low", lows), ("close", closes),
    ):
        if len(series) != count:
            raise ChartsError(
                f"Candle payload arrays disagree: {count} timestamps but "
                f"{len(series)} {name} values"
            )

    by_time: Dict[int, Candle] = {}
    for index in range(count):
        raw_time = _as_float(timestamps[index])
        open_price = _as_float(opens[index])
        high_price = _as_float(highs[index])
        low_price = _as_float(lows[index])
        close_price = _as_float(closes[index])
        if None in (raw_time, open_price, high_price, low_price, close_price):
            continue
        by_time[int(raw_time)] = Candle(
            time=int(raw_time),
            open=open_price,
            high=high_price,
            low=low_price,
            close=close_price,
            volume=_as_float(volumes[index]) if index < len(volumes) else None,
            open_interest=(
                _as_float(open_interest[index]) if index < len(open_interest) else None
            ),
        )

    return [by_time[key] for key in sorted(by_time)]


class DhanChartsClient:
    """Read-only client for the two chart endpoints."""

    def __init__(self) -> None:
        self._last_request_at: Dict[str, float] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
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

    @staticmethod
    def _credentials() -> tuple:
        return (
            config_utils.get_property_value("dhan.client_id", "") or "",
            config_utils.get_property_value("dhan.access_token", "") or "",
        )

    def has_credentials(self) -> bool:
        client_id, access_token = self._credentials()
        return bool(client_id and access_token)

    def _headers(self) -> Dict[str, str]:
        client_id, access_token = self._credentials()
        return {
            "access-token": access_token,
            "client-id": client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # --- rate limiting -----------------------------------------------------
    def _lock_for(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def _respect_rate_limit(self, key: str) -> None:
        last = self._last_request_at.get(key)
        if last is not None:
            elapsed = time.monotonic() - last
            if elapsed < MIN_REQUEST_INTERVAL_SECONDS:
                await asyncio.sleep(MIN_REQUEST_INTERVAL_SECONDS - elapsed)
        self._last_request_at[key] = time.monotonic()

    # --- requests ----------------------------------------------------------
    async def _post(self, endpoint: str, body: Dict[str, Any]) -> Dict[str, Any]:
        if endpoint not in (ENDPOINT_DAILY, ENDPOINT_INTRADAY):
            # Belt and braces: this client is market-data only by construction.
            raise ChartsError(f"Endpoint not permitted by this client: {endpoint}")
        if not self.has_credentials():
            raise ChartsError(
                "Dhan market-data credentials are not configured "
                "(DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN)."
            )

        url = self._base_url() + endpoint
        self.request_count += 1
        logger.debug("POST %s body=%s", url, body)
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout()) as client:
                response = await client.post(url, json=body, headers=self._headers())
        except Exception:
            self.error_count += 1
            logger.exception("Chart request to %s failed to complete (body=%s)", url, body)
            raise ChartsError(f"Chart request to {endpoint} could not be completed")
        elapsed_ms = (time.monotonic() - started) * 1000

        if response.status_code == 429:
            self.rate_limited_count += 1
            logger.warning(
                "Dhan rate limited %s (body=%s); %s rate-limited response(s) so far",
                endpoint, body, self.rate_limited_count,
            )
            raise ChartsRateLimited("Dhan rate limited the chart request")
        if response.status_code >= 400:
            self.error_count += 1
            logger.error(
                "Chart request failed: HTTP %s from %s (body=%s): %s",
                response.status_code, url, body, response.text[:300],
            )
            raise ChartsError(
                f"Chart request failed: HTTP {response.status_code} {response.text[:300]}"
            )

        payload = response.json()
        if isinstance(payload, dict) and payload.get("status") not in (None, "success"):
            self.error_count += 1
            logger.error("Dhan rejected the chart request for %s: %s", body, payload)
            raise ChartsError(f"Chart request rejected: {payload}")
        logger.debug("%s responded 200 in %.0f ms", endpoint, elapsed_ms)
        return payload if isinstance(payload, dict) else {}

    async def fetch_daily(
        self,
        security_id: str,
        exchange_segment: str,
        instrument: str,
        from_date: datetime,
        to_date: datetime,
        expiry_code: int = 0,
        include_oi: bool = False,
    ) -> List[Candle]:
        """Daily candles between two dates. `to_date` is non-inclusive."""
        body = {
            "securityId": str(security_id),
            "exchangeSegment": exchange_segment,
            "instrument": instrument,
            "expiryCode": expiry_code,
            "oi": include_oi,
            "fromDate": from_date.strftime(DAILY_DATE_FORMAT),
            "toDate": to_date.strftime(DAILY_DATE_FORMAT),
        }
        key = f"daily:{security_id}"
        async with self._lock_for(key):
            await self._respect_rate_limit(key)
            payload = await self._post(ENDPOINT_DAILY, body)
        return parse_candles(payload)

    async def fetch_intraday(
        self,
        security_id: str,
        exchange_segment: str,
        instrument: str,
        interval_minutes: int,
        from_date: datetime,
        to_date: datetime,
        include_oi: bool = False,
    ) -> List[Candle]:
        """Intraday candles at one of Dhan's native intervals."""
        if interval_minutes not in NATIVE_INTRADAY_INTERVALS:
            raise ChartsError(
                f"Dhan does not serve a {interval_minutes}-minute interval; "
                f"native intervals are {NATIVE_INTRADAY_INTERVALS}"
            )
        span_days = (to_date - from_date).days
        if span_days > MAX_INTRADAY_DAYS:
            raise ChartsError(
                f"Intraday requests are limited to {MAX_INTRADAY_DAYS} days; "
                f"asked for {span_days}"
            )
        body = {
            "securityId": str(security_id),
            "exchangeSegment": exchange_segment,
            "instrument": instrument,
            "interval": str(interval_minutes),
            "oi": include_oi,
            "fromDate": from_date.strftime(INTRADAY_DATE_FORMAT),
            "toDate": to_date.strftime(INTRADAY_DATE_FORMAT),
        }
        key = f"intraday:{security_id}:{interval_minutes}"
        async with self._lock_for(key):
            await self._respect_rate_limit(key)
            payload = await self._post(ENDPOINT_INTRADAY, body)
        return parse_candles(payload)
