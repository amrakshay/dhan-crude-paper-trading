"""Candle history: aggregation, the read-only Dhan client, and the endpoint.

The aggregation tests matter more than they look. Dhan serves 1/5/15/25/60
minute and daily bars; every other timeframe the UI offers is built here, so a
bucketing bug produces a chart that is wrong but entirely plausible.
"""
from datetime import datetime, timedelta

import pytest

from src.core.time_utils import IST
from src.market.services import synthetic_candles
from src.market.services.candle_service import (
    CandleService,
    CandlesUnavailable,
    TIMEFRAMES,
    UnknownTimeframe,
    aggregate_calendar,
    aggregate_intraday,
    apply_timeframe,
    get_timeframe,
)
from src.market.services.dhan_charts_client import (
    Candle,
    ChartsError,
    DhanChartsClient,
    ENDPOINT_DAILY,
    ENDPOINT_INTRADAY,
    MAX_INTRADAY_DAYS,
    NATIVE_INTRADAY_INTERVALS,
    parse_candles,
)


def _epoch(day: str, hhmm: str) -> int:
    """Epoch seconds for an IST wall-clock time, as the exchange quotes it."""
    moment = datetime.strptime(f"{day} {hhmm}", "%Y-%m-%d %H:%M").replace(tzinfo=IST)
    return int(moment.timestamp())


def _minute_bars(day: str, start: str, count: int, first_price: float = 100.0):
    """`count` consecutive 1-minute bars whose closes step up by 1."""
    base = _epoch(day, start)
    bars = []
    for index in range(count):
        price = first_price + index
        bars.append(
            Candle(
                time=base + index * 60,
                open=price,
                high=price + 0.5,
                low=price - 0.5,
                close=price,
                volume=10.0,
            )
        )
    return bars


# --- payload parsing -------------------------------------------------------
def test_candles_are_parsed_from_the_documented_parallel_arrays():
    candles = parse_candles(
        {
            "open": [10.0, 11.0],
            "high": [12.0, 13.0],
            "low": [9.0, 10.5],
            "close": [11.0, 12.5],
            "volume": [100, 200],
            "timestamp": [1_700_000_000, 1_700_000_060],
        }
    )

    assert [candle.time for candle in candles] == [1_700_000_000, 1_700_000_060]
    assert candles[0].open == 10.0 and candles[0].close == 11.0
    assert candles[1].volume == 200


def test_parsing_sorts_ascending_and_collapses_duplicate_timestamps():
    """lightweight-charts rejects a series that is not strictly ascending."""
    candles = parse_candles(
        {
            "open": [10.0, 20.0, 30.0],
            "high": [10.0, 20.0, 30.0],
            "low": [10.0, 20.0, 30.0],
            "close": [10.0, 20.0, 30.0],
            "timestamp": [200, 100, 200],
        }
    )

    times = [candle.time for candle in candles]
    assert times == sorted(times) == [100, 200]
    assert len(set(times)) == len(times)
    assert candles[-1].close == 30.0, "the later duplicate should win"


def test_a_row_with_a_missing_price_is_dropped_rather_than_defaulted():
    """A candle with an invented price is worse than a missing candle."""
    candles = parse_candles(
        {
            "open": [10.0, None],
            "high": [12.0, 13.0],
            "low": [9.0, 10.0],
            "close": [11.0, 12.0],
            "timestamp": [100, 200],
        }
    )

    assert [candle.time for candle in candles] == [100]


def test_arrays_of_disagreeing_length_are_rejected():
    with pytest.raises(ChartsError):
        parse_candles({"open": [1.0], "high": [], "low": [], "close": [], "timestamp": [1]})


# --- intraday aggregation --------------------------------------------------
def test_three_one_minute_bars_become_one_three_minute_bar():
    bars = _minute_bars("2026-09-16", "09:00", 3)

    merged = aggregate_intraday(bars, 180)

    assert len(merged) == 1
    candle = merged[0]
    assert candle.time == _epoch("2026-09-16", "09:00")
    assert candle.open == bars[0].open
    assert candle.close == bars[-1].close
    assert candle.high == max(bar.high for bar in bars)
    assert candle.low == min(bar.low for bar in bars)
    assert candle.volume == 30.0


