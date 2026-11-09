"""The order engine on hand-built bars, one rule per test.

A one-name, one-day panel; bars default to a flat 100 with a 0.1 range, and
each test writes the bars it needs. The order is a long limit at Z=99 with
S1=98 and T=103, marked at bar 5, live from bar 6.
"""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from islib.panel import N_BARS, Panel
from obl import sim as SM

SLIP = 0.0005


def panel(bars: dict[int, tuple[float, float, float, float]]):
    o = np.full((1, 1, N_BARS), 100.0, dtype=np.float32)
    h = o + 0.05
    l = o - 0.05
    c = o.copy()
    for b, (oo, hh, ll, cc) in bars.items():
        o[0, 0, b], h[0, 0, b], l[0, 0, b], c[0, 0, b] = oo, hh, ll, cc
    z = np.zeros((1, N_BARS), np.float32)
    return Panel(["X"], [date(2020, 1, 1)], o, h, l, c, np.ones_like(o), z, z,
                 np.ones((1, 1), np.float32), np.zeros((1, 1), bool))


def order(sign=1, k=5, end=57, Z=99.0, S1=98.0, T=103.0, obl=98.1, trade=True):
    return pd.DataFrame([dict(grp=1, s_exec=0, s_gate=0, d=0, gside=sign, sign=sign, k=k, end=end,
                              Z=Z, S1=S1, T=T, obl=obl, trade=trade)])


def gates(live=True):
    g = np.full((1, 1, N_BARS), live)
    return g, g


def run(bars, o=None, live=True, **kw):
    p = panel(bars)
    return SM.simulate(p, gates(live), o if o is not None else order(), slip=SLIP, **kw)


def test_touching_z_is_not_a_fill():
    t = run({6: (100, 100, 99.0, 99.5)})          # low == Z, no trade-through
    assert t.empty


def test_trade_through_fills_at_z_and_runs_to_time():
    t = run({6: (100, 100, 98.9, 99.5)})
    assert len(t) == 1
    r = t.iloc[0]
    assert r.fill_bar == 6 and r.entry_ls == pytest.approx(99.0)
    assert r.reason == "time" and r.exit_bar == 71
    assert r.exit_ls == pytest.approx(100 - SLIP * 100)


def test_gap_below_z_fills_at_the_open():
    t = run({6: (98.5, 99.2, 98.4, 99.0)})
    assert t.iloc[0].entry_ls == pytest.approx(98.5)


def test_stop_in_the_fill_bar_and_no_target_credit_there():
    t = run({6: (100, 104, 97.9, 101)})            # reaches T and S1 in the fill bar
    r = t.iloc[0]
    assert r.reason == "stop_fill_bar"
    assert r.exit_ls == pytest.approx(98 - SLIP * 98)


def test_open_through_the_stop_in_the_fill_bar_exits_at_the_open():
    t = run({6: (97.5, 97.6, 97.0, 97.2)})
    r = t.iloc[0]
    assert r.entry_ls == pytest.approx(97.5) and r.reason == "stop_fill_bar"
    assert r.exit_ls == pytest.approx(97.5 - SLIP * 97.5)


def test_target_after_the_fill_bar_has_no_slippage():
    t = run({6: (100, 100, 98.9, 99.5), 8: (101, 103.5, 100.9, 103)})
    r = t.iloc[0]
    assert r.reason == "target" and r.exit_ls == pytest.approx(103.0) and r.exit_bar == 8


def test_bar_touching_stop_and_target_is_a_stop():
    t = run({6: (100, 100, 98.9, 99.5), 8: (100, 103.5, 97.5, 100)})
    assert t.iloc[0].reason == "stop"


def test_gap_through_stop_fills_at_the_open_minus_slippage():
    t = run({6: (100, 100, 98.9, 99.5), 8: (97.0, 97.2, 96.5, 97.0)})
    r = t.iloc[0]
    assert r.reason == "stop_gap" and r.exit_ls == pytest.approx(97.0 - SLIP * 97.0)


def test_target_reached_before_the_retest_cancels():
    t = run({6: (100, 103.2, 100, 103), 8: (100, 100, 98.5, 99)})
    assert t.empty


def test_first_retest_only():
    # visit 1 touches Z exactly (no fill), price leaves, visit 2 trades through
    bars = {6: (100, 100, 99.0, 99.6), 7: (99.6, 100, 99.5, 99.8), 9: (99.5, 99.6, 98.8, 99.2)}
    assert run(bars).empty
    t = run(bars, first_only=False)
    assert len(t) == 1 and t.iloc[0].retest == 2 and t.iloc[0].fill_bar == 9


def test_order_not_live_skips_the_retest():
    assert run({6: (100, 100, 98.9, 99.5)}, live=False).empty


def test_gate_is_read_at_the_previous_bar():
    p = panel({6: (100, 100, 98.9, 99.5)})
    g = np.zeros((1, 1, N_BARS), bool)
    g[0, 0, 6] = True                 # live in bar 7, not bar 6
    assert SM.simulate(p, (g, g), order(), slip=SLIP).empty
    g[0, 0, 5] = True                 # now live in bar 6
    assert len(SM.simulate(p, (g, g), order(), slip=SLIP)) == 1


def test_window_ends_at_replacement_and_at_bar_57():
    assert run({6: (100, 100, 98.9, 99.5)}, order(end=5)).empty   # replaced at its own bar
    assert run({58: (100, 100, 98.9, 99.5)}).empty                # after the last live bar


def test_untradeable_order_never_fills():
    assert run({6: (100, 100, 98.9, 99.5)}, order(trade=False)).empty


def test_short_side_mirrors():
    """A short limit at 101 (above price), stop 102, target 97 -- in long
    space Z=-101, S1=-102, T=-97. A high through 101 fills; a later low at 97
    hits the target."""
    o = order(sign=-1, Z=-101.0, S1=-102.0, T=-97.0, obl=-101.9)
    t = run({6: (100, 101.1, 100, 100.5), 8: (99, 99.2, 96.8, 97)}, o)
    r = t.iloc[0]
    assert r.reason == "target"
    assert -r.entry_ls == pytest.approx(101.0) and -r.exit_ls == pytest.approx(97.0)
    priced = SM.price_trades(t, panel({}))
    assert priced.iloc[0].gross_ret == pytest.approx((101 - 97) / 101)


def test_chase_enters_at_next_open_with_slippage():
    t = run({6: (100.2, 100.3, 100.1, 100.2)}, chase=True)
    r = t.iloc[0]
    assert r.fill_bar == 6 and r.entry_ls == pytest.approx(100.2 + SLIP * 100.2)
    assert r.risk_ps == pytest.approx(r.entry_ls - 98.0)


def test_sizing_and_charges():
    t = SM.price_trades(run({6: (100, 100, 98.9, 99.5)}), panel({}))
    r = t.iloc[0]
    # risk Rs 5,000 over Z-S1 = 1.00 -> 5,000 shares, Rs 4.95 L notional (under the 25 L cap)
    assert r.qty == pytest.approx(5000) and r.notional == pytest.approx(495_000)
    assert r.gross_R == pytest.approx(5000 * (r.exit_ls - 99.0) / 5000)
    assert 0 < r.charges_R < 0.1
