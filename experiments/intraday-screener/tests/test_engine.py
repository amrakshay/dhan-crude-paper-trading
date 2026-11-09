"""Every fill rule in engine.py, on hand-built days where the answer is known."""
import numpy as np
import pandas as pd
import pytest

from islib.costs import RateCard
from islib.engine import ExecConfig, simulate
from islib.panel import LAST_EXIT_BAR, N_BARS, Panel
from datetime import date

NO_SLIP = ExecConfig(slippage=0.0)


def one_day(bars):
    """bars: {bar: (o, h, l, c)}; every other bar flat at 100."""
    o = np.full((1, 1, N_BARS), 100.0, np.float32)
    h, l, c = o.copy(), o.copy(), o.copy()
    for b, (oo, hh, ll, cc) in bars.items():
        o[0, 0, b], h[0, 0, b], l[0, 0, b], c[0, 0, b] = oo, hh, ll, cc
    v = np.ones_like(o)
    return Panel(["X"], [date(2020, 1, 1)], o, h, l, c, v, c[0].copy(), o[0].copy(),
                 np.ones((1, 1), np.float32), np.zeros((1, 1), bool))


def run(p, side, stop, target=np.nan, k=2, cfg=NO_SLIP):
    sig = pd.DataFrame({"s": [0], "d": [0], "k": [k], "side": [side], "stop": [stop], "target": [target]})
    return simulate(p, sig, cfg).iloc[0]


def test_entry_is_next_bar_open_not_signal_close():
    p = one_day({2: (100, 100, 100, 105), 3: (101, 102, 100.5, 101)})
    t = run(p, 1, stop=95)
    assert t.entry_bar == 3 and t.entry_px == pytest.approx(101)


def test_slippage_is_adverse_on_entry_and_time_exit():
    p = one_day({3: (100, 100, 100, 100)})
    t = run(p, 1, stop=90, cfg=ExecConfig(slippage=0.001))
    assert t.entry_px == pytest.approx(100.1)
    assert t.exit_reason == "time" and t.exit_px == pytest.approx(99.9)
    assert t.gross_ret < 0


def test_stop_fills_at_stop():
    p = one_day({5: (100, 100, 97, 98)})
    t = run(p, 1, stop=98.5)
    assert t.exit_reason == "stop" and t.exit_px == pytest.approx(98.5) and t.exit_bar == 5


def test_gap_through_stop_fills_at_open_not_stop():
    p = one_day({5: (96, 97, 95, 96)})
    t = run(p, 1, stop=98.5)
    assert t.exit_reason == "stop_gap" and t.exit_px == pytest.approx(96)


def test_bar_touching_both_is_a_stop():
    p = one_day({5: (100, 110, 90, 100)})
    t = run(p, 1, stop=95, target=105)
    assert t.exit_reason == "stop"


def test_target_is_a_limit_no_slippage_and_gap_fills_at_open():
    p = one_day({5: (100, 106, 99.5, 105)})
    t = run(p, 1, stop=95, target=105, cfg=ExecConfig(slippage=0.001))
    assert t.exit_reason == "target" and t.exit_px == pytest.approx(105)
    p = one_day({5: (108, 109, 107, 108)})
    t = run(p, 1, stop=95, target=105)
    assert t.exit_reason == "target" and t.exit_px == pytest.approx(108)


def test_short_is_symmetric():
    p = one_day({5: (100, 103, 99, 102)})
    t = run(p, -1, stop=102.5)
    assert t.exit_reason == "stop" and t.exit_px == pytest.approx(102.5)
    assert t.gross_ret == pytest.approx(-(102.5 / 100 - 1))


def test_time_exit_is_bar_71_and_later_bars_are_ignored():
    p = one_day({LAST_EXIT_BAR: (100, 100, 100, 101), LAST_EXIT_BAR + 1: (101, 150, 50, 101)})
    t = run(p, 1, stop=60, target=140)
    assert t.exit_reason == "time" and t.exit_bar == LAST_EXIT_BAR and t.exit_px == pytest.approx(101)


def test_signal_whose_stop_is_already_breached_is_not_taken():
    p = one_day({3: (94, 95, 93, 94)})
    t = run(p, 1, stop=95)
    assert t.exit_reason == "not_taken:stop_already_breached" and np.isnan(t.net_ret)


def test_missing_bars_are_skipped_for_entry():
    p = one_day({3: (np.nan,) * 4, 4: (100.5, 101, 100, 100.5)})
    t = run(p, 1, stop=95)
    assert t.entry_bar == 4 and t.entry_px == pytest.approx(100.5)


def test_rate_card_matches_hand_calculation():
    """README's table, computed by hand from dhan.co/pricing (2026-09-27)."""
    card = RateCard.load()
    assert float(card.round_trip(100_000, 100_000)) == pytest.approx(82.68, abs=0.05)
    assert float(card.round_trip(500_000, 500_000)) == pytest.approx(224.6, abs=0.1)


def test_costs_are_charged_on_the_trade():
    p = one_day({3: (100, 100, 100, 100)})
    t = run(p, 1, stop=90, cfg=ExecConfig(slippage=0.0, notional=100_000))
    assert t.cost_frac == pytest.approx(82.68 / 100_000, rel=1e-3)
    assert t.net_ret == pytest.approx(-t.cost_frac)
