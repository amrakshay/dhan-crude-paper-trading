"""SYNTHETIC MARKET DATA -- THIS IS NOT REAL.

Every price produced here is generated locally by a random walk. No upstream
connection is opened and no Dhan endpoint is contacted. It exists so the whole
stack -- chain, order entry, fill simulation, positions, P&L -- can be
exercised without market-data credentials and outside MCX hours (09:00-23:30
IST).

It is gated behind `market_feed.synthetic_feed` (env: DHAN_SYNTHETIC_FEED) and
reports its connection state as SYNTHETIC, which the UI renders as a loud
banner. Nothing silently falls back to this: if credentials are missing and the
flag is false, the feed reports an error instead of inventing prices.

To keep it useful rather than merely non-empty, the generated chain is
internally consistent: the future follows a random walk, and every option is
priced off it with Black-76 plus a volatility smile, so puts and calls respect
parity and the greeks match the prices.
"""
import asyncio
import math
import random
import time
from datetime import date
from typing import Dict, Iterable, List, Optional, Set, Tuple

from src import config_utils
from src.constants import ConnectionState
from src.core.time_utils import ist_now
from src.logging_config import get_logger
from src.market.services import black76
from src.market.services.market_book import MarketBook, now_ms

logger = get_logger("market.synthetic")


