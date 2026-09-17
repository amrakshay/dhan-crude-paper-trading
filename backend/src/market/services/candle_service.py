"""Candle history for the price chart.

The application persists no price history: MarketBook holds one current row per
instrument and nothing writes ticks to the database. History therefore comes
from Dhan's two read-only chart endpoints (dhan_charts_client), and the browser
updates only the newest bar from the existing WebSocket.

## Native versus derived timeframes

Dhan serves intraday candles at 1, 5, 15, 25 and 60 minutes, and daily candles
from a second endpoint. Everything else the UI offers is aggregated here from
the nearest finer native interval. `native` on each timeframe says which is
which, and the API returns it so the chart can be honest about what it drew.

    1m  5m  15m  1h        native      one Dhan interval, passed straight through
    1D                     native      the daily endpoint
    3m                     derived     from 1m
    30m                    derived     from 15m
    4h                     derived     from 1h
    1W  1M                 derived     from daily

Dhan's 25m interval is deliberately not exposed: nobody asked for it, and an
odd bucket width in a timeframe switcher reads as a bug.

## Bucket anchoring

Intraday aggregation is anchored to each IST trading day's FIRST bar, not to
midnight and not to a bar count. Anchoring to midnight would put a 4h bucket
boundary at 08:00, an hour before MCX opens, and the session's first bucket
would hold one hour of trading. Anchoring to a count would silently mis-group
every bucket after a gap in the data. Weekly bars are anchored to Monday and
monthly bars to the 1st, both in IST.

## Freshness

Responses are cached per (security id, timeframe) for a few seconds -- long
enough that flipping between timeframes does not re-hit Dhan, short enough that
the newest bar is never far behind the feed the chart is updating it from.
"""
import time as time_module
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src import config_utils
from src.core.time_utils import IST, ist_now
from src.logging_config import get_logger
from src.market.services import synthetic_candles
from src.market.services.dhan_charts_client import (
    Candle,
    ChartsError,
    ChartsRateLimited,
    DhanChartsClient,
    MAX_INTRADAY_DAYS,
)

logger = get_logger("market.candles")

SOURCE_INTRADAY = "intraday"
SOURCE_DAILY = "daily"

CALENDAR_WEEK = "week"
CALENDAR_MONTH = "month"

MINUTE = 60
HOUR = 60 * MINUTE
DAY = 24 * HOUR

# How many bars the chart is ever given for one timeframe. A chart cannot show
# more than a few thousand usefully, and the cap bounds both the response size
# and the synthetic generator's work.
MAX_CANDLES = 1500

# Enough trading days to build ten years of monthly bars before the cap above.
MAX_SYNTHETIC_DAILY = 3000

CACHE_TTL_INTRADAY_SECONDS = 15.0
CACHE_TTL_DAILY_SECONDS = 300.0


class CandleError(Exception):
    """Candles could not be produced. Carries a message meant for the UI."""


class UnknownTimeframe(CandleError):
    pass


class CandlesUnavailable(CandleError):
    """No credentials and no synthetic feed -- there is nothing to draw."""


@dataclass(frozen=True)
class Timeframe:
    key: str                      # what the API and the UI call it
    label: str                    # button text
    step_seconds: int             # bucket width
    source: str                   # SOURCE_INTRADAY | SOURCE_DAILY
    lookback_days: int            # how far back to ask
    native_interval: Optional[int] = None   # Dhan intraday minutes, when native
    aggregate_from: Optional[int] = None    # Dhan intraday minutes to aggregate
    calendar: Optional[str] = None          # CALENDAR_WEEK | CALENDAR_MONTH

    @property
    def native(self) -> bool:
        return self.aggregate_from is None and self.calendar is None

    def as_payload(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "stepSeconds": self.step_seconds,
            "source": self.source,
            "native": self.native,
            "derivedFrom": (
                f"{self.aggregate_from}m" if self.aggregate_from
                else ("1D" if self.calendar else None)
            ),
            "lookbackDays": self.lookback_days,
        }