def test_aggregation_is_anchored_to_the_session_open_not_to_midnight():
    """A 4h bucket anchored to midnight would break at 08:00, an hour before
    MCX opens, leaving the first bucket of the day one hour long."""
    bars = [
        Candle(time=_epoch("2026-09-16", "09:00"), open=1, high=1, low=1, close=1),
        Candle(time=_epoch("2026-09-16", "12:59"), open=2, high=2, low=2, close=2),
        Candle(time=_epoch("2026-09-16", "13:00"), open=3, high=3, low=3, close=3),
    ]

    merged = aggregate_intraday(bars, 4 * 3600)

    assert [candle.time for candle in merged] == [
        _epoch("2026-09-16", "09:00"),
        _epoch("2026-09-16", "13:00"),
    ]


def test_each_trading_day_starts_a_fresh_bucket():
    """Buckets must never span a session boundary."""
    bars = _minute_bars("2026-09-16", "23:28", 2) + _minute_bars("2026-09-17", "09:00", 1)

    merged = aggregate_intraday(bars, 15 * 60)

    assert len(merged) == 2
    assert merged[0].time == _epoch("2026-09-16", "23:28")
    assert merged[1].time == _epoch("2026-09-17", "09:00")


def test_a_gap_in_the_data_does_not_shift_later_buckets():
    """Counting bars instead of using their timestamps would mis-group
    everything after a missing minute."""
    bars = _minute_bars("2026-09-16", "09:00", 2)
    bars += _minute_bars("2026-09-16", "09:05", 1)   # 09:02-09:04 missing

    merged = aggregate_intraday(bars, 180)

    assert [candle.time for candle in merged] == [
        _epoch("2026-09-16", "09:00"),
        _epoch("2026-09-16", "09:03"),
    ]


def test_aggregating_an_empty_series_returns_an_empty_series():
    assert aggregate_intraday([], 300) == []
    assert aggregate_calendar([], "week") == []


# --- calendar aggregation --------------------------------------------------
def test_daily_bars_group_into_weeks_anchored_to_monday():
    days = [
        Candle(time=_epoch(day, "09:00"), open=1, high=9, low=0.5, close=index + 1)
        for index, day in enumerate(
            ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-21"]
        )
    ]

    weeks = aggregate_calendar(days, "week")

    assert len(weeks) == 2
    assert weeks[0].time == _epoch("2026-09-14", "00:00")   # Monday
    assert weeks[0].close == 3, "the week closes on its last session"
    assert weeks[1].time == _epoch("2026-09-21", "00:00")


def test_daily_bars_group_into_calendar_months():
    days = [
        Candle(time=_epoch(day, "09:00"), open=1, high=2, low=0.5, close=index + 1)
        for index, day in enumerate(["2026-09-16", "2026-09-30", "2026-10-01"])
    ]

    months = aggregate_calendar(days, "month")

    assert [candle.time for candle in months] == [
        _epoch("2026-09-01", "00:00"),
        _epoch("2026-10-01", "00:00"),
    ]
    assert months[0].close == 2


# --- the timeframe registry ------------------------------------------------
def test_every_offered_timeframe_resolves_to_something_dhan_actually_serves():
    """A button the backend would 400 on is a bug, not a feature."""
    for timeframe in TIMEFRAMES.values():
        if timeframe.source == "intraday":
            interval = timeframe.native_interval or timeframe.aggregate_from
            assert interval in NATIVE_INTRADAY_INTERVALS, timeframe.key
        else:
            assert timeframe.native_interval is None


def test_derived_timeframes_are_whole_multiples_of_their_source():
    for timeframe in TIMEFRAMES.values():
        if timeframe.aggregate_from:
            assert timeframe.step_seconds % (timeframe.aggregate_from * 60) == 0
            assert timeframe.step_seconds > timeframe.aggregate_from * 60


