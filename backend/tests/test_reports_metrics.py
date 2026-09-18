"""Performance metrics, and what they refuse to report.

The backtest's own statistics -- CAGR, maximum drawdown, MAR, win rate, profit
factor, average hold, return concentration -- computed from this book's own
realised history. Most of these tests are about the refusals:

* a deposit is not a gain and a withdrawal is not a drawdown, so a curve that
  contains either withholds CAGR, MAR and drawdown rather than reporting a
  number that looks like skill;
* profit factor is undefined, not infinite, when nothing has lost money;
* concentration is undefined when the summed return is not positive, because a
  share of a loss is not a figure anyone can read.

Return concentration itself is pinned because the specification calls it the
single most important statistic on its page: the top 10 of 672 backtested
trades were 55% of the summed return, which is what puts wide error bars on
every other number.
"""
from datetime import datetime, timedelta
from decimal import Decimal

from src.reports.services.metrics_service import (
    CONCENTRATION_TOP_N,
    MetricsService,
    Trade,
)

START = datetime(2026, 1, 1, 10, 0)


def _trade(day, net, hold_days=10, charges="0", start=START):
    opened = start + timedelta(days=day)
    return Trade(
        symbol=f"SYM{day}",
        opened_at=opened,
        closed_at=opened + timedelta(days=hold_days),
        quantity=100,
        gross_pnl=Decimal(str(net)) + Decimal(charges),
        charges=Decimal(charges),
    )


def _curve(points, start_equity=1000000):
    """An equity curve of (date, equity) pairs in the shape pnl_service emits."""
    return [
        {
            "date": date_str,
            "grossPnl": "0",
            "charges": "0",
            "netPnl": "0",
            "cumulativeGross": "0",
            "cumulativeNet": "0",
            "cashFlow": "0",
            "equity": str(equity),
        }
        for date_str, equity in points
    ]


def _service():
    return MetricsService(session=None)


# --- trade statistics ---------------------------------------------------------


def test_the_win_rate_counts_a_scratch_as_neither():
    """A trade that came out exactly flat is not a loss.

    The specification's median trade is +0.11%, so a rule that swept scratches
    into the loss column would understate the win rate on exactly the kind of
    distribution this strategy produces.
    """
    trades = [_trade(0, 100), _trade(1, -50), _trade(2, 0)]
    metrics = _service().compute(trades)

    assert metrics.trades == 3
    assert metrics.wins == 1
    assert metrics.losses == 1
    assert metrics.scratches == 1
    assert metrics.win_rate == round(1 / 3, 4)


def test_the_profit_factor_is_gross_profit_over_gross_loss():
    trades = [_trade(0, 300), _trade(1, 200), _trade(2, -250)]
    metrics = _service().compute(trades)

    assert metrics.gross_profit == Decimal("500.00")
    assert metrics.gross_loss == Decimal("250.00")
    assert metrics.profit_factor == 2.0


def test_the_profit_factor_is_undefined_rather_than_infinite_with_no_losses():
    """Dividing by a gross loss of zero. Null, not a very large number."""
    metrics = _service().compute([_trade(0, 100), _trade(1, 50)])
    assert metrics.gross_loss == Decimal("0.00")
    assert metrics.profit_factor is None


def test_charges_come_out_of_every_trade_figure():
    """Gross is not the number a trader lives on.

    A paper-trading tool that quotes only gross P&L is lying by omission --
    charges are frequently the difference between a winning and a losing
    strategy at this size.
    """
    trades = [_trade(0, 100, charges="40")]
    metrics = _service().compute(trades)
    assert metrics.average_trade == Decimal("100.00")
    assert trades[0].gross_pnl == Decimal("140")


def test_the_average_hold_is_measured_in_days_of_the_actual_positions():
    trades = [_trade(0, 100, hold_days=10), _trade(1, 100, hold_days=30)]
    metrics = _service().compute(trades)
    assert metrics.average_hold_days == 20.0
    assert metrics.median_hold_days == 20.0


# --- concentration -------------------------------------------------------------


