"""Instrument API payloads."""
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class InstrumentResponse(BaseModel):
    security_id: str = Field(alias="securityId")
    exchange_segment: str = Field(alias="exchangeSegment")
    instrument_type: str = Field(alias="instrumentType")
    underlying_symbol: str = Field(alias="underlyingSymbol")
    trading_symbol: str = Field(alias="tradingSymbol")
    display_name: Optional[str] = Field(None, alias="displayName")
    expiry_date: Optional[date] = Field(None, alias="expiryDate")
    strike_price: Optional[Decimal] = Field(None, alias="strikePrice")
    option_type: Optional[str] = Field(None, alias="optionType")
    lot_size: int = Field(alias="lotSize")
    tick_size: Optional[Decimal] = Field(None, alias="tickSize")
    is_active: bool = Field(alias="isActive")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class ChainRowResponse(BaseModel):
    strike_price: Decimal = Field(alias="strikePrice")
    call: Optional[InstrumentResponse] = None
    put: Optional[InstrumentResponse] = None

    model_config = ConfigDict(populate_by_name=True)


class ChainResponse(BaseModel):
    expiry: date
    underlying_symbol: str = Field(alias="underlyingSymbol")
    underlying_future: Optional[InstrumentResponse] = Field(None, alias="underlyingFuture")
    strike_step: Optional[Decimal] = Field(None, alias="strikeStep")
    rows: List[ChainRowResponse] = []

    model_config = ConfigDict(populate_by_name=True)


class ExpiryListResponse(BaseModel):
    option_expiries: List[date] = Field(alias="optionExpiries")
    futures_expiries: List[date] = Field(alias="futuresExpiries")
    # Option expiry -> the futures contract it settles against. These roll on
    # different dates, so the mapping is explicit rather than inferred.
    underlying_future_by_expiry: Dict[str, Optional[str]] = Field(
        default_factory=dict, alias="underlyingFutureByExpiry"
    )

    model_config = ConfigDict(populate_by_name=True)


class RefreshResponse(BaseModel):
    downloaded: bool
    rows_scanned: int = Field(alias="rowsScanned")
    rows_matched: int = Field(alias="rowsMatched")
    inserted: int
    updated: int
    unchanged: int
    deactivated: int
    expiries: List[str]
    duration_seconds: float = Field(alias="durationSeconds")
    warnings: List[str] = []

    model_config = ConfigDict(populate_by_name=True)


class InstrumentStatusResponse(BaseModel):
    instrument_count: int = Field(alias="instrumentCount")
    last_refreshed_at: Optional[str] = Field(None, alias="lastRefreshedAt")
    cache_fresh: bool = Field(alias="cacheFresh")
    option_expiries: List[date] = Field(default_factory=list, alias="optionExpiries")
    near_future: Optional[InstrumentResponse] = Field(None, alias="nearFuture")

    model_config = ConfigDict(populate_by_name=True)
