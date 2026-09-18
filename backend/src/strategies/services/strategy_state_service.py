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
    SCOPE_AUTOMATION,
    SCOPE_CAPABILITY,
    SCOPE_POLICY,
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
            states.get(SCOPE_STRATEGY, {}),
            states.get(SCOPE_CAPABILITY, {}),
            states.get(SCOPE_AUTOMATION, {}),
            states.get(SCOPE_POLICY, {}),
        )
        return {
            "strategies": registry.strategy_states(),
            "capabilities": registry.capability_states(),
            "armed": registry.armed_states(),
            "policies": registry.policy_states(),
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

    async def set_strategy_armed(
        self, strategy_key: str, armed: bool, user_id: int = None
    ) -> Dict[str, Any]:
        """Let an automated strategy submit orders, or stop it.

        Nothing about the feed changes -- arming does not subscribe an
        instrument or start a task -- so this deliberately does NOT resync.
        What it changes is whether the rebalance and the stop monitor are
        allowed to place an order; both read `registry.is_armed()` at the
        moment they would place one, so disarming takes effect on the next
        decision rather than at the next restart.

        Disarming NEVER cancels or closes anything. A position already opened
        stays open and its stop keeps being recomputed and recorded; what stops
        is the placing of new orders. Trapping a trader in a position by
        disarming would be the same mistake as cancelling their resting orders
        when a strategy is switched off.
        """
        registry = get_strategy_registry()
        definition = registry.get(strategy_key)
        if definition is None:
            raise StrategyConfigError(f"Unknown strategy module {strategy_key!r}")

        warnings = await self.warnings_for_arming(strategy_key) if armed else []

        # set_armed refuses a module with no automation block, before anything
        # is written -- a stored row must not be able to arm a discretionary
        # strategy.
        registry.set_armed(strategy_key, armed)
        await self.repository.set_state(
            SCOPE_AUTOMATION, strategy_key, armed, updated_by_user_id=user_id
        )
        await self.session.commit()

        logger.warning(
            "Strategy %s is now %s. It %s submit orders of its own accord.",
            strategy_key,
            "ARMED" if armed else "DISARMED",
            "MAY" if armed else "may not",
        )
        return {"effect": {"armed": registry.is_armed(strategy_key)}, "warnings": warnings}

    async def warnings_for_arming(self, strategy_key: str) -> List[str]:
        """What arming actually lets loose, said before it is let loose.

        Arming is the one control in this application that lets software spend
        money without a person clicking anything. It is paper money, and the
        warning still belongs in front of the switch.
        """
        registry = get_strategy_registry()
        definition = registry.get(strategy_key)
        if definition is None or not definition.automation.automated:
            return []

        warnings = [
            f"{definition.label} will place orders on its own schedule, with "
            f"nobody watching. Every order is paper money in this database and "
            f"nothing reaches a broker -- but the decisions, the sizes and the "
            f"stops will be its own.",
        ]
        if not registry.is_enabled(strategy_key):
            warnings.append(
                "It is currently switched OFF, so arming it changes nothing "
                "until it is switched on again."
            )

        portfolios = await self._portfolios_running(strategy_key)
        if not portfolios:
            warnings.append(
                "No active portfolio runs this strategy, so there is no book "
                "for it to trade. Attach it to one on the Portfolios page."
            )
        else:
            warnings.append(
                "It will trade in: " + ", ".join(sorted(portfolios)) + "."
            )
        return warnings

    async def _portfolios_running(self, strategy_key: str) -> List[str]:
        from src.portfolios.database.db_operations.portfolio_repository import (
            PortfolioRepository,
        )

        rows = await PortfolioRepository(self.session).portfolios_running(strategy_key)
        return [row.name for row in rows]

    async def set_strategy_policy(
        self,
        strategy_key: str,
        policy: str,
        enforced: bool,
        user_id: int = None,
    ) -> Dict[str, Any]:
        """Enforce, or stop enforcing, one of a strategy's own rules.

        Root `CLAUDE.md` section 3a survives this and this fits inside it: what
        is being changed is not a PARAMETER of the rule -- the lookbacks, the
        thresholds, the multiples all stay in the YAML and are editable from
        nowhere -- but whether the rule is OBEYED. That is the same kind of
        fact as enabled and armed: runtime state overlaid on a default the YAML
        declares.

        Nothing about the feed changes, so this deliberately does not resync.
        What changes is what the NEXT decision does; a run already in flight
        keeps the policy it started under.

        THE CONTRADICTORY PAIR IS REFUSED HERE, before anything is written --
        not only in the UI. A stored row, a script or a future caller would
        walk straight past a check that lived on a page.
        """
        registry = get_strategy_registry()
        definition = registry.get(strategy_key)
        if definition is None:
            raise StrategyConfigError(f"Unknown strategy module {strategy_key!r}")
        if not definition.automation.automated:
            raise StrategyConfigError(
                f"{definition.label} declares no automation block, so it has no "
                f"rules of its own to enforce. Every order in it comes from a "
                f"person."
            )

        self._validate_policy(definition, policy, enforced)
        warnings = self._policy_warnings(definition, policy, enforced)

        registry.set_policy(strategy_key, policy, enforced)
        await self.repository.set_state(
            SCOPE_POLICY,
            f"{strategy_key}/{policy}",
            enforced,
            updated_by_user_id=user_id,
        )
        await self.session.commit()

        logger.warning(
            "Strategy %s: policy %s is now %s. It applies at the next decision.",
            strategy_key, policy, "ENFORCED" if enforced else "NOT ENFORCED",
        )
        return {
            "effect": {"policy": policy, "enforced": bool(enforced)},
            "warnings": warnings,
        }

    async def policy_warnings(self, strategy_key: str, policy: str, enforced: bool):
        """What flipping this switch would do, BEFORE it is flipped."""
        definition = get_strategy_registry().get(strategy_key)
        if definition is None or not definition.automation.automated:
            return []
        return self._policy_warnings(definition, policy, enforced)

    # A strategy's own module owns both the meaning of its policies and the
    # prose in front of the switch, because the prose is about the rule rather
    # than about the framework. Only automated modules have policies and only
    # one automated module exists; a SECOND one needs a lookup by strategy key
    # here rather than a second import.
    @staticmethod
    def _policy_warnings(definition, policy: str, enforced: bool):
        from src.swing.services.gate_policy import policy_warnings

        return policy_warnings(definition, policy, enforced)

    @staticmethod
    def _validate_policy(definition, policy: str, enforced: bool) -> None:
        from src.swing.services.gate_policy import (
            GatePolicyError,
            resolve_gate_policy,
        )

        registry = get_strategy_registry()
        overrides = {}
        from src.strategies.services.strategy_definition import KNOWN_POLICIES

        for known in KNOWN_POLICIES:
            stored = registry.policy_override(definition.key, known)
            if stored is not None:
                overrides[known] = stored
        overrides[policy] = bool(enforced)

        try:
            resolve_gate_policy(definition, overrides=overrides)
        except GatePolicyError as error:
            raise StrategyConfigError(str(error)) from error

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

        definition = get_strategy_registry().get(strategy_key)
        if definition is not None and definition.automation.automated:
            warnings.append(
                "Its scheduled runs stop: nothing is decided, nothing is "
                "journalled and no trailing stop is recomputed or acted on "
                "while it is off. Its arming switch keeps whatever state it "
                "has; switching the strategy back on resumes both."
            )

        return warnings
