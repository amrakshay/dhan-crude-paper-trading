"""IPO dashboard payloads. camelCase out, snake_case in Python (backend §1)."""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from src.core.time_utils import as_utc_aware
from src.ipo.database.db_models.ipo_model import IPO_ACTIONS


class GmpResponse(BaseModel):
    """A GMP reading and whether it can be trusted as current.

    `isStale` is not decoration. A page that showed a number with no age would
    let a two-day-old premium read as this morning's, which is the one thing
    this feature must not do.
    """

    gmp: Optional[Decimal] = None
    gmp_percent: Optional[Decimal] = Field(None, alias="gmpPercent")
    captured_at: Optional[datetime] = Field(None, alias="capturedAt")
    source_updated_at: Optional[datetime] = Field(None, alias="sourceUpdatedAt")
    is_stale: bool = Field(alias="isStale")
    stale_reason: Optional[str] = Field(None, alias="staleReason")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_serializer("captured_at", "source_updated_at")
    def _ts(self, value: Optional[datetime]) -> Optional[str]:
        return as_utc_aware(value).isoformat() if value else None


class IpoResponse(BaseModel):
    id: int
    source_id: int = Field(alias="sourceId")
    company_name: str = Field(alias="companyName")
    board: str
    status: str

    issue_price: Optional[Decimal] = Field(None, alias="issuePrice")
    # The source's own spelling beside the number. It publishes ONE price, not
    # a band; the UI labels it "Issue price" for that reason.
    issue_price_text: Optional[str] = Field(None, alias="issuePriceText")
    lot_size: Optional[int] = Field(None, alias="lotSize")

    open_date: Optional[date] = Field(None, alias="openDate")
    close_date: Optional[date] = Field(None, alias="closeDate")
    listing_date: Optional[date] = Field(None, alias="listingDate")
    listing_price: Optional[Decimal] = Field(None, alias="listingPrice")
    # Derived from the two prices, never stored: two stored numbers that must
    # agree with a third are two chances to disagree.
    listing_gain: Optional[Decimal] = Field(None, alias="listingGain")
    listing_gain_percent: Optional[Decimal] = Field(None, alias="listingGainPercent")

    source_path: Optional[str] = Field(None, alias="sourcePath")
    first_seen_at: datetime = Field(alias="firstSeenAt")

    gmp: GmpResponse

    applied: bool
    applied_at: Optional[datetime] = Field(None, alias="appliedAt")
    mandate_accepted: bool = Field(alias="mandateAccepted")
    mandate_accepted_at: Optional[datetime] = Field(None, alias="mandateAcceptedAt")
    rejected: bool
    rejected_at: Optional[datetime] = Field(None, alias="rejectedAt")

    is_outstanding: bool = Field(alias="isOutstanding")
    pending_steps: List[str] = Field(alias="pendingSteps")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_serializer(
        "first_seen_at", "applied_at", "mandate_accepted_at", "rejected_at"
    )
    def _ts(self, value: Optional[datetime]) -> Optional[str]:
        return as_utc_aware(value).isoformat() if value else None


class IpoTabResponse(BaseModel):
    """One tab, plus what the reader needs in order to trust it."""

    tab: str
    ipos: List[IpoResponse]
    # The IST day this tab is about. `closing-next` carries the day it resolved
    # to, so the page can print it rather than saying "next".
    day: Optional[date] = None
    # Said on the tab, not only in a document: there is no holiday list in this
    # codebase, so a public holiday can put an IPO here a day early.
    caveat: Optional[str] = None
    last_refresh_at: Optional[datetime] = Field(None, alias="lastRefreshAt")
    last_refresh_detail: Optional[str] = Field(None, alias="lastRefreshDetail")

    model_config = ConfigDict(populate_by_name=True)

    @field_serializer("last_refresh_at")
    def _ts(self, value: Optional[datetime]) -> Optional[str]:
        return as_utc_aware(value).isoformat() if value else None


class SetActionRequest(BaseModel):
    """One of the three actions, and its new value.

    A VALUE rather than a verb, because all three are togglable -- Applied and
    Accepted because a mis-tap has to be undoable, Reject because changing your
    mind about an IPO has to be possible before it closes.
    """

    action: str = Field(..., description=f"One of: {', '.join(IPO_ACTIONS)}")
    value: bool


class IpoStatusResponse(BaseModel):
    """What the clock is doing, for the page's footer."""

    enabled: bool
    running: bool
    daily_refresh_at: str = Field(alias="dailyRefreshAt")
    reminder_from: str = Field(alias="reminderFrom")
    reminder_to: str = Field(alias="reminderTo")
    last_refresh_at: Optional[datetime] = Field(None, alias="lastRefreshAt")
    last_refresh_detail: Optional[str] = Field(None, alias="lastRefreshDetail")
    last_error: Optional[str] = Field(None, alias="lastError")
    stored_ipos: int = Field(alias="storedIpos")

    model_config = ConfigDict(populate_by_name=True)

    @field_serializer("last_refresh_at")
    def _ts(self, value: Optional[datetime]) -> Optional[str]:
        return as_utc_aware(value).isoformat() if value else None
