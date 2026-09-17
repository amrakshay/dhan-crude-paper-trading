"""Validation of the charges engine against a published broker calculator.

Source of truth for these fixtures: Zerodha's brokerage calculator
(https://zerodha.com/brokerage-calculator, Commodities -> Options -> MCX ->
CRUDEOIL), run live on 2026-09-16, whose internal formulas were read from the
page:

    turnover  = (buy_premium + sell_premium) * lot * qty
    brokerage = min(20, 0.0003 * (premium + strike) * lot * qty)   per side
    ctt       = 0.0005 * sell_premium * lot * qty
    etc       = 0.000418 * turnover
    sebi      = 0.000001 * turnover
    stamp     = Math.round(0.00003 * buy_premium * lot * qty)
    gst       = 0.18 * (brokerage + etc + sebi)

Where this engine and the calculator disagree, BOTH numbers are asserted and
the reason is stated. Nothing is tuned to force agreement.
"""
import copy
from decimal import Decimal

import pytest

from src.charges.services.charges_engine import ChargesEngine

LOT_SIZE = 100          # CRUDEOIL: 100 barrels per lot (MCX contract spec)
FLAT_BROKERAGE = Decimal("20")


@pytest.fixture
def engine():
    """Engine in `exact` mode -- no intermediate rounding."""
    return ChargesEngine()


@pytest.fixture
def broker_engine():
    """Engine configured to reproduce a discount broker's displayed note."""
    rates = copy.deepcopy(ChargesEngine.load_rates())
    rates["rounding"]["mode"] = "broker_compatible"
    return ChargesEngine(rates=rates)


# ---------------------------------------------------------------------------
# Example A -- BUY 1 lot CRUDEOIL option, premium 100
# ---------------------------------------------------------------------------
def test_example_a_buy_one_lot_exact(engine):
    """Hand arithmetic: 20 + 0 + 4.18 + 0.01 + 0.30 + 4.35 = 28.84"""
    result = engine.compute_order_charges("BUY", Decimal("100"), LOT_SIZE, 1)

    assert result.turnover == Decimal("10000.00")
    assert result.brokerage == FLAT_BROKERAGE
    assert result.ctt == Decimal("0.00"), "CTT is a sell-side charge"
    assert result.exchange_transaction_charge == Decimal("4.18")   # 0.000418 * 10000
    assert result.sebi_turnover_fee == Decimal("0.01")             # 0.000001 * 10000
    assert result.stamp_duty == Decimal("0.30")                    # 0.00003 * 10000
    assert result.gst == Decimal("4.35")                           # 0.18 * 24.19
    assert result.total == Decimal("28.84")


def test_example_a_matches_the_broker_calculator(broker_engine):
    """Zerodha reports 28.54: identical except stamp duty, which it rounds to
    the nearest rupee (Math.round(0.30) == 0)."""
    result = broker_engine.compute_order_charges("BUY", Decimal("100"), LOT_SIZE, 1)

    assert result.stamp_duty == Decimal("0.00")
    assert result.total == Decimal("28.54")


def test_example_a_stamp_duty_is_the_entire_difference(engine, broker_engine):
    exact = engine.compute_order_charges("BUY", Decimal("100"), LOT_SIZE, 1)
    broker = broker_engine.compute_order_charges("BUY", Decimal("100"), LOT_SIZE, 1)

    assert exact.total - broker.total == Decimal("0.30") == exact.stamp_duty


# ---------------------------------------------------------------------------
# Example B -- SELL 1 lot CRUDEOIL option, premium 100
# ---------------------------------------------------------------------------
def test_example_b_sell_one_lot_exact(engine):
    """Hand arithmetic: 20 + 5 + 4.18 + 0.01 + 0 + 4.35 = 33.54"""
    result = engine.compute_order_charges("SELL", Decimal("100"), LOT_SIZE, 1)

    assert result.turnover == Decimal("10000.00")
    assert result.brokerage == FLAT_BROKERAGE
    assert result.ctt == Decimal("5.00"), "0.0005 * 10000, sell side"
    assert result.exchange_transaction_charge == Decimal("4.18")
    assert result.sebi_turnover_fee == Decimal("0.01")
    assert result.stamp_duty == Decimal("0.00"), "stamp duty is buy-side only"
    assert result.gst == Decimal("4.35")
    assert result.total == Decimal("33.54")


def test_example_b_matches_the_broker_calculator_exactly(broker_engine):
    """No gap at all on this one -- nothing rounds."""
    result = broker_engine.compute_order_charges("SELL", Decimal("100"), LOT_SIZE, 1)
    assert result.total == Decimal("33.54")


def test_example_b_net_credit():
    engine = ChargesEngine()
    result = engine.compute_order_charges("SELL", Decimal("100"), LOT_SIZE, 1)

    net_credit = result.turnover - result.total
    assert net_credit == Decimal("9966.46")


