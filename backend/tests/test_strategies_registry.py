"""The strategy registry: what exists, what is on, and what follows."""
from decimal import Decimal

import pytest

from src.strategies.services.strategy_definition import (
    CAPABILITY_CHART_TRADING,
    CAPABILITY_GREEKS,
    CAPABILITY_OPTION_CHAIN,
    CAPABILITY_PNL_REPORTS,
    StrategyConfigError,
    build_definition,
)
from src.strategies.services.strategy_registry import (
    StrategyRegistry,
    get_strategy_registry,
)

CRUDE = "mcx-crude-options"


def _document(**overrides):
    document = {
        "key": "test-strategy",
        "label": "Test",
        "underlying": {
            "symbol": "TESTOIL",
            "underlying_scrip": 1,
            "exchange_segment": "MCX_COMM",
            "exchange_segment_code": 5,
            "exchange_id": "MCX",
            "option_instrument_type": "OPTFUT",
            "futures_instrument_type": "FUTCOM",
        },
        "contract_specs": {"TESTOIL": {"lot_size": 100}, "tick_size_divisor": 100},
        "market_hours": {
            "open": "09:00",
            "close": "23:30",
            "trading_days": [0, 1, 2, 3, 4],
        },
        "subscription": {"strike_window": 20, "expiries_to_subscribe": 2},
        "capabilities": [CAPABILITY_OPTION_CHAIN],
    }
    document.update(overrides)
    return document


# --- the shipped module ----------------------------------------------------
def test_the_crude_module_loads_with_the_values_it_had_in_the_flat_config():
    """The config split must not have changed a single number."""
    strategy = get_strategy_registry().require(CRUDE)

    assert strategy.symbol == "CRUDEOIL"
    assert strategy.underlying_scrip == 294
    assert strategy.exchange_segment == "MCX_COMM"
    assert strategy.exchange_segment_code == 5
    assert strategy.exchange_id == "MCX"
    assert strategy.option_instrument_type == "OPTFUT"
    assert strategy.futures_instrument_type == "FUTCOM"
    # The lot size Dhan's master does not publish for MCX.
    assert strategy.lot_size() == 100
    assert strategy.lot_size("CRUDEOILM") == 10
    # TICK_SIZE is in paise.
    assert strategy.tick_size_divisor == 100
    assert strategy.subscription.strike_window == 20
    assert strategy.subscription.expiries_to_subscribe == 2
    assert strategy.subscription.resubscribe_move_strikes == 3
    assert strategy.greeks_expiries_to_poll == 2
    assert strategy.market_hours.open.strftime("%H:%M") == "09:00"
    assert strategy.market_hours.close.strftime("%H:%M") == "23:30"
    assert strategy.market_hours.trading_days == (0, 1, 2, 3, 4)


def test_the_crude_module_declares_a_margin_estimate_not_a_broker_figure():
    strategy = get_strategy_registry().require(CRUDE)

    assert strategy.margin.model == "percent_of_notional"
    assert strategy.margin.short_option_percent_of_notional == Decimal("0.10")


def test_the_crude_module_is_enabled_and_owns_crude_contracts():
    registry = get_strategy_registry()

    assert registry.is_enabled(CRUDE)
    assert registry.for_instrument("MCX_COMM", "CRUDEOIL").key == CRUDE
    assert registry.for_instrument("NSE_FNO", "NIFTY") is None
    # Same underlying on another segment is not the same instrument.
    assert registry.for_instrument("NSE_FNO", "CRUDEOIL") is None


# --- definition validation -------------------------------------------------
def test_a_strategy_may_not_advertise_a_capability_this_codebase_does_not_have():
    with pytest.raises(StrategyConfigError) as exc_info:
        build_definition(_document(capabilities=["teleportation"]), source="x.yaml")

    assert "teleportation" in str(exc_info.value)


def test_a_missing_required_key_names_the_file_and_the_key():
    document = _document()
    del document["underlying"]["underlying_scrip"]

    with pytest.raises(StrategyConfigError) as exc_info:
        build_definition(document, source="broken.yaml")

    assert "broken.yaml" in str(exc_info.value)
    assert "underlying.underlying_scrip" in str(exc_info.value)


