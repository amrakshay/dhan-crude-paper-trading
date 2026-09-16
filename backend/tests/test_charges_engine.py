"""Charges engine unit tests: each component, its base, and its side."""
import copy
from decimal import Decimal

import pytest

from src.charges.services.charges_engine import (
    ChargesConfigError,
    ChargesEngine,
)

LOT_SIZE = 100


@pytest.fixture
def engine():
    return ChargesEngine()


# --- turnover --------------------------------------------------------------
def test_turnover_is_premium_times_lot_size_times_lots(engine):
    """CRUDEOIL is quoted per barrel; one lot is 100 barrels."""
    assert engine.compute_turnover(Decimal("100"), 100, 1) == Decimal("10000")
    assert engine.compute_turnover(Decimal("100"), 100, 3) == Decimal("30000")
    assert engine.compute_turnover(Decimal("12.5"), 100, 2) == Decimal("2500")


def test_charges_scale_linearly_with_lots(engine):
    one = engine.compute_order_charges("SELL", Decimal("100"), LOT_SIZE, 1)
    three = engine.compute_order_charges("SELL", Decimal("100"), LOT_SIZE, 3)

    assert three.ctt == one.ctt * 3
    assert three.exchange_transaction_charge == one.exchange_transaction_charge * 3
    # Brokerage is per order, not per lot -- it must NOT scale.
    assert three.brokerage == one.brokerage


# --- CTT -------------------------------------------------------------------
def test_ctt_is_charged_on_the_sell_side_only(engine):
    buy = engine.compute_order_charges("BUY", Decimal("100"), LOT_SIZE, 1)
    sell = engine.compute_order_charges("SELL", Decimal("100"), LOT_SIZE, 1)

    assert buy.ctt == Decimal("0.00")
    assert sell.ctt == Decimal("5.00")


def test_ctt_rate_is_five_basis_points_of_premium(engine):
    result = engine.compute_order_charges("SELL", Decimal("250"), LOT_SIZE, 1)
    # 0.0005 * (250 * 100) = 12.50
    assert result.ctt == Decimal("12.50")


def test_ctt_component_records_its_legal_basis(engine):
    result = engine.compute_order_charges("SELL", Decimal("100"), LOT_SIZE, 1)
    ctt = next(c for c in result.components if c.name == "ctt")

    assert "Finance Act 2013" in ctt.note
    assert ctt.rate == Decimal("0.0005")
    assert ctt.base == Decimal("10000")


# --- exchange transaction charge -------------------------------------------
def test_exchange_charge_uses_the_options_rate_not_the_futures_rate(engine):
    result = engine.compute_order_charges("BUY", Decimal("100"), LOT_SIZE, 1)
    component = next(
        c for c in result.components if c.name == "exchange_transaction_charge"
    )

    assert component.rate == Decimal("0.000418"), "Rs 41.80 per lakh, options"
    assert component.rate != Decimal("0.000021"), "that is the futures rate"


def test_exchange_charge_is_not_the_refuted_figure(engine):
    """0.053% appears in no MCX circular and must not be used."""
    result = engine.compute_order_charges("BUY", Decimal("100"), LOT_SIZE, 1)

    assert result.exchange_transaction_charge == Decimal("4.18")
    assert result.exchange_transaction_charge != Decimal("5.30")


def test_exchange_charge_is_levied_on_both_sides(engine):
    buy = engine.compute_order_charges("BUY", Decimal("100"), LOT_SIZE, 1)
    sell = engine.compute_order_charges("SELL", Decimal("100"), LOT_SIZE, 1)

    assert buy.exchange_transaction_charge == sell.exchange_transaction_charge


# --- SEBI fee --------------------------------------------------------------
def test_sebi_fee_is_ten_rupees_per_crore(engine):
    # A turnover of exactly Rs 1 crore should attract exactly Rs 10.
    result = engine.compute_order_charges("BUY", Decimal("100000"), LOT_SIZE, 1)

    assert result.turnover == Decimal("10000000.00")
    assert result.sebi_turnover_fee == Decimal("10.00")


# --- stamp duty ------------------------------------------------------------
def test_stamp_duty_is_charged_on_the_buy_side_only(engine):
    buy = engine.compute_order_charges("BUY", Decimal("100"), LOT_SIZE, 1)
    sell = engine.compute_order_charges("SELL", Decimal("100"), LOT_SIZE, 1)

    assert buy.stamp_duty == Decimal("0.30")
    assert sell.stamp_duty == Decimal("0.00")


def test_stamp_duty_is_three_hundred_per_crore(engine):
    result = engine.compute_order_charges("BUY", Decimal("100000"), LOT_SIZE, 1)

    assert result.turnover == Decimal("10000000.00")
    assert result.stamp_duty == Decimal("300.00")


