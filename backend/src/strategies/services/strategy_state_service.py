"""Loading and applying enable/disable state.

The pattern is the one `SettingsService` already uses: stored state is overlaid
on the in-memory defaults at startup and again after every save, and the change
is applied to the running process immediately rather than at the next restart.
Freeing the resources a strategy was using is the entire point of switching it
off, and "takes effect on restart" would not free anything.

What "off" must actually stop, and where each part of it happens:

* no instruments on the wire       -- `FeedManager._resolve_targets` loops over
                                      enabled strategies only, and `resync()`
                                      unsubscribes what is no longer wanted
* no greeks poll, no Dhan REST     -- `GreeksPoller._strategies()`
* no instrument-master refresh     -- `InstrumentMasterService.strategies()`
* no candles fetched               -- the chart endpoints refuse for it
* background tasks skip it         -- the matcher and bracket monitor filter
* its live pages disappear         -- `pages_for_role` intersects feature pages
* its endpoints refuse             -- with a specific message, not a 404
* **its history stays readable**   -- nothing filters on enabled state when
                                      reading orders, positions or reports

Resting orders and armed brackets are LEFT IN PLACE, frozen. They stay OPEN,
nothing fills them and no stop fires while the strategy is off, and they resume
when it comes back on. Cancelling them would destroy work an operator set up;
freezing them is recoverable, and the warnings this service returns say exactly
that so the choice is made with open eyes.
"""
from typing import Any, Dict, List

from sqlalchemy.ext.asyncio import AsyncSession

from src.logging_config import get_logger
from src.strategies.database.db_models.feature_toggle_model import (
    SCOPE_CAPABILITY,
    SCOPE_STRATEGY,
)
from src.strategies.database.db_operations.feature_toggle_repository import (
    FeatureToggleRepository,
)
from src.strategies.services.strategy_definition import (
    KNOWN_CAPABILITIES,
    StrategyConfigError,
)
from src.strategies.services.strategy_registry import get_strategy_registry

logger = get_logger("strategies.state")


