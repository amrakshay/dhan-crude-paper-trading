"""What a strategy module IS.

A strategy is the bundle of everything specific to trading one thing:

    underlying + exchange segment + instrument types
  + contract specification
  + market hours
  + subscription policy
  + charge rate card
  + margin model
  + the generic capabilities it supports

Everything else in this application is generic and knows nothing about
CRUDEOIL. The definitions are read from ``<CONFIG_PATH>/strategies/*.yaml`` --
one file per strategy -- and are immutable once loaded.

**A definition says nothing about whether the strategy is running.** That is
runtime state in the database, owned by the Strategies & Features page. Keeping
the two apart is what makes "off" a decision an operator takes rather than a
redeploy.
"""
from dataclasses import dataclass, field
from datetime import time
from decimal import Decimal
from typing import Any, Dict, FrozenSet, List, Optional, Tuple


class StrategyConfigError(Exception):
    """A strategy YAML is missing something it cannot run without."""


# The generic capability catalogue. A strategy may support any subset of these;
# it may not invent one, because a capability is a feature of THIS codebase
# (pages, pollers, background work) and not of the strategy.
CAPABILITY_OPTION_CHAIN = "option-chain"
CAPABILITY_PRICE_CHART = "price-chart"
CAPABILITY_CHART_TRADING = "chart-trading"
CAPABILITY_GREEKS = "greeks"
CAPABILITY_TRADE_NOTES = "trade-notes"
CAPABILITY_PNL_REPORTS = "pnl-reports"

KNOWN_CAPABILITIES: Tuple[str, ...] = (
    CAPABILITY_OPTION_CHAIN,
    CAPABILITY_PRICE_CHART,
    CAPABILITY_CHART_TRADING,
    CAPABILITY_GREEKS,
    CAPABILITY_TRADE_NOTES,
    CAPABILITY_PNL_REPORTS,
)

CAPABILITY_LABELS: Dict[str, str] = {
    CAPABILITY_OPTION_CHAIN: "Option chain",
    CAPABILITY_PRICE_CHART: "Price chart",
    CAPABILITY_CHART_TRADING: "Chart trading",
    CAPABILITY_GREEKS: "Greeks",
    CAPABILITY_TRADE_NOTES: "Trade notes",
    CAPABILITY_PNL_REPORTS: "P&L reports",
}

CAPABILITY_DESCRIPTIONS: Dict[str, str] = {
    CAPABILITY_OPTION_CHAIN: "The paired-strike chain page.",
    CAPABILITY_PRICE_CHART: "Candle history from Dhan's chart endpoints.",
    CAPABILITY_CHART_TRADING: "One-click entry from the futures chart.",
    CAPABILITY_GREEKS: "Option-chain REST poll for delta, IV and the rest.",
    CAPABILITY_TRADE_NOTES: "Journal entries attached to trades.",
    CAPABILITY_PNL_REPORTS: "Realised P&L slices and the equity curve.",
}

# Which page a capability grants, where it grants one. The trading pages that
# show HISTORY (/orders, /positions) are deliberately absent: a strategy going
# off must never hide trades that already happened.
CAPABILITY_PAGES: Dict[str, str] = {
    CAPABILITY_OPTION_CHAIN: "/chain",
    CAPABILITY_TRADE_NOTES: "/notes",
    CAPABILITY_PNL_REPORTS: "/reports",
}

# Pages that exist only while at least one strategy is live. Live Price has
# nothing to show without a subscribed instrument.
STRATEGY_LIVE_PAGES: Tuple[str, ...] = ("/live",)


@dataclass(frozen=True)
class MarketHours:
    timezone: str
    open: time
    close: time
    close_us_dst: Optional[time]
    trading_days: Tuple[int, ...]


@dataclass(frozen=True)
class SubscriptionPolicy:
    strike_window: int
    expiries_to_subscribe: int
    resubscribe_move_strikes: int


@dataclass(frozen=True)
class MarginModel:
    """An ESTIMATE of what a short position ties up. Never a broker figure.

    `model` is carried so a surface can say which approximation it is looking
    at, and so an unknown model fails loudly rather than silently blocking
    zero.
    """

    model: str
    short_option_percent_of_notional: Decimal

    PERCENT_OF_NOTIONAL = "percent_of_notional"


@dataclass(frozen=True)
class StrategyDefinition:
    key: str
    label: str
    description: str
    enabled_by_default: bool

    # underlying
    symbol: str
    underlying_scrip: int
    exchange_segment: str
    exchange_segment_code: int
    exchange_id: str
    option_instrument_type: str
    futures_instrument_type: str

    contract_specs: Dict[str, Any]
    tick_size_divisor: int
    market_hours: MarketHours
    subscription: SubscriptionPolicy
    greeks_expiries_to_poll: int
    charges_rate_card: str
    margin: MarginModel
    capabilities: FrozenSet[str] = field(default_factory=frozenset)

    # --- derived ----------------------------------------------------------
    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def lot_size(self, symbol: Optional[str] = None) -> Optional[int]:
        spec = self.contract_specs.get(symbol or self.symbol)
        if not isinstance(spec, dict):
            return None
        value = spec.get("lot_size")
        return int(value) if value is not None else None

    def owns_instrument(self, exchange_segment: str, underlying_symbol: str) -> bool:
        """Does this strategy trade that contract?

        Resolved from the instrument's own segment and underlying rather than
        from a stored key, and only ever called when a trade is written or a
        subscription is built -- never on the tick path.
        """
        return (
            str(exchange_segment) == self.exchange_segment
            and str(underlying_symbol) == self.symbol
        )

    def pages(self, enabled_capabilities: FrozenSet[str]) -> List[str]:
        """Live pages this strategy contributes, given the capabilities on."""
        granted = list(STRATEGY_LIVE_PAGES)
        for capability in sorted(self.capabilities & enabled_capabilities):
            page = CAPABILITY_PAGES.get(capability)
            if page:
                granted.append(page)
        return granted


