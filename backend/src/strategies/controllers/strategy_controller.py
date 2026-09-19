"""Strategies & Features orchestration.

The cost figures on each card come from state the process already keeps -- the
feed's per-strategy state, the poller's interval, counts from the database --
rather than from measurement added for this page. Nothing here touches the tick
path.
"""
from typing import Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.logging_config import get_logger
from src.strategies.api_schemas.strategy_schemas import (
    ArmResponse,
    CapabilityResponse,
    PolicyResponse,
    PolicyToggleResponse,
    SettingResponse,
    SettingToggleResponse,
    StrategyCostResponse,
    StrategyListResponse,
    StrategyResponse,
    ToggleResponse,
)
from src.strategies.services.strategy_definition import (
    CAPABILITY_DESCRIPTIONS,
    CAPABILITY_LABELS,
    CAPABILITY_PAGES,
    KNOWN_CAPABILITIES,
    StrategyConfigError,
)
from src.strategies.services.strategy_registry import get_strategy_registry
from src.strategies.services.strategy_state_service import StrategyStateService

logger = get_logger("strategies.controller")

# Cross-package imports are done INSIDE the functions that need them. Every
# low-level module in this application reads the strategy registry, so
# `src.strategies` must stay import-light: pulling the orders, positions,
# chart-trading or portfolio packages in at module scope closes an import loop
# and the app will not start. The same rule the auth <-> users pair follows
# (backend/CLAUDE.md section 10).