class StrategyStateService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repository = FeatureToggleRepository(session)

    # --- loading -----------------------------------------------------------
    async def apply_stored_state(self) -> Dict[str, Any]:
        """Overlay the database on the registry's defaults.

        Called from the lifespan BEFORE the feed starts, for the same reason
        `_apply_stored_settings` is: the feed reads which strategies are
        enabled when it builds its first subscription, and applying the state
        afterwards would start the feed on the wrong set and only correct it at
        the next toggle.
        """
        states = await self.repository.states()
        registry = get_strategy_registry()
        registry.apply_state(
            states.get(SCOPE_STRATEGY, {}), states.get(SCOPE_CAPABILITY, {})
        )
        return {
            "strategies": registry.strategy_states(),
            "capabilities": registry.capability_states(),
        }

    # --- writing -----------------------------------------------------------
    async def set_strategy_enabled(
        self, strategy_key: str, enabled: bool, user_id: int = None
    ) -> Dict[str, Any]:
        registry = get_strategy_registry()
        definition = registry.get(strategy_key)
        if definition is None:
            raise StrategyConfigError(f"Unknown strategy module {strategy_key!r}")

        warnings = await self.warnings_for_disabling(strategy_key) if not enabled else []

        await self.repository.set_state(
            SCOPE_STRATEGY, strategy_key, enabled, updated_by_user_id=user_id
        )
        registry.set_enabled(strategy_key, enabled)
        await self.session.commit()

        effect = await self._apply_live()
        logger.info(
            "Strategy %s %s; feed effect: %s",
            strategy_key, "enabled" if enabled else "disabled", effect,
        )
        return {"effect": effect, "warnings": warnings}

    async def set_capability_enabled(
        self, capability: str, enabled: bool, user_id: int = None
    ) -> Dict[str, Any]:
        if capability not in KNOWN_CAPABILITIES:
            raise StrategyConfigError(f"Unknown capability {capability!r}")

        await self.repository.set_state(
            SCOPE_CAPABILITY, capability, enabled, updated_by_user_id=user_id
        )
        get_strategy_registry().set_capability_enabled(capability, enabled)
        await self.session.commit()

        effect = await self._apply_live()
        logger.info(
            "Capability %s %s; feed effect: %s",
            capability, "enabled" if enabled else "disabled", effect,
        )
        return {"effect": effect, "warnings": []}

    # --- applying ----------------------------------------------------------
    @staticmethod
    async def _apply_live() -> Dict[str, Any]:
        """Make the running process match the new state, now.

        This reuses the feed's own resync rather than `reconfigure()`: nothing
        about the credentials or the synthetic flag has changed, so tearing the
        client down would drop the upstream connection and every browser's
        prices with it. `resync()` diffs the target set and unsubscribes only
        what is no longer wanted.
        """
        from src.market.services.feed_manager import get_feed_manager

        manager = get_feed_manager()
        effect: Dict[str, Any] = {}
        try:
            effect = await manager.resync()
        except Exception:  # noqa: BLE001 - a toggle must not 500 on a feed error
            logger.exception("Could not resync the feed after a toggle")
            effect = {"error": "the feed could not be resynced; see the logs"}

        # The greeks poller stops entirely when no running strategy wants
        # greeks any more, and starts again when one does.
        try:
            poller = manager.greeks_poller
            wants_greeks = bool(poller._strategies())  # noqa: SLF001
            running = poller._task is not None and not poller._task.done()  # noqa: SLF001
            if running and not wants_greeks:
                await poller.stop()
                effect["greeksPoller"] = "stopped"
            elif not running and wants_greeks and manager.feed is not None:
                await poller.start(is_synthetic=manager.is_synthetic)
                effect["greeksPoller"] = "started"
        except Exception:  # noqa: BLE001
            logger.exception("Could not adjust the greeks poller after a toggle")

        return effect

    # --- warnings ----------------------------------------------------------
    async def warnings_for_disabling(self, strategy_key: str) -> List[str]:
        """What an operator is about to lose sight of.

        Switching a strategy off with open positions stops its bracket monitor
        watching them and stops their marks moving in every portfolio that
        holds them. That is a real consequence and it belongs in front of the
        toggle, not in a log line afterwards.
        """
        from src.chart_trading.database.db_models.chart_trade_model import ChartTrade
        from src.orders.database.db_models.order_model import Order
        from src.positions.database.db_models.position_model import Position
        from sqlalchemy import func, select

        warnings: List[str] = []

        open_positions = int(
            (
                await self.session.execute(
                    select(func.count())
                    .select_from(Position)
                    .where(
                        Position.strategy_key == strategy_key,
                        Position.is_open.is_(True),
                    )
                )
            ).scalar_one()
            or 0
        )
        if open_positions:
            warnings.append(
                f"{open_positions} open position"
                f"{'' if open_positions == 1 else 's'} will stop being marked. "
                f"Every portfolio holding one will show 'no mark' instead of a "
                f"price, and its equity figure will say it is incomplete."
            )

        resting = int(
            (
                await self.session.execute(
                    select(func.count())
                    .select_from(Order)
                    .where(
                        Order.strategy_key == strategy_key,
                        Order.status.in_(["OPEN", "PARTIALLY_FILLED"]),
                    )
                )
            ).scalar_one()
            or 0
        )
        if resting:
            warnings.append(
                f"{resting} resting order{'' if resting == 1 else 's'} will stay "
                f"open but will not fill while this strategy is off. Nothing is "
                f"cancelled; they resume when it is switched back on."
            )

        armed = int(
            (
                await self.session.execute(
                    select(func.count())
                    .select_from(ChartTrade)
                    .where(
                        ChartTrade.strategy_key == strategy_key,
                        ChartTrade.status == "OPEN",
                    )
                )
            ).scalar_one()
            or 0
        )
        if armed:
            warnings.append(
                f"{armed} open chart trade{'' if armed == 1 else 's'} will stop "
                f"being watched: a stop-loss or take-profit level will NOT fire "
                f"while this strategy is off."
            )

        return warnings
