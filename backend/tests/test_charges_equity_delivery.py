"""NSE equity delivery charges, and the levy mechanism that made them possible.

Before 2026-09-18 the engine computed a fixed set of six components by calling
six methods. NSE delivery needs two that do not exist in that set -- STT on
BOTH legs, and a flat depository fee per sell -- so the card now declares its
line items and the engine iterates them.

The load-bearing assertion in this file is not any equity number. It is that
the MCX crude card, rewritten into the same declarative form, still produces
exactly the same six components in exactly the same order. Its totals are
pinned to the paisa by `test_charges_rate_cards.py` and
`test_charges_worked_examples.py`, including the documented Rs 0.01 divergence
from a published broker calculator.
"""
from decimal import Decimal

import pytest

from src.charges.services.charges_engine import ChargesConfigError, ChargesEngine


EQUITY_CARD = "nse-equity-delivery"
CRUDE_CARD = "mcx-commodity-options"

# A realistic position for this strategy: ~Rs 1,00,000, which is one tenth of
# the Rs 10,00,000 book.
PRICE = Decimal("429.20")
QUANTITY = 233


@pytest.fixture
def equity():
    return ChargesEngine(rate_card=EQUITY_CARD)


@pytest.fixture
def crude():
    return ChargesEngine(rate_card=CRUDE_CARD)


def _by_name(breakdown):
    return {component.name: component.amount for component in breakdown.components}


# --- the crude card must not have moved -------------------------------------


def test_the_commodity_card_still_charges_exactly_its_historical_six(crude):
    """Rewriting the card into levies must not add, drop or reorder a line."""
    breakdown = crude.compute_order_charges("SELL", Decimal("100"), lot_size=100)

    assert [component.name for component in breakdown.components] == [
        "brokerage",
        "ctt",
        "exchange_transaction_charge",
        "sebi_turnover_fee",
        "stamp_duty",
        "gst",
    ]


def test_the_commodity_card_has_no_stt_and_no_dp_charge(crude):
    """Commodity options pay CTT, and there is no depository to charge."""
    names = {
        component.name
        for side in ("BUY", "SELL")
        for component in crude.compute_order_charges(
            side, Decimal("100"), lot_size=100
        ).components
    }

    assert "stt" not in names
    assert "dp_charge" not in names
    assert "ctt" in names


def test_commodity_ctt_is_still_sell_side_only(crude):
    buy = _by_name(crude.compute_order_charges("BUY", Decimal("100"), lot_size=100))
    sell = _by_name(crude.compute_order_charges("SELL", Decimal("100"), lot_size=100))

    assert buy["ctt"] == Decimal("0.00")
    assert sell["ctt"] > 0


# --- STT is charged on both legs, which CTT is not --------------------------


def test_stt_is_charged_on_both_legs(equity):
    """The whole reason compute_ctt could not be reused under another name."""
    buy = _by_name(equity.compute_order_charges("BUY", PRICE, 1, QUANTITY))
    sell = _by_name(equity.compute_order_charges("SELL", PRICE, 1, QUANTITY))

    assert buy["stt"] > 0
    assert sell["stt"] > 0
    assert buy["stt"] == sell["stt"], "the same rate applies to purchase and sale"


def test_stt_is_a_tenth_of_a_percent_of_turnover(equity):
    breakdown = equity.compute_order_charges("BUY", PRICE, 1, QUANTITY)
    stt = _by_name(breakdown)["stt"]

    assert stt == (breakdown.turnover * Decimal("0.001")).quantize(Decimal("0.01"))


def test_the_equity_card_charges_no_ctt(equity):
    """STT and CTT are different taxes, not one tax under two names."""
    names = {
        component.name
        for component in equity.compute_order_charges("SELL", PRICE, 1, QUANTITY).components
    }
    assert "ctt" not in names


# --- the flat depository charge ---------------------------------------------


def test_the_dp_charge_is_levied_only_on_a_sell(equity):
    buy = _by_name(equity.compute_order_charges("BUY", PRICE, 1, QUANTITY))
    sell = _by_name(equity.compute_order_charges("SELL", PRICE, 1, QUANTITY))

    assert buy["dp_charge"] == Decimal("0.00")
    assert sell["dp_charge"] == Decimal("12.50")


def test_the_dp_charge_does_not_scale_with_quantity(equity):
    """It is per ISIN, not per share -- a flat fee that is not brokerage."""
    small = _by_name(equity.compute_order_charges("SELL", PRICE, 1, 1))
    large = _by_name(equity.compute_order_charges("SELL", PRICE, 1, 10_000))

    assert small["dp_charge"] == large["dp_charge"] == Decimal("12.50")