# Ordered as the switcher shows them.
TIMEFRAMES: Dict[str, Timeframe] = {
    timeframe.key: timeframe
    for timeframe in (
        Timeframe("1m", "1m", 1 * MINUTE, SOURCE_INTRADAY, 5, native_interval=1),
        Timeframe("3m", "3m", 3 * MINUTE, SOURCE_INTRADAY, 10, aggregate_from=1),
        Timeframe("5m", "5m", 5 * MINUTE, SOURCE_INTRADAY, 20, native_interval=5),
        Timeframe("15m", "15m", 15 * MINUTE, SOURCE_INTRADAY, 45, native_interval=15),
        Timeframe("30m", "30m", 30 * MINUTE, SOURCE_INTRADAY, 60, aggregate_from=15),
        Timeframe("1h", "1h", 1 * HOUR, SOURCE_INTRADAY, MAX_INTRADAY_DAYS, native_interval=60),
        Timeframe("4h", "4h", 4 * HOUR, SOURCE_INTRADAY, MAX_INTRADAY_DAYS, aggregate_from=60),
        Timeframe("1D", "1D", DAY, SOURCE_DAILY, 730),
        Timeframe("1W", "1W", 7 * DAY, SOURCE_DAILY, 1825, calendar=CALENDAR_WEEK),
        Timeframe("1M", "1M", 30 * DAY, SOURCE_DAILY, 3650, calendar=CALENDAR_MONTH),
    )
}

DEFAULT_TIMEFRAME = "5m"


def get_timeframe(key: str) -> Timeframe:
    timeframe = TIMEFRAMES.get(key)
    if timeframe is None:
        raise UnknownTimeframe(
            f"Unknown timeframe {key!r}. Available: {', '.join(TIMEFRAMES)}"
        )
    return timeframe


# --- aggregation -----------------------------------------------------------
def _merge(bucket_time: int, group: List[Candle]) -> Candle:
    volumes = [candle.volume for candle in group if candle.volume is not None]
    return Candle(
        time=bucket_time,
        open=group[0].open,
        high=max(candle.high for candle in group),
        low=min(candle.low for candle in group),
        close=group[-1].close,
        volume=sum(volumes) if volumes else None,
        open_interest=group[-1].open_interest,
    )


