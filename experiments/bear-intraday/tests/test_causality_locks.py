"""Shortlist and regime for day d use nothing from day d's bars (beyond the
09:15 open's gap flag, which is stored, not recomputed); locked bars refuse
entries and delay covers."""
from __future__ import annotations

import numpy as np
import pandas as pd

from islib.features import Features
from islib.panel import Panel
from islib.synth import random_walk_panel

from bearlib import engine as E
from bearlib.universe import Universe


def _corrupt_from(p: Panel, d0: int, seed=9) -> Panel:
    rng = np.random.default_rng(seed)
    f = np.ones(p.close.shape, dtype=np.float32)
    f[:, d0:, :] = rng.uniform(0.5, 1.5, size=f[:, d0:, :].shape)
    fi = np.ones(p.index_close.shape, dtype=np.float32)
    fi[d0:, :] = rng.uniform(0.5, 1.5, size=fi[d0:, :].shape)
    return Panel(p.symbols, p.dates, p.open * f, p.high * f, p.low * f, p.close * f, p.volume,
                 p.index_close * fi, p.index_open * fi, p.turnover_cr, p.gap_flag)


def test_shortlist_and_regime_are_causal():
    p = random_walk_panel(S=30, D=200, seed=3)
    d0 = 150
    a, b = Universe(p), Universe(_corrupt_from(p, d0))
    assert np.array_equal(a.shortlist[:, : d0 + 1], b.shortlist[:, : d0 + 1])
    assert np.array_equal(a.regime_b[: d0 + 1], b.regime_b[: d0 + 1])
    np.testing.assert_allclose(a.score[:, : d0 + 1], b.score[:, : d0 + 1], equal_nan=True)
    # ...and the corruption does reach later days (the test can fail).
    assert not np.allclose(a.score[:, d0 + 2:], b.score[:, d0 + 2:], equal_nan=True)


def _one_name_panel(close_path, prev_close):
    """One symbol, two days; day 1 carries `close_path` (75 bars)."""
    S, D, B = 1, 2, 75
    c = np.full((S, D, B), np.nan, dtype=np.float32)
    c[0, 0, :] = prev_close
    c[0, 1, :] = close_path
    o = c.copy()
    o[0, 1, 1:] = c[0, 1, :-1]
    h, l = np.fmax(o, c), np.fmin(o, c)
    v = np.ones_like(c)
    from datetime import date
    return Panel(["X"], [date(2020, 1, 1), date(2020, 1, 2)], o, h, l, c, v,
                 np.full((D, B), 100, np.float32), np.full((D, B), 100, np.float32),
                 np.ones((S, D), np.float32), np.zeros((S, D), bool))


def test_locked_up_bar_delays_a_short_cover():
    path = np.full(75, 100.0)
    path[10:20] = 111.0          # +11% vs prev close 100, high == low: locked up
    path[20:] = 108.0
    p = _one_name_panel(path, 100.0)
    # The lock starts with a GAP: bar 10 opens at the band and never trades
    # below it. (A bar that trades from 100 up to 111 passes through the stop
    # and fills there, correctly.)
    p.open[0, 1, 10] = p.high[0, 1, 10] = p.low[0, 1, 10] = 111.0
    F = Features(p)
    sig = pd.DataFrame({"s": [0], "d": [1], "k": [3], "side": [-1], "stop": [105.0], "trail": [np.nan]})
    t = E.simulate(p, F, sig, E.Exec(slippage=0.0))
    assert t.loc[0, "exit_reason"] in ("stop_gap", "stop")
    assert t.loc[0, "exit_bar"] == 20, "cover must wait for the first unlocked bar"


def test_locked_down_bar_refuses_a_short_entry():
    path = np.full(75, 89.0)     # locked down all day (-11%)
    p = _one_name_panel(path, 100.0)
    F = Features(p)
    sig = pd.DataFrame({"s": [0], "d": [1], "k": [3], "side": [-1], "stop": [95.0], "trail": [np.nan]})
    t = E.simulate(p, F, sig, E.Exec(slippage=0.0))
    assert str(t.loc[0, "exit_reason"]).startswith("not_taken")


def test_trailing_stop_only_ratchets_toward_price():
    path = np.linspace(100, 95, 75)
    p = _one_name_panel(path, 100.0)
    F = Features(p)
    sig = pd.DataFrame({"s": [0], "d": [1], "k": [3], "side": [-1], "stop": [101.0], "trail": [3.0]})
    t = E.simulate(p, F, sig, E.Exec(slippage=0.0))
    assert t.loc[0, "stop"] <= 101.0