def test_gst_is_charged_on_the_dp_charge(equity):
    """Dhan publishes "Rs 12.50 / instruction / ISIN + GST"."""
    sell = _by_name(equity.compute_order_charges("SELL", PRICE, 1, QUANTITY))
    buy = _by_name(equity.compute_order_charges("BUY", PRICE, 1, QUANTITY))

    # The only difference in the GST base between the two sides is the DP fee.
    assert sell["gst"] - buy["gst"] == (
        Decimal("12.50") * Decimal("0.18")
    ).quantize(Decimal("0.01"))


def test_gst_is_not_charged_on_stt_or_stamp_duty(equity):
    breakdown = equity.compute_order_charges("BUY", PRICE, 1, QUANTITY)
    amounts = _by_name(breakdown)
    gst_component = next(c for c in breakdown.components if c.name == "gst")

    assert amounts["stt"] > gst_component.base
    assert amounts["stamp_duty"] > 0
    assert gst_component.base < amounts["stt"] + amounts["stamp_duty"]


# --- stamp duty and brokerage ------------------------------------------------


def test_stamp_duty_is_buy_side_only(equity):
    buy = _by_name(equity.compute_order_charges("BUY", PRICE, 1, QUANTITY))
    sell = _by_name(equity.compute_order_charges("SELL", PRICE, 1, QUANTITY))

    assert buy["stamp_duty"] > 0
    assert sell["stamp_duty"] == Decimal("0.00")


def test_delivery_brokerage_is_zero_and_says_so(equity):
    buy = equity.compute_order_charges("BUY", PRICE, 1, QUANTITY)

    assert _by_name(buy)["brokerage"] == Decimal("0.00")
    brokerage = next(c for c in buy.components if c.name == "brokerage")
    assert "0" in brokerage.formula


def test_the_ipft_contribution_is_a_separate_line(equity):
    """NSE split Rs 307/crore into Rs 306.99 + Rs 0.01 on 1 March 2026."""
    names = [c.name for c in equity.compute_order_charges("BUY", PRICE, 1, QUANTITY).components]

    assert "exchange_transaction_charge" in names
    assert "ipft" in names
    assert names.index("exchange_transaction_charge") < names.index("gst")


# --- the round trip ----------------------------------------------------------


def test_a_round_trip_costs_roughly_a_quarter_percent(equity):
    """Sanity, not a pinned figure: STT on both legs dominates everything else.

    The strategy specification quotes "round trip ~0.30%", which includes its
    0.05%/side slippage assumption; this engine applies slippage in the fill
    simulator, not here.
    """
    buy = equity.compute_order_charges("BUY", PRICE, 1, QUANTITY)
    sell = equity.compute_order_charges("SELL", PRICE, 1, QUANTITY)
    total = (buy + sell).total

    as_percent = total / buy.turnover * 100
    assert Decimal("0.20") < as_percent < Decimal("0.30"), as_percent


def test_every_equity_component_is_named_by_the_card_not_by_python(equity):
    """A card can introduce a tax without a schema change or a code change."""
    breakdown = equity.compute_order_charges("SELL", PRICE, 1, QUANTITY)
    labels = {component.name: component.display_label() for component in breakdown.components}

    assert labels["stt"] == "STT"
    assert labels["dp_charge"] == "Depository (DP) charge"
    assert labels["ipft"] == "NSE IPFT contribution"


# --- configuration errors ----------------------------------------------------


def test_a_card_with_no_levies_is_refused():
    engine = ChargesEngine(rate_card="x", rates={"version": "test"})
    with pytest.raises(ChargesConfigError, match="levies"):
        engine.compute_order_charges("BUY", Decimal("100"), 1, 1)


def test_a_levy_may_not_reference_a_line_item_below_it():
    """GST cannot be charged on something that has not been computed yet."""
    engine = ChargesEngine(
        rate_card="x",
        rates={
            "version": "test",
            "levies": [
                {
                    "name": "gst",
                    "kind": "percent_of_components",
                    "rate": 0.18,
                    "applies_to": ["brokerage"],
                },
                {"name": "brokerage", "kind": "brokerage"},
            ],
            "brokerage": {"mode": "flat", "flat_per_order": 20},
        },
    )
    with pytest.raises(ChargesConfigError, match="listed earlier"):
        engine.compute_order_charges("BUY", Decimal("100"), 1, 1)


def test_an_unknown_levy_kind_is_refused():
    engine = ChargesEngine(
        rate_card="x",
        rates={
            "version": "test",
            "levies": [{"name": "vibes_tax", "kind": "vibes", "rate": 0.5}],
        },
    )
    with pytest.raises(ChargesConfigError, match="unknown kind"):
        engine.compute_order_charges("BUY", Decimal("100"), 1, 1)


def test_the_swing_strategy_names_this_card():
    from src.strategies.services.strategy_registry import get_strategy_registry

    swing = get_strategy_registry().require("nse-swing-momentum")
    assert swing.charges_rate_card == EQUITY_CARD
    assert ChargesEngine.for_strategy(swing).rate_card_name == EQUITY_CARD
