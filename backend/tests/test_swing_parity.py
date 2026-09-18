"""Parity: the pure-Python indicators must reproduce the backtest's pandas.

The decision not to add pandas to this backend is only safe because of this
file. Golden values in `tests/fixtures/swing_parity_golden.json` were computed
by `research2/swing_backtest.py`'s own expressions, under pandas 3.0.3 and
numpy 2.4.6, over the fixture CSVs beside them -- 400-session slices of the
research project's ten-year Dhan panel for three real symbols.

The specification is explicit that ATR smoothing is where this goes wrong:
"small differences here move the stop and therefore every result". Two details
decide it, and both are pinned below before anything else:

1. The first true range. `concat([h-l, |h-pc|, |l-pc|], axis=1).max(axis=1)`
   SKIPS the two NaNs on the first bar, so TR[0] is `high - low`, not NaN.
2. The EWM seed. `ewm(alpha=1/14, adjust=False)` starts at the first
   observation -- not at a mean of the first fourteen, which is the other
   common spelling of Wilder smoothing and diverges forever.

Tolerances are absolute and tight. These are the same arithmetic in two
languages, so the only legitimate difference is IEEE-754 accumulation order.
"""
import csv
import json
import pathlib

import pytest

from src.swing.services import indicators


FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SYMBOLS = ("BHEL", "HFCL", "DIVISLAB")

# Float accumulation order is the only difference allowed. A rolling mean over
# 200 values and an EWM over 400 both stay far inside this.
PRICE_TOLERANCE = 1e-9
RATIO_TOLERANCE = 1e-12
# ADV20 is a mean of numbers around 1e9, so its representable step is ~1e-7.
TURNOVER_RELATIVE_TOLERANCE = 1e-12


@pytest.fixture(scope="module")
def golden():
    return json.loads((FIXTURES / "swing_parity_golden.json").read_text())


