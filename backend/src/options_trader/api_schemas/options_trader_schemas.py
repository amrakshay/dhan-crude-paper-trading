"""Response shapes for the Options Trader page. camelCase on the wire."""
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class UnderlyingResponse(BaseModel):
    symbol: str
    kind: str
    lot_size: int = Field(alias="lotSize")

    model_config = ConfigDict(populate_by_name=True)


class UnderlyingListResponse(BaseModel):
    strategy_key: str = Field(alias="strategyKey")
    underlyings: List[UnderlyingResponse]

    model_config = ConfigDict(populate_by_name=True)


class ChainResponse(BaseModel):
    """One chain. `rows` are per strike with optional `call` / `put` contracts.

    The contract dictionaries are passed through as built by the engine rather
    than modelled field by field: they carry live book values and option-chain
    values side by side, and both are optional by nature.
    """

    strategy_key: str = Field(alias="strategyKey")
    underlying: Dict[str, Any]
    expiry: str
    expiries: List[str]
    spot: Optional[float] = None
    spot_source: Optional[str] = Field(None, alias="spotSource")
    spot_security_id: Optional[str] = Field(None, alias="spotSecurityId")
    atm_strike: Optional[float] = Field(None, alias="atmStrike")
    strike_window: int = Field(alias="strikeWindow")
    rows: List[Dict[str, Any]]
    snapshot_note: Optional[str] = Field(None, alias="snapshotNote")
    synthetic: bool = False
    margin_note: str = Field(alias="marginNote")

    model_config = ConfigDict(populate_by_name=True)


class BasketLegRequest(BaseModel):
    security_id: str = Field(..., alias="securityId")
    side: str
    lots: int = Field(1, gt=0)
    order_type: str = Field("MARKET", alias="orderType")
    limit_price: Optional[Decimal] = Field(None, alias="limitPrice")

    model_config = ConfigDict(populate_by_name=True)


class BasketRequest(BaseModel):
    portfolio_id: int = Field(..., alias="portfolioId")
    product: Optional[str] = None
    legs: List[BasketLegRequest]

    model_config = ConfigDict(populate_by_name=True)


class ExitGroupRequest(BaseModel):
    portfolio_id: int = Field(..., alias="portfolioId")
    underlying: str
    expiry: Optional[date] = None

    model_config = ConfigDict(populate_by_name=True)
