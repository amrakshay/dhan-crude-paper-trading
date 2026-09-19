"""The live scan: what qualifies, what does not, and what it refuses to guess.

Most of these assert a NEGATIVE, which is the shape this repository's tests
take deliberately. The two that matter most are the transposition guard and the
partial-day volume rule, because both are ways this strategy could be silently
wrong rather than visibly broken.
"""
from datetime import date

import pytest

from src.btst.services.btst_parameters import BtstParameters
from src.btst.services.btst_policy import BtstPolicy
from src.btst.services import scan_service
from src.strategies.services.strategy_registry import get_strategy_registry
from src.swing.services import indicators

STRATEGY = "nse-btst-overnight"


@pytest.fixture
def parameters():
    return BtstParameters.from_definition(get_strategy_registry().require(STRATEGY))


@pytest.fixture
def policy():
    return BtstPolicy(enforce_regime=True, exclude_fno=True)


def _flat_series(parameters, close, volume=200_000, count=None):
    """A long, flat history: every filter passes except the ones a test breaks."""
    count = count or max(parameters.minimum_sessions, 320)
    base = close * 0.80
    highs = [base * 1.002] * count
    lows = [base * 0.998] * count
    closes = [base] * count
    volumes = [float(volume)] * count
    # B4: the prior 55-session high sits just under today's price.
    for index in range(count - parameters.breakout_lookback_sessions, count):
        highs[index] = close * 0.99
    # B8: six-month momentum comfortably above the floor.
    closes[-parameters.momentum_lookback_sessions] = closes[-5] / 1.40
    return highs, lows, closes, volumes


def _window(parameters, close, volume=200_000, symbol="X", live=True):
    highs, lows, closes, volumes = _flat_series(parameters, close, volume)
    return scan_service.build_window(
        parameters, highs, lows, closes, volumes, symbol,
        include_last_in_averages=not live,
    )


def _quote(close, *, high=None, low=None, volume=None, fno=False, price=None):
    """A session whose close sits near the TOP of the range -- CLV ~0.9."""
    span = close * 0.02
    high = high if high is not None else close + span * 0.1
    low = low if low is not None else high - span
    return scan_service.Quote(
        security_id="1", symbol="X", price=price if price is not None else close,
        session_high=high, session_low=low,
        session_volume=volume if volume is not None else 800_000.0,
        fno_eligible=fno,
    )


def _evaluate(parameters, policy, window, quote, symbol="X"):
    return scan_service.evaluate(
        parameters, policy, {symbol: quote}, {symbol: window}, [symbol]
    )


def test_a_clean_name_qualifies(parameters, policy):
    """The positive case, so every negative below means something."""
    result = _evaluate(parameters, policy, _window(parameters, 1_000.0), _quote(1_000.0))
    assert [one.symbol for one in result.candidates] == ["X"]
    assert result.counts["momentum"] == 1


# --- THE TRANSPOSITION GUARD ------------------------------------------------


def test_a_quote_whose_high_is_below_its_low_is_REFUSED_not_computed_through(
    parameters, policy
):
    """The signature of the unverified field mapping, caught rather than traded.

    Root `CLAUDE.md` section 5: the Quote/Full packet's four price fields are
    mapped open/close/high/low per the SDK and have never been checked against
    a live feed. If high and low are transposed, B6's CLV does not drift -- it
    INVERTS, and the strategy buys the weakest closes in the market while every
    number it produces looks plausible.

    This does not make the mapping verified; `scripts/
    verify_feed_session_fields.py` does. It makes the failure a scan that
    qualifies nothing and says why, instead of a book of inverted trades.
    """
    quote = scan_service.Quote(
        security_id="1", symbol="X", price=1_000.0,
        session_high=980.0, session_low=1_020.0,   # the wrong way round
        session_volume=800_000.0,
    )
    assert quote.usable is False
    assert "transposed field mapping" in quote.unusable_reason

    result = _evaluate(parameters, policy, _window(parameters, 1_000.0), quote)
    assert not result.candidates
    assert result.rejections[0].stage == "quoted"
    assert "transposed" in result.rejections[0].reason


def test_a_last_trade_outside_the_sessions_own_range_is_refused(parameters, policy):
    """The other shape the same defect takes.

    A price above the session's reported high or below its reported low is not
    a thing a working feed produces, and computing a CLV from it would give a
    number outside 0..1 that no filter would notice.
    """
    quote = _quote(1_000.0, high=1_005.0, low=995.0, price=1_200.0)
    assert quote.usable is False
    result = _evaluate(parameters, policy, _window(parameters, 1_000.0), quote)
    assert result.rejections[0].stage == "quoted"


