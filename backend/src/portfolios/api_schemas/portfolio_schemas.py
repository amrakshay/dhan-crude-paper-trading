"""Portfolio API payloads.

Four numbers travel together everywhere -- cash, blocked margin, available and
equity -- because collapsing them into one "balance" is what makes a short
position's effect invisible. `marginIsEstimate` is always true and is part of
the payload rather than a note in the docs, so a UI cannot print the figure
without having been told.
"""
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from src.core.time_utils import as_utc_aware


class PortfolioBalanceResponse(BaseModel):
    cash: Decimal
    blocked_margin: Decimal = Field(alias="blockedMargin")
    # ALWAYS true. Real margin is SPAN + exposure from exchange files this tool
    # does not consume; this is a configured proxy so a balance means something
    # when a short is open.
    margin_is_estimate: bool = Field(True, alias="marginIsEstimate")
    available: Decimal
    # None when any open position has no mark. An equity number that quietly
    # treats an unmarked position as worthless is the worst failure this page
    # can have, so it is withheld rather than guessed.
    equity: Optional[Decimal] = None
    mark_to_market: Optional[Decimal] = Field(None, alias="markToMarket")
    open_positions: int = Field(0, alias="openPositions")
    unmarked_positions: int = Field(0, alias="unmarkedPositions")
    unmarked_strategies: List[str] = Field(default_factory=list, alias="unmarkedStrategies")
    equity_includes_unmarked: bool = Field(False, alias="equityIncludesUnmarked")

    model_config = ConfigDict(populate_by_name=True)


class PortfolioResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    status: str
    strategies: List[str] = Field(default_factory=list)
    balance: Optional[PortfolioBalanceResponse] = None
    created_at: Optional[datetime] = Field(None, alias="createdAt")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_serializer("created_at")
    def _ts(self, value: Optional[datetime]) -> Optional[str]:
        return as_utc_aware(value).isoformat() if value else None


class PortfolioListResponse(BaseModel):
    portfolios: List[PortfolioResponse]


class CreatePortfolioRequest(BaseModel):
    name: str = Field(..., max_length=80)
    description: Optional[str] = Field(None, max_length=255)
    strategies: List[str] = Field(default_factory=list)
    opening_balance: Decimal = Field(
        Decimal("0"),
        ge=0,
        alias="openingBalance",
        description="Recorded as the first DEPOSIT in the ledger.",
    )

    model_config = ConfigDict(populate_by_name=True)


class UpdatePortfolioRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=80)
    description: Optional[str] = Field(None, max_length=255)
    strategies: Optional[List[str]] = None

    model_config = ConfigDict(populate_by_name=True)


class MoneyRequest(BaseModel):
    amount: Decimal = Field(..., gt=0)
    note: Optional[str] = Field(None, max_length=255)

    model_config = ConfigDict(populate_by_name=True)


class LedgerEntryResponse(BaseModel):
    id: int
    entry_type: str = Field(alias="entryType")
    # SIGNED: positive is money in, negative is money out.
    amount: Decimal
    order_id: Optional[int] = Field(None, alias="orderId")
    note: Optional[str] = None
    entry_at: datetime = Field(alias="entryAt")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_serializer("entry_at")
    def _ts(self, value: datetime) -> str:
        return as_utc_aware(value).isoformat()


class LedgerResponse(BaseModel):
    portfolio_id: int = Field(alias="portfolioId")
    entries: List[LedgerEntryResponse]
    total: int
    page: int
    size: int
    balance: PortfolioBalanceResponse

    model_config = ConfigDict(populate_by_name=True)
