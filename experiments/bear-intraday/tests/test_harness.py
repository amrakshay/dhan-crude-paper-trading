"""The harness must report nothing where there is nothing, and find an edge
that has been planted. A null result only means something from a harness
that passes both (handoff, 'Prove the harness first')."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from islib.panel import Panel
from islib.synth import random_walk_panel

from bearlib import engine as E
from bearlib import stats as St
from bearlib.hyp import CONTROLS, HYPOTHESES
from bearlib.run import Context, index_bars_from_panel
from bearlib.universe import Universe

SEEDS = (0, 1, 2, 3)


def _ctx(seed, D=260, S=40):
    p = random_walk_panel(S=S, D=D, seed=seed)
    return Context(p)


def _on_days(p, seed, share=0.4):
    rng = np.random.default_rng(100 + seed)
    return rng.random(len(p.dates)) < share


@pytest.fixture(scope="module")
def nulls():
    return [(_ctx(s), s) for s in SEEDS]


@pytest.mark.parametrize("name", list(HYPOTHESES))
def test_random_walk_hypothesis_is_zero_gross(nulls, name):
    """No slippage: gross per trade pooled over seeds must not differ from 0."""
    frames = []
    for ctx, seed in nulls:
        sig = HYPOTHESES[name].signals(ctx.U, _on_days(ctx.p, seed), ctx.names(name))
        if not sig.empty:
            frames.append(St.taken(E.simulate(ctx.p, ctx.F, sig, E.Exec(slippage=0.0), locks=ctx.locks)))
    t = pd.concat(frames)
    assert len(t) > 100, f"{name}: only {len(t)} trades on synthetic data"
    g = t["gross_ret"]
    tstat = g.mean() / (g.std() / np.sqrt(len(g)))
    assert abs(tstat) < 3, f"{name}: gross {1e4 * g.mean():.2f} bps, t={tstat:.2f} on a random walk"


@pytest.mark.parametrize("ctl", list(CONTROLS))
def test_random_walk_controls_are_unbiased(nulls, ctl):
    frames, n_sig = [], 0
    for ctx, seed in nulls:
        rng = np.random.default_rng(seed)
        sig = HYPOTHESES["B1_breakdown"].signals(ctx.U, _on_days(ctx.p, seed), ctx.names("B1_breakdown"))
        n_sig += 5 * len(sig)
        for _ in range(5):
            c = CONTROLS[ctl](ctx.U, sig, rng)
            frames.append(St.taken(E.simulate(ctx.p, ctx.F, c, E.Exec(slippage=0.0), locks=ctx.locks)))
    t = pd.concat(frames)
    g = t["gross_ret"]
    tstat = g.mean() / (g.std() / np.sqrt(len(g)))
    assert abs(tstat) < 3, f"{ctl}: gross {1e4 * g.mean():.2f} bps, t={tstat:.2f}"
    # A control must actually take its trades (leak 1: a control that skips
    # the trades the hypothesis would not have taken is not a control).
    assert len(t) > 0.8 * n_sig, f"{ctl}: took {len(t)} of {n_sig}"


def test_index_short_is_zero_gross_on_random_walk(nulls):
    from bearlib.run import index_short_signals
    g = []
    for ctx, seed in nulls:
        it = E.simulate_index(ctx.p, ctx.ib, index_short_signals(_on_days(ctx.p, seed)), slippage=0.0)
        g.append(it["gross_ret"])
    g = pd.concat(g)
    tstat = g.mean() / (g.std() / np.sqrt(len(g)))
    assert abs(tstat) < 3


# --- planted edge ----------------------------------------------------------

def _plant_b1(p: Panel, U: Universe, on: np.ndarray, drift=-0.0004) -> Panel:
    """After B1's trigger on a shortlisted, regime-on name, every later bar of
    that day moves `drift` per bar (compounded). Bars up to the trigger are
    untouched, so the trigger itself is unchanged."""
    sig = HYPOTHESES["B1_breakdown"].signals(U, on, U.shortlist)
    S, D, B = p.close.shape
    mult = np.ones((S, D, B))
    for s, d, k in sig[["s", "d", "k"]].astype(int).itertuples(index=False, name=None):
        n = np.arange(B) - k
        mult[s, d] = np.where(n > 0, (1 + drift) ** n, 1.0)
    f = mult.astype(np.float32)
    return Panel(p.symbols, p.dates, p.open * f, p.high * f, p.low * f, p.close * f, p.volume,
                 p.index_close, p.index_open, p.turnover_cr, p.gap_flag)


def test_planted_bear_edge_is_found_and_beats_controls():
    found, diffs = [], {k: [] for k in CONTROLS}
    for seed in (0, 1):
        p0 = random_walk_panel(S=40, D=260, seed=seed)
        U0 = Universe(p0)
        on = _on_days(p0, seed)
        p1 = _plant_b1(p0, U0, on)
        U1 = Universe(p1)
        # Planting moves day-d closes, which would move later shortlists; pin
        # the original so the test isolates trigger + execution + controls.
        U1.__dict__["shortlist"] = U0.shortlist
        ctx = Context(p1, U1)
        sig = HYPOTHESES["B1_breakdown"].signals(U1, on, U1.shortlist)
        t = E.simulate(p1, U1.F, sig, E.Exec(slippage=0.0005), locks=ctx.locks)
        b = St.day_bootstrap(t)
        found.append(b["ci95"][0] > 0)
        rng = np.random.default_rng(seed)
        for name, fn in CONTROLS.items():
            pool = pd.concat([E.simulate(p1, U1.F, fn(U1, sig, rng), E.Exec(slippage=0.0005),
                                         locks=ctx.locks) for _ in range(5)])
            diffs[name].append(St.diff_bootstrap(t, pool)["ci95_bps"][0])
    assert all(found), "B1 did not find a planted short edge"
    for name, lo in diffs.items():
        assert min(lo) > 0, f"B1 did not beat {name} on planted data: {lo}"


def test_null_strategy_does_not_pass_bar_1_by_chance(nulls):
    """Pooled over seeds, net after 5 bps + charges must be negative (it pays costs)."""
    frames = []
    for ctx, seed in nulls:
        sig = HYPOTHESES["B1_breakdown"].signals(ctx.U, _on_days(ctx.p, seed), ctx.names("B1_breakdown"))
        frames.append(St.taken(E.simulate(ctx.p, ctx.F, sig, E.Exec(slippage=0.0005), locks=ctx.locks)))
    t = pd.concat(frames)
    assert t["net_ret"].mean() < 0