def test_a_zero_range_session_has_no_close_location_and_does_not_qualify(
    parameters, policy
):
    """Undefined is not one.

    A stock that has not moved all day has no close location. Treating that as
    a CLV of 1.0 -- which `(p - lo) / 0` would suggest to anyone reaching for a
    default -- would buy every untraded name in the universe.
    """
    assert indicators.close_location_value(100.0, 100.0, 100.0) is None
    quote = _quote(1_000.0, high=1_000.0, low=1_000.0)
    result = _evaluate(parameters, policy, _window(parameters, 1_000.0), quote)
    assert not result.candidates


# --- the filters ------------------------------------------------------------


def test_volume_is_measured_on_what_has_traded_SO_FAR_and_is_not_scaled_up(
    parameters, policy
):
    """Section 14 item 1, which is the easiest trap in the whole specification.

    At 15:20 roughly 95% of the day's volume has traded. The backtest's
    `vol_ratio` uses the FULL day, so the honest live substitution is
    `vol_sofar / advq` used directly -- stricter, therefore conservative, and
    what the section 10.3 validation measured at 83.4% precision. Scaling it up
    to a projected full day would manufacture signals the backtest never had.

    So a name at 1.9x on partial volume does NOT qualify, even though it would
    plausibly finish the day above 2x.
    """
    window = _window(parameters, 1_000.0, volume=200_000)
    just_under = _quote(1_000.0, volume=200_000 * 1.9)
    result = _evaluate(parameters, policy, window, just_under)
    assert not result.candidates
    assert result.rejections[0].stage == "volume"

    just_over = _quote(1_000.0, volume=200_000 * 2.05)
    assert _evaluate(parameters, policy, window, just_over).candidates


def test_the_breakout_is_against_the_PRIOR_window_not_one_including_today(
    parameters,
):
    """B4's `shift(1)`, which is the rule and not a detail.

    `high.rolling(55).max()` including today compares today's price against a
    window today is itself setting: on the day a stock makes a new high, its
    own high IS the maximum, so a breakout could never happen. Asserted on the
    indicator directly because it is the kind of thing a refactor silently
    flips.
    """
    highs = [10.0, 11.0, 12.0, 9.0, 8.0]
    prior = indicators.rolling_max_prior(highs, 3)
    assert prior[:3] == [None, None, None]
    # At index 3 the prior three highs are 10, 11, 12.
    assert prior[3] == 12.0
    # At index 4 they are 11, 12, 9 -- today's own 8.0 is not in the window.
    assert prior[4] == 12.0


def test_a_name_below_its_own_sma200_does_not_qualify(parameters, policy):
    """B7, with the live price standing in for today's unfinished close.

    Section 3's NOTE: the backtest computed SMA200 including day T's close and
    at 15:20 the close is not final, so the current price is the correct
    substitution. A price below the line fails whatever the history did.
    """
    highs, lows, closes, volumes = _flat_series(parameters, 1_000.0)
    # Make the history sit far ABOVE today's price.
    closes = [2_000.0] * len(closes)
    closes[-parameters.momentum_lookback_sessions] = closes[-5] / 1.40
    window = scan_service.build_window(
        parameters, highs, lows, closes, volumes, "X",
        include_last_in_averages=False,
    )
    result = _evaluate(parameters, policy, window, _quote(1_000.0))
    assert not result.candidates
    assert result.rejections[0].stage == "trend"


def test_an_undefined_momentum_is_not_a_pass(parameters, policy):
    """Undefined is not zero and is certainly not "above the floor".

    A symbol with enough rows to build a window but a zero close 126 sessions
    back has no momentum. Treating that as a pass would buy names on the
    strength of a division nobody could perform.
    """
    highs, lows, closes, volumes = _flat_series(parameters, 1_000.0)
    closes[-parameters.momentum_lookback_sessions] = 0.0
    window = scan_service.build_window(
        parameters, highs, lows, closes, volumes, "X",
        include_last_in_averages=False,
    )
    assert window.momentum is None
    result = _evaluate(parameters, policy, window, _quote(1_000.0))
    assert not result.candidates
    assert result.rejections[0].stage == "momentum"


def test_too_little_history_excludes_a_symbol_entirely(parameters, policy):
    """Section 6's panel rule: a symbol needs `minimum_sessions` rows.

    Excluded from the universe rather than failed on a filter, so a
    recently-listed name never reaches a comparison it has no data for.
    """
    highs, lows, closes, volumes = _flat_series(
        parameters, 1_000.0, count=parameters.minimum_sessions - 1
    )
    window = scan_service.build_window(
        parameters, highs, lows, closes, volumes, "X",
        include_last_in_averages=False,
    )
    assert window is None

    result = scan_service.evaluate(
        parameters, policy, {"X": _quote(1_000.0)}, {}, ["X"]
    )
    assert result.rejections[0].stage == "history"
    assert result.counts["history"] == 0


def test_a_name_with_no_live_price_is_skipped_never_priced_off_a_bar(
    parameters, policy
):
    """The rule buys at the live price or not at all.

    Falling back to yesterday's close would compare a stale number against a
    live 55-day high and a live volume, which is three measurements of three
    different moments.
    """
    quote = scan_service.Quote(
        security_id="1", symbol="X", price=None,
        session_high=None, session_low=None, session_volume=None,
    )
    result = _evaluate(parameters, policy, _window(parameters, 1_000.0), quote)
    assert not result.candidates
    assert result.rejections[0].stage == "quoted"


