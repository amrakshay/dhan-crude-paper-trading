"""The strategy registry: which strategies exist, and which are running.

One process-wide registry. It answers three kinds of question:

* **What exists** -- the definitions parsed from ``<CONFIG_PATH>/strategies``.
* **What is on** -- the enabled state, which is runtime state in the database
  overlaid on each definition's ``enabled_by_default``.
* **What follows from that** -- which contracts to subscribe, which pages to
  offer, which capabilities are effective.

Two things this registry is deliberately NOT:

* It is **not a feed**. A strategy contributes ``(segment, security_id)``
  targets to the ONE process-wide `DhanFeedClient`; it never gets a connection
  of its own. Dhan allows five per user and two connections mean a
  desynchronised book (root CLAUDE.md section 4).
* It is **not on the tick path**. Membership is resolved when a subscription is
  built or a trade is written, never when a packet arrives.

The enabled state is cached in memory because it is read from synchronous code
(`expected_task_names`, the feed manager's target resolution) that cannot await
a database round trip. `StrategyStateService` refreshes the cache at startup
and after every toggle, exactly as `SettingsService.apply_to_config()` does for
settings.
"""
import os
import threading
from typing import Any, Dict, FrozenSet, List, Optional

import yaml

from src import config_utils
from src.logging_config import get_logger
from src.strategies.services.strategy_definition import (
    CAPABILITY_CHART_TRADING,
    CAPABILITY_GREEKS,
    CAPABILITY_PAGES,
    KNOWN_CAPABILITIES,
    STRATEGY_LIVE_PAGES,
    StrategyConfigError,
    StrategyDefinition,
    build_definition,
)

logger = get_logger("strategies.registry")

STRATEGIES_DIRNAME = "strategies"

# Capability defaults come from these legacy keys where one already existed, so
# that unifying the ad-hoc enable flags changes no behaviour: an operator who
# had set `chart_trading.enabled: false` still finds chart trading off.
LEGACY_CAPABILITY_KEYS: Dict[str, str] = {
    CAPABILITY_CHART_TRADING: "chart_trading.enabled",
    CAPABILITY_GREEKS: "greeks_poller.enabled",
}


