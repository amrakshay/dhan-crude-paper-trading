"""Fill simulation.

These tests exist to stop the simulator drifting toward flattering the trader.
Several of them assert that a fill does NOT happen, or happens at a worse price
than the last traded price.
"""
from decimal import Decimal

import pytest

from src.orders.services.fill_simulator import (
    apply_slippage,
    extract_side_levels,
    is_marketable,
    simulate_marketable_fill,
    try_fill_resting_limit,
)

TICK = Decimal("0.1")

# (bid_qty, ask_qty, bid_orders, ask_orders, bid_price, ask_price)
BOOK = [
    (100, 50, 3, 2, 50.0, 51.0),
    (200, 80, 4, 3, 49.9, 51.1),
    (300, 120, 5, 4, 49.8, 51.2),
    (0, 0, 0, 0, 0.0, 0.0),
    (0, 0, 0, 0, 0.0, 0.0),
]


# --- side selection --------------------------------------------------------
def test_a_buy_consumes_the_ask_side():
    levels = extract_side_levels(BOOK, "BUY")
    assert [price for price, _qty in levels] == [
        Decimal("51.0"), Decimal("51.1"), Decimal("51.2")
    ]


def test_a_sell_consumes_the_bid_side():
    levels = extract_side_levels(BOOK, "SELL")
    assert [price for price, _qty in levels] == [
        Decimal("50.0"), Decimal("49.9"), Decimal("49.8")
    ]


def test_empty_levels_are_dropped():
    levels = extract_side_levels(BOOK, "BUY")
    assert len(levels) == 3, "zero-quantity padding levels must not be tradable"


def test_missing_depth_yields_no_levels():
    assert extract_side_levels(None, "BUY") == []
    assert extract_side_levels([], "BUY") == []


# --- market orders ---------------------------------------------------------
def test_market_buy_pays_the_ask_not_the_last_traded_price():
    """The single most important behaviour in this module."""
    result = simulate_marketable_fill("BUY", 10, BOOK, TICK, slippage_ticks=0)

    assert result.average_price == Decimal("51.0000")
    assert result.average_price > Decimal("50.0"), "must not fill at the bid"


def test_market_sell_hits_the_bid():
    result = simulate_marketable_fill("SELL", 10, BOOK, TICK, slippage_ticks=0)
    assert result.average_price == Decimal("50.0000")


def test_a_large_order_walks_the_book_and_gets_a_worse_average():
    result = simulate_marketable_fill("BUY", 200, BOOK, TICK, slippage_ticks=0)

    assert result.filled_quantity == 200
    assert [f.book_level for f in result.fills] == [1, 2, 3]
    assert [f.quantity for f in result.fills] == [50, 80, 70]
    # (50*51.0 + 80*51.1 + 70*51.2) / 200
    assert result.average_price == Decimal("51.1100")
    assert result.average_price > Decimal("51.0"), "walking the book must cost more"


def test_order_larger_than_the_visible_book_partially_fills():
    """Five levels is all the feed publishes; the rest is not invented."""
    result = simulate_marketable_fill("BUY", 1000, BOOK, TICK, slippage_ticks=0)

    assert result.filled_quantity == 250, "50 + 80 + 120 displayed"
    assert result.rejection_reason is None
    assert "Partially filled" in result.note


def test_partial_fills_can_be_disallowed():
    result = simulate_marketable_fill(
        "BUY", 1000, BOOK, TICK, slippage_ticks=0, allow_partial=False
    )

    assert result.filled_quantity == 0
    assert "Insufficient depth" in result.rejection_reason


def test_no_depth_means_rejection_not_a_phantom_fill():
    result = simulate_marketable_fill("BUY", 10, None, TICK)

    assert result.filled_quantity == 0
    assert result.rejection_reason is not None


def test_zero_quantity_is_rejected():
    result = simulate_marketable_fill("BUY", 0, BOOK, TICK)
    assert result.rejection_reason is not None


# --- slippage --------------------------------------------------------------
def test_slippage_is_always_adverse():
    buy = apply_slippage(Decimal("51.0"), "BUY", 2, TICK)
    sell = apply_slippage(Decimal("50.0"), "SELL", 2, TICK)

    assert buy == Decimal("51.2000"), "a buy pays more"
    assert sell == Decimal("49.8000"), "a sell receives less"


def test_slippage_never_drives_a_price_negative():
    assert apply_slippage(Decimal("0.1"), "SELL", 50, TICK) == Decimal("0.1000")


