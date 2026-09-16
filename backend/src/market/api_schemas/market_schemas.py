"""Market data API payloads."""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class FeedStatusResponse(BaseModel):
    feed: Dict[str, Any]
    greeks: Dict[str, Any] = Field(default_factory=dict)
    synthetic: bool
    book: Dict[str, Any]
    fanout: Dict[str, Any]
    market: Dict[str, Any]
    near_future_security_id: Optional[str] = Field(None, alias="nearFutureSecurityId")
    subscribed_expiries: List[str] = Field(default_factory=list, alias="subscribedExpiries")
    strike_window: int = Field(alias="strikeWindow")
    window_centre: Optional[float] = Field(None, alias="windowCentre")
    last_resync_ms: Optional[int] = Field(None, alias="lastResyncMs")
    error: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class SnapshotResponse(BaseModel):
    ts: int
    rows: List[Dict[str, Any]]
    status: Dict[str, Any]


class ResyncResponse(BaseModel):
    subscribed: int
    unsubscribed: int
    reason: Optional[str] = None


class CandleResponse(BaseModel):
    """Candle history for one instrument at one timeframe, oldest bar first.

    `native` is False when the timeframe was aggregated here from a finer Dhan
    interval, and `synthetic` is True when the bars were generated locally. The
    chart surfaces both rather than presenting every series as equally real.
    """

    security_id: str = Field(alias="securityId")
    timeframe: str
    step_seconds: int = Field(alias="stepSeconds")
    native: bool
    source: str
    synthetic: bool
    candles: List[Dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class TimeframeOption(BaseModel):
    key: str
    label: str
    step_seconds: int = Field(alias="stepSeconds")
    source: str
    native: bool
    derived_from: Optional[str] = Field(None, alias="derivedFrom")
    lookback_days: int = Field(alias="lookbackDays")

    model_config = ConfigDict(populate_by_name=True)


class TimeframesResponse(BaseModel):
    default: str
    timeframes: List[TimeframeOption] = Field(default_factory=list)
