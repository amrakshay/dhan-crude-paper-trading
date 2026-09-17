"""SYNTHETIC CANDLE HISTORY -- THIS IS NOT REAL.

Every bar produced here is generated locally. No upstream connection is opened
and no Dhan endpoint is contacted. It is the chart's counterpart to
synthetic_feed.py and is gated by the same switch: bars are only ever generated
when `market_feed.synthetic_feed` is on, in which case the whole application is
already showing a permanent SYNTHETIC banner and the candle response carries
`synthetic: true` so the chart can label itself too.

Nothing silently falls back to this. With the synthetic flag off and no
credentials, the candle endpoint reports an error, exactly as the feed does.

Properties chosen deliberately:

  * **Deterministic.** Each bar's random increment is derived from
    sha256(security_id, bar epoch), so the same bar is identical on every
    request. Reloading the page does not reshuffle the past.
  * **Anchored to the live price.** The walk is rescaled so the newest close
    equals the current book price, which is what the live ticks then update.
    The shape of history is fixed; only its level follows the feed.
  * **Inside market hours only.** Intraday bars are generated on the configured
    MCX session grid (09:00-23:30 IST, trading days only), so the chart never
    shows a bar at an hour the exchange is shut.

Known limitation, documented rather than hidden: series at different timeframes
are generated independently, so the synthetic 1h bars are not exactly the
aggregate of the synthetic 5m bars. Real data does not have this problem.
"""
import hashlib
import math
from datetime import datetime, timedelta
from typing import List, Optional

from src import config_utils
from src.core.time_utils import IST, parse_hhmm
from src.market.services.dhan_charts_client import Candle

# Trading seconds in a year, used to scale the annualised volatility down to
# one bar: ~252 sessions of the 14.5-hour MCX day.
TRADING_SECONDS_PER_YEAR = 252 * 14.5 * 3600

# A wick is drawn as a fraction of the bar's own move, so a quiet bar gets a
# small wick and a violent one a large one.
WICK_FRACTION = 0.6


def _unit_draws(security_id: str, bar_epoch: int) -> tuple:
    """Three deterministic draws in [0, 1) for one bar."""
    digest = hashlib.sha256(f"{security_id}:{bar_epoch}".encode("utf-8")).digest()
    return (
        int.from_bytes(digest[0:8], "big") / 2 ** 64,
        int.from_bytes(digest[8:16], "big") / 2 ** 64,
        int.from_bytes(digest[16:24], "big") / 2 ** 64,
    )


def _gaussian(first: float, second: float) -> float:
    """Box-Muller, guarding the log against a zero draw."""
    first = max(first, 1e-12)
    return math.sqrt(-2.0 * math.log(first)) * math.cos(2.0 * math.pi * second)


def _market_hours() -> tuple:
    """Session bounds for the running strategy.

    The synthetic grid must match the hours the strategy actually trades, or
    the fake bars appear at times the real exchange is shut.
    """
    from src.strategies.services.strategy_registry import get_strategy_registry

    registry = get_strategy_registry()
    running = registry.enabled()
    hours = (running[0] if running else registry.default()).market_hours
    return hours.open, hours.close, {int(day) for day in hours.trading_days}


def _session_grid(
    step_seconds: int, lookback_days: int, now: datetime, limit: int
) -> List[int]:
    """Bar start times (epoch seconds) on the MCX session grid, ascending.

    Anchored to each day's 09:00 IST open, which is the same anchor the real
    aggregator uses, so a 4h bar starts at 09:00 rather than at midnight.
    """
    open_time, close_time, trading_days = _market_hours()
    times: List[int] = []
    day = now.date()
    days_examined = 0

    while days_examined <= lookback_days and len(times) < limit:
        if day.weekday() in trading_days:
            session_open = datetime.combine(day, open_time, tzinfo=IST)
            session_close = datetime.combine(day, close_time, tzinfo=IST)
            cutoff = min(session_close, now)
            bar = session_open
            day_times = []
            while bar < cutoff:
                day_times.append(int(bar.timestamp()))
                bar += timedelta(seconds=step_seconds)
            times = day_times + times
        day -= timedelta(days=1)
        days_examined += 1

    return times[-limit:] if len(times) > limit else times


def _daily_grid(lookback_days: int, now: datetime, limit: int) -> List[int]:
    """One bar per trading day, timestamped at that day's 09:00 IST open."""
    open_time, _close_time, trading_days = _market_hours()
    times: List[int] = []
    day = now.date()
    days_examined = 0

    while days_examined <= lookback_days and len(times) < limit:
        if day.weekday() in trading_days:
            times.append(int(datetime.combine(day, open_time, tzinfo=IST).timestamp()))
        day -= timedelta(days=1)
        days_examined += 1

    times.reverse()
    return times


def generate(
    security_id: str,
    step_seconds: int,
    lookback_days: int,
    last_price: float,
    now: datetime,
    daily: bool = False,
    limit: int = 1500,
    volatility: Optional[float] = None,
) -> List[Candle]:
    """A deterministic random walk on the session grid, ending at `last_price`."""
    if last_price <= 0:
        return []

    times = (
        _daily_grid(lookback_days, now, limit)
        if daily
        else _session_grid(step_seconds, lookback_days, now, limit)
    )
    if not times:
        return []

    annual_vol = (
        volatility
        if volatility is not None
        else config_utils.get_property_value_float("market_feed.synthetic_volatility", 0.35)
    )
    sigma = annual_vol * math.sqrt(step_seconds / TRADING_SECONDS_PER_YEAR)

    # Walk forward in log space from an arbitrary origin, then shift the whole
    # series so the final close lands exactly on the live price.
    log_prices: List[float] = []
    running = 0.0
    for bar_epoch in times:
        first, second, _third = _unit_draws(security_id, bar_epoch)
        running += sigma * _gaussian(first, second)
        log_prices.append(running)

    shift = math.log(last_price) - log_prices[-1]

    candles: List[Candle] = []
    previous_close: Optional[float] = None
    for index, bar_epoch in enumerate(times):
        close_price = math.exp(log_prices[index] + shift)
        open_price = previous_close if previous_close is not None else close_price * (
            1 - sigma
        )
        _first, _second, third = _unit_draws(security_id, bar_epoch)
        reach = abs(close_price - open_price) * WICK_FRACTION * (0.2 + third)
        high_price = max(open_price, close_price) + reach
        low_price = max(min(open_price, close_price) - reach, 0.01)
        volume = round(1000 * (0.5 + third) * (step_seconds / 60))

        candles.append(
            Candle(
                time=bar_epoch,
                open=round(open_price, 2),
                high=round(high_price, 2),
                low=round(low_price, 2),
                close=round(close_price, 2),
                volume=float(volume),
            )
        )
        previous_close = close_price

    return candles
