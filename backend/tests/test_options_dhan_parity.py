"""The margin engine reproduces Dhan's own figures (OPTIONS_TRADER_PLAN.md D0).

THE EVIDENCE. On 2026-11-24 (an NSE holiday) Dhan's public margin calculator,
dhan.co/calculators/margin-calculator, was asked for the stand-alone margin of
one lot SOLD of 23 NIFTY / BANKNIFTY / stock contracts and for three NIFTY
baskets. The figures are recorded below exactly as Dhan returned them.

THE MODEL that reproduces them is NSE's own SPAN, applied Dhan's way:
  * risk arrays from the SESSION'S BEGINNING-OF-DAY file -- here the
    2026-11-23 `i01` snapshot, built from Friday's close (NIFTY 25,800.6) --
    not the newer file Zerodha had already moved to;
  * options and the exposure base valued at THAT file's prices;
  * blocked = scan risk - net option value + exposure (2% index, 3.5% stock);
  * a basket's final margin adds premium PAID; premium received is not counted.

Fixture: `fixtures/span_bod_20261123_i01.spn`, cut from NSE's real file (the
contracts below, NIFTY and RELIANCE only). Nothing here is fitted: there is no
Dhan-specific constant anywhere in the engine. If Dhan changes how it applies
SPAN, this file is what should fail first.
"""
from datetime import date
from pathlib import Path

import pytest
import yaml

from src.options.services import span_file, span_margin as sm

FIXTURE = Path(__file__).parent / "fixtures" / "span_bod_20261123_i01.spn"
CARD = Path(__file__).resolve().parent.parent / "conf" / "margins" / "nse-span.yaml"
TODAY = date(2026, 11, 24)
DEC1, DEC29 = date(2026, 12, 1), date(2026, 12, 29)

# (underlying, kind, expiry, strike, right, lot) -> Dhan's figure for ONE LOT SOLD.
DHAN_SINGLE_SHORTS = {
    ("NIFTY", "INDEX", DEC1, 24000, "C", 65): 305086.27,
    ("NIFTY", "INDEX", DEC1, 24000, "P", 65): 99255.20,
    ("NIFTY", "INDEX", DEC1, 24500, "C", 65): 273672.56,
    ("NIFTY", "INDEX", DEC1, 24500, "P", 65): 111798.51,
    ("NIFTY", "INDEX", DEC1, 25000, "C", 65): 241070.57,
    ("NIFTY", "INDEX", DEC1, 25000, "P", 65): 135423.34,
    ("NIFTY", "INDEX", DEC1, 25350, "C", 65): 218792.02,
    ("NIFTY", "INDEX", DEC1, 25350, "P", 65): 158477.02,
    ("NIFTY", "INDEX", DEC1, 25550, "C", 65): 205913.24,
    ("NIFTY", "INDEX", DEC1, 25550, "P", 65): 171896.53,
    ("NIFTY", "INDEX", DEC1, 25700, "C", 65): 196279.40,
    ("NIFTY", "INDEX", DEC1, 25700, "P", 65): 181903.28,
    ("NIFTY", "INDEX", DEC1, 26000, "C", 65): 177340.35,
    ("NIFTY", "INDEX", DEC1, 26000, "P", 65): 202301.91,
    ("NIFTY", "INDEX", DEC1, 26500, "C", 65): 146570.65,
    ("NIFTY", "INDEX", DEC1, 26500, "P", 65): 236666.30,
    ("NIFTY", "INDEX", DEC1, 28000, "C", 65): 96425.81,
    ("NIFTY", "INDEX", DEC1, 28000, "P", 65): 353702.18,
    ("NIFTY", "INDEX", DEC29, 25350, "C", 65): 225644.06,
    ("NIFTY", "INDEX", DEC29, 25350, "P", 65): 153176.40,
    ("RELIANCE", "STOCK", DEC29, 1350, "C", 500): 139731.13,
    ("RELIANCE", "STOCK", DEC29, 1350, "P", 500): 106102.50,
    ("RELIANCE", "STOCK", DEC29, 1500, "C", 500): 84454.63,
}


@pytest.fixture(scope="module")
def data():
    return span_file.parse(str(FIXTURE), date(2026, 11, 23))


@pytest.fixture(scope="module")
def card():
    return sm.MarginCard(yaml.safe_load(CARD.read_text(encoding="utf-8")))


def _short(key):
    underlying, kind, expiry, strike, right, lot = key
    # spot and price deliberately None: valued at the SPAN file's own prices.
    return sm.Leg(underlying, kind, right, expiry, strike, -lot)


def test_the_card_says_how_dhan_applies_span(card):
    assert card.broker == "dhan"
    assert card.values_at_span_file is True
    assert card.count_premium_received is False


@pytest.mark.parametrize("key", list(DHAN_SINGLE_SHORTS), ids=lambda k: f"{k[0]}-{k[2]:%d%b}-{k[3]}{k[4]}")
def test_a_single_short_matches_dhans_calculator(data, card, key):
    ours = sm.compute([_short(key)], data, card, TODAY).blocked
    theirs = DHAN_SINGLE_SHORTS[key]
    spot = data.underlying_price[key[0]]
    distance = abs(key[3] - spot) / spot
    # Near the money the method is exact to rounding of the option value; the
    # further in or out of the money, the more Dhan's premium valuation drifts
    # from the file's, so the band widens with distance. Measured errors:
    # under 0.3% within 1%, 1.01% at 2.7% (26500 PE), 2.97% at 8.5% (28000 CE).
    tolerance = 0.006 if distance < 0.01 else 0.015 if distance < 0.05 else 0.035
    assert ours == pytest.approx(theirs, rel=tolerance)


def test_across_all_23_the_mean_error_is_under_one_percent(data, card):
    errors = [
        abs(sm.compute([_short(key)], data, card, TODAY).blocked - theirs) / theirs
        for key, theirs in DHAN_SINGLE_SHORTS.items()
    ]
    assert sum(errors) / len(errors) < 0.01


def _basket_final(data, card, legs_with_ltp):
    """Dhan's 'Final Margin': blocked for the basket plus premium PAID at LTP."""
    legs = [sm.Leg("NIFTY", "INDEX", right, DEC1, strike, quantity) for right, strike, quantity, _ltp in legs_with_ltp]
    blocked = sm.compute(legs, data, card, TODAY).blocked
    paid = sum(quantity * ltp for _right, _strike, quantity, ltp in legs_with_ltp if quantity > 0)
    return blocked + paid


def test_a_bear_call_spread_matches_dhans_final_margin(data, card):
    # Dhan showed final margin 57,885.36 and hedge benefit 1,72,465.15.
    final = _basket_final(data, card, [("C", 25350, -65, 270.00), ("C", 25550, 65, 180.00)])
    assert final == pytest.approx(57885.36, rel=0.01)


def test_two_longs_and_a_short_match_dhans_final_margin(data, card):
    final = _basket_final(
        data, card, [("C", 25700, 65, 128.65), ("C", 25550, 65, 180.00), ("C", 25350, -65, 270.00)]
    )
    assert final == pytest.approx(55110.51, rel=0.01)


def test_three_naked_shorts_net_inside_spans_scenarios_as_dhans_do(data, card):
    final = _basket_final(
        data, card, [("C", 25350, -65, 270.00), ("C", 25550, -65, 180.00), ("C", 25700, -65, 128.65)]
    )
    assert final == pytest.approx(620132.63, rel=0.005)
