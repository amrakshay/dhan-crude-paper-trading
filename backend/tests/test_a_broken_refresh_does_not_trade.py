"""A failed download must not become a sell order.

Both halves of what happened on 2026-09-23, and they compound. The Dhan token
expired overnight, the 08:00 nightly refreshed 129 of 500 symbols and failed
371 -- and then:

  1. marked itself DONE for the day, so it would not retry once the credential
     was replaced; and
  2. left the regime index among the 129, so the session date advanced to
     2026-09-22 while five of six holdings had no bar on it.

`plan_sells` reads an unranked holding as a rotation exit, so the 09:16
rebalance was about fourteen minutes from selling roughly Rs 4.4 lakh of
positions because a credential had lapsed. A manual gap-fill finished at
09:01:52 and the rebalance then held all six on ranks 1, 2, 3, 4, 8 and 12.

Neither half is hypothetical and neither was caught by a test, which is why
these are written against the real numbers.
"""
from datetime import date

import pytest

from src.swing.services.rebalance_planner import (
    DATA_ABSENT_REASONS,
    MINIMUM_SESSION_COVERAGE,
    RebalancePlanner,
)
from src.swing.services.ranking_service import (
    SKIP_MOMENTUM,
    SKIP_NOT_TRADED,
    Candidate,
    RankingSnapshot,
    RegimeState,
)
from src.strategies.services.strategy_registry import get_strategy_registry
from src.swing.services.swing_parameters import SwingParameters

STRATEGY = "nse-swing-momentum"


@pytest.fixture
def parameters():
    return SwingParameters.from_definition(get_strategy_registry().require(STRATEGY))


class _Holding:
    def __init__(self, symbol):
        self.symbol = symbol
        self.security_id = "1"
        self.quantity = 10
        self.entry_regime_enforced = False


def _snapshot(skipped, universe_size, candidates=()):
    return RankingSnapshot(
        strategy_key=STRATEGY,
        as_of=date(2026, 9, 22),
        regime=RegimeState(
            index_symbol="NIFTY", bar_date=date(2026, 9, 22), close=23414.3,
            sma200=24473.4, gate_on=False, return_over_window=0.0,
            return_window_sessions=63, entries_allowed=True, reason="gate off",
        ),
        liquid_count=100,
        above_sma_count=50,
        breadth=0.5,
        slots=5,
        candidates=list(candidates),
        skipped=dict(skipped),
        universe_size=universe_size,
    )


# --- the coverage measure ---------------------------------------------------


def test_coverage_counts_names_absent_from_the_SESSION_not_names_without_history():
    """`symbols_with_bars` counts any history; this counts today's.

    A symbol whose newest bar is a week old HAS stored bars and contributed
    nothing to this session. Conflating the two is what made the shortfall
    invisible.
    """
    snapshot = _snapshot({f"A{i}": SKIP_NOT_TRADED for i in range(371)}, 500)

    assert snapshot.absent_from_session == 371
    assert snapshot.session_coverage == pytest.approx((500 - 371) / 500)


def test_an_unknown_universe_size_is_not_zero_coverage(parameters):
    """Undefined is not zero -- the rule the whole ranking follows."""
    assert _snapshot({}, 0).session_coverage is None


# --- the sell decision ------------------------------------------------------


def test_a_holding_with_no_bar_is_HELD_when_the_session_is_barely_covered(parameters):
    """2026-09-23 exactly: 129 of 500 present, five holdings absent.

    Selling here is an infrastructure failure expressed as a trade, and it is
    irreversible. Holding costs one session of exposure with the chandelier
    stop still underneath.
    """
    skipped = {f"GONE{i}": SKIP_NOT_TRADED for i in range(371)}
    for held in ("HFCL", "LAURUSLABS", "CEMPRO", "ATHERENERG", "OFSS"):
        skipped[held] = SKIP_NOT_TRADED
    snapshot = _snapshot(skipped, 500)

    planner = RebalancePlanner(parameters, STRATEGY)
    plan = planner.plan_sells(
        snapshot,
        [_Holding(one) for one in ("HFCL", "LAURUSLABS", "CEMPRO", "ATHERENERG", "OFSS")],
    )

    assert plan.sells == [], "a missing bar is not a rotation signal"
    assert len(plan.holds) == 5
    assert plan.data_gap is True
    assert "data gap" in plan.holds[0].reason
    assert "trailing stop still applies" in plan.holds[0].reason


def test_a_genuinely_delisted_name_is_STILL_sold_on_a_well_covered_session(parameters):
    """The guard must not become "never exit an unranked holding".

    One name absent from a session everything else traded is a delisting --
    JBCHEPHARM, which stayed rankable on a stale close in an early draft. That
    is a real rotation exit and the fix must not swallow it.
    """
    snapshot = _snapshot({"JBCHEPHARM": SKIP_NOT_TRADED}, 500)

    plan = RebalancePlanner(parameters, STRATEGY).plan_sells(snapshot, [_Holding("JBCHEPHARM")])

    assert [one.symbol for one in plan.sells] == ["JBCHEPHARM"]
    assert plan.data_gap is False


def test_a_name_that_FAILED_A_FILTER_is_sold_however_thin_the_session(parameters):
    """Measured and refused is not the same as absent.

    A name below the momentum floor was ranked and rejected on real data. The
    coverage guard covers absence only; it must not excuse a name the rule
    actually looked at.
    """
    skipped = {f"GONE{i}": SKIP_NOT_TRADED for i in range(400)}
    skipped["SLOWNAME"] = SKIP_MOMENTUM
    snapshot = _snapshot(skipped, 500)

    plan = RebalancePlanner(parameters, STRATEGY).plan_sells(snapshot, [_Holding("SLOWNAME")])

    assert [one.symbol for one in plan.sells] == ["SLOWNAME"]
    assert SKIP_MOMENTUM not in DATA_ABSENT_REASONS


def test_the_floor_is_crossed_not_approached(parameters):
    """Coverage exactly at the floor still trades; below it does not."""
    universe = 1000
    at_floor = int(universe * (1 - MINIMUM_SESSION_COVERAGE))       # 100 absent -> 0.90
    below = at_floor + 1

    exactly = _snapshot({f"X{i}": SKIP_NOT_TRADED for i in range(at_floor)}, universe)
    exactly.skipped["HELD"] = SKIP_NOT_TRADED
    just_under = _snapshot({f"X{i}": SKIP_NOT_TRADED for i in range(below)}, universe)
    just_under.skipped["HELD"] = SKIP_NOT_TRADED

    planner = RebalancePlanner(parameters, STRATEGY)
    assert planner.plan_sells(exactly, [_Holding("HELD")]).sells, "at the floor, act"
    assert not planner.plan_sells(just_under, [_Holding("HELD")]).sells, "below it, hold"
