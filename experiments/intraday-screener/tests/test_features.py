"""No feature at bar k may see bar k+1. Enforced by corrupting the future."""
import numpy as np
import pytest

from islib.features import Features
from islib.hypotheses import H1OpeningRangeBreakout, LAST_DECISION_BAR
from islib.panel import Panel
from islib.synth import random_walk_panel

DAY, K = 150, 20


def corrupt_after(p: Panel, d: int, k: int, seed=1) -> Panel:
    rng = np.random.default_rng(seed)
    q = [x.copy() for x in (p.open, p.high, p.low, p.close, p.volume)]
    for x in q:
        x[:, d, k + 1:] *= rng.uniform(0.5, 2.0, size=x[:, d, k + 1:].shape).astype(np.float32)
    ic, io = p.index_close.copy(), p.index_open.copy()
    ic[d, k + 1:] *= 1.5
    # Later DAYS too: nothing at day d may depend on them.
    for x in q:
        x[:, d + 1:] *= 1.7
    return Panel(p.symbols, p.dates, *q, ic, io, p.turnover_cr, p.gap_flag)


@pytest.fixture(scope="module")
def pair():
    p = random_walk_panel(S=12, D=200, seed=3)
    return Features(p, n_liquid=8), Features(corrupt_after(p, DAY, K), n_liquid=8)


@pytest.mark.parametrize("name", ["close_ff", "ret_since_open", "vwap", "atr5", "cumvol", "relvol"])
def test_bar_features_do_not_see_the_future(pair, name):
    a, b = (getattr(f, name)[:, DAY, :K + 1] for f in pair)
    np.testing.assert_allclose(a, b, rtol=1e-5, equal_nan=True)


def test_index_return_does_not_see_the_future(pair):
    a, b = (f.index_ret_since_open[DAY, :K + 1] for f in pair)
    np.testing.assert_allclose(a, b, rtol=1e-5, equal_nan=True)


@pytest.mark.parametrize("name", ["day_open", "prev_close", "gap", "liquid"])
def test_day_features_do_not_see_the_future(pair, name):
    a, b = (getattr(f, name)[:, DAY] for f in pair)
    np.testing.assert_array_equal(np.nan_to_num(a), np.nan_to_num(b))


def test_opening_range_is_bars_0_to_2_only(pair):
    f = pair[0]
    orh, orl = f.opening_range
    np.testing.assert_allclose(orh[:, DAY], f.p.high[:, DAY, :3].max(axis=1))
    np.testing.assert_allclose(orl[:, DAY], f.p.low[:, DAY, :3].min(axis=1))


def test_h1_fires_on_the_first_qualifying_bar_and_never_before_0935():
    p = random_walk_panel(S=20, D=120, seed=5)
    F = Features(p, n_liquid=20)
    days = np.ones(len(p.dates), bool)
    sig = H1OpeningRangeBreakout().signals(F, days)
    assert len(sig) > 0
    assert sig["k"].min() >= 3 and sig["k"].max() <= LAST_DECISION_BAR
    orh, orl = F.opening_range
    for r in sig.sample(50, random_state=0).itertuples():
        c = p.close[r.s, r.d]
        brk = (c > orh[r.s, r.d]) if r.side > 0 else (c < orl[r.s, r.d])
        ok = brk & (F.relvol[r.s, r.d] >= 1.5)
        assert ok[r.k] and not ok[3:r.k].any()
        # and the other side did not break (with volume) earlier either
        other = ((c < orl[r.s, r.d]) if r.side > 0 else (c > orh[r.s, r.d])) & (F.relvol[r.s, r.d] >= 1.5)
        assert not other[3:r.k].any()


from islib.hypotheses import HYPOTHESES


