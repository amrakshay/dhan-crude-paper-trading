"""Round 2 must also be ~zero gross on random walks, and its order-flow proxies
must be causal."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from islib.features import Features
from islib.synth import random_walk_panel

from bearlib import engine as E
from bearlib import hyp2
from bearlib import stats as St
from bearlib.run import Context

NAMES = [h.name for h in hyp2.ROUND2]


@pytest.fixture(scope="module")
def nulls():
    out = []
    for seed in (0, 1, 2, 3):
        p = random_walk_panel(S=40, D=260, seed=seed)
        rng = np.random.default_rng(100 + seed)
        out.append((Context(p), rng.random(len(p.dates)) < 0.4))
    return out


@pytest.mark.parametrize("name", NAMES)
def test_round2_is_zero_gross_on_random_walk(nulls, name):
    h = hyp2.ROUND2[NAMES.index(name)]
    frames = []
    for ctx, on in nulls:
        sig = h.signals(ctx.U, on, ctx.U.shortlist)
        if not sig.empty:
            frames.append(St.taken(E.simulate(ctx.p, ctx.F, sig, E.Exec(slippage=0.0), locks=ctx.locks)))
    t = pd.concat(frames)
    # S6 needs a -0.75% opening drive, which the synthetic walk (0.2%/bar)
    # rarely produces; its floor is lower so the bias check still runs.
    floor = 15 if name == "S6_opening_drive" else 50
    assert len(t) > floor, f"{name}: only {len(t)} trades"
    g = t["gross_ret"]
    tstat = g.mean() / (g.std() / np.sqrt(len(g)))
    assert abs(tstat) < 3, f"{name}: {1e4 * g.mean():.2f} bps, t={tstat:.2f}"


def test_money_flow_is_causal():
    p = random_walk_panel(S=10, D=30, seed=5)
    a = hyp2.money_flow(Features(p)).copy()
    k = 20
    for arr in (p.open, p.high, p.low, p.close, p.volume):
        arr[:, :, k + 1:] *= 1.37
    b = hyp2.money_flow(Features(p))
    np.testing.assert_allclose(a[:, :, : k + 1], b[:, :, : k + 1], equal_nan=True)
