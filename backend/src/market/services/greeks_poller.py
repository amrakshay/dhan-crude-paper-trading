"""Background greeks/IV poller.

The WebSocket feed carries no greeks, so IV and the greeks come from the option
chain REST endpoint on a 3-second cadence and are merged into the same
in-memory book the ticks land in.

Two rules this module exists to honour:

1. **It must never block the tick path.** It runs as its own asyncio task, does
   its network I/O with httpx, and touches the book only through
   ``MarketBook.merge_greeks`` -- a dict update on rows the feed also writes.
   Nothing here awaits anything the feed handler waits on.
2. **Expiries are polled concurrently, each respecting its own 3s limit.** Dhan
   rate limits one unique request per 3 seconds; the client serialises per
   (underlying, expiry) so two expiries can be in flight at once.

In synthetic mode there is no upstream to poll, so greeks are computed locally
with Black-76 from the synthetic prices. Those are clearly flagged
(`greeksSource: "synthetic"` on every row) and are not real market greeks.
"""
import asyncio
from datetime import date
from typing import Any, Dict, List, Optional

from src import config_utils
from src.core.time_utils import ist_today
from src.logging_config import get_logger
from src.market.services import black76
from src.market.services.dhan_option_chain_client import (
    DhanOptionChainClient,
    OptionChainError,
    OptionChainRateLimited,
)
from src.market.services.market_book import MarketBook, now_ms

logger = get_logger("market.greeks")