def test_the_odd_twenty_five_minute_interval_is_not_offered():
    """Dhan serves 25m. Nobody asked for it, and an odd bucket width in a
    timeframe switcher reads as a bug."""
    assert "25m" not in TIMEFRAMES
    assert [timeframe.step_seconds for timeframe in TIMEFRAMES.values()].count(25 * 60) == 0


def test_intraday_lookbacks_stay_inside_dhans_ninety_day_limit():
    for timeframe in TIMEFRAMES.values():
        if timeframe.source == "intraday":
            assert timeframe.lookback_days <= MAX_INTRADAY_DAYS, timeframe.key


def test_native_timeframes_are_passed_through_unaggregated():
    bars = _minute_bars("2026-09-16", "09:00", 4)

    assert apply_timeframe(bars, get_timeframe("1m")) == bars


def test_an_unknown_timeframe_is_rejected_by_name():
    with pytest.raises(UnknownTimeframe):
        get_timeframe("7m")


# --- the client is market data only ----------------------------------------
async def test_the_client_refuses_any_endpoint_but_the_two_chart_ones():
    client = DhanChartsClient()

    with pytest.raises(ChartsError, match="not permitted"):
        await client._post("/something-else", {})

    assert (ENDPOINT_DAILY, ENDPOINT_INTRADAY) == ("/charts/historical", "/charts/intraday")


async def test_the_client_refuses_an_interval_dhan_does_not_serve():
    client = DhanChartsClient()
    now = datetime.now()

    with pytest.raises(ChartsError, match="30-minute"):
        await client.fetch_intraday("565899", "MCX_COMM", "FUTCOM", 30, now, now)


async def test_the_client_refuses_a_span_beyond_the_documented_limit():
    client = DhanChartsClient()
    now = datetime.now()

    with pytest.raises(ChartsError, match="90 days"):
        await client.fetch_intraday(
            "565899", "MCX_COMM", "FUTCOM", 5, now - timedelta(days=120), now
        )


async def test_the_client_refuses_to_call_dhan_without_credentials(monkeypatch):
    from src import config_utils

    monkeypatch.setattr(config_utils, "get_property_value", lambda key, default=None: "")
    client = DhanChartsClient()

    with pytest.raises(ChartsError, match="credentials"):
        await client._post(ENDPOINT_DAILY, {})


# --- synthetic history -----------------------------------------------------
def test_synthetic_candles_are_deterministic():
    """A page refresh must not reshuffle the past."""
    now = datetime(2026, 9, 16, 15, 0, tzinfo=IST)
    kwargs = dict(step_seconds=300, lookback_days=2, last_price=6800.0, now=now)

    first = synthetic_candles.generate("565899", **kwargs)
    second = synthetic_candles.generate("565899", **kwargs)

    assert first == second
    assert first, "expected some bars"


def test_synthetic_candles_end_at_the_live_price():
    """The newest bar is what the live feed then updates, so it must line up."""
    now = datetime(2026, 9, 16, 15, 0, tzinfo=IST)

    candles = synthetic_candles.generate(
        "565899", step_seconds=300, lookback_days=2, last_price=6543.0, now=now
    )

    assert candles[-1].close == pytest.approx(6543.0, abs=0.01)


def test_synthetic_candles_are_ascending_unique_and_inside_market_hours():
    now = datetime(2026, 9, 16, 15, 0, tzinfo=IST)

    candles = synthetic_candles.generate(
        "565899", step_seconds=900, lookback_days=5, last_price=6800.0, now=now
    )

    times = [candle.time for candle in candles]
    assert times == sorted(times)
    assert len(set(times)) == len(times)
    for candle in candles:
        moment = datetime.fromtimestamp(candle.time, IST)
        assert moment.weekday() < 5, "MCX does not trade at the weekend"
        assert 9 <= moment.hour <= 23
        assert candle.time <= int(now.timestamp())