def test_slippage_applies_to_every_level_of_a_walked_book():
    result = simulate_marketable_fill("BUY", 200, BOOK, TICK, slippage_ticks=1)

    assert all(f.slippage_ticks == 1 for f in result.fills)
    assert result.fills[0].price == Decimal("51.1000")
    assert result.fills[0].reference_price == Decimal("51.0000")


# --- limit orders ----------------------------------------------------------
def test_a_limit_order_never_fills_beyond_its_limit():
    result = simulate_marketable_fill(
        "BUY", 200, BOOK, TICK, slippage_ticks=0, limit_price=Decimal("51.1")
    )

    assert result.filled_quantity == 130, "levels at 51.0 and 51.1 only"
    assert all(f.price <= Decimal("51.1") for f in result.fills)


def test_slippage_that_breaches_the_limit_prevents_the_fill():
    """A limit order must not fill at a price worse than its limit, even once
    slippage is applied."""
    result = simulate_marketable_fill(
        "BUY", 50, BOOK, TICK, slippage_ticks=1, limit_price=Decimal("51.0")
    )

    assert result.filled_quantity == 0


def test_marketability_detection():
    assert is_marketable("BUY", Decimal("51.5"), BOOK) is True
    assert is_marketable("BUY", Decimal("50.5"), BOOK) is False
    assert is_marketable("SELL", Decimal("49.5"), BOOK) is True
    assert is_marketable("SELL", Decimal("50.5"), BOOK) is False


def test_marketability_without_depth_is_false():
    assert is_marketable("BUY", Decimal("51.5"), None) is False


# --- resting limit orders --------------------------------------------------
def test_a_resting_buy_does_not_fill_when_the_ask_merely_touches_it():
    """Touching your price means joining the back of a queue, not trading.

    Assuming a fill here is the most flattering error a paper simulator can
    make, so it is explicitly not done.
    """
    result = try_fill_resting_limit(
        "BUY", 10, Decimal("51.0"), BOOK, TICK, requires_cross=True
    )
    assert result.filled_quantity == 0


def test_a_resting_buy_fills_when_the_ask_trades_through_it():
    result = try_fill_resting_limit(
        "BUY", 10, Decimal("51.5"), BOOK, TICK, requires_cross=True
    )
    assert result.filled_quantity == 10


def test_a_resting_sell_requires_the_bid_to_trade_above_it():
    assert try_fill_resting_limit(
        "SELL", 10, Decimal("50.0"), BOOK, TICK, requires_cross=True
    ).filled_quantity == 0
    assert try_fill_resting_limit(
        "SELL", 10, Decimal("49.5"), BOOK, TICK, requires_cross=True
    ).filled_quantity == 10


def test_touch_fills_can_be_enabled_but_are_not_the_default():
    lenient = try_fill_resting_limit(
        "BUY", 10, Decimal("51.0"), BOOK, TICK, requires_cross=False
    )
    assert lenient.filled_quantity == 10


def test_a_resting_order_fills_at_its_own_limit_not_at_a_better_price():
    """Price improvement would require queue priority the feed does not show."""
    result = try_fill_resting_limit(
        "BUY", 10, Decimal("51.5"), BOOK, TICK, requires_cross=True
    )

    assert result.fills[0].price == Decimal("51.5")
    assert result.fills[0].reference_price == Decimal("51.0000")


def test_a_resting_order_fills_only_the_displayed_quantity():
    result = try_fill_resting_limit(
        "BUY", 1000, Decimal("51.5"), BOOK, TICK, requires_cross=True
    )

    # Every ask level (51.0/51.1/51.2) is strictly below the 51.5 limit, so all
    # three cross: 50 + 80 + 120 displayed. The rest is not invented.
    assert result.filled_quantity == 250
    assert "Partially filled" in result.note


def test_resting_fill_applies_no_slippage():
    """A resting order that fills is not crossing the spread."""
    result = try_fill_resting_limit(
        "BUY", 10, Decimal("51.5"), BOOK, TICK, requires_cross=True
    )
    assert all(fill.slippage_ticks == 0 for fill in result.fills)


def test_prices_are_free_of_float_noise():
    """Depth arrives as IEEE floats; fill prices must not carry the artefacts."""
    noisy = [(10, 10, 1, 1, 50.900000000000004, 51.300000000000004)] + [
        (0, 0, 0, 0, 0.0, 0.0)
    ] * 4

    result = simulate_marketable_fill("BUY", 5, noisy, TICK, slippage_ticks=0)

    assert result.fills[0].price == Decimal("51.3000")
    assert "0000000" not in str(result.fills[0].price)