# --- GST -------------------------------------------------------------------
def test_gst_applies_to_service_charges_not_to_trade_value(engine):
    result = engine.compute_order_charges("SELL", Decimal("100"), LOT_SIZE, 1)
    gst = next(c for c in result.components if c.name == "gst")

    expected_base = (
        result.brokerage + result.exchange_transaction_charge + result.sebi_turnover_fee
    )
    assert gst.base == expected_base
    assert gst.rate == Decimal("0.18")
    # It must not be 18% of the Rs 10,000 turnover.
    assert result.gst < Decimal("10")


def test_gst_excludes_ctt_and_stamp_duty(engine):
    """A sell order pays CTT; a buy pays stamp duty. If either entered the GST
    base, the two GST figures would differ."""
    buy = engine.compute_order_charges("BUY", Decimal("100"), LOT_SIZE, 1)
    sell = engine.compute_order_charges("SELL", Decimal("100"), LOT_SIZE, 1)

    assert buy.gst == sell.gst == Decimal("4.35")


# --- brokerage -------------------------------------------------------------
def test_brokerage_is_flat_per_order_by_default(engine):
    small = engine.compute_order_charges("BUY", Decimal("1"), LOT_SIZE, 1)
    large = engine.compute_order_charges("BUY", Decimal("5000"), LOT_SIZE, 1)

    assert small.brokerage == large.brokerage == Decimal("20.00")


def test_brokerage_can_be_configured_as_a_capped_percentage():
    rates = copy.deepcopy(ChargesEngine.load_rates())
    rates["brokerage"]["mode"] = "percentage"
    engine = ChargesEngine(rates=rates)

    # 0.0003 * 10000 = 3.00, below the Rs 20 cap.
    small = engine.compute_order_charges("BUY", Decimal("100"), LOT_SIZE, 1)
    assert small.brokerage == Decimal("3.00")

    # 0.0003 * 1,000,000 = 300, capped at 20.
    large = engine.compute_order_charges("BUY", Decimal("10000"), LOT_SIZE, 1)
    assert large.brokerage == Decimal("20.00")


# --- validation ------------------------------------------------------------
@pytest.mark.parametrize("lots", [0, -1])
def test_non_positive_lots_are_rejected(engine, lots):
    with pytest.raises(ValueError):
        engine.compute_order_charges("BUY", Decimal("100"), LOT_SIZE, lots)


def test_negative_premium_is_rejected(engine):
    with pytest.raises(ValueError):
        engine.compute_order_charges("BUY", Decimal("-1"), LOT_SIZE, 1)


def test_zero_premium_produces_only_fixed_charges(engine):
    """A zero-premium fill still costs brokerage plus GST on it."""
    result = engine.compute_order_charges("BUY", Decimal("0"), LOT_SIZE, 1)

    assert result.turnover == Decimal("0.00")
    assert result.brokerage == Decimal("20.00")
    assert result.ctt == Decimal("0.00")
    assert result.gst == Decimal("3.60")      # 0.18 * 20
    assert result.total == Decimal("23.60")


def test_a_missing_rate_fails_loudly():
    """A rate card with a hole must raise, never silently charge zero."""
    rates = copy.deepcopy(ChargesEngine.load_rates())
    del rates["ctt"]["sell_option_premium_rate"]
    engine = ChargesEngine(rates=rates)

    with pytest.raises(ChargesConfigError):
        engine.compute_order_charges("SELL", Decimal("100"), LOT_SIZE, 1)


# --- auditability ----------------------------------------------------------
def test_every_component_is_returned_with_its_formula(engine):
    result = engine.compute_order_charges("SELL", Decimal("100"), LOT_SIZE, 1)

    names = {component.name for component in result.components}
    assert names == {
        "brokerage", "ctt", "exchange_transaction_charge",
        "sebi_turnover_fee", "stamp_duty", "gst",
    }
    for component in result.components:
        assert component.formula, f"{component.name} has no formula recorded"


def test_breakdown_records_the_rate_card_version(engine):
    result = engine.compute_order_charges("BUY", Decimal("100"), LOT_SIZE, 1)

    assert result.rates_version == ChargesEngine.load_rates()["version"]
    assert result.rounding_mode in ("exact", "broker_compatible")


def test_components_sum_to_the_total(engine):
    result = engine.compute_order_charges("SELL", Decimal("137.5"), LOT_SIZE, 2)

    summed = (
        result.brokerage
        + result.ctt
        + result.exchange_transaction_charge
        + result.sebi_turnover_fee
        + result.stamp_duty
        + result.gst
    )
    assert abs(summed - result.total) <= Decimal("0.01")


def test_all_amounts_are_decimal_not_float(engine):
    """Float money would make these totals irreproducible."""
    result = engine.compute_order_charges("SELL", Decimal("100"), LOT_SIZE, 1)

    for value in (
        result.turnover, result.brokerage, result.ctt,
        result.exchange_transaction_charge, result.sebi_turnover_fee,
        result.stamp_duty, result.gst, result.total,
    ):
        assert isinstance(value, Decimal)
