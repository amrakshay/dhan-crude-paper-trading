"""The harness on data with a known answer.

No edge: H1 must lose about its slippage gross, and the day-bootstrap CI of
(gross + slippage) must cover zero. Planted edge: H1 must find it, and the
random-side control must not -- otherwise the harness is finding edges in its
own mechanics.
"""
import numpy as np
import pytest

from islib.engine import ExecConfig, simulate
from islib.features import Features
from islib.hypotheses import CONTROLS, H1OpeningRangeBreakout
from islib.stats import day_bootstrap, taken
from islib.synth import plant_orb_edge, random_walk_panel

CFG = ExecConfig(slippage=0.0005)


@pytest.fixture(scope="module")
def base():
    return random_walk_panel(S=40, D=250, seed=11)


def run(p, control=None, seed=0):
    F = Features(p, n_liquid=40)
    h = H1OpeningRangeBreakout()
    days = np.ones(len(p.dates), bool)
    sig = h.signals(F, days)
    if control:
        sig = CONTROLS[control](h, F, sig, np.random.default_rng(seed))
    return simulate(p, sig, CFG)


def test_no_edge_means_gross_is_minus_slippage(base):
    t = run(base)
    assert len(taken(t)) > 500
    t = t.assign(adj=t["gross_ret"] + 2 * CFG.slippage)
    b = day_bootstrap(t, col="adj")
    assert b["ci95"][0] < 0 < b["ci95"][1], b
    assert day_bootstrap(t)["mean"] < 0          # and net is a loss


def test_planted_edge_is_found_and_controls_do_not_find_it(base):
    p = plant_orb_edge(base, drift_per_bar=0.0008)
    real = day_bootstrap(run(p))
    assert real["ci95"][0] > 0, real
    rs_t = run(p, "random_side")
    rs = day_bootstrap(rs_t)
    # A coin is NOT expected to earn zero here: planted break days trend, so
    # a coin with a stop rides the right guesses and is cut on the wrong ones
    # (measured: +1.5% against H1's +4.5%). What must hold is that knowing the
    # direction is worth clearly more than guessing it.
    assert real["ci95"][0] > rs["ci95"][1], (rs, real)
    # ...and the control must actually TAKE its trades, or it is filtered by
    # something that knows the answer (the bug this test first caught).
    assert taken(rs_t).shape[0] > 0.9 * len(rs_t)
    assert (taken(rs_t)["side"] > 0).mean() == pytest.approx(0.5, abs=0.05)


def test_controls_are_unbiased_on_no_edge_data():
    """Pooled over seeds, because one seed's 95% interval misses zero one time
    in twenty by construction -- it did, once, for random_name (-8.7 bps,
    zero slippage, no gap fills); across 18 runs it averaged +0.05 bps."""
    h = H1OpeningRangeBreakout()
    zero = ExecConfig(slippage=0.0)
    for control in ("random_side", "random_time", "random_name"):
        means = []
        for ps in range(4):
            p = random_walk_panel(S=40, D=250, seed=200 + ps)
            F = Features(p, n_liquid=40)
            sig = h.signals(F, np.ones(len(p.dates), bool))
            for r in range(2):
                c = CONTROLS[control](h, F, sig, np.random.default_rng(r))
                means.append(simulate(p, c, zero)["gross_ret"].mean())
        means = np.array(means)
        t_stat = means.mean() / (means.std(ddof=1) / np.sqrt(len(means)))
        assert abs(t_stat) < 3, (control, means.mean(), t_stat)


from islib.hypotheses import HYPOTHESES


@pytest.mark.parametrize("name", sorted(HYPOTHESES))
def test_every_hypothesis_is_null_on_a_random_walk(name):
    """No edge exists, so gross (at zero slippage) must be ~0 for every
    hypothesis, pooled over seeds -- a harness that finds one here is broken."""
    h = HYPOTHESES[name]
    means = []
    for ps in range(4):
        p = random_walk_panel(S=40, D=250, seed=300 + ps)
        t = simulate(p, h.signals(Features(p, n_liquid=40), np.ones(len(p.dates), bool)),
                     ExecConfig(slippage=0.0))
        assert taken(t).shape[0] > 50, (name, len(t))
        means.append(taken(t)["gross_ret"].mean())
    means = np.array(means)
    t_stat = means.mean() / (means.std(ddof=1) / np.sqrt(len(means)))
    assert abs(t_stat) < 3.5, (name, means)
