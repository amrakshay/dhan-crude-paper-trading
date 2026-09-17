"""Response models for the system health endpoint.

The nested sections are typed as `Dict[str, Any]`, the same way
`FeedStatusResponse` types the status dicts it passes through. Re-declaring
forty fields that already have a single producer would be a second place to
keep in sync, and the producers here are the components' own `status()` /
`stats()` methods — the schema would drift from them, not guard them.

What *is* declared is the top-level shape, because that is the contract the
page renders against.
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class HealthSummary(BaseModel):
    """The headline verdict, so the page answers the question before the cards."""

    healthy: bool
    problems: List[str] = Field(default_factory=list)
    synthetic: bool = False


class SystemHealthResponse(BaseModel):
    generated_at_ms: int = Field(alias="generatedAtMs")
    summary: HealthSummary

    process: Dict[str, Any]
    tasks: Dict[str, Any]
    upstream_feed: Dict[str, Any] = Field(alias="upstreamFeed")
    browser_sockets: Dict[str, Any] = Field(alias="browserSockets")
    dhan_api: Dict[str, Any] = Field(alias="dhanApi")
    credentials: Dict[str, Any]
    book: Dict[str, Any] = Field(default_factory=dict)
    market: Dict[str, Any] = Field(default_factory=dict)
    workers: Dict[str, Any] = Field(default_factory=dict)
    data_freshness: Dict[str, Any] = Field(alias="dataFreshness")
    # Which strategies and capabilities are running. Public facts only -- a
    # key, a label, an underlying and a segment, all of which the Option Chain
    # page already shows -- so there is nothing here to redact.
    features: Dict[str, Any] = Field(default_factory=dict)
    problems: Dict[str, Any]

    model_config = ConfigDict(populate_by_name=True)


class ProblemEntry(BaseModel):
    """One buffered WARNING+ record. Already redacted before it gets here."""

    timestamp_ms: int = Field(alias="timestampMs")
    level: str
    logger: str
    message: str

    model_config = ConfigDict(populate_by_name=True)


class ProblemsResponse(BaseModel):
    available: bool
    entries: List[ProblemEntry] = Field(default_factory=list)
    counts: Dict[str, int] = Field(default_factory=dict)
    capacity: int = 0
    note: Optional[str] = None