# ---------------------------------------------------------------------------
# Example C -- round trip: BUY 1 lot @ 100, SELL 1 lot @ 150
# ---------------------------------------------------------------------------
def test_example_c_round_trip_exact(engine):
    """Hand arithmetic: 40 + 7.50 + 10.45 + 0.025 + 0.30 + 9.0855 = 67.36"""
    result = engine.compute_round_trip_charges(
        Decimal("100"), Decimal("150"), LOT_SIZE, 1
    )

    assert result.turnover == Decimal("25000.00")
    assert result.brokerage == Decimal("40.00"), "Rs 20 per executed order, two orders"
    assert result.ctt == Decimal("7.50"), "0.0005 * 15000, sell leg only"
    assert result.exchange_transaction_charge == Decimal("10.45")   # 0.000418 * 25000
    assert result.stamp_duty == Decimal("0.30"), "0.00003 * 10000, buy leg only"
    assert result.total == Decimal("67.36")


def test_example_c_net_pnl(engine):
    gross_pnl = (Decimal("150") - Decimal("100")) * LOT_SIZE
    result = engine.compute_round_trip_charges(
        Decimal("100"), Decimal("150"), LOT_SIZE, 1
    )

    assert gross_pnl == Decimal("5000")
    assert gross_pnl - result.total == Decimal("4932.64")


def test_example_c_documented_gap_against_the_broker_calculator(broker_engine):
    """Zerodha reports 67.05; this engine reports 67.06. The 1 paise is REAL
    and is not tuned away.

    Cause: Zerodha computes the SEBI fee once on the combined round-trip
    turnover (0.000001 * 25000 = 0.025, displayed 0.02), whereas this engine
    charges and rounds per order (buy 0.01 + sell 0.015 -> 0.01 + 0.02 = 0.03),
    with a small knock-on through GST.

    Per-order attribution is required here: every order carries its own
    auditable charges row in order history, which a combined-turnover
    calculation cannot provide. The difference is accepted deliberately.
    """
    result = broker_engine.compute_round_trip_charges(
        Decimal("100"), Decimal("150"), LOT_SIZE, 1
    )

    zerodha_total = Decimal("67.05")
    assert result.total == Decimal("67.06")
    assert result.total - zerodha_total == Decimal("0.01")


def test_round_trip_equals_the_sum_of_its_legs(engine):
    buy = engine.compute_order_charges("BUY", Decimal("100"), LOT_SIZE, 1)
    sell = engine.compute_order_charges("SELL", Decimal("150"), LOT_SIZE, 1)
    round_trip = engine.compute_round_trip_charges(
        Decimal("100"), Decimal("150"), LOT_SIZE, 1
    )

    assert round_trip.total == buy.total + sell.total
    assert round_trip.brokerage == buy.brokerage + sell.brokerage
    assert round_trip.ctt == sell.ctt


# ---------------------------------------------------------------------------
# The exercise leg, which no broker calculator publishes
# ---------------------------------------------------------------------------
def test_exercise_ctt_on_settlement_price(engine):
    """Finance Act 2013 s.117 Sl.5: 0.0001% of settlement price, paid by the
    PURCHASER. For 1 lot settling at 5500: 0.000001 * 5500 * 100 = Rs 0.55."""
    result = engine.compute_exercise_charges(Decimal("5500"), LOT_SIZE, 1)

    assert result.turnover == Decimal("550000.00")
    # Exercise CTT is Sl.5, a different taxable transaction from the Sl.3 CTT
    # on a sale, so it is its own line item rather than merged into "ctt".
    assert result.amount("ctt_exercise") == Decimal("0.55")
    assert result.ctt == Decimal("0.00"), "no sale, so no Sl.3 CTT"
    assert result.total == Decimal("0.55")


def test_exercise_does_not_attract_the_ordinary_trading_charges(engine):
    result = engine.compute_exercise_charges(Decimal("5500"), LOT_SIZE, 1)

    assert result.brokerage == Decimal("0")
    assert result.exchange_transaction_charge == Decimal("0")
    assert result.stamp_duty == Decimal("0")
    assert result.gst == Decimal("0")


def test_exercise_sebi_notional_leg_is_off_by_default(engine):
    """SEBI mandates it; brokers omit it. Default matches broker notes."""
    result = engine.compute_exercise_charges(Decimal("5500"), LOT_SIZE, 1)
    assert result.amount("sebi_turnover_fee_exercise") == Decimal("0")
    assert result.sebi_turnover_fee == Decimal("0")


def test_exercise_sebi_notional_leg_can_be_enabled():
    rates = copy.deepcopy(ChargesEngine.load_rates())
    rates["sebi_turnover_fee"]["charge_on_exercise_notional"] = True
    engine = ChargesEngine(rates=rates)

    result = engine.compute_exercise_charges(Decimal("5500"), LOT_SIZE, 1)

    # 0.000001 * 550000
    assert result.amount("sebi_turnover_fee_exercise") == Decimal("0.55")
    assert result.total == Decimal("1.10")


def test_the_wrong_ctt_rate_is_not_used(engine):
    """0.125% belongs to 'option IN GOODS, exercised' -- a different taxable
    transaction. Applying it to CRUDEOIL OPTFUT would overcharge 2500x."""
    result = engine.compute_order_charges("SELL", Decimal("100"), LOT_SIZE, 1)

    assert result.ctt == Decimal("5.00")
    assert result.ctt != Decimal("1250.00")
