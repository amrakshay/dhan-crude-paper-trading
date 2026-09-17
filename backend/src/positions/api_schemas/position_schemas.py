"""Position API payloads."""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from src.core.time_utils import as_utc_aware


class PositionResponse(BaseModel):
    id: int
    strategy_key: str = Field(alias="strategyKey")
    portfolio_id: int = Field(alias="portfolioId")
    # Whether that strategy is currently RUNNING. A position held under a
    # disabled strategy is still real and still counts, but nothing is marking
    # it any more, so the UI has to say so rather than show a stale price.
    strategy_enabled: bool = Field(True, alias="strategyEnabled")
    strategy_label: Optional[str] = Field(None, alias="strategyLabel")
    security_id: str = Field(alias="securityId")
    trading_symbol: str = Field(alias="tradingSymbol")
    expiry_date: Optional[date] = Field(None, alias="expiryDate")
    strike_price: Optional[Decimal] = Field(None, alias="strikePrice")
    option_type: Optional[str] = Field(None, alias="optionType")
    lot_size: int = Field(alias="lotSize")
    net_quantity: int = Field(alias="netQuantity")
    net_lots: Optional[float] = Field(None, alias="netLots")
    average_price: Decimal = Field(alias="averagePrice")
    realized_pnl: Decimal = Field(alias="realizedPnl")
    total_charges: Decimal = Field(alias="totalCharges")
    is_open: bool = Field(alias="isOpen")
    opened_at: datetime = Field(alias="openedAt")
    closed_at: Optional[datetime] = Field(None, alias="closedAt")

    # Live marks, merged from the in-memory book.
    mark_price: Optional[Decimal] = Field(None, alias="markPrice")
    unrealized_pnl: Optional[Decimal] = Field(None, alias="unrealizedPnl")
    # None rather than 0 when there is no live price -- a position with an
    # unknown mark must not read as flat.
    has_mark: bool = Field(False, alias="hasMark")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_serializer("opened_at", "closed_at")
    def _ts(self, value: Optional[datetime]) -> Optional[str]:
        return as_utc_aware(value).isoformat() if value else None


class PositionSummaryResponse(BaseModel):
    open_positions: int = Field(alias="openPositions")
    total_realized_pnl: Decimal = Field(alias="totalRealizedPnl")
    total_unrealized_pnl: Optional[Decimal] = Field(None, alias="totalUnrealizedPnl")
    total_charges: Decimal = Field(alias="totalCharges")
    net_pnl: Optional[Decimal] = Field(None, alias="netPnl")
    positions_without_marks: int = Field(0, alias="positionsWithoutMarks")

    model_config = ConfigDict(populate_by_name=True)


class PositionListResponse(BaseModel):
    positions: List[PositionResponse]
    summary: PositionSummaryResponse


class ClosePositionRequest(BaseModel):
    lots: Optional[int] = Field(None, gt=0, description="Omit to close the whole position")
    order_type: str = Field("MARKET", alias="orderType")
    limit_price: Optional[Decimal] = Field(None, alias="limitPrice")

    model_config = ConfigDict(populate_by_name=True)
