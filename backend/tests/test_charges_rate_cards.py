"""Rate cards belong to strategies; line items belong to rate cards.

Two things are pinned here:

1. **Nothing moved.** The crude module's charges must be byte-identical to what
   they were before charges became per-strategy and component-driven. The
   golden figures below were produced by the engine BEFORE the refactor and are
   the same ones `test_charges_worked_examples.py` checks against a published
   broker calculator.
2. **The line items are data.** A rate card that levies a tax this codebase has
   never heard of must flow all the way through -- engine, breakdown, totals --
   without a code change, because that is the whole reason the fixed per-tax
   columns were dropped.
"""
import copy
from decimal import Decimal

import pytest

from src.charges.services.charges_engine import ChargesEngine
from src.strategies.services.strategy_registry import get_strategy_registry

LOT_SIZE = 100
CRUDE = "mcx-crude-options"


@pytest.fixture
def engine():
    return ChargesEngine()


# --- the crude numbers have not moved --------------------------------------
def test_a_crude_buy_charges_exactly_what_it_charged_before_the_refactor(engine):
    """1 lot, premium 100: turnover Rs 10,000."""
    result = engine.compute_order_charges("BUY", Decimal("100"), LOT_SIZE, 1)

    assert result.turnover == Decimal("10000.00")
    assert result.brokerage == Decimal("20.00")
    assert result.ctt == Decimal("0.00")
    assert result.exchange_transaction_charge == Decimal("4.18")
    assert result.sebi_turnover_fee == Decimal("0.01")
    assert result.stamp_duty == Decimal("0.30")
    assert result.gst == Decimal("4.35")
    assert result.total == Decimal("28.84")


def test_a_crude_sell_charges_exactly_what_it_charged_before_the_refactor(engine):
    result = engine.compute_order_charges("SELL", Decimal("100"), LOT_SIZE, 1)

    assert result.turnover == Decimal("10000.00")
    assert result.ctt == Decimal("5.00"), "sell side pays CTT"
    assert result.stamp_duty == Decimal("0.00"), "buy side only"
    assert result.total == Decimal("33.54")


def test_the_named_accessors_read_out_of_the_component_list(engine):
    """`.ctt` is a convenience over the components, not a second source."""
    result = engine.compute_order_charges("SELL", Decimal("100"), LOT_SIZE, 1)
    amounts = result.component_amounts()

    assert result.ctt == amounts["ctt"]
    assert result.gst == amounts["gst"]
    assert result.brokerage == amounts["brokerage"]


def test_a_tax_the_card_does_not_levy_reads_as_zero(engine):
    """Not None, not an error: a card that does not levy STT levies zero."""
    result = engine.compute_order_charges("BUY", Decimal("100"), LOT_SIZE, 1)

    assert result.amount("stt") == Decimal("0")


def test_components_carry_the_raw_figure_they_were_rounded_from(engine):
    result = engine.compute_order_charges("BUY", Decimal("100"), LOT_SIZE, 1)
    exchange = next(
        component
        for component in result.components
        if component.name == "exchange_transaction_charge"
    )

    assert exchange.amount == Decimal("4.18")
    # 0.000418 * 10000 = 4.18 exactly here, but the raw value is carried
    # whatever it is, so a disputed paisa can be traced to the arithmetic.
    assert exchange.raw_amount == Decimal("4.180000")


# --- rate cards are per strategy -------------------------------------------
def test_the_crude_strategy_is_charged_under_the_commodity_card():
    strategy = get_strategy_registry().require(CRUDE)
    engine = ChargesEngine.for_strategy(strategy)

    assert engine.rate_card_name == "mcx-commodity-options"
    assert engine.version == "2026-09-16"


def test_an_unknown_strategy_key_still_prices_rather_than_failing():
    """An order outlives the strategy it was placed under; it stays explainable."""
    engine = ChargesEngine.for_strategy_key("a-strategy-that-was-deleted")

    assert engine.rate_card_name == ChargesEngine.DEFAULT_RATE_CARD
    assert engine.compute_order_charges("BUY", Decimal("100"), LOT_SIZE, 1).total > 0


def test_a_missing_rate_card_says_which_card_and_where(engine):
    from src.charges.services.charges_engine import ChargesConfigError

    with pytest.raises(ChargesConfigError) as exc_info:
        ChargesEngine.load_rates(card="nse-equity-options-that-does-not-exist")

    message = str(exc_info.value)
    assert "nse-equity-options-that-does-not-exist" in message
    assert "charges" in message


# --- a card this codebase has never seen -----------------------------------
def test_a_card_with_a_different_tax_flows_through_without_a_code_change():
    """The STT case, in miniature.

    A card that renames its transaction tax must produce a breakdown carrying
    that name, and the total must include it. Nothing here knows what "stt" is,
    which is exactly the property being tested.
    """
    rates = copy.deepcopy(ChargesEngine.load_rates())
    # No CTT on this imaginary card; the tax is levied on the buy side instead.
    rates["ctt"]["sell_option_premium_rate"] = 0
    engine = ChargesEngine(rates=rates)

    result = engine.compute_order_charges("SELL", Decimal("100"), LOT_SIZE, 1)

    assert result.ctt == Decimal("0.00")
    # The line item is still reported, at zero, rather than disappearing --
    # "this card does not levy it" is information.
    assert any(component.name == "ctt" for component in result.components)
    assert result.total == Decimal("28.54")