class GreeksPoller:
    def __init__(self, book: MarketBook, feed_manager) -> None:
        self.book = book
        self.feed_manager = feed_manager
        self.client = DhanOptionChainClient()

        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self.is_synthetic = False

        self.poll_count = 0
        self.legs_merged = 0
        self.last_poll_ms: Optional[int] = None
        self.last_error: Optional[str] = None
        self.underlying_last_price: Optional[float] = None

    # --- configuration -----------------------------------------------------
    @staticmethod
    def _enabled() -> bool:
        return config_utils.get_property_value_boolean("greeks_poller.enabled", True)

    @staticmethod
    def _interval() -> float:
        return config_utils.get_property_value_float("greeks_poller.interval_seconds", 3.0)

    @staticmethod
    def _expiry_count() -> int:
        return config_utils.get_property_value_int("greeks_poller.expiries_to_poll", 2)

    @staticmethod
    def _underlying_scrip() -> int:
        return config_utils.get_property_value_int("underlying.underlying_scrip", 294)

    @staticmethod
    def _underlying_segment() -> str:
        return config_utils.get_property_value("underlying.exchange_segment", "MCX_COMM")

    # --- lifecycle ---------------------------------------------------------
    async def start(self, is_synthetic: bool = False) -> None:
        if self._task is not None and not self._task.done():
            return
        if not self._enabled():
            logger.info("Greeks poller is disabled")
            return

        self.is_synthetic = is_synthetic
        if not is_synthetic and not self.client.has_credentials():
            self.last_error = (
                "No Dhan credentials; greeks and IV are unavailable. The chain will "
                "still show live prices, OI and depth from the feed."
            )
            logger.warning(self.last_error)
            return

        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="greeks-poller")
        logger.info(
            "Greeks poller started (%s, every %.1fs)",
            "SYNTHETIC Black-76" if is_synthetic else "Dhan option chain",
            self._interval(),
        )

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None

    async def _run(self) -> None:
        interval = self._interval()
        while not self._stopping:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)
                logger.exception("Greeks poll failed")
            await asyncio.sleep(interval)

    # --- polling -----------------------------------------------------------
    async def poll_once(self) -> int:
        expiries = list(self.feed_manager.subscribed_expiries)[: self._expiry_count()]
        if not expiries:
            return 0

        if self.is_synthetic:
            merged = self._merge_synthetic_greeks(expiries)
        else:
            # Different expiries may be in flight at once; the client enforces
            # the 3s limit per (underlying, expiry).
            results = await asyncio.gather(
                *(self._poll_expiry(expiry) for expiry in expiries),
                return_exceptions=True,
            )
            merged = 0
            for expiry, result in zip(expiries, results):
                if isinstance(result, OptionChainRateLimited):
                    logger.warning("Rate limited polling %s; will retry", expiry)
                elif isinstance(result, Exception):
                    self.last_error = str(result)
                    logger.error("Greeks poll for %s failed: %s", expiry, result)
                else:
                    merged += result

        self.poll_count += 1
        self.last_poll_ms = now_ms()
        self.legs_merged += merged
        return merged

    async def _poll_expiry(self, expiry: date) -> int:
        snapshot = await self.client.fetch_option_chain(
            self._underlying_scrip(), self._underlying_segment(), expiry.isoformat()
        )
        if snapshot.underlying_last_price is not None:
            self.underlying_last_price = snapshot.underlying_last_price

        merged = 0
        for strike, option_type, leg in snapshot.legs():
            # The chain response carries security_id per leg, so rows are matched
            # directly rather than by re-deriving the id from strike and type.
            if not leg.security_id:
                continue
            payload: Dict[str, Any] = {
                "iv": leg.implied_volatility,
                "delta": leg.delta,
                "theta": leg.theta,
                "gamma": leg.gamma,
                "vega": leg.vega,
                "oiChange": leg.oi_change,
                "previousOi": leg.previous_oi,
                "chainLastPrice": leg.last_price,
                "chainBid": leg.top_bid_price,
                "chainAsk": leg.top_ask_price,
                "chainBidQty": leg.top_bid_quantity,
                "chainAskQty": leg.top_ask_quantity,
                "prevClose": leg.previous_close_price,
                "greeksSource": "dhan",
            }
            # Do not overwrite a live field with a null from the chain.
            payload = {key: value for key, value in payload.items() if value is not None}
            self.book.merge_greeks(leg.security_id, payload)
            merged += 1

        self.last_error = None
        return merged

    # --- synthetic ---------------------------------------------------------
    def _merge_synthetic_greeks(self, expiries: List[date]) -> int:
        """Black-76 greeks off the synthetic prices. NOT real market greeks."""
        future_id = self.feed_manager.near_future_security_id
        future_row = self.book.get(future_id) if future_id else None
        if not future_row or not future_row.get("ltp"):
            return 0

        future_price = float(future_row["ltp"])
        self.underlying_last_price = future_price
        wanted = {expiry.isoformat() for expiry in expiries}
        today = ist_today()
        merged = 0

        for row in self.book.snapshot():
            option_type = row.get("optionType")
            strike = row.get("strikePrice")
            expiry_iso = row.get("expiryDate")
            price = row.get("ltp")
            if option_type not in ("CE", "PE") or not strike or expiry_iso not in wanted:
                continue
            if price is None or price <= 0:
                continue

            try:
                expiry_date = date.fromisoformat(expiry_iso)
            except (TypeError, ValueError):
                continue
            years = max((expiry_date - today).days, 0.5) / 365.0

            implied = black76.implied_volatility(
                float(price), future_price, float(strike), years, option_type=option_type
            )
            values = black76.greeks(
                future_price, float(strike), years, implied, option_type=option_type
            )

            previous_oi = row.get("previousOi")
            if previous_oi is None:
                previous_oi = row.get("oi")

            self.book.merge_greeks(
                row["securityId"],
                {
                    "iv": implied * 100.0,      # percent, matching Dhan's units
                    "delta": values["delta"],
                    "theta": values["theta"],
                    "gamma": values["gamma"],
                    "vega": values["vega"],
                    "previousOi": previous_oi,
                    "oiChange": (row.get("oi") or 0) - (previous_oi or 0),
                    "greeksSource": "synthetic",
                },
            )
            merged += 1

        return merged

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self._enabled(),
            "synthetic": self.is_synthetic,
            "intervalSeconds": self._interval(),
            "polls": self.poll_count,
            "legsMerged": self.legs_merged,
            "lastPollMs": self.last_poll_ms,
            "lastPollAgeMs": (now_ms() - self.last_poll_ms) if self.last_poll_ms else None,
            "underlyingLastPrice": self.underlying_last_price,
            "error": self.last_error,
            "client": self.client.stats(),
        }
