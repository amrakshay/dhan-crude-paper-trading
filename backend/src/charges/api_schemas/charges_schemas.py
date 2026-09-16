"""Charges API payloads."""
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ChargeEstimateRequest(BaseModel):
    side: str = Field(..., description="BUY or SELL")
    premium: Decimal = Field(..., ge=0, description="Option premium, rupees per barrel")
    lot_size: int = Field(..., gt=0, alias="lotSize")
    lots: int = Field(1, gt=0)
    strike_price: Optional[Decimal] = Field(None, alias="strikePrice")

    model_config = ConfigDict(populate_by_name=True)


class ChargeComponentResponse(BaseModel):
    name: str
    amount: str
    rate: Optional[str] = None
    base: Optional[str] = None
    formula: str = ""
    note: str = ""


class ChargeBreakdownResponse(BaseModel):
    turnover: str
    brokerage: str
    ctt: str
    exchange_transaction_charge: str = Field(alias="exchangeTransactionCharge")
    sebi_turnover_fee: str = Field(alias="sebiTurnoverFee")
    stamp_duty: str = Field(alias="stampDuty")
    gst: str
    total: str
    rates_version: str = Field(alias="ratesVersion")
    rounding_mode: str = Field(alias="roundingMode")
    components: List[ChargeComponentResponse] = []

    model_config = ConfigDict(populate_by_name=True)


class RoundTripEstimateRequest(BaseModel):
    buy_premium: Decimal = Field(..., ge=0, alias="buyPremium")
    sell_premium: Decimal = Field(..., ge=0, alias="sellPremium")
    lot_size: int = Field(..., gt=0, alias="lotSize")
    lots: int = Field(1, gt=0)

    model_config = ConfigDict(populate_by_name=True)


class RateCardResponse(BaseModel):
    version: str
    currency: str
    rates: Dict[str, Any]
