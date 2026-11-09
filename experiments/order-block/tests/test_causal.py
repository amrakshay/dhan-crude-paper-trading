"""No lookahead: nothing decided at bar k may change when later bars change.

Corrupts every bar after k on one day, and every later day, then asserts that
(a) every setup marked at or before k on that day, and every setup on an
earlier day, is identical, and (b) every gate at or before k is identical.
"""
import numpy as np
import pytest

from islib.features import Features
from islib.synth import random_walk_panel
from islib.panel import Panel
from obl import setups as SU
from obl.board import Board


def corrupt(p: Panel, d0: int, k: int, seed=5) -> Panel:
    rng = np.random.default_rng(seed)
    arrs = {}
    for f in ("open", "high", "low", "close", "volume"):
        a = getattr(p, f).copy()
        noise = rng.uniform(0.5, 1.5, size=a.shape).astype(np.float32)
        a[:, d0, k + 1:] *= noise[:, d0, k + 1:]
        a[:, d0 + 1:] *= noise[:, d0 + 1:]
        arrs[f] = a
    lo = np.minimum.reduce([arrs["open"], arrs["close"], arrs["low"]])
    hi = np.maximum.reduce([arrs["open"], arrs["close"], arrs["high"]])
    arrs["low"], arrs["high"] = lo, hi
    return Panel(p.symbols, p.dates, arrs["open"], arrs["high"], arrs["low"], arrs["close"],
                 arrs["volume"], p.index_close, p.index_open, p.turnover_cr, p.gap_flag)


@pytest.fixture(scope="module")
def pair():
    p = random_walk_panel(S=30, D=40, seed=3, vol_bar=0.003)
    return p, corrupt(p, d0=30, k=25)


def _setups(p, sign):
    F = Features(p, n_liquid=30)
    valid = np.ones((len(p.symbols), len(p.dates)), bool)
    return SU.detect(p, F.atr5, valid, sign)


@pytest.mark.parametrize("sign", [1, -1])
def test_setups_are_causal(pair, sign):
    a, b = (_setups(x, sign) for x in pair)
    keep = lambda st: st[(st[:, 1] < 30) | ((st[:, 1] == 30) & (st[:, 2] <= 25))]
    ka, kb = keep(a), keep(b)
    assert len(ka) > 50
    np.testing.assert_array_equal(ka, kb)
    # ...and the corruption did change something later, or the test proves nothing
    assert len(a) != len(b) or not np.array_equal(a, b)


@pytest.mark.parametrize("mode", ["align3", "align4", "top20", "none", "picks"])
@pytest.mark.parametrize("side", [1, -1])
def test_gates_are_causal(pair, mode, side):
    sector = np.arange(30) % 5
    ga, gb = (Board(Features(x, n_liquid=30), sector).gate(side, mode) for x in pair)
    np.testing.assert_array_equal(ga[:, :30], gb[:, :30])
    np.testing.assert_array_equal(ga[:, 30, :26], gb[:, 30, :26])
    assert ga[:, :30].any()