def _series(symbol):
    with open(FIXTURES / f"swing_parity_{symbol}.csv", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return (
        [row["date"] for row in rows],
        [float(row["open"]) for row in rows],
        [float(row["high"]) for row in rows],
        [float(row["low"]) for row in rows],
        [float(row["close"]) for row in rows],
        [float(row["volume"]) for row in rows],
    )


# --- the two decisions that move every stop ---------------------------------


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_the_first_true_range_is_high_minus_low_not_nan(symbol, golden):
    """pandas' max(axis=1) skips the NaNs a missing previous close produces."""
    _, _, highs, lows, closes, _ = _series(symbol)

    ours = indicators.true_range(highs, lows, closes)

    assert ours[0] == pytest.approx(highs[0] - lows[0], abs=PRICE_TOLERANCE)
    for index, expected in enumerate(golden[symbol]["_tr_head"]):
        assert ours[index] == pytest.approx(expected, abs=PRICE_TOLERANCE), index


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_the_wilder_ewm_seeds_on_the_first_observation(symbol, golden):
    """adjust=False, NOT a mean of the first fourteen true ranges."""
    _, _, highs, lows, closes, _ = _series(symbol)

    ours = indicators.atr(highs, lows, closes, 14)
    expected_head = golden[symbol]["_atr_head"]

    assert ours[0] == pytest.approx(indicators.true_range(highs, lows, closes)[0])
    for index, expected in enumerate(expected_head):
        assert ours[index] == pytest.approx(expected, abs=PRICE_TOLERANCE), index


# --- every indicator, at four points through each series --------------------


def _checkpoints(symbol, golden):
    return {
        key: value
        for key, value in golden[symbol].items()
        if not key.startswith("_")
    }


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_atr14_matches_the_backtest(symbol, golden):
    _, _, highs, lows, closes, _ = _series(symbol)
    ours = indicators.atr(highs, lows, closes, 14)

    for day, expected in _checkpoints(symbol, golden).items():
        index = expected["index"]
        assert ours[index] == pytest.approx(expected["atr14"], abs=PRICE_TOLERANCE), day


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_sma200_matches_the_backtest_including_where_it_is_undefined(symbol, golden):
    _, _, _, _, closes, _ = _series(symbol)
    ours = indicators.sma(closes, 200)

    assert ours[198] is None, "199 observations is not 200"
    assert ours[199] is not None

    for day, expected in _checkpoints(symbol, golden).items():
        index = expected["index"]
        if expected["sma200"] is None:
            assert ours[index] is None, day
        else:
            assert ours[index] == pytest.approx(
                expected["sma200"], abs=PRICE_TOLERANCE
            ), day


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_adv20_is_rupee_turnover_and_matches(symbol, golden):
    """Turnover, not share count. A share count would pass every penny stock."""
    _, _, _, _, closes, volumes = _series(symbol)
    ours = indicators.average_daily_value(closes, volumes, 20)

    for day, expected in _checkpoints(symbol, golden).items():
        index = expected["index"]
        assert ours[index] == pytest.approx(
            expected["adv20"], rel=TURNOVER_RELATIVE_TOLERANCE
        ), day


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_mom126_skips_the_most_recent_week(symbol, golden):
    _, _, _, _, closes, _ = _series(symbol)
    ours = indicators.momentum(closes, 126, 5)

    assert ours[125] is None, "126 sessions of lookback are needed"
    assert ours[126] is not None

    for day, expected in _checkpoints(symbol, golden).items():
        index = expected["index"]
        if expected["mom126"] is None:
            assert ours[index] is None, day
        else:
            assert ours[index] == pytest.approx(
                expected["mom126"], abs=RATIO_TOLERANCE
            ), day


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_the_rank_score_matches_the_backtest(symbol, golden):
    """mom / (ATR14 / close) -- P7, the differentiating idea of the strategy."""
    _, _, highs, lows, closes, _ = _series(symbol)
    atr_series = indicators.atr(highs, lows, closes, 14)
    momentum_series = indicators.momentum(closes, 126, 5)

    for day, expected in _checkpoints(symbol, golden).items():
        index = expected["index"]
        atr_percent = atr_series[index] / closes[index]
        assert atr_percent == pytest.approx(expected["atrpct"], abs=RATIO_TOLERANCE), day

        ours = indicators.score(momentum_series[index], atr_percent)
        if expected["score"] is None:
            assert ours is None, day
        else:
            assert ours == pytest.approx(expected["score"], rel=1e-12), day


# --- properties the golden values cannot express ----------------------------


def test_an_undefined_indicator_is_none_and_never_zero():
    """A filter treating an undefined SMA200 as "above" buys the universe."""
    closes = [100.0, 101.0, 102.0]

    assert indicators.sma(closes, 200) == [None, None, None]
    assert indicators.momentum(closes, 126, 5) == [None, None, None]
    assert indicators.score(None, 0.03) is None
    assert indicators.score(0.5, None) is None


def test_a_non_positive_atr_percentage_yields_no_score():
    """The engine's own `if P.vol_adj and r.atrpct > 0` guard."""
    assert indicators.score(0.5, 0.0) is None
    assert indicators.score(0.5, -0.01) is None


def test_a_gap_in_the_series_makes_the_windows_containing_it_undefined():
    """A missing volume must not silently become a zero an average absorbs."""
    values = [1.0, 2.0, None, 4.0, 5.0, 6.0]

    result = indicators.sma(values, 3)

    assert result[2] is None
    assert result[5] == pytest.approx(5.0)


# --- the slot ramp -----------------------------------------------------------


def _parameters():
    from src.strategies.services.strategy_registry import get_strategy_registry
    from src.swing.services.swing_parameters import SwingParameters

    return SwingParameters.from_definition(
        get_strategy_registry().require("nse-swing-momentum")
    )


@pytest.mark.parametrize(
    "breadth,expected",
    [
        (0.00, 0),
        (0.35, 0),
        (0.34, 0),
        (0.48, 4),   # the specification's section 13 snapshot
        (0.50, 5),
        (0.65, 10),
        (0.90, 10),
    ],
)
def test_the_breadth_ramp_matches_the_specification(breadth, expected):
    assert _parameters().slots_for_breadth(breadth) == expected


def test_the_slot_ramp_is_the_engines_expression_to_the_last_float():
    """`int(round(MAX_POS * min(max((b - 0.35) / 0.30, 0), 1)))`, verbatim.

    Asserted against the expression rather than against a rounding rule,
    because the rounding rule is not what decides the edge cases. A breadth of
    0.365 looks like exactly half a slot, but `0.365 - 0.35` is
    0.015000000000000013 in IEEE-754, so the ramp lands just above 0.5 and
    rounds to 1 -- and the backtest, computing the same expression on the same
    floats, lands in exactly the same place. Reimplementing this with Decimal,
    or with a "cleaner" round-half-up, would silently buy or skip a position
    the research did not.
    """
    parameters = _parameters()

    for step in range(0, 1001):
        breadth = step / 1000.0
        expected = int(round(10 * min(max((breadth - 0.35) / 0.30, 0.0), 1.0)))
        assert parameters.slots_for_breadth(breadth) == expected, breadth


def test_unmeasurable_breadth_is_not_reported_as_zero_slots():
    """"We could not measure breadth" is not "breadth says buy nothing"."""
    assert _parameters().slots_for_breadth(None) is None


def test_every_specification_parameter_is_configuration_not_code():
    parameters = _parameters()

    assert parameters.liquidity_floor_rupees == 1e8      # P2, Rs 10 crore
    assert parameters.price_floor == 50.0                # P3
    assert parameters.trend_sma_sessions == 200          # P4
    assert parameters.momentum_lookback_sessions == 126  # P5
    assert parameters.momentum_skip_sessions == 5        # P5
    assert parameters.momentum_floor == 0.10             # P6
    assert parameters.volatility_adjusted_score is True  # P7
    assert (parameters.breadth_lower, parameters.breadth_span) == (0.35, 0.30)  # P11
    assert parameters.max_positions == 10                # P12
    assert parameters.position_size_divisor == 10        # P13
    assert parameters.trail_atr_multiple == 3.5          # P14 / P15
    assert parameters.rotation_exit_rank == 15           # P16
    assert parameters.schedule.rebalance_cadence == "daily"  # P18
    assert parameters.minimum_sessions == 260


def test_the_off_gate_variant_ships_disabled():
    """V3b ends LOWER than the baseline. Enabling it is an informed choice."""
    off_gate = _parameters().off_gate

    assert off_gate.enabled is False
    assert off_gate.slots == 3
    assert off_gate.require_entry_return is False


def test_the_strategy_ships_unarmed():
    assert _parameters().armed_by_default is False