class StrategyController:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.service = StrategyStateService(session)

    @property
    def portfolios(self):
        from src.portfolios.database.db_operations.portfolio_repository import (
            PortfolioRepository,
        )

        return PortfolioRepository(self.session)

    # --- reads -------------------------------------------------------------
    async def list_strategies(self) -> StrategyListResponse:
        registry = get_strategy_registry()

        strategies: List[StrategyResponse] = []
        for definition in registry.all():
            strategies.append(
                StrategyResponse(
                    key=definition.key,
                    label=definition.label,
                    description=definition.description,
                    enabled=registry.is_enabled(definition.key),
                    automated=definition.automation.automated,
                    armed=registry.is_armed(definition.key),
                    armedByDefault=definition.automation.armed_by_default,
                    symbol=definition.symbol,
                    exchangeSegment=definition.exchange_segment,
                    exchangeId=definition.exchange_id,
                    lotSize=definition.lot_size(),
                    marketOpen=definition.market_hours.open.strftime("%H:%M"),
                    marketClose=definition.market_hours.close.strftime("%H:%M"),
                    tradingDays=list(definition.market_hours.trading_days),
                    strikeWindow=definition.subscription.strike_window,
                    expiriesToSubscribe=definition.subscription.expiries_to_subscribe,
                    chargesRateCard=definition.charges_rate_card,
                    chargesVersion=self._rate_card_version(definition),
                    marginModel=definition.margin.model,
                    marginPercentOfNotional=(
                        definition.margin.short_option_percent_of_notional
                    ),
                    capabilities=sorted(definition.capabilities),
                    activeCapabilities=sorted(
                        capability
                        for capability in definition.capabilities
                        if registry.capability_active(capability, definition.key)
                    ),
                    pages=definition.pages(registry.enabled_capabilities()),
                    policies=self._policies_for(definition),
                    policyContradiction=self._policy_contradiction(definition),
                    settings=self._settings_for(definition),
                    cost=await self._cost_for(definition),
                )
            )

        capabilities = [
            CapabilityResponse(
                key=capability,
                label=CAPABILITY_LABELS.get(capability, capability),
                description=CAPABILITY_DESCRIPTIONS.get(capability, ""),
                enabled=registry.is_capability_enabled(capability),
                page=CAPABILITY_PAGES.get(capability),
                supportedBy=[
                    definition.key
                    for definition in registry.all()
                    if definition.supports(capability)
                ],
            )
            for capability in KNOWN_CAPABILITIES
        ]

        return StrategyListResponse(
            strategies=strategies,
            capabilities=capabilities,
            pages=sorted(registry.feature_pages()),
        )

    @staticmethod
    def _describe_policies(definition) -> Dict[str, object]:
        """Whether each of this strategy's own rules is enforced.

        Empty for a discretionary module, so the page offers no control: there
        is nothing to enforce when a person is in front of every order. Only an
        automated module has rules it applies by itself.
        """
        from src.strategies.services.strategy_modules import hooks_for

        try:
            hooks = hooks_for(definition)
            if hooks is None:
                return {"policies": [], "contradiction": None}
            return hooks.describe_policies(definition)
        except Exception:  # noqa: BLE001 - the page must still render
            logger.exception(
                "Could not describe the policies for %s", definition.key
            )
            return {"policies": [], "contradiction": None}

    def _policies_for(self, definition) -> List[PolicyResponse]:
        return [
            PolicyResponse(**row)
            for row in self._describe_policies(definition)["policies"]
        ]

    def _policy_contradiction(self, definition) -> Optional[str]:
        return self._describe_policies(definition)["contradiction"]

    @staticmethod
    def _settings_for(definition) -> List[SettingResponse]:
        """The runtime values an operator may move. Empty for a discretionary
        module: nothing schedules it, so it has no times."""
        from src.strategies.services.strategy_modules import hooks_for

        try:
            hooks = hooks_for(definition)
            if hooks is None:
                return []
            return [
                SettingResponse(**row)
                for row in hooks.describe_settings(definition)["settings"]
            ]
        except Exception:  # noqa: BLE001 - the page must still render
            logger.exception(
                "Could not describe the settings for %s", definition.key
            )
            return []

    async def set_strategy_setting(
        self,
        strategy_key: str,
        setting: str,
        value: Optional[str],
        user_id: Optional[int] = None,
    ) -> SettingToggleResponse:
        try:
            result = await self.service.set_strategy_setting(
                strategy_key, setting, value, user_id
            )
        except StrategyConfigError as error:
            # "Unknown strategy" is a 404. A discretionary module, an unknown
            # setting name, and a value that would break something are all 400:
            # the strategy exists and the request does not apply to it.
            status = 404 if "Unknown strategy" in str(error) else 400
            raise HTTPException(status_code=status, detail=str(error)) from error

        return SettingToggleResponse(
            key=strategy_key,
            setting=setting,
            value=result["effect"]["value"],
            effect=result["effect"],
            warnings=result["warnings"],
        )

    async def setting_warnings(
        self, strategy_key: str, setting: str, value: str
    ) -> Dict[str, object]:
        """What moving this time does, and whether it would be refused."""
        if get_strategy_registry().get(strategy_key) is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown strategy module {strategy_key!r}"
            )
        return await self.service.setting_warnings(strategy_key, setting, value)

    @staticmethod
    def _rate_card_version(definition) -> Optional[str]:
        from src.charges.services.charges_engine import (
            ChargesConfigError,
            ChargesEngine,
        )

        try:
            return ChargesEngine.for_strategy(definition).version
        except ChargesConfigError:
            # A missing rate card is a real problem, but it must not stop the
            # page that would let an operator see the strategy at all.
            logger.warning(
                "Strategy %s names rate card %r, which could not be loaded",
                definition.key, definition.charges_rate_card,
            )
            return None

    async def _cost_for(self, definition) -> StrategyCostResponse:
        """What this strategy is using right now. Zeroes when it is off."""
        from src.chart_trading.database.db_models.chart_trade_model import ChartTrade
        from src.market.services.feed_manager import get_feed_manager
        from src.orders.database.db_models.order_model import Order
        from src.positions.database.db_models.position_model import Position

        registry = get_strategy_registry()
        manager = get_feed_manager()
        state = manager.strategy_state.get(definition.key)

        open_positions = int(
            (
                await self.session.execute(
                    select(func.count()).select_from(Position).where(
                        Position.strategy_key == definition.key,
                        Position.is_open.is_(True),
                    )
                )
            ).scalar_one()
            or 0
        )
        resting = int(
            (
                await self.session.execute(
                    select(func.count()).select_from(Order).where(
                        Order.strategy_key == definition.key,
                        Order.status.in_(["OPEN", "PARTIALLY_FILLED"]),
                    )
                )
            ).scalar_one()
            or 0
        )
        chart_trades = int(
            (
                await self.session.execute(
                    select(func.count()).select_from(ChartTrade).where(
                        ChartTrade.strategy_key == definition.key,
                        ChartTrade.status == "OPEN",
                    )
                )
            ).scalar_one()
            or 0
        )
        portfolios = await self.portfolios.portfolios_running(definition.key)

        greeks_active = registry.capability_active("greeks", definition.key)
        return StrategyCostResponse(
            instrumentsSubscribed=state.instrument_count if state else 0,
            expiriesSubscribed=(
                [expiry.isoformat() for expiry in state.subscribed_expiries]
                if state
                else []
            ),
            nearFutureSecurityId=state.near_future_security_id if state else None,
            greeksIntervalSeconds=(
                manager.greeks_poller._interval() if greeks_active else None  # noqa: SLF001
            ),
            greeksExpiriesPolled=(
                definition.greeks_expiries_to_poll if greeks_active else 0
            ),
            openPositions=open_positions,
            restingOrders=resting,
            openChartTrades=chart_trades,
            portfolios=[portfolio.name for portfolio in portfolios],
        )

    # --- writes ------------------------------------------------------------
    async def set_strategy_enabled(
        self, strategy_key: str, enabled: bool, user_id: Optional[int] = None
    ) -> ToggleResponse:
        try:
            result = await self.service.set_strategy_enabled(
                strategy_key, enabled, user_id
            )
        except StrategyConfigError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

        return ToggleResponse(
            key=strategy_key,
            enabled=enabled,
            effect=result["effect"],
            warnings=result["warnings"],
        )

    async def set_capability_enabled(
        self, capability: str, enabled: bool, user_id: Optional[int] = None
    ) -> ToggleResponse:
        try:
            result = await self.service.set_capability_enabled(
                capability, enabled, user_id
            )
        except StrategyConfigError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

        return ToggleResponse(
            key=capability,
            enabled=enabled,
            effect=result["effect"],
            warnings=result["warnings"],
        )

    async def set_strategy_armed(
        self, strategy_key: str, armed: bool, user_id: Optional[int] = None
    ) -> ArmResponse:
        try:
            result = await self.service.set_strategy_armed(
                strategy_key, armed, user_id
            )
        except StrategyConfigError as error:
            # "Unknown strategy" is a 404; "this module is discretionary and
            # has nothing to arm" is a 400 -- the strategy exists, the request
            # does not apply to it.
            status = 404 if "Unknown strategy" in str(error) else 400
            raise HTTPException(status_code=status, detail=str(error)) from error

        return ArmResponse(
            key=strategy_key,
            armed=get_strategy_registry().is_armed(strategy_key),
            effect=result["effect"],
            warnings=result["warnings"],
        )

    async def set_strategy_policy(
        self,
        strategy_key: str,
        policy: str,
        enforced: bool,
        user_id: Optional[int] = None,
    ) -> PolicyToggleResponse:
        try:
            result = await self.service.set_strategy_policy(
                strategy_key, policy, enforced, user_id
            )
        except StrategyConfigError as error:
            # "Unknown strategy" is a 404. A discretionary module, an unknown
            # policy name, or the contradictory pair are all 400: the strategy
            # exists and the request does not apply to it.
            status = 404 if "Unknown strategy" in str(error) else 400
            raise HTTPException(status_code=status, detail=str(error)) from error

        return PolicyToggleResponse(
            key=strategy_key,
            policy=policy,
            enforced=bool(enforced),
            effect=result["effect"],
            warnings=result["warnings"],
        )

    async def policy_warnings(
        self, strategy_key: str, policy: str, enforced: bool
    ) -> Dict[str, List[str]]:
        """What flipping this switch would do, BEFORE it is flipped."""
        definition = get_strategy_registry().get(strategy_key)
        if definition is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown strategy module {strategy_key!r}"
            )
        return {
            "warnings": await self.service.policy_warnings(
                strategy_key, policy, enforced
            )
        }

    async def arm_warnings(self, strategy_key: str) -> Dict[str, List[str]]:
        """What arming would let loose, BEFORE it is armed."""
        if get_strategy_registry().get(strategy_key) is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown strategy module {strategy_key!r}"
            )
        return {"warnings": await self.service.warnings_for_arming(strategy_key)}

    async def disable_warnings(self, strategy_key: str) -> Dict[str, List[str]]:
        """What switching this off would cost, BEFORE it is switched off."""
        if get_strategy_registry().get(strategy_key) is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown strategy module {strategy_key!r}"
            )
        return {"warnings": await self.service.warnings_for_disabling(strategy_key)}