def aggregate_intraday(candles: List[Candle], step_seconds: int) -> List[Candle]:
    """Group intraday bars into wider ones, anchored to each IST day's open."""
    if step_seconds <= 0:
        raise CandleError("Aggregation step must be positive")
    if not candles:
        return []

    merged: List[Candle] = []
    group: List[Candle] = []
    bucket_time: Optional[int] = None
    anchor: Optional[int] = None
    anchor_day: Optional[Any] = None

    for candle in sorted(candles, key=lambda item: item.time):
        day = datetime.fromtimestamp(candle.time, IST).date()
        if day != anchor_day:
            anchor_day = day
            anchor = candle.time
        offset = candle.time - anchor
        start = anchor + (offset // step_seconds) * step_seconds
        if start != bucket_time:
            if group:
                merged.append(_merge(bucket_time, group))
            group = []
            bucket_time = start
        group.append(candle)

    if group:
        merged.append(_merge(bucket_time, group))
    return merged


def _calendar_bucket(candle_time: int, calendar: str) -> int:
    moment = datetime.fromtimestamp(candle_time, IST)
    if calendar == CALENDAR_WEEK:
        start = moment.date() - timedelta(days=moment.weekday())
    elif calendar == CALENDAR_MONTH:
        start = moment.date().replace(day=1)
    else:
        raise CandleError(f"Unknown calendar bucket {calendar!r}")
    return int(datetime(start.year, start.month, start.day, tzinfo=IST).timestamp())


def aggregate_calendar(candles: List[Candle], calendar: str) -> List[Candle]:
    """Group daily bars into ISO weeks (Monday) or calendar months, in IST."""
    if not candles:
        return []

    merged: List[Candle] = []
    group: List[Candle] = []
    bucket_time: Optional[int] = None

    for candle in sorted(candles, key=lambda item: item.time):
        start = _calendar_bucket(candle.time, calendar)
        if start != bucket_time:
            if group:
                merged.append(_merge(bucket_time, group))
            group = []
            bucket_time = start
        group.append(candle)

    if group:
        merged.append(_merge(bucket_time, group))
    return merged


def apply_timeframe(candles: List[Candle], timeframe: Timeframe) -> List[Candle]:
    """Reduce native candles to the requested timeframe."""
    if timeframe.calendar:
        return aggregate_calendar(candles, timeframe.calendar)
    if timeframe.aggregate_from:
        return aggregate_intraday(candles, timeframe.step_seconds)
    return sorted(candles, key=lambda item: item.time)


# --- service ---------------------------------------------------------------
class CandleService:
    """Assembles candle history: Dhan when live, generated when synthetic."""

    def __init__(self, client: Optional[DhanChartsClient] = None) -> None:
        self.client = client or DhanChartsClient()
        self._cache: Dict[tuple, tuple] = {}

    # --- configuration -----------------------------------------------------
    def _exchange_segment(self, security_id: Optional[str] = None) -> str:
        """The segment to ask Dhan for, resolved from the instrument's strategy.

        The book's registered metadata carries the strategy key (attached once,
        when the subscription was built), so this is a dictionary lookup rather
        than a guess or a second database read.
        """
        from src.market.services.feed_manager import get_feed_manager
        from src.strategies.services.strategy_registry import get_strategy_registry

        registry = get_strategy_registry()
        if security_id is not None:
            meta = get_feed_manager().book.get(str(security_id)) or {}
            key = meta.get("strategyKey")
            if key:
                strategy = registry.get(key)
                if strategy is not None:
                    return strategy.exchange_segment
        running = registry.enabled()
        return (running[0] if running else registry.default()).exchange_segment

    @staticmethod
    def _synthetic_enabled() -> bool:
        return config_utils.get_property_value_boolean("market_feed.synthetic_feed", False)

    def timeframes(self) -> List[Dict[str, Any]]:
        return [timeframe.as_payload() for timeframe in TIMEFRAMES.values()]

    # --- instrument resolution ---------------------------------------------
    def _resolve_instrument_type(self, security_id: str) -> str:
        """The Dhan `instrument` enum for a subscribed instrument.

        Read from the book's registered contract metadata rather than guessed,
        because options (OPTFUT) and futures (FUTCOM) take different values and
        sending the wrong one is a silent empty chart.
        """
        from src.market.services.feed_manager import get_feed_manager

        row = get_feed_manager().book.get(str(security_id))
        instrument_type = (row or {}).get("instrumentType")
        if instrument_type:
            return str(instrument_type)
        raise CandleError(
            f"Instrument {security_id} is not in the subscribed book, so its "
            "contract type is unknown. Refresh the instrument master, or pick "
            "an instrument the feed is subscribed to."
        )

    def _last_price(self, security_id: str) -> Optional[float]:
        from src.market.services.feed_manager import get_feed_manager

        row = get_feed_manager().book.get(str(security_id)) or {}
        for field in ("ltp", "close", "open"):
            value = row.get(field)
            if value:
                return float(value)
        return None

    # --- cache -------------------------------------------------------------
    def _cache_ttl(self, timeframe: Timeframe) -> float:
        return (
            CACHE_TTL_DAILY_SECONDS
            if timeframe.source == SOURCE_DAILY
            else CACHE_TTL_INTRADAY_SECONDS
        )

    def _cached(self, key: tuple, timeframe: Timeframe) -> Optional[Dict[str, Any]]:
        entry = self._cache.get(key)
        if entry is None:
            return None
        stored_at, payload = entry
        if time_module.monotonic() - stored_at > self._cache_ttl(timeframe):
            return None
        return payload

    def invalidate(self) -> None:
        """Drop everything cached. Called when credentials or the feed mode change."""
        self._cache.clear()

    # --- fetching ----------------------------------------------------------
    async def _fetch_live(
        self, security_id: str, timeframe: Timeframe, now: datetime
    ) -> List[Candle]:
        instrument = self._resolve_instrument_type(security_id)
        segment = self._exchange_segment()
        # toDate is documented as non-inclusive, so ask through tomorrow to be
        # sure today's bars are included.
        to_date = now + timedelta(days=1)
        from_date = now - timedelta(days=timeframe.lookback_days)

        if timeframe.source == SOURCE_DAILY:
            return await self.client.fetch_daily(
                security_id, segment, instrument, from_date, to_date
            )

        interval = timeframe.native_interval or timeframe.aggregate_from
        return await self.client.fetch_intraday(
            security_id, segment, instrument, interval, from_date, to_date
        )

    def _generate_synthetic(
        self, security_id: str, timeframe: Timeframe, now: datetime
    ) -> List[Candle]:
        last_price = self._last_price(security_id) or config_utils.get_property_value_float(
            "market_feed.synthetic_base_price", 6800.0
        )
        if timeframe.source == SOURCE_DAILY:
            # Weekly and monthly bars are aggregated from daily ones, so the
            # daily series has to be generated in full before the cap applies
            # -- capping it first would silently shorten the monthly chart.
            daily_limit = MAX_SYNTHETIC_DAILY if timeframe.calendar else MAX_CANDLES
            daily = synthetic_candles.generate(
                security_id,
                DAY,
                timeframe.lookback_days,
                last_price,
                now,
                daily=True,
                limit=daily_limit,
            )
            return aggregate_calendar(daily, timeframe.calendar) if timeframe.calendar else daily
        return synthetic_candles.generate(
            security_id,
            timeframe.step_seconds,
            timeframe.lookback_days,
            last_price,
            now,
            limit=MAX_CANDLES,
        )

    async def get_candles(
        self, security_id: str, timeframe_key: str = DEFAULT_TIMEFRAME
    ) -> Dict[str, Any]:
        """History for one instrument at one timeframe, newest bar last."""
        security_id = str(security_id).strip()
        if not security_id:
            raise CandleError("A securityId is required")
        timeframe = get_timeframe(timeframe_key)

        synthetic = self._synthetic_enabled()
        cache_key = (security_id, timeframe.key, synthetic)
        cached = self._cached(cache_key, timeframe)
        if cached is not None:
            return cached

        now = ist_now()
        if synthetic:
            candles = self._generate_synthetic(security_id, timeframe, now)
        elif self.client.has_credentials():
            try:
                native = await self._fetch_live(security_id, timeframe, now)
            except ChartsRateLimited as error:
                raise CandleError(
                    "Dhan rate limited the chart request. Try again in a moment."
                ) from error
            except ChartsError as error:
                raise CandleError(str(error)) from error
            candles = apply_timeframe(native, timeframe)
        else:
            raise CandlesUnavailable(
                "No Dhan credentials are configured and the synthetic feed is "
                "off, so there is no candle history to draw. Add market-data "
                "credentials on the Settings page."
            )

        if len(candles) > MAX_CANDLES:
            candles = candles[-MAX_CANDLES:]

        payload = {
            "securityId": security_id,
            "timeframe": timeframe.key,
            "stepSeconds": timeframe.step_seconds,
            "native": timeframe.native,
            "source": timeframe.source,
            "synthetic": synthetic,
            "candles": [candle.as_payload() for candle in candles],
        }
        self._cache[cache_key] = (time_module.monotonic(), payload)
        logger.debug(
            "Candles for %s %s: %s bars (synthetic=%s native=%s)",
            security_id, timeframe.key, len(candles), synthetic, timeframe.native,
        )
        return payload


_candle_service: Optional[CandleService] = None


def get_candle_service() -> CandleService:
    global _candle_service
    if _candle_service is None:
        _candle_service = CandleService()
    return _candle_service
