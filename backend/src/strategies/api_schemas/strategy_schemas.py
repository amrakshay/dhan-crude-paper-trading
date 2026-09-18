"""Strategy and capability API payloads.

A card on the Strategies & Features page has to state what a strategy is
currently COSTING -- instruments subscribed, expiries polled, greeks interval,
open positions, which portfolios run it -- because that is what makes the
toggle an informed decision rather than a switch in the dark. Those numbers are
all in this payload, and all of them are read from state the process already
keeps rather than measured for this page.
"""
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class CapabilityResponse(BaseModel):
    key: str
    label: str
    description: str = ""
    # Globally on? A strategy additionally has to support it.
    enabled: bool
    # Which page it grants, where it grants one.
    page: Optional[str] = None
    # Strategies that declare support for it.
    supported_by: List[str] = Field(default_factory=list, alias="supportedBy")

    model_config = ConfigDict(populate_by_name=True)


class StrategyCostResponse(BaseModel):
    """What this strategy is currently using. Zeroes when it is off."""

    instruments_subscribed: int = Field(0, alias="instrumentsSubscribed")
    expiries_subscribed: List[str] = Field(default_factory=list, alias="expiriesSubscribed")
    near_future_security_id: Optional[str] = Field(None, alias="nearFutureSecurityId")
    greeks_interval_seconds: Optional[float] = Field(None, alias="greeksIntervalSeconds")
    greeks_expiries_polled: int = Field(0, alias="greeksExpiriesPolled")
    open_positions: int = Field(0, alias="openPositions")
    resting_orders: int = Field(0, alias="restingOrders")
    open_chart_trades: int = Field(0, alias="openChartTrades")
    portfolios: List[str] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class StrategyResponse(BaseModel):
    key: str
    label: str
    description: str = ""
    enabled: bool

    # Enabled and ARMED are two switches. `automated` says whether this module
    # trades on a schedule at all -- false for a discretionary one, where there
    # is nothing to arm and the UI must offer no control. `armed` is the
    # effective state (armed AND enabled); `armedByDefault` is what a fresh
    # install starts from, shown so the page can say the switch was moved.
    automated: bool = False
    armed: bool = False
    armed_by_default: bool = Field(False, alias="armedByDefault")

    symbol: str
    exchange_segment: str = Field(alias="exchangeSegment")
    exchange_id: str = Field(alias="exchangeId")
    lot_size: Optional[int] = Field(None, alias="lotSize")
    market_open: str = Field(alias="marketOpen")
    market_close: str = Field(alias="marketClose")
    trading_days: List[int] = Field(default_factory=list, alias="tradingDays")

    strike_window: int = Field(alias="strikeWindow")
    expiries_to_subscribe: int = Field(alias="expiriesToSubscribe")

    charges_rate_card: str = Field(alias="chargesRateCard")
    charges_version: Optional[str] = Field(None, alias="chargesVersion")

    # ALWAYS an estimate. The UI must say so wherever it prints the number.
    margin_model: str = Field(alias="marginModel")
    margin_percent_of_notional: Decimal = Field(alias="marginPercentOfNotional")
    margin_is_estimate: bool = Field(True, alias="marginIsEstimate")

    capabilities: List[str] = Field(default_factory=list)
    active_capabilities: List[str] = Field(default_factory=list, alias="activeCapabilities")
    pages: List[str] = Field(default_factory=list)

    cost: StrategyCostResponse

    model_config = ConfigDict(populate_by_name=True)


class StrategyListResponse(BaseModel):
    strategies: List[StrategyResponse]
    capabilities: List[CapabilityResponse]
    # Pages the current configuration grants, before the role is intersected.
    pages: List[str] = Field(default_factory=list)


class ToggleRequest(BaseModel):
    enabled: bool


class ArmRequest(BaseModel):
    """Arming is its own request type, so `enabled` can never be sent by
    mistake to the endpoint that lets software spend money."""

    armed: bool


class ArmResponse(BaseModel):
    key: str
    armed: bool
    effect: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)


class ToggleResponse(BaseModel):
    key: str
    enabled: bool
    # What changed as a result, so the UI can say "unsubscribed 165
    # instruments" rather than just flipping a switch.
    effect: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
