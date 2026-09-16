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
