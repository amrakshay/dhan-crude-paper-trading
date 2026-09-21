"""Which rejections get a journal row, in which order, carrying what.

The funnel counts on the session say how many names reached each filter. These
rows say WHICH, and they are the only place that answer exists -- a candidate's
running high, low and cumulative volume at 15:20 live in the feed and nowhere
else, and an hour later the same numbers can only be recovered as a finished
bar, which is a different number.

The regression these guard is real and dated. On 2026-09-21 the scan's funnel
ran 241 -> 7 at the breakout, 7 -> 4 -> 1 -> 1 -> 0 below it. One name cleared
B4, B5, B6 and B7 and died at B8. The journal's forty rows went to 3MINDIA
through BHARTIHEXA saying "not above its 55-day high", and that one name was
crowded out: the counts proved it existed and nothing recorded which it was.
"""
from datetime import date

import pytest

from src.btst.services import journal_service, scan_service
from src.btst.services.btst_parameters import BtstParameters
from src.btst.services.btst_policy import BtstPolicy
from src.btst.services.scan_service import Rejection, ScanResult
from src.strategies.services.strategy_registry import get_strategy_registry

STRATEGY = "nse-btst-overnight"


@pytest.fixture
def parameters():
    return BtstParameters.from_definition(get_strategy_registry().require(STRATEGY))


@pytest.fixture
def policy():
    return BtstPolicy(enforce_regime=True, exclude_fno=True)


def _a_real_days_shape() -> ScanResult:
    """2026-09-21's funnel: a flood of breakout failures, one deep near-miss.

    The breakout names are deliberately named so they sort ALPHABETICALLY
    AHEAD of the deep one -- that ordering is what the bug turned on.
    """
    rejections = [
        Rejection(symbol=f"AAA{index:03d}", reason="not above its 55-day high",
                  stage="breakout")
        for index in range(234)
    ]
    rejections.append(
        Rejection(
            symbol="ZDEEPEST", reason="six-month momentum below the floor",
            stage="momentum", security_id="42", price=1_000.0,
            session_high=1_005.0, session_low=980.0, session_volume=900_000.0,
            vol_ratio=2.4, clv=0.87, breakout_high=995.0, momentum=0.05,
            sma=940.0, turnover=85_000_000.0,
        )
    )
    return ScanResult(session_date=date(2026, 9, 21), rejections=rejections)


def test_the_deepest_rejection_survives_the_cap(parameters):
    """The bug, stated as the thing it cost.

    234 breakout failures sort ahead of the one name that reached B8. Slicing
    in scan order drops it; ordering by funnel depth first cannot.
    """
    rows = journal_service.rejection_decisions(_a_real_days_shape())

    assert len(rows) == journal_service.MAX_REJECTION_ROWS
    assert "ZDEEPEST" in [row.symbol for row in rows]
    # And it is FIRST, not merely present.
    assert rows[0].symbol == "ZDEEPEST"


def test_rows_are_ordered_deepest_first(parameters):
    """A reader scanning from the top reads the closest names first."""
    rejections = [
        Rejection(symbol="B_BREAKOUT", reason="r", stage="breakout"),
        Rejection(symbol="M_MOMENTUM", reason="r", stage="momentum"),
        Rejection(symbol="V_VOLUME", reason="r", stage="volume"),
        Rejection(symbol="T_TREND", reason="r", stage="trend"),
        Rejection(symbol="C_CLOSE", reason="r", stage="close_strength"),
    ]
    rows = journal_service.rejection_decisions(
        ScanResult(session_date=date(2026, 9, 21), rejections=rejections)
    )
    assert [row.symbol for row in rows] == [
        "M_MOMENTUM", "T_TREND", "C_CLOSE", "V_VOLUME", "B_BREAKOUT",
    ]


def test_ties_are_broken_alphabetically_so_rows_are_stable(parameters):
    """Two names at the same depth must not reorder between runs."""
    rejections = [
        Rejection(symbol="ZZZ", reason="r", stage="momentum"),
        Rejection(symbol="AAA", reason="r", stage="momentum"),
    ]
    rows = journal_service.rejection_decisions(
        ScanResult(session_date=date(2026, 9, 21), rejections=rejections)
    )
    assert [row.symbol for row in rows] == ["AAA", "ZZZ"]


def test_the_reason_names_the_stage_it_reached(parameters):
    """"below the volume multiple" does not say how far the name got."""
    rows = journal_service.rejection_decisions(_a_real_days_shape())
    deepest = rows[0]
    assert "B8" in deepest.reason
    assert "six-month momentum below the floor" in deepest.reason


def test_a_near_miss_row_carries_its_live_inputs(parameters):
    """Store the INPUTS, not just the conclusion -- for rejections too.

    Without these the deepest row is a bare sentence, and "how close was it"
    is unanswerable after the session ends.
    """
    deepest = journal_service.rejection_decisions(_a_real_days_shape())[0]
    assert deepest.price == 1_000.0
    assert deepest.vol_ratio == 2.4
    assert deepest.clv == 0.87
    assert deepest.momentum == 0.05
    assert deepest.sma == 940.0
    assert deepest.breakout_high == 995.0
    assert deepest.session_volume == 900_000.0


def test_a_shallow_rejection_carries_no_invented_numbers(parameters):
    """A name rejected before B4 was never measured; it must not read as zero."""
    rows = journal_service.rejection_decisions(
        ScanResult(
            session_date=date(2026, 9, 21),
            rejections=[Rejection(symbol="X", reason="r", stage="breakout")],
        )
    )
    assert rows[0].price is None
    assert rows[0].momentum is None


# --- and the same thing end to end, through the scan itself -----------------


def _flat_series(parameters, close, volume=200_000, count=None):
    count = count or max(parameters.minimum_sessions, 320)
    base = close * 0.80
    highs = [base * 1.002] * count
    lows = [base * 0.998] * count
    closes = [base] * count
    volumes = [float(volume)] * count
    for index in range(count - parameters.breakout_lookback_sessions, count):
        highs[index] = close * 0.99
    closes[-parameters.momentum_lookback_sessions] = closes[-5] / 1.40
    return highs, lows, closes, volumes


def test_the_scan_attaches_the_inputs_to_a_momentum_rejection(parameters, policy):
    """A name that reaches B8 and fails records what it looked like there."""
    highs, lows, closes, volumes = _flat_series(parameters, 1_000.0)
    # Flatten the six-month return so B8 -- and only B8 -- refuses it.
    closes[-parameters.momentum_lookback_sessions] = closes[-5]
    window = scan_service.build_window(
        parameters, highs, lows, closes, volumes, "X", include_last_in_averages=False
    )
    quote = scan_service.Quote(
        security_id="1", symbol="X", price=1_000.0,
        session_high=1_002.0, session_low=982.0, session_volume=800_000.0,
        fno_eligible=False,
    )
    result = scan_service.evaluate(parameters, policy, {"X": quote}, {"X": window}, ["X"])

    assert not result.candidates
    rejection = result.rejections[0]
    assert rejection.stage == "momentum"
    # It got far enough for all of these to have been computed.
    assert rejection.price == 1_000.0
    assert rejection.clv is not None
    assert rejection.vol_ratio is not None
    assert rejection.sma is not None