def test_return_concentration_is_the_share_the_best_handful_produced():
    """Twelve trades, of which the best ten are almost all of the return."""
    trades = [_trade(index, 1000) for index in range(CONCENTRATION_TOP_N)]
    trades += [_trade(20, 1), _trade(21, 1)]
    metrics = _service().compute(trades)

    total = CONCENTRATION_TOP_N * 1000 + 2
    assert metrics.concentration_share == round(
        (CONCENTRATION_TOP_N * 1000) / total, 4
    )
    assert "single most important" not in (metrics.concentration_note or "")
    assert "55%" in metrics.concentration_note


def test_concentration_is_withheld_when_the_summed_return_is_not_positive():
    trades = [_trade(0, -100), _trade(1, -50)]
    metrics = _service().compute(trades)

    assert metrics.concentration_share is None
    assert "undefined" in metrics.concentration_note


# --- the curve -----------------------------------------------------------------


def test_cagr_drawdown_and_mar_are_measured_off_the_equity_curve():
    curve = _curve(
        [
            ("2024-01-01", 1000000),
            ("2024-07-01", 1300000),
            ("2024-10-01", 1100000),   # the drawdown
            ("2026-01-01", 2000000),
        ]
    )
    trades = [_trade(0, 100)]
    metrics = _service().compute(trades, curve, cash_flow_days=[])

    assert metrics.starting_equity == Decimal("1000000.00")
    assert metrics.ending_equity == Decimal("2000000.00")
    # Peak 13,00,000 down to 11,00,000 is 15.38%.
    assert round(metrics.max_drawdown, 4) == 0.1538
    assert metrics.max_drawdown_from == "2024-07-01"
    assert metrics.max_drawdown_to == "2024-10-01"
    # Doubling over two years.
    assert 0.40 < metrics.cagr < 0.42
    assert metrics.mar == round(metrics.cagr / metrics.max_drawdown, 4)


def test_a_deposit_after_the_first_trade_withholds_cagr_drawdown_and_mar():
    """A deposit is not a gain and a withdrawal is not a drawdown.

    The same rule `BalanceService` applies to an unmarked position: a figure
    that cannot be computed honestly is withheld with its reason, not printed
    as a number that looks like skill.
    """
    from datetime import date

    curve = _curve(
        [("2024-01-01", 1000000), ("2024-06-01", 900000), ("2026-01-01", 2000000)]
    )
    # The trade closes on 2024-01-11; the money moves nearly a year later.
    trades = [_trade(0, 100, start=datetime(2024, 1, 1, 10, 0))]
    metrics = _service().compute(
        trades, curve, cash_flow_days=[date(2025, 1, 1)]
    )

    assert metrics.cash_flows_after_first_trade == 1
    assert metrics.cagr is None
    assert metrics.mar is None
    assert metrics.max_drawdown is None
    assert "not a gain" in metrics.curve_note
    # The raw equity figures are still reported -- they are facts.
    assert metrics.starting_equity == Decimal("1000000.00")
    assert metrics.ending_equity == Decimal("2000000.00")


def test_the_opening_deposit_does_not_withhold_anything():
    """The first deposit IS the opening balance and predates every trade."""
    from datetime import date

    curve = _curve([("2024-01-01", 1000000), ("2026-01-01", 1500000)])
    trades = [_trade(0, 100, start=datetime(2024, 1, 1, 10, 0))]
    metrics = _service().compute(
        trades, curve, cash_flow_days=[date(2023, 12, 31)]
    )

    assert metrics.cash_flows_after_first_trade == 0
    assert metrics.cagr is not None


def test_a_short_window_says_the_cagr_is_arithmetic_rather_than_a_track_record():
    curve = _curve([("2026-01-01", 1000000), ("2026-02-01", 1100000)])
    metrics = _service().compute([_trade(0, 100)], curve, cash_flow_days=[])

    assert metrics.days_covered == 31
    assert "no live track record" in metrics.curve_note


def test_a_curve_with_one_point_reports_nothing_rather_than_zero():
    metrics = _service().compute([_trade(0, 100)], _curve([("2026-01-01", 1000000)]))

    assert metrics.cagr is None
    assert metrics.max_drawdown is None
    assert "Not enough history" in metrics.curve_note


def test_no_trades_at_all_reports_no_trade_statistics():
    metrics = _service().compute([])
    assert metrics.trades == 0
    assert metrics.win_rate is None
    assert metrics.profit_factor is None
    assert metrics.average_trade is None
    assert metrics.as_dict()["trades"] == 0
