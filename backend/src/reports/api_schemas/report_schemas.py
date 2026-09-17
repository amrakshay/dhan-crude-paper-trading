"""P&L report payloads."""
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class BucketResponse(BaseModel):
    key: str
    gross_pnl: Decimal = Field(alias="grossPnl")
    trades: int
    wins: int
    losses: int
    quantity: int

    model_config = ConfigDict(populate_by_name=True)


class EquityPointResponse(BaseModel):
    date: str
    gross_pnl: Decimal = Field(alias="grossPnl")
    charges: Decimal
    net_pnl: Decimal = Field(alias="netPnl")
    cumulative_gross: Decimal = Field(alias="cumulativeGross")
    cumulative_net: Decimal = Field(alias="cumulativeNet")
    # Deposits and withdrawals on this day, signed. Carried so the chart can
    # MARK them: a curve that jumps because money was paid in, with nothing
    # saying so, reads as a trading result and is not one.
    cash_flow: Decimal = Field(Decimal("0"), alias="cashFlow")
    # Opening balance + cash flows + realised net. None in the all-portfolios
    # view, where summing cash across unrelated books would be meaningless.
    equity: Optional[Decimal] = None

    model_config = ConfigDict(populate_by_name=True)


class RealisationResponse(BaseModel):
    at: str
    security_id: str = Field(alias="securityId")
    trading_symbol: str = Field(alias="tradingSymbol")
    expiry_date: Optional[str] = Field(None, alias="expiryDate")
    strike_price: Optional[Decimal] = Field(None, alias="strikePrice")
    option_type: Optional[str] = Field(None, alias="optionType")
    quantity: int
    entry_price: Decimal = Field(alias="entryPrice")
    exit_price: Decimal = Field(alias="exitPrice")
    gross_pnl: Decimal = Field(alias="grossPnl")
    order_id: int = Field(alias="orderId")

    model_config = ConfigDict(populate_by_name=True)


class PnlReportResponse(BaseModel):
    realised_gross: Decimal = Field(alias="realisedGross")
    total_charges: Decimal = Field(alias="totalCharges")
    realised_net: Decimal = Field(alias="realisedNet")
    unrealised: Optional[Decimal] = None
    net_including_unrealised: Optional[Decimal] = Field(
        None, alias="netIncludingUnrealised"
    )
    charge_components: Dict[str, Decimal] = Field(alias="chargeComponents")
    by_day: List[BucketResponse] = Field(alias="byDay")
    by_expiry: List[BucketResponse] = Field(alias="byExpiry")
    by_strike: List[BucketResponse] = Field(alias="byStrike")
    equity_curve: List[EquityPointResponse] = Field(alias="equityCurve")
    realisations: List[RealisationResponse] = []
    trade_count: int = Field(alias="tradeCount")
    win_count: int = Field(alias="winCount")
    loss_count: int = Field(alias="lossCount")
    open_positions_without_marks: int = Field(0, alias="openPositionsWithoutMarks")

    model_config = ConfigDict(populate_by_name=True)
