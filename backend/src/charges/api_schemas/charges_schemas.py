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
    """One line item. `name` comes from the rate card, not from this codebase.

    `label` is what a UI should print, so a card can introduce a tax without
    the frontend needing a new hardcoded string for it.
    """

    name: str
    label: str = ""
    amount: str
    raw_amount: Optional[str] = Field(None, alias="rawAmount")
    rate: Optional[str] = None
    base: Optional[str] = None
    formula: str = ""
    note: str = ""

    model_config = ConfigDict(populate_by_name=True)


class ChargeBreakdownResponse(BaseModel):
    """Turnover, total and the line items.

    There are deliberately no per-tax fields: which taxes exist is a property
    of the rate card, and a fixed field per tax is what would make adding one
    a schema migration.
    """

    turnover: str
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
