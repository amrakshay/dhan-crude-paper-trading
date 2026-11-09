"""The whole pipeline on data with a known answer.

Null (random walk): the strategy must NOT beat its controls. The fill rules
are deliberately pessimistic (a stop in the fill bar counts, a target there
does not), so the strategy itself is expected to lose a little even before
charges; what must hold is that the controls lose the same.

Planted edge: after every bullish (bearish) break of structure, price drifts
up (down) for the rest of the day. The strategy must find it, and the coin
must not do as well.
"""
import numpy as np
import pandas as pd
import pytest

from islib.features import Features
from islib.panel import Panel
from islib.stats import day_bootstrap, diff_bootstrap
from islib.synth import random_walk_panel
from obl import controls as CT
from obl import setups as SU
from obl import sim as SM
from obl.board import Board

S, D = 40, 220


def run(p: Panel, control=None, seed=0, mode="none"):
    F = Features(p, n_liquid=S)
    b = Board(F, np.arange(S) % 6)
    valid = b.universe
    gates = (b.gate(1, mode), b.gate(-1, mode))
    o = pd.concat([SM.orders_from_setups(SU.detect(p, F.atr5, valid, sg), sg, D) for sg in (1, -1)],
                  ignore_index=True)
    if control:
        o = CT.apply(control, o, F.close_ff, valid, seed)
    t = SM.simulate(p, gates, o, slip=0.0)
    t = SM.price_trades(SM.resolve_sides(t, D), p)
    return t.assign(net_ret=t["gross_R"])      # judge GROSS R: the mechanics, not the costs


def plant_bos_drift(p: Panel, drift=0.0006) -> Panel:
    """After the first break of structure of the day (either side), drift every
    later bar that way. Uses the detector itself, so 'BOS' means exactly what
    the strategy means by it."""
    F = Features(p, n_liquid=S)
    valid = np.ones((S, D), bool)
    first = {}
    for sg in (1, -1):
        st = SU.detect(p, F.atr5, valid, sg)
        for s, d, k in st[:, :3].astype(int):
            if (s, d) not in first or k < first[(s, d)][0]:
                first[(s, d)] = (k, sg)
    B = p.close.shape[2]
    mult = np.ones(p.close.shape)
    for (s, d), (k, sg) in first.items():
        n = np.arange(B) - k
        mult[s, d] = np.where(n > 0, (1 + sg * drift) ** n, 1.0)
    f = mult.astype(np.float32)
    return Panel(p.symbols, p.dates, p.open * f, p.high * f, p.low * f, p.close * f, p.volume,
                 p.index_close, p.index_open, p.turnover_cr, p.gap_flag)


@pytest.fixture(scope="module")
def null_panel():
    return random_walk_panel(S=S, D=D, seed=21, vol_bar=0.003)


def test_enough_trades_and_both_sides(null_panel):
    t = run(null_panel)
    assert len(t) > 500
    assert 0.35 < (t["side"] > 0).mean() < 0.65


@pytest.mark.parametrize("control", ["random_side", "random_depth", "random_name"])
def test_null_strategy_does_not_beat_controls(control):
    """Pooled over seeds. One seed is not enough, and this test is why: on
    seed 21 the strategy beat the coin by +0.067 R with a CI clear of zero,
    which looked like a leak. Over 12 seeds the three differences averaged
    +0.002, -0.008 and -0.003 R (sd 0.03), with misses in both directions.
    Two causes, both real and both kept in mind for the real runs: R is
    fat-tailed (targets up to ~20 R), and the day bootstrap does not see the
    control's OWN randomness, so its CI missed zero in 11% of seeds rather
    than 5%. The real runs therefore pool 5 control repetitions."""
    diffs = []
    for seed in range(60, 66):
        p = random_walk_panel(S=S, D=D, seed=seed, vol_bar=0.003)
        real = run(p)
        ctl = pd.concat([run(p, control, seed=r) for r in range(2)], ignore_index=True)
        diffs.append(real["gross_R"].mean() - ctl["gross_R"].mean())
    diffs = np.array(diffs)
    t = diffs.mean() / (diffs.std(ddof=1) / np.sqrt(len(diffs)))
    assert abs(t) < 3, (diffs, t)


def test_null_gross_is_small(null_panel):
    """A random walk with no costs: the pessimistic fill-bar rule costs a
    little, nothing else should. |mean| < 0.1 R."""
    b = day_bootstrap(run(null_panel))
    assert abs(b["mean"]) < 0.1, b


def test_planted_edge_is_found_and_beats_the_coin(null_panel):
    p = plant_bos_drift(null_panel)
    real = run(p)
    rb = day_bootstrap(real)
    assert rb["ci95"][0] > 0, rb
    coin = pd.concat([run(p, "random_side", seed=r) for r in range(3)], ignore_index=True)
    diff = diff_bootstrap(real, coin)
    assert diff["ci95_bps"][0] > 0, diff
    # Its FILLS are not 50/50 here (measured 40% unflipped), and should not be:
    # under a planted drift price moves INTO a flipped order and away from an
    # unflipped one. The order split is what must be fair; see below.


def test_coin_orders_are_fair_and_unflipped_orders_are_untouched(null_panel):
    p = null_panel
    F = Features(p, n_liquid=S)
    valid = Board(F, np.arange(S) % 6).universe
    o = pd.concat([SM.orders_from_setups(SU.detect(p, F.atr5, valid, sg), sg, D) for sg in (1, -1)],
                  ignore_index=True)
    c = CT.apply("random_side", o, F.close_ff, valid, seed=0)
    t = o["trade"]
    same = (c["sign"] == o["sign"])[t]
    assert same.mean() == pytest.approx(0.5, abs=0.02)
    # an unflipped order must be exactly the real order
    cols = ["Z", "S1", "T", "obl"]
    np.testing.assert_allclose(c.loc[t][same][cols].to_numpy(), o.loc[t][same][cols].to_numpy(),
                               rtol=1e-9)
    # random_depth keeps the side and stays on the same side of price
    rd = CT.apply("random_depth", o, F.close_ff, valid, seed=0)
    assert (rd["sign"] == o["sign"]).all()
    close_ls = o["sign"] * F.close_ff[o["s_exec"], o["d"], o["k"]]
    assert (rd.loc[t, "Z"] <= close_ls[t] + 1e-9).all()