@pytest.mark.parametrize("name", sorted(HYPOTHESES))
@pytest.mark.parametrize("K", [8, 20, 40])
def test_no_hypothesis_signal_sees_the_future(name, K):
    """Signals on day DAY decided at or before bar K must not change when
    every later bar of that day, and every later day, is scrambled."""
    p = random_walk_panel(S=30, D=200, seed=7)
    h = HYPOTHESES[name]
    days = np.ones(len(p.dates), bool)
    cols = ["s", "k", "side", "stop"]
    a = h.signals(Features(p, n_liquid=30), days)
    b = h.signals(Features(corrupt_after(p, DAY, K), n_liquid=30), days)
    a = a[(a.d == DAY) & (a.k <= K)][cols].sort_values(["s", "k"]).reset_index(drop=True)
    b = b[(b.d == DAY) & (b.k <= K)][cols].sort_values(["s", "k"]).reset_index(drop=True)
    assert a[["s", "k", "side"]].equals(b[["s", "k", "side"]]), name
    np.testing.assert_allclose(a["stop"], b["stop"], rtol=1e-5)
    # and every earlier day is untouched too
    a_all = h.signals(Features(p, n_liquid=30), days)
    b_all = h.signals(Features(corrupt_after(p, DAY, K), n_liquid=30), days)
    np.testing.assert_array_equal(a_all[a_all.d < DAY][["s", "d", "k", "side"]].to_numpy(),
                                  b_all[b_all.d < DAY][["s", "d", "k", "side"]].to_numpy())


# --- H5's indicators (2026-11-09) -------------------------------------------

@pytest.mark.parametrize("name", ["adx14", "prior_bar_vol_mean20"])
def test_h5_bar_features_do_not_see_the_future(pair, name):
    a, b = (getattr(f, name)[:, DAY, :K + 1] for f in pair)
    np.testing.assert_allclose(a, b, rtol=1e-5, equal_nan=True)


@pytest.mark.parametrize("span", [10, 20, 50])
def test_emas_do_not_see_the_future(pair, span):
    a, b = (f.ema(span)[:, DAY, :K + 1] for f in pair)
    np.testing.assert_allclose(a, b, rtol=1e-5, equal_nan=True)


@pytest.mark.parametrize("name", ["prev_day_high", "first_bar_high"])
def test_h5_day_features_do_not_see_the_future(pair, name):
    a, b = (getattr(f, name)[:, DAY] for f in pair)
    np.testing.assert_array_equal(np.nan_to_num(a), np.nan_to_num(b))


def test_prev_day_high_is_yesterdays_whole_day():
    f = Features(random_walk_panel(S=6, D=40, seed=7), n_liquid=6)
    np.testing.assert_allclose(f.prev_day_high[:, 10], np.nanmax(f.p.high[:, 9, :], axis=1))


def test_prior_bar_volume_mean_excludes_the_bar_itself():
    f = Features(random_walk_panel(S=4, D=30, seed=8), n_liquid=4)
    v = f.p.volume[0].reshape(-1)
    v = v[~np.isnan(v)]
    flat = f.prior_bar_vol_mean20[0].reshape(-1)[~np.isnan(f.p.volume[0].reshape(-1))]
    np.testing.assert_allclose(flat[100], v[80:100].mean(), rtol=1e-4)


def test_h5_is_long_only_with_a_2r_target_and_never_before_0930():
    from islib.hypotheses import H5BullishBreakoutChecklist
    p = random_walk_panel(S=20, D=150, seed=9)
    F = Features(p, n_liquid=20)
    sig = H5BullishBreakoutChecklist().signals(F, np.ones(len(p.dates), bool))
    assert len(sig) > 0
    assert (sig["side"] == 1).all()
    assert sig["k"].between(2, LAST_DECISION_BAR).all()
    assert not sig.duplicated(["s", "d"]).any()
    ref = p.close[sig["s"], sig["d"], sig["k"]]
    np.testing.assert_allclose(sig["target"] - ref, 2 * (ref - sig["stop"]), rtol=1e-5)
    assert (sig["stop"] < ref).all()