class SyntheticFeed:
    """Generates a self-consistent CRUDEOIL book. Mirrors DhanFeedClient's API."""

    def __init__(self, book: MarketBook, on_state_change=None) -> None:
        self.book = book
        self._on_state_change = on_state_change

        self.state = ConnectionState.DISCONNECTED
        self.state_detail: Optional[str] = None
        self.reconnect_count = 0
        self.frames_received = 0
        self.packets_applied = 0
        self.last_message_ms = 0
        self.connected_at_ms: Optional[int] = None

        self._subscribed: Set[Tuple[str, str]] = set()
        self._task: Optional[asyncio.Task] = None
        self._stopping = False

        self._future_price: float = float(
            config_utils.get_property_value_float("market_feed.synthetic_base_price", 6800.0)
        )
        self._volume: Dict[str, int] = {}
        self._open_interest: Dict[str, int] = {}
        self._session_open: Optional[float] = None
        self._session_high: Optional[float] = None
        self._session_low: Optional[float] = None
        self._previous_close: float = self._future_price

    # --- contract metadata, supplied by the feed manager -------------------
    def set_contracts(self, contracts: Dict[str, dict]) -> None:
        """contracts: security_id -> {optionType, strikePrice, expiryDate, tickSize}"""
        self._contracts = contracts

    _contracts: Dict[str, dict] = {}

    @staticmethod
    def _interval_seconds() -> float:
        return config_utils.get_property_value_float(
            "market_feed.synthetic_tick_interval_ms", 250.0
        ) / 1000.0

    @staticmethod
    def _volatility() -> float:
        return config_utils.get_property_value_float("market_feed.synthetic_volatility", 0.35)

    def _set_state(self, state: ConnectionState, detail: Optional[str] = None) -> None:
        self.state = state
        self.state_detail = detail
        if self._on_state_change is not None:
            try:
                self._on_state_change(state, detail)
            except Exception:
                logger.exception("Synthetic feed state callback failed")

    # --- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._session_open = self._future_price
        self._session_high = self._future_price
        self._session_low = self._future_price
        self.connected_at_ms = now_ms()
        self._set_state(
            ConnectionState.SYNTHETIC,
            "Locally generated prices -- NOT real market data",
        )
        logger.warning(
            "SYNTHETIC FEED ENABLED: prices are generated locally and are not real "
            "market data. Set DHAN_SYNTHETIC_FEED=false with valid DHAN_CLIENT_ID / "
            "DHAN_ACCESS_TOKEN for the live feed."
        )
        self._task = asyncio.create_task(self._run(), name="synthetic-feed")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self._set_state(ConnectionState.DISCONNECTED, "stopped")

    async def _run(self) -> None:
        interval = self._interval_seconds()
        while not self._stopping:
            try:
                self._generate_tick_batch()
            except Exception:
                logger.exception(
                    "Synthetic tick generation failed; the synthetic book will "
                    "stop advancing until this recovers"
                )
            await asyncio.sleep(interval)

    # --- generation --------------------------------------------------------
    def _step_future(self) -> float:
        """Geometric random walk on the front-month future."""
        annual_vol = self._volatility()
        interval = self._interval_seconds()
        years_per_step = interval / (365.0 * 24 * 3600)
        shock = random.gauss(0.0, 1.0) * annual_vol * math.sqrt(years_per_step)
        self._future_price *= math.exp(shock)

        # Keep it in a plausible band so a long session cannot wander absurdly.
        self._future_price = max(3000.0, min(12000.0, self._future_price))

        if self._session_high is None or self._future_price > self._session_high:
            self._session_high = self._future_price
        if self._session_low is None or self._future_price < self._session_low:
            self._session_low = self._future_price
        return self._future_price

    @staticmethod
    def _smile_vol(base_vol: float, future: float, strike: float) -> float:
        """A simple smile: vol rises as the strike moves away from the money."""
        moneyness = math.log(strike / future) if future > 0 and strike > 0 else 0.0
        return max(0.05, base_vol + 0.35 * moneyness * moneyness + 0.02 * -moneyness)

    def _years_to_expiry(self, expiry_date) -> float:
        """Year fraction to expiry. Accepts a date or an ISO string.

        Book metadata carries expiryDate as an ISO string (it is serialised
        straight to the browser), so both forms arrive here.
        """
        if expiry_date is None:
            return 1.0 / 365.0
        if isinstance(expiry_date, str):
            try:
                expiry_date = date.fromisoformat(expiry_date)
            except ValueError:
                return 1.0 / 365.0
        now = ist_now().date()
        days = (expiry_date - now).days
        # Never zero: a zero-time option prices to intrinsic and its depth
        # collapses, which is not a useful state to test against.
        return max(days, 0.5) / 365.0

    def _depth_for(self, price_value: float, tick_size: float, is_option: bool) -> List[tuple]:
        """Five plausible levels around a mid."""
        tick = tick_size if tick_size and tick_size > 0 else (0.1 if is_option else 1.0)
        half_spread = tick * random.choice([1, 1, 1, 2, 3])
        base_qty = random.randint(2, 40) * (1 if is_option else 5)

        levels = []
        for level in range(5):
            bid_price = max(tick, price_value - half_spread - level * tick)
            ask_price = price_value + half_spread + level * tick
            depth_factor = 1 + level
            levels.append(
                (
                    base_qty * depth_factor + random.randint(0, 10),   # bid qty
                    base_qty * depth_factor + random.randint(0, 10),   # ask qty
                    random.randint(1, 6),                              # bid orders
                    random.randint(1, 6),                              # ask orders
                    round(bid_price / tick) * tick,                    # bid price
                    round(ask_price / tick) * tick,                    # ask price
                )
            )
        return levels

    def _generate_tick_batch(self) -> None:
        if not self._subscribed:
            return

        future_price = self._step_future()
        base_vol = self._volatility()
        subscribed_ids = {security_id for _segment, security_id in self._subscribed}

        packets = []
        for security_id in subscribed_ids:
            contract = self._contracts.get(security_id, {})
            option_type = contract.get("optionType")
            tick_size = float(contract.get("tickSize") or (0.1 if option_type else 1.0))

            if option_type in ("CE", "PE"):
                strike = float(contract.get("strikePrice") or future_price)
                years = self._years_to_expiry(contract.get("expiryDate"))
                vol = self._smile_vol(base_vol, future_price, strike)
                theoretical = black76.price(
                    future_price, strike, years, vol, option_type=option_type
                )
                # Options are not continuously quoted; skip most ticks for the
                # far wings so the UI shows realistic staleness rather than
                # every strike updating in lockstep.
                distance = abs(strike - future_price) / max(future_price, 1.0)
                if distance > 0.06 and random.random() > 0.15:
                    continue
                price_value = max(tick_size, round(theoretical / tick_size) * tick_size)
                is_option = True
            else:
                price_value = round(future_price / tick_size) * tick_size
                is_option = False
                # Keep session OHLC on the tick grid too, so the UI never shows
                # a high of 6800.797209829441.
                session_open = round(self._session_open / tick_size) * tick_size
                session_high = round(self._session_high / tick_size) * tick_size
                session_low = round(self._session_low / tick_size) * tick_size
                previous_close = round(self._previous_close / tick_size) * tick_size

            self._volume[security_id] = self._volume.get(security_id, 0) + random.randint(0, 25)
            if security_id not in self._open_interest:
                self._open_interest[security_id] = random.randint(500, 20000)
            else:
                self._open_interest[security_id] = max(
                    0, self._open_interest[security_id] + random.randint(-60, 60)
                )

            depth = self._depth_for(price_value, tick_size, is_option)
            packets.append(
                (
                    8,  # PACKET_FULL
                    {
                        "security_id": security_id,
                        "segment": 5,
                        "ltp": price_value,
                        "ltq": random.randint(1, 8),
                        "ltt": int(time.time()),
                        "avg_price": price_value,
                        "volume": self._volume[security_id],
                        "total_buy_qty": random.randint(100, 5000),
                        "total_sell_qty": random.randint(100, 5000),
                        "oi": self._open_interest[security_id],
                        "oi_day_high": self._open_interest[security_id] + 500,
                        "oi_day_low": max(0, self._open_interest[security_id] - 500),
                        "open": price_value if is_option else session_open,
                        "high": price_value if is_option else session_high,
                        "low": price_value if is_option else session_low,
                        "close": price_value if is_option else previous_close,
                        "depth": depth,
                    },
                )
            )

        if packets:
            self.frames_received += 1
            self.packets_applied += self.book.apply_frame(packets)
            self.last_message_ms = now_ms()

    # --- subscription API (mirrors DhanFeedClient) -------------------------
    async def subscribe(self, instruments: Iterable[Tuple[str, str]]) -> int:
        new = {(segment, str(security_id)) for segment, security_id in instruments}
        added = new - self._subscribed
        self._subscribed |= added
        return len(added)

    async def unsubscribe(self, instruments: Iterable[Tuple[str, str]]) -> int:
        existing = {(segment, str(security_id)) for segment, security_id in instruments}
        removed = existing & self._subscribed
        self._subscribed -= removed
        return len(removed)

    @property
    def subscribed_count(self) -> int:
        return len(self._subscribed)

    def subscribed_security_ids(self) -> Set[str]:
        return {security_id for _segment, security_id in self._subscribed}

    @property
    def reference_price(self) -> float:
        """Current synthetic future price, used to centre the first strike
        window before any tick has been generated."""
        return self._future_price

    @staticmethod
    def has_credentials() -> bool:
        return True   # the synthetic feed needs none

    def status(self) -> Dict[str, object]:
        return {
            "state": self.state.value,
            "detail": self.state_detail,
            "mode": "SYNTHETIC",
            "requestCode": None,
            "subscribed": self.subscribed_count,
            "reconnects": 0,
            "framesReceived": self.frames_received,
            "packetsApplied": self.packets_applied,
            "connectedAtMs": self.connected_at_ms,
            "lastMessageAgeMs": (
                now_ms() - self.last_message_ms if self.last_message_ms else None
            ),
            "synthetic": True,
            "warning": "SYNTHETIC DATA -- prices are generated locally, not real",
        }