def test_an_unimplemented_margin_model_is_refused_rather_than_blocking_zero():
    with pytest.raises(StrategyConfigError) as exc_info:
        build_definition(
            _document(margin={"model": "span_plus_exposure"}), source="x.yaml"
        )

    assert "span_plus_exposure" in str(exc_info.value)


# --- capabilities ----------------------------------------------------------
def test_a_capability_is_effective_only_where_the_strategy_supports_it():
    registry = StrategyRegistry()
    registry._definitions = {  # noqa: SLF001 - building a registry by hand
        "a": build_definition(
            _document(key="a", capabilities=[CAPABILITY_OPTION_CHAIN]), "a.yaml"
        ),
    }
    registry._strategy_enabled = {"a": True}
    registry._capability_enabled = {
        CAPABILITY_OPTION_CHAIN: True,
        CAPABILITY_GREEKS: True,
    }
    registry._loaded = True

    assert registry.capability_active(CAPABILITY_OPTION_CHAIN, "a")
    # Globally on, but this strategy does not support it.
    assert not registry.capability_active(CAPABILITY_GREEKS, "a")


def test_a_capability_switched_off_globally_is_off_for_every_strategy():
    registry = get_strategy_registry()
    try:
        registry.set_capability_enabled(CAPABILITY_CHART_TRADING, False)
        assert not registry.capability_active(CAPABILITY_CHART_TRADING, CRUDE)
        assert not registry.capability_active_anywhere(CAPABILITY_CHART_TRADING)
    finally:
        registry.set_capability_enabled(CAPABILITY_CHART_TRADING, True)


def test_a_disabled_strategy_makes_its_capabilities_ineffective():
    registry = get_strategy_registry()
    try:
        registry.set_enabled(CRUDE, False)
        assert not registry.capability_active(CAPABILITY_OPTION_CHAIN, CRUDE)
        assert not registry.capability_active_anywhere(CAPABILITY_OPTION_CHAIN)
        assert registry.enabled() == []
    finally:
        registry.set_enabled(CRUDE, True)


# --- pages -----------------------------------------------------------------
def test_pages_follow_the_enabled_strategies_and_capabilities():
    registry = get_strategy_registry()

    assert "/live" in registry.feature_pages()
    assert "/chain" in registry.feature_pages()

    try:
        registry.set_capability_enabled(CAPABILITY_OPTION_CHAIN, False)
        assert "/chain" not in registry.feature_pages()
        # Turning the chain off does not take the reports page with it.
        assert "/reports" in registry.feature_pages()
    finally:
        registry.set_capability_enabled(CAPABILITY_OPTION_CHAIN, True)


def test_pages_that_show_history_are_never_gated():
    """Decision 3: past trades stay readable whatever the toggles say."""
    gated = StrategyRegistry.gated_pages()

    assert "/orders" not in gated
    assert "/positions" not in gated


def test_no_strategy_running_means_no_live_page():
    registry = get_strategy_registry()
    try:
        registry.set_enabled(CRUDE, False)
        assert "/live" not in registry.feature_pages()
        assert "/chain" not in registry.feature_pages()
    finally:
        registry.set_enabled(CRUDE, True)
    assert "/live" in registry.feature_pages()


# --- stored state ----------------------------------------------------------
def test_stored_state_for_an_unknown_strategy_is_ignored_not_trusted():
    """The MANAGED_KEYS property: a stray row cannot start influencing config."""
    registry = get_strategy_registry()

    registry.apply_state({"no-such-strategy": True}, {"no-such-capability": True})

    assert registry.get("no-such-strategy") is None
    assert not registry.is_capability_enabled("no-such-capability")
    assert registry.is_enabled(CRUDE)


def test_capability_defaults_come_from_the_legacy_enable_flags():
    """`chart_trading.enabled` and `greeks_poller.enabled` still mean what they did."""
    registry = get_strategy_registry()

    assert registry.is_capability_enabled(CAPABILITY_CHART_TRADING)
    assert registry.is_capability_enabled(CAPABILITY_GREEKS)
    assert registry.is_capability_enabled(CAPABILITY_PNL_REPORTS)