class StrategyRegistry:
    """Definitions plus enabled state. Use `get_strategy_registry()`."""

    def __init__(self) -> None:
        self._definitions: Dict[str, StrategyDefinition] = {}
        self._strategy_enabled: Dict[str, bool] = {}
        self._capability_enabled: Dict[str, bool] = {}
        self._lock = threading.RLock()
        self._loaded = False

    # --- loading -----------------------------------------------------------
    def directory(self) -> str:
        return os.path.join(config_utils.get_config_path(), STRATEGIES_DIRNAME)

    def load(self, force: bool = False) -> None:
        """Parse every strategy YAML. Raises if a file is unusable.

        A malformed strategy is fatal rather than skipped: silently running
        with one fewer strategy than the operator configured would mean an
        instrument set, a rate card and a margin model quietly disappearing,
        and the first symptom would be a wrong number rather than an error.
        """
        with self._lock:
            if self._loaded and not force:
                return

            directory = self.directory()
            definitions: Dict[str, StrategyDefinition] = {}

            if not os.path.isdir(directory):
                raise StrategyConfigError(
                    f"No strategy directory at {directory}. Every tradable "
                    f"instrument in this application belongs to a strategy "
                    f"module; without one there is nothing to trade."
                )

            for name in sorted(os.listdir(directory)):
                if not name.endswith((".yaml", ".yml")) or name.startswith("."):
                    continue
                path = os.path.join(directory, name)
                with open(path, "r", encoding="utf-8") as handle:
                    document = yaml.safe_load(handle) or {}
                document = config_utils._substitute_env(document)  # noqa: SLF001
                definition = build_definition(document, source=name)
                if definition.key in definitions:
                    raise StrategyConfigError(
                        f"{name}: duplicate strategy key {definition.key!r}; "
                        f"it is already defined by another file in {directory}."
                    )
                definitions[definition.key] = definition

            if not definitions:
                raise StrategyConfigError(
                    f"No strategy modules found in {directory}."
                )

            self._definitions = definitions
            # Defaults until the database says otherwise.
            self._strategy_enabled = {
                key: definition.enabled_by_default
                for key, definition in definitions.items()
            }
            self._capability_enabled = {
                capability: self._capability_default(capability)
                for capability in KNOWN_CAPABILITIES
            }
            self._loaded = True

            logger.info(
                "Loaded %s strategy module(s) from %s: %s",
                len(definitions), directory,
                {key: definition.label for key, definition in definitions.items()},
            )

    @staticmethod
    def _capability_default(capability: str) -> bool:
        legacy = LEGACY_CAPABILITY_KEYS.get(capability)
        if legacy is not None:
            return config_utils.get_property_value_boolean(legacy, True)
        return config_utils.get_property_value_boolean(
            f"capabilities.{capability}.enabled", True
        )

    def _ensure(self) -> None:
        if not self._loaded:
            self.load()

    # --- definitions -------------------------------------------------------
    def all(self) -> List[StrategyDefinition]:
        self._ensure()
        return [self._definitions[key] for key in sorted(self._definitions)]

    def get(self, key: str) -> Optional[StrategyDefinition]:
        self._ensure()
        return self._definitions.get(str(key))

    def require(self, key: str) -> StrategyDefinition:
        definition = self.get(key)
        if definition is None:
            raise StrategyConfigError(f"Unknown strategy {key!r}")
        return definition

    def default(self) -> StrategyDefinition:
        """The strategy to assume when a caller has not said which.

        Single-strategy call sites (and every row written before strategies
        existed) resolve here. With one module configured this is that module;
        with several it is the first by key, and the caller should be passing
        one explicitly.
        """
        self._ensure()
        return self._definitions[sorted(self._definitions)[0]]

    def for_instrument(
        self, exchange_segment: str, underlying_symbol: str
    ) -> Optional[StrategyDefinition]:
        """Which strategy owns a contract, by its segment and underlying."""
        self._ensure()
        for key in sorted(self._definitions):
            definition = self._definitions[key]
            if definition.owns_instrument(exchange_segment, underlying_symbol):
                return definition
        return None

    # --- enabled state -----------------------------------------------------
    def apply_state(
        self,
        strategy_states: Dict[str, bool],
        capability_states: Dict[str, bool],
    ) -> None:
        """Overlay stored state on the defaults. Called at startup and on save.

        Unknown keys are ignored rather than trusted: a stale row for a
        strategy that no longer exists must not resurrect anything, the same
        property `MANAGED_KEYS` gives the settings table.
        """
        self._ensure()
        with self._lock:
            for key, enabled in (strategy_states or {}).items():
                if key in self._definitions:
                    self._strategy_enabled[key] = bool(enabled)
                else:
                    logger.warning(
                        "Ignoring stored state for unknown strategy %r", key
                    )
            for capability, enabled in (capability_states or {}).items():
                if capability in KNOWN_CAPABILITIES:
                    self._capability_enabled[capability] = bool(enabled)
                else:
                    logger.warning(
                        "Ignoring stored state for unknown capability %r", capability
                    )
        logger.info(
            "Strategy state applied: strategies=%s capabilities=%s",
            self._strategy_enabled, self._capability_enabled,
        )

    def is_enabled(self, key: str) -> bool:
        self._ensure()
        return bool(self._strategy_enabled.get(str(key), False))

    def set_enabled(self, key: str, enabled: bool) -> None:
        self._ensure()
        with self._lock:
            self.require(key)
            self._strategy_enabled[key] = bool(enabled)

    def enabled(self) -> List[StrategyDefinition]:
        """Every running strategy, in key order."""
        return [
            definition
            for definition in self.all()
            if self._strategy_enabled.get(definition.key, False)
        ]

    def disabled(self) -> List[StrategyDefinition]:
        return [
            definition
            for definition in self.all()
            if not self._strategy_enabled.get(definition.key, False)
        ]

    def strategy_states(self) -> Dict[str, bool]:
        self._ensure()
        return dict(self._strategy_enabled)

    # --- capabilities ------------------------------------------------------
    def is_capability_enabled(self, capability: str) -> bool:
        """Globally on? Says nothing about any particular strategy."""
        self._ensure()
        return bool(self._capability_enabled.get(str(capability), False))

    def set_capability_enabled(self, capability: str, enabled: bool) -> None:
        self._ensure()
        if capability not in KNOWN_CAPABILITIES:
            raise StrategyConfigError(f"Unknown capability {capability!r}")
        with self._lock:
            self._capability_enabled[str(capability)] = bool(enabled)

    def capability_states(self) -> Dict[str, bool]:
        self._ensure()
        return dict(self._capability_enabled)

    def enabled_capabilities(self) -> FrozenSet[str]:
        self._ensure()
        return frozenset(
            capability
            for capability, enabled in self._capability_enabled.items()
            if enabled
        )

    def capability_active(self, capability: str, strategy_key: str) -> bool:
        """Effective state: the capability is on AND the strategy supports it.

        A strategy that cannot support a capability can never advertise it --
        the definition refuses to declare one it does not list -- so this is a
        plain intersection rather than a precedence rule.
        """
        definition = self.get(strategy_key)
        if definition is None or not self.is_enabled(strategy_key):
            return False
        return self.is_capability_enabled(capability) and definition.supports(capability)

    def capability_active_anywhere(self, capability: str) -> bool:
        """Is this capability effective for at least one running strategy?"""
        if not self.is_capability_enabled(capability):
            return False
        return any(
            definition.supports(capability) for definition in self.enabled()
        )

    # --- pages -------------------------------------------------------------
    def feature_pages(self) -> FrozenSet[str]:
        """Every page the current configuration grants.

        Intersected with the role's pages in `pages_for_role`, so the sidebar,
        the client routes and anything else reading `/auth/me` follow from one
        computation. Pages that show HISTORY are not in here and are never
        withdrawn: a strategy going off must not hide trades that happened
        (decision 3).
        """
        granted: set = set()
        running = self.enabled()
        capabilities = self.enabled_capabilities()

        if running:
            granted.update(STRATEGY_LIVE_PAGES)
        for definition in running:
            granted.update(definition.pages(capabilities))

        return frozenset(granted)

    @staticmethod
    def gated_pages() -> FrozenSet[str]:
        """Pages whose visibility depends on strategies and capabilities.

        Everything outside this set is ungated -- `pages_for_role` leaves it
        alone -- so adding a page to the role file does not accidentally make
        it disappear behind a toggle nobody connected to it.
        """
        return frozenset({*STRATEGY_LIVE_PAGES, *CAPABILITY_PAGES.values()})

    # --- reporting ---------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        """Flat state for the health page and the Strategies page."""
        return {
            "strategies": {
                definition.key: {
                    "label": definition.label,
                    "enabled": self.is_enabled(definition.key),
                    "symbol": definition.symbol,
                    "exchangeSegment": definition.exchange_segment,
                    "capabilities": sorted(definition.capabilities),
                }
                for definition in self.all()
            },
            "capabilities": self.capability_states(),
            "pages": sorted(self.feature_pages()),
        }


_REGISTRY: Optional[StrategyRegistry] = None
_REGISTRY_LOCK = threading.Lock()


def get_strategy_registry() -> StrategyRegistry:
    global _REGISTRY

    if _REGISTRY is None:
        with _REGISTRY_LOCK:
            if _REGISTRY is None:
                registry = StrategyRegistry()
                registry.load()
                _REGISTRY = registry
    return _REGISTRY


def reset_strategy_registry() -> None:
    """Drop the singleton. For tests and for a config reload."""
    global _REGISTRY

    with _REGISTRY_LOCK:
        _REGISTRY = None