# --- the funnel and the policy ---------------------------------------------


def test_the_funnel_counts_every_stage_so_nothing_qualified_is_readable(
    parameters, policy
):
    """At ~0.54 signals a session, nothing qualifying is the ORDINARY outcome.

    A page that could only say "no candidates" would look broken far more often
    than it looked right, so every stage is counted: "289 tradable, 284
    measured, 31 above their 55-day high, 6 on 2x volume, 0 closing strong"
    reads as a working scan and a bare zero does not.
    """
    window = _window(parameters, 1_000.0)
    weak_close = _quote(1_000.0, high=1_100.0, low=990.0, price=995.0)
    result = _evaluate(parameters, policy, window, weak_close)

    assert not result.candidates
    assert result.counts["universe"] == 1
    assert result.counts["breakout"] == 1
    assert result.counts["volume"] == 1
    # It got as far as the close-strength filter and stopped there.
    assert result.counts["close_strength"] == 0
    assert [key for key, _ in scan_service.FILTER_STAGES][:2] == [
        "universe", "tradable",
    ]


def test_an_fno_name_is_excluded_before_anything_about_it_is_measured(
    parameters
):
    """The policy decides the POPULATION, not the outcome.

    A name excluded from the universe must not appear in the liquidity count as
    though it had been considered and passed -- the funnel is a census and a
    census of the wrong population is worse than no census.
    """
    policy = BtstPolicy(enforce_regime=True, exclude_fno=True)
    result = _evaluate(
        parameters, policy, _window(parameters, 1_000.0), _quote(1_000.0, fno=True)
    )
    assert not result.candidates
    assert result.counts["tradable"] == 0
    assert result.counts["liquidity"] == 0
    assert result.rejections[0].stage == "tradable"

    # And with the policy off it is scanned like anything else.
    included = BtstPolicy(enforce_regime=True, exclude_fno=False)
    assert _evaluate(
        parameters, included, _window(parameters, 1_000.0), _quote(1_000.0, fno=True)
    ).candidates


def test_the_regime_gate_is_computed_and_recorded_even_when_not_enforced(
    parameters
):
    """`observe` stops the gate ACTING, never stops it being measured.

    That distinction is what makes section 11's measurement of the gate's cost
    possible: every trade taken while it was relaxed carries the gate state on
    its own decision row and can be filtered out of the numbers afterwards.
    """
    closes = [100.0] * 199 + [90.0]
    regime = scan_service.evaluate_regime(parameters, closes, index_symbol="NIFTY")
    assert regime["gate_on"] is False
    assert regime["index_close"] == 90.0
    assert regime["index_sma"] == pytest.approx(99.95, abs=0.01)

    result = scan_service.ScanResult(session_date=date(2026, 9, 3))
    result.gate_on = False
    result.regime_enforced = False
    # Not enforced: entries are allowed, and the gate's own boolean is still
    # False and still on the record.
    assert result.entries_allowed is True
    assert result.gate_on is False


def test_a_gate_that_could_not_be_evaluated_blocks_entries_rather_than_defaulting(
    parameters
):
    """Undefined is not "on".

    Too little index history gives `gate_on: None`, which is "we could not
    measure it" -- a different answer from "the market is healthy" and not a
    reason to trade.
    """
    regime = scan_service.evaluate_regime(parameters, [100.0] * 10)
    assert regime["gate_on"] is None
    assert regime["index_sma"] is None

    result = scan_service.ScanResult(session_date=date(2026, 9, 3))
    result.gate_on = None
    result.regime_enforced = True
    assert result.entries_allowed is False
    assert "could not be evaluated" in result.blocked_reason


def test_ranks_cover_every_candidate_not_just_the_traded_ones(parameters, policy):
    """More than `slots` qualify on ~5.7% of signal-days, and it has to show.

    An operator has to be able to see that a sixth name existed and where it
    stood, which is the same reason the rotation ranks every candidate rather
    than its top ten.
    """
    windows, quotes = {}, {}
    for index in range(8):
        symbol = f"S{index}"
        windows[symbol] = _window(parameters, 1_000.0, symbol=symbol)
        quotes[symbol] = scan_service.Quote(
            security_id=symbol, symbol=symbol, price=1_000.0,
            session_high=1_002.0, session_low=982.0,
            # Descending volume, so the ranking has something to order.
            session_volume=200_000.0 * (10 - index),
        )
    result = scan_service.evaluate(
        parameters, policy, quotes, windows, sorted(quotes)
    )
    assert len(result.candidates) == 8 > parameters.slots
    assert [one.rank for one in result.candidates] == list(range(1, 9))
    assert [one.symbol for one in result.candidates][0] == "S0"