def _require(document: Dict[str, Any], key: str, source: str) -> Any:
    node: Any = document
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            raise StrategyConfigError(f"{source}: missing required key '{key}'")
        node = node[part]
    if node is None:
        raise StrategyConfigError(f"{source}: '{key}' is empty")
    return node


def _parse_hhmm(value: Any, source: str, key: str) -> time:
    text = str(value).strip()
    try:
        hour, minute = text.split(":")
        return time(int(hour), int(minute))
    except Exception as exc:  # noqa: BLE001 - config error, reported as one
        raise StrategyConfigError(
            f"{source}: '{key}' must be HH:MM, got {value!r}"
        ) from exc


def build_definition(document: Dict[str, Any], source: str) -> StrategyDefinition:
    """Turn one parsed YAML document into a definition, or explain why not.

    Every failure here is a configuration mistake an operator can fix, so each
    one names the file and the key rather than raising a KeyError from the
    middle of a dictionary walk.
    """
    key = str(_require(document, "key", source)).strip()
    if not key:
        raise StrategyConfigError(f"{source}: 'key' is empty")

    hours = _require(document, "market_hours", source)
    close_us_dst = hours.get("close_us_dst")
    subscription = _require(document, "subscription", source)
    specs = dict(_require(document, "contract_specs", source))
    tick_divisor = int(specs.pop("tick_size_divisor", 1) or 1)

    declared = [str(item) for item in (document.get("capabilities") or [])]
    unknown = [item for item in declared if item not in KNOWN_CAPABILITIES]
    if unknown:
        raise StrategyConfigError(
            f"{source}: unknown capabilit{'y' if len(unknown) == 1 else 'ies'} "
            f"{', '.join(sorted(unknown))}. Known: {', '.join(KNOWN_CAPABILITIES)}."
        )

    margin = document.get("margin") or {}
    margin_model = str(margin.get("model") or MarginModel.PERCENT_OF_NOTIONAL)
    if margin_model != MarginModel.PERCENT_OF_NOTIONAL:
        raise StrategyConfigError(
            f"{source}: margin.model {margin_model!r} is not implemented. "
            f"Only {MarginModel.PERCENT_OF_NOTIONAL!r} is."
        )

    return StrategyDefinition(
        key=key,
        label=str(document.get("label") or key),
        description=str(document.get("description") or "").strip(),
        enabled_by_default=bool(document.get("enabled_by_default", True)),
        symbol=str(_require(document, "underlying.symbol", source)),
        underlying_scrip=int(_require(document, "underlying.underlying_scrip", source)),
        exchange_segment=str(_require(document, "underlying.exchange_segment", source)),
        exchange_segment_code=int(
            _require(document, "underlying.exchange_segment_code", source)
        ),
        exchange_id=str(_require(document, "underlying.exchange_id", source)),
        option_instrument_type=str(
            _require(document, "underlying.option_instrument_type", source)
        ),
        futures_instrument_type=str(
            _require(document, "underlying.futures_instrument_type", source)
        ),
        contract_specs=specs,
        tick_size_divisor=tick_divisor,
        market_hours=MarketHours(
            timezone=str(hours.get("timezone") or "Asia/Kolkata"),
            open=_parse_hhmm(_require(hours, "open", source), source, "market_hours.open"),
            close=_parse_hhmm(
                _require(hours, "close", source), source, "market_hours.close"
            ),
            close_us_dst=(
                _parse_hhmm(close_us_dst, source, "market_hours.close_us_dst")
                if close_us_dst
                else None
            ),
            trading_days=tuple(int(day) for day in (hours.get("trading_days") or [])),
        ),
        subscription=SubscriptionPolicy(
            strike_window=int(_require(subscription, "strike_window", source)),
            expiries_to_subscribe=int(
                _require(subscription, "expiries_to_subscribe", source)
            ),
            resubscribe_move_strikes=int(
                subscription.get("resubscribe_move_strikes", 3)
            ),
        ),
        greeks_expiries_to_poll=int(
            (document.get("greeks") or {}).get("expiries_to_poll", 2)
        ),
        charges_rate_card=str(
            (document.get("charges") or {}).get("rate_card") or "charges"
        ),
        margin=MarginModel(
            model=margin_model,
            short_option_percent_of_notional=Decimal(
                str(margin.get("short_option_percent_of_notional", "0"))
            ),
        ),
        capabilities=frozenset(declared),
    )
