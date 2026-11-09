"""One hypothesis against the pass bar (README) and every control (METHOD.md)."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from islib.panel import FIRST_DECISION_BAR, Panel

from . import engine as E
from . import portfolio as P
from . import stats as St
from .hyp import CONTROLS, HYPOTHESES, UNIVERSE_OF
from .universe import Universe

SLIPS = (0.0002, 0.0005, 0.0010)
REPS = 5
COVID = (date(2020, 2, 1), date(2020, 5, 31))


def index_bars_from_panel(p: Panel) -> dict:
    o, c = p.index_open.astype(float), p.index_close.astype(float)
    return {"open": o, "close": c, "high": np.fmax(o, c), "low": np.fmin(o, c)}


def index_short_signals(days: np.ndarray) -> pd.DataFrame:
    d = np.flatnonzero(days)
    return pd.DataFrame({"d": d, "k": FIRST_DECISION_BAR, "side": -1})


def bps(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(1e4 * float(x), 2)


def _ci(boot):
    return {"mean_bps": bps(boot["mean"]), "ci95_bps": [bps(v) for v in boot["ci95"]],
            "days": boot["days"]}


class Context:
    def __init__(self, p: Panel, U: Universe | None = None, ib: dict | None = None):
        self.p = p
        self.U = U or Universe(p)
        self.F = self.U.F
        self.locks = E.lock_masks(p, self.F)
        self.ib = ib if ib is not None else index_bars_from_panel(p)
        self.halt = E.market_halt(self.ib)

    def names(self, hyp_name: str) -> np.ndarray:
        return self.U.shortlist if UNIVERSE_OF[hyp_name] == "shortlist" else self.U.eligible

    def sim(self, sig, slip=0.0005):
        return E.simulate(self.p, self.F, sig, E.Exec(slippage=slip), locks=self.locks, halt=self.halt)


def _months(ctx: Context, period: np.ndarray) -> pd.PeriodIndex:
    idx = np.flatnonzero(period)
    return pd.period_range(pd.Timestamp(ctx.p.dates[idx[0]]), pd.Timestamp(ctx.p.dates[idx[-1]]), freq="M")


def evaluate(ctx: Context, hyp_name: str, on_days: np.ndarray, period: np.ndarray,
             episodes: list[dict] | None = None, seed: int = 0, controls: bool = True,
             portfolio: bool = True) -> dict:
    """on_days: regime-on days inside the period; period: every session of it."""
    h = HYPOTHESES[hyp_name]
    off_days = period & ~on_days
    names = ctx.names(hyp_name)
    sig = h.signals(ctx.U, on_days, names)
    out: dict = {"hypothesis": hyp_name, "signals": int(len(sig))}
    if sig.empty:
        return out
    t5 = ctx.sim(sig, 0.0005)
    out["summary_5bps"] = St.summary(t5)
    out["net_5bps"] = _ci(St.day_bootstrap(t5, seed=seed))
    out["net_by_slippage_bps"] = {f"{int(s*1e4)}bps": bps(St.taken(ctx.sim(sig, s))["net_ret"].mean())
                                  for s in SLIPS}
    tk = St.taken(t5)
    out["stop_distance_median_pct"] = round(100 * float(tk["risk_frac"].median()), 3)
    out["cost_in_R_median"] = round(float((tk["cost_frac"] + 2 * 0.0005).div(tk["risk_frac"]).median()), 3)
    out["locked_exits"] = int(tk["exit_locked"].sum())
    out["drop_best_10_days_bps"] = St.drop_best_days(t5, 10)
    dts = pd.to_datetime(tk["date"]).dt.date
    no_covid = tk[~((dts >= COVID[0]) & (dts <= COVID[1]))]
    out["without_2020_02_05_bps"] = bps(no_covid["net_ret"].mean())
    if episodes is not None:
        ep = St.episode_table(t5, episodes)
        out["episodes"] = ep
        out["episodes_positive"] = f"{sum(e['positive'] for e in ep)}/{len(ep)}"

    if controls:
        rng = np.random.default_rng(seed)
        ctl = {}
        for name, fn in CONTROLS.items():
            reps = [ctx.sim(fn(ctx.U, sig, rng), 0.0005) for _ in range(REPS)]
            pool = St.pooled(reps)
            ctl[name] = {"net": _ci(St.day_bootstrap(pool, seed=seed)),
                         "diff": St.diff_bootstrap(t5, pool, seed=seed)}
        # The shortlist on its own: same trigger on 5 random eligible names.
        if UNIVERSE_OF[hyp_name] == "shortlist":
            reps = []
            for _ in range(REPS):
                rs = h.signals(ctx.U, on_days, ctx.U.random_shortlist(rng))
                if not rs.empty:
                    reps.append(ctx.sim(rs, 0.0005))
            pool = St.pooled(reps)
            ctl["random_shortlist"] = {"net": _ci(St.day_bootstrap(pool, seed=seed)),
                                       "diff": St.diff_bootstrap(t5, pool, seed=seed),
                                       "trades": int(len(St.taken(pool)) / REPS)}
        if off_days.any():
            so = h.signals(ctx.U, off_days, names)
            if not so.empty:
                to = ctx.sim(so, 0.0005)
                ctl["regime_off"] = {"net": _ci(St.day_bootstrap(to, seed=seed)),
                                     "diff": St.diff_bootstrap(t5, to, seed=seed),
                                     "trades": int(len(St.taken(to)))}
        it = E.simulate_index(ctx.p, ctx.ib, index_short_signals(on_days), slippage=0.0001)
        ctl["index_short"] = {"net": _ci(St.day_bootstrap(it, seed=seed)),
                              "diff": St.diff_bootstrap(t5, it, seed=seed)}
        out["controls"] = ctl
        out["beats_all_controls"] = all(c["diff"]["ci95_bps"][0] > 0 for c in ctl.values()
                                        if c is not ctl.get("random_shortlist"))

    if portfolio:
        months = _months(ctx, period)
        sessions = int(period.sum())
        pf = {}
        for risk in (0.005, 0.01):
            book = P.run_stocks(t5, risk=risk)
            st = P.book_stats(book, months, sessions)
            st["month_bootstrap"] = St.month_bootstrap(P.monthly(book, months))
            pf[f"risk_{risk}"] = st
        out["portfolio"] = pf
    return out


def evaluate_index_short(ctx: Context, on_days: np.ndarray, period: np.ndarray,
                         episodes: list[dict] | None = None, seed: int = 0) -> dict:
    """The key control as a strategy in its own right (bars 1, 3, 4, 5)."""
    out = {}
    for slip in (0.0001, 0.0002, 0.0005):
        it = E.simulate_index(ctx.p, ctx.ib, index_short_signals(on_days), slippage=slip)
        out[f"net_{int(slip*1e4)}bps"] = _ci(St.day_bootstrap(it, seed=seed))
    it = E.simulate_index(ctx.p, ctx.ib, index_short_signals(on_days), slippage=0.0001)
    out["gross_bps"] = bps(it["gross_ret"].mean())
    out["cost_bps"] = bps(it["cost_frac"].mean())
    out["drop_best_10_days_bps"] = St.drop_best_days(it, 10)
    dts = pd.to_datetime(it["date"]).dt.date
    out["without_2020_02_05_bps"] = bps(it[~((dts >= COVID[0]) & (dts <= COVID[1]))]["net_ret"].mean())
    if episodes is not None:
        ep = St.episode_table(it, episodes)
        out["episodes"] = ep
        out["episodes_positive"] = f"{sum(e['positive'] for e in ep)}/{len(ep)}"
    yr = pd.to_datetime(it["date"]).dt.year
    out["by_year_bps"] = (1e4 * it.groupby(yr)["net_ret"].mean()).round(2).to_dict()
    off = period & ~on_days
    io = E.simulate_index(ctx.p, ctx.ib, index_short_signals(off), slippage=0.0001)
    out["regime_off_net"] = _ci(St.day_bootstrap(io, seed=seed))
    out["diff_vs_regime_off"] = St.diff_bootstrap(it, io, seed=seed)
    months = _months(ctx, period)
    pf = {}
    for one in (False, True):
        book = P.run_index(it, one_lot=one)
        st = P.book_stats(book, months, int(period.sum()))
        st["month_bootstrap"] = St.month_bootstrap(P.monthly(book, months))
        pf["one_lot" if one else "cap_5x"] = st
    out["portfolio"] = pf
    # The left tail: worst days and the big up-days it met.
    out["worst_days_bps"] = {str(r.date): round(1e4 * r.net_ret, 1) for r in it.nsmallest(5, "net_ret").itertuples()}
    return out