def test_a_synthetic_bar_is_internally_consistent():
    now = datetime(2026, 9, 16, 15, 0, tzinfo=IST)

    for candle in synthetic_candles.generate(
        "565899", step_seconds=300, lookback_days=1, last_price=6800.0, now=now
    ):
        assert candle.high >= max(candle.open, candle.close)
        assert candle.low <= min(candle.open, candle.close)
        assert candle.low > 0


def test_different_instruments_get_different_synthetic_histories():
    now = datetime(2026, 9, 16, 15, 0, tzinfo=IST)
    kwargs = dict(step_seconds=300, lookback_days=2, last_price=6800.0, now=now)

    assert synthetic_candles.generate("565899", **kwargs) != synthetic_candles.generate(
        "576266", **kwargs
    )


# --- the service -----------------------------------------------------------
async def test_synthetic_mode_returns_bars_flagged_as_synthetic():
    """conftest runs the whole suite with DHAN_SYNTHETIC_FEED=true."""
    payload = await CandleService().get_candles("565899", "5m")

    assert payload["synthetic"] is True
    assert payload["timeframe"] == "5m"
    assert payload["candles"], "synthetic mode should still draw a chart"
    assert payload["candles"][0]["time"] < payload["candles"][-1]["time"]


async def test_the_service_never_invents_history_when_the_feed_is_real(monkeypatch):
    """No credentials and no synthetic flag must be an error, not fake bars."""
    from src.market.services import candle_service

    monkeypatch.setattr(candle_service.CandleService, "_synthetic_enabled", staticmethod(lambda: False))
    service = candle_service.CandleService()
    monkeypatch.setattr(service.client, "has_credentials", lambda: False)

    with pytest.raises(CandlesUnavailable):
        await service.get_candles("565899", "5m")


async def test_a_repeat_request_is_served_from_the_cache():
    service = CandleService()

    first = await service.get_candles("565899", "15m")
    second = await service.get_candles("565899", "15m")

    assert first is second, "the second request should not regenerate anything"
    service.invalidate()
    assert await service.get_candles("565899", "15m") is not first


async def test_the_service_rejects_an_unknown_timeframe():
    with pytest.raises(UnknownTimeframe):
        await CandleService().get_candles("565899", "42m")


async def test_the_service_rejects_a_blank_security_id():
    from src.market.services.candle_service import CandleError

    with pytest.raises(CandleError):
        await CandleService().get_candles("  ", "5m")


# --- the endpoint ----------------------------------------------------------
async def test_candles_require_a_session(api_client):
    response = await api_client.get("/api/market/candles?securityId=565899")

    assert response.status_code == 401


async def test_timeframes_require_a_session(api_client):
    assert (await api_client.get("/api/market/timeframes")).status_code == 401


async def test_the_timeframe_list_is_served_to_the_ui(auth_client):
    response = await auth_client.get("/api/market/timeframes")

    assert response.status_code == 200
    body = response.json()
    keys = [option["key"] for option in body["timeframes"]]
    assert keys == ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1D", "1W", "1M"]
    assert body["default"] in keys
    derived = {option["key"]: option["derivedFrom"] for option in body["timeframes"]}
    assert derived["3m"] == "1m" and derived["30m"] == "15m" and derived["4h"] == "60m"
    assert derived["1W"] == "1D" and derived["5m"] is None


async def test_the_endpoint_returns_candles_for_the_near_future(auth_client):
    response = await auth_client.get(
        "/api/market/candles?securityId=565899&timeframe=15m"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["securityId"] == "565899"
    assert body["stepSeconds"] == 900
    assert body["native"] is True
    assert body["synthetic"] is True
    assert len(body["candles"]) > 1
    for candle in body["candles"][:5]:
        assert {"time", "open", "high", "low", "close"} <= set(candle)


async def test_the_endpoint_rejects_a_timeframe_it_does_not_serve(auth_client):
    response = await auth_client.get(
        "/api/market/candles?securityId=565899&timeframe=25m"
    )

    assert response.status_code == 400
    assert "25m" in response.json()["detail"]
