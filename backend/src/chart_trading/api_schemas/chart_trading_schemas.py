"""Chart trading payloads. camelCase on the wire, snake_case in Python."""
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ChartClickRequest(BaseModel):
    """One click on the chart's Buy or Sell button."""

    security_id: str = Field(alias="securityId", description="The charted contract")
    side: str = Field(description="BUY buys the ATM call, SELL buys the ATM put")
    expiry: Optional[date] = Field(None, description="Defaults to the nearest expiry")
    lots: Optional[int] = Field(None, ge=1)
    portfolio_id: Optional[int] = Field(
        None,
        alias="portfolioId",
        description=(
            "Whose money this click spends. Required once more than one "
            "portfolio is active -- the server refuses rather than guessing."
        ),
    )

    model_config = ConfigDict(populate_by_name=True)


class ChartLevelsRequest(BaseModel):
    """Where the dragged stop-loss / take-profit lines now sit.

    Both are prices of the UNDERLYING FUTURE. Omitting a field leaves it as it
    was; the explicit clear flags remove a line, because null cannot mean both
    "unchanged" and "removed".
    """

    stop_loss: Optional[Decimal] = Field(None, alias="stopLoss")
    take_profit: Optional[Decimal] = Field(None, alias="takeProfit")
    clear_stop_loss: bool = Field(False, alias="clearStopLoss")
    clear_take_profit: bool = Field(False, alias="clearTakeProfit")

    model_config = ConfigDict(populate_by_name=True)


class ChartTradeResponse(BaseModel):
    id: int
    chart_side: str = Field(alias="chartSide")
    option_security_id: str = Field(alias="optionSecurityId")
    option_symbol: str = Field(alias="optionSymbol")
    option_type: str = Field(alias="optionType")
    strike_price: Optional[Decimal] = Field(None, alias="strikePrice")
    expiry_date: Optional[date] = Field(None, alias="expiryDate")
    lots: int
    quantity: int = 0
    average_price: Optional[Decimal] = Field(None, alias="averagePrice")
    mark_price: Optional[Decimal] = Field(None, alias="markPrice")
    underlying_at_entry: Optional[Decimal] = Field(None, alias="underlyingAtEntry")
    stop_loss_level: Optional[Decimal] = Field(None, alias="stopLossLevel")
    take_profit_level: Optional[Decimal] = Field(None, alias="takeProfitLevel")
    unrealized_pnl: Optional[Decimal] = Field(None, alias="unrealizedPnl")
    realized_pnl: Optional[Decimal] = Field(None, alias="realizedPnl")
    charges: Optional[Decimal] = None
    net_pnl: Optional[Decimal] = Field(None, alias="netPnl")
    opened_at: Optional[datetime] = Field(None, alias="openedAt")

    model_config = ConfigDict(populate_by_name=True)


class ChartStateResponse(BaseModel):
    """The chart's own position. `trade` is null when flat."""

    underlying_security_id: str = Field(alias="underlyingSecurityId")
    underlying_price: Optional[Decimal] = Field(None, alias="underlyingPrice")
    trade: Optional[ChartTradeResponse] = None

    model_config = ConfigDict(populate_by_name=True)


class ChartSidePreview(BaseModel):
    """What one button would buy right now, and what it would cost."""

    security_id: Optional[str] = Field(None, alias="securityId")
    trading_symbol: Optional[str] = Field(None, alias="tradingSymbol")
    option_type: Optional[str] = Field(None, alias="optionType")
    strike_price: Optional[Decimal] = Field(None, alias="strikePrice")
    expiry_date: Optional[date] = Field(None, alias="expiryDate")
    lots: Optional[int] = None
    quantity: Optional[int] = None
    estimated_price: Optional[Decimal] = Field(None, alias="estimatedPrice")
    estimated_charges: Optional[Decimal] = Field(None, alias="estimatedCharges")
    gross_value: Optional[Decimal] = Field(None, alias="grossValue")
    net_amount: Optional[Decimal] = Field(None, alias="netAmount")
    would_partially_fill: Optional[bool] = Field(None, alias="wouldPartiallyFill")
    rejection_reason: Optional[str] = Field(None, alias="rejectionReason")
    underlying_price: Optional[Decimal] = Field(None, alias="underlyingPrice")
    error: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class ChartPreviewResponse(BaseModel):
    underlying_security_id: str = Field(alias="underlyingSecurityId")
    underlying_price: Optional[Decimal] = Field(None, alias="underlyingPrice")
    expiries: List[date] = Field(default_factory=list)
    lots: int
    buy: ChartSidePreview
    sell: ChartSidePreview

    model_config = ConfigDict(populate_by_name=True)


class ChartClickResponse(BaseModel):
    action: str = Field(description="OPENED or CLOSED")
    state: ChartStateResponse
