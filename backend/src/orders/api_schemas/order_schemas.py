"""Order API payloads."""
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from src.core.time_utils import as_utc_aware


class PlaceOrderRequest(BaseModel):
    security_id: str = Field(..., alias="securityId")
    side: str = Field(..., description="BUY or SELL")
    order_type: str = Field("MARKET", alias="orderType", description="MARKET or LIMIT")
    lots: int = Field(1, gt=0)
    limit_price: Optional[Decimal] = Field(None, alias="limitPrice")
    is_close_order: bool = Field(False, alias="isCloseOrder")

    model_config = ConfigDict(populate_by_name=True)


class PreviewOrderRequest(PlaceOrderRequest):
    pass


class OrderEventResponse(BaseModel):
    event_type: str = Field(alias="eventType")
    status: str
    event_at: datetime = Field(alias="eventAt")
    price: Optional[Decimal] = None
    quantity: Optional[int] = None
    message: Optional[str] = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_serializer("event_at")
    def _ts(self, value: datetime) -> str:
        # Millisecond precision matters for order history; isoformat on a
        # microsecond value keeps it.
        return as_utc_aware(value).isoformat()


class OrderFillResponse(BaseModel):
    fill_at: datetime = Field(alias="fillAt")
    price: Decimal
    quantity: int
    book_level: Optional[int] = Field(None, alias="bookLevel")
    slippage_ticks: int = Field(0, alias="slippageTicks")
    reference_price: Optional[Decimal] = Field(None, alias="referencePrice")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_serializer("fill_at")
    def _ts(self, value: datetime) -> str:
        return as_utc_aware(value).isoformat()


class OrderChargeResponse(BaseModel):
    turnover: Decimal
    brokerage: Decimal
    ctt: Decimal
    exchange_transaction_charge: Decimal = Field(alias="exchangeTransactionCharge")
    sebi_turnover_fee: Decimal = Field(alias="sebiTurnoverFee")
    stamp_duty: Decimal = Field(alias="stampDuty")
    gst: Decimal
    total_charges: Decimal = Field(alias="totalCharges")
    rates_version: Optional[str] = Field(None, alias="ratesVersion")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class OrderResponse(BaseModel):
    id: int
    client_order_id: str = Field(alias="clientOrderId")
    security_id: str = Field(alias="securityId")
    trading_symbol: str = Field(alias="tradingSymbol")
    expiry_date: Optional[date] = Field(None, alias="expiryDate")
    strike_price: Optional[Decimal] = Field(None, alias="strikePrice")
    option_type: Optional[str] = Field(None, alias="optionType")
    lot_size: int = Field(alias="lotSize")
    side: str
    order_type: str = Field(alias="orderType")
    lots: int
    quantity: int
    limit_price: Optional[Decimal] = Field(None, alias="limitPrice")
    status: str
    filled_quantity: int = Field(alias="filledQuantity")
    average_fill_price: Optional[Decimal] = Field(None, alias="averageFillPrice")
    rejection_reason: Optional[str] = Field(None, alias="rejectionReason")
    is_close_order: bool = Field(False, alias="isCloseOrder")
    placed_at: datetime = Field(alias="placedAt")
    last_event_at: datetime = Field(alias="lastEventAt")
    completed_at: Optional[datetime] = Field(None, alias="completedAt")
    events: List[OrderEventResponse] = []
    fills: List[OrderFillResponse] = []
    charges: Optional[OrderChargeResponse] = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_serializer("placed_at", "last_event_at", "completed_at")
    def _ts(self, value: Optional[datetime]) -> Optional[str]:
        return as_utc_aware(value).isoformat() if value else None


class OrderListResponse(BaseModel):
    orders: List[OrderResponse]
    total: int
    page: int
    size: int


class PreviewFillResponse(BaseModel):
    price: Decimal
    quantity: int
    book_level: Optional[int] = Field(None, alias="bookLevel")

    model_config = ConfigDict(populate_by_name=True)


class PreviewOrderResponse(BaseModel):
    """What the order would do. Shown before the trader confirms."""

    security_id: str = Field(alias="securityId")
    trading_symbol: str = Field(alias="tradingSymbol")
    side: str
    order_type: str = Field(alias="orderType")
    lots: int
    quantity: int
    lot_size: int = Field(alias="lotSize")
    limit_price: Optional[Decimal] = Field(None, alias="limitPrice")
    estimated_price: Optional[Decimal] = Field(None, alias="estimatedPrice")
    estimated_fill_quantity: int = Field(alias="estimatedFillQuantity")
    would_rest: bool = Field(alias="wouldRest")
    would_partially_fill: bool = Field(alias="wouldPartiallyFill")
    rejection_reason: Optional[str] = Field(None, alias="rejectionReason")
    note: Optional[str] = None
    gross_value: Decimal = Field(alias="grossValue")
    net_amount: Decimal = Field(alias="netAmount")
    charges: Optional[Dict[str, Any]] = None
    fills: List[PreviewFillResponse] = []

    model_config = ConfigDict(populate_by_name=True)
