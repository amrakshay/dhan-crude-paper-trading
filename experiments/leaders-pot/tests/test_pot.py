"""Mechanics tests for the leaders pot, on the real extended panel.

Run from experiments/leaders-pot:
  PYTHONDONTWRITEBYTECODE=1 <pullback venv python> -m pytest tests -q

Proves:
  1. pot disabled == V0 to the rupee, trade for trade
  2. the pot holds positions only after a gate-OFF close
  3. the pot is fully liquidated at the open after the first gate-ON close,
     and its cash rejoins the main book that same open
  4. ledger conservation: main cash after the merge == main cash before + pot
     cash; pot episode P&L == sum of its trades' net
  5. until the first post-pot main entry, combined equity == baseline equity +
     pot P&L exactly, so the main book has baseline capital + pot P&L at the flip
  6. no lookahead: scrambling every bar after date X leaves everything up to X
     unchanged; every pot entry fills the session after a gate-OFF weekly close
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lpot.data import load, prep  # noqa: E402
from lpot.engine import Cfg, run, stats  # noqa: E402

V0_FINAL = 7_093_012


@pytest.fixture(scope="module")
def panel():
    return load()


def _run(panel, **kw):
    syms, nifty = panel
    return run({k: v.copy() for k, v in syms.items()}, nifty, Cfg(**kw))


@pytest.fixture(scope="module")
def base(panel):
    return _run(panel)


@pytest.fixture(scope="module")
def pot25(panel):
    return _run(panel, name="pot25", pot_frac=0.25)


def _gate(panel):
    _, nifty = panel
    n = nifty.copy(); n["sma"] = n.close.rolling(200).mean()
    return dict(zip(n.date, n.close > n.sma)), n.date.tolist()


def test_pot_disabled_is_v0(base):
    m = stats(base["trades"], base["curve"])
    assert round(m["final"]) == V0_FINAL
    assert m["n"] == 669
    assert (base["trades"].book == "main").all()
    assert base["daily"].pot_mtm.abs().max() == 0.0


def test_pot_only_after_gate_off_close(panel, pot25):
    gate, cal = _gate(panel)
    prev = {cal[i]: cal[i - 1] for i in range(1, len(cal))}
    d = pot25["daily"]
    held = d[d.n_pot > 0]
    assert len(held) > 0
    assert all(not gate[prev[x]] for x in held.date), "pot held a position the day after a gate-ON close"
    active = d[d.pot_active]
    assert all(not gate[prev[x]] for x in active.date)
    # and while the gate has been ON for 2+ closes the pot holds no capital at all
    on2 = [x for x in d.date if gate[x] and gate[prev[x]]]
    assert d.set_index("date").loc[on2, "pot_mtm"].abs().max() == 0.0


def test_full_liquidation_at_open_after_flip(panel, pot25):
    gate, cal = _gate(panel)
    pos_of = {x: i for i, x in enumerate(cal)}
    d = pot25["daily"].set_index("date")
    ev = pot25["events"]
    liq = ev[ev.kind == "liquidate"]
    assert len(liq) > 0
    assert (liq.stragglers == 0).all()
    for x in liq.date:
        flip_close = cal[pos_of[x] - 1]
        assert gate[flip_close], "liquidation not triggered by a gate-ON close"
        assert d.loc[x, "n_pot"] == 0 and d.loc[x, "pot_cash"] == 0.0 and not d.loc[x, "pot_active"]
    t = pot25["trades"]
    flips = t[(t.book == "pot") & (t.reason == "flip")]
    for x in flips.exit_date:
        assert gate[cal[pos_of[x] - 1]] and x in set(liq.date)


def test_ledger_conservation(pot25):
    ev, t = pot25["events"], pot25["trades"]
    act = ev[ev.kind == "activate"].set_index("episode")
    liq = ev[ev.kind == "liquidate"].set_index("episode")
    pt = t[t.book == "pot"]
    for e, row in liq.iterrows():
        # the pot's episode P&L is exactly the sum of its trades' net (costs included)
        assert row.pot_cash - act.loc[e, "allotted"] == pytest.approx(pt[pt.episode == e].net.sum(), abs=1e-6)
        # and the main book received the whole pot at that open
        assert row.main_cash_after == pytest.approx(row.main_cash_before + row.pot_cash, abs=1e-6)
    # allotment = 25% of combined equity at the signal close, never more than main cash
    assert (act.allotted <= act.requested + 1e-9).all()


def test_main_capital_at_flip_is_baseline_plus_pot_pnl(base, pot25):
    b = base["daily"].set_index("date"); c = pot25["daily"].set_index("date")
    ev, ct = pot25["events"], pot25["trades"]
    first_act = ev.query("kind == 'activate'").iloc[0]
    first_liq = ev.query("kind == 'liquidate'").iloc[0]
    assert first_act.episode == first_liq.episode
    # identical before the pot exists
    pre = c.index[c.index < first_act.date]
    assert np.allclose(c.loc[pre, "equity"], b.loc[pre, "equity"], rtol=0, atol=1e-6)
    # while the pot is live: combined == baseline + pot P&L (marked), every day
    live = c.index[(c.index >= first_act.date) & (c.index < first_liq.date)]
    assert len(live) > 0
    assert np.allclose(c.loc[live, "equity"] - b.loc[live, "equity"],
                       c.loc[live, "pot_mtm"] - first_act.allotted, rtol=0, atol=1e-4)
    # at the flip open, before any main entry: main cash == baseline main cash + pot P&L
    ep_pnl = ct[(ct.book == "pot") & (ct.episode == first_liq.episode)].net.sum()
    assert c.loc[first_liq.date, "main_cash_open"] == pytest.approx(
        b.loc[first_liq.date, "main_cash_open"] + ep_pnl, abs=1e-4)


def test_pot_entries_fill_after_gate_off_weekly_close(panel, pot25):
    gate, cal = _gate(panel)
    pos_of = {x: i for i, x in enumerate(cal)}
    t = pot25["trades"]
    for x in t[t.book == "pot"].entry_date:
        i = pos_of[x]; sig = cal[i - 1]
        assert not gate[sig], "pot entry signalled at a gate-ON close"
        # signal day was the last session of its calendar week
        assert pd.Timestamp(sig).weekday() >= pd.Timestamp(cal[i]).weekday()


def test_no_lookahead_scramble_future(panel, pot25):
    syms, nifty = panel
    X = "2022-06-15"   # inside the 2022 gate-off window, pot active
    rng = np.random.default_rng(7)

    def scramble(d):
        d = d[["date", "open", "high", "low", "close", "volume"]].copy()
        m = d.date > X
        k = rng.uniform(0.3, 3.0, m.sum())
        for col in ("open", "high", "low", "close"):
            d.loc[m, col] = d.loc[m, col].values * k
        d.loc[m, "volume"] = d.loc[m, "volume"].values * rng.uniform(0.1, 10, m.sum())
        return d

    s2 = {k: prep(scramble(v)) for k, v in syms.items()}
    n2 = scramble(nifty)
    r2 = run(s2, n2, Cfg(name="scrambled", pot_frac=0.25))
    a = pot25["daily"].set_index("date"); b = r2["daily"].set_index("date")
    upto = a.index[a.index <= X]
    assert np.allclose(a.loc[upto, "equity"].values, b.loc[upto, "equity"].values, rtol=0, atol=1e-6)
    assert (a.loc[upto, "n_pot"].values == b.loc[upto, "n_pot"].values).all()
    assert a.loc[X, "n_pot"] > 0, "test date should be inside an active pot window"
    ta, tb = pot25["trades"], r2["trades"]
    ea = ta[ta.entry_date <= X][["book", "symbol", "entry_date", "qty"]].sort_values(["book", "symbol", "entry_date"])
    eb = tb[tb.entry_date <= X][["book", "symbol", "entry_date", "qty"]].sort_values(["book", "symbol", "entry_date"])
    assert ea.values.tolist() == eb.values.tolist()
