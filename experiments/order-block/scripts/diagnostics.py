"""The exploratory cuts listed in README.md, development period only.

Each one tests one specific claim from the blog. None of them is the pass bar:
they are many looks at the same data, and any "best" cut found here would be
chosen after the fact (the screener's H3c lesson -- +27 bps on development,
+6 on the holdout).

Writes out/diagnostics_dev.json.
"""
from __future__ import annotations

import json
import time
from dataclasses import replace

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from obl import EXP
from obl import sim as SM
from obl.run import Config, World, summarise

OUT = EXP / "out"
KEYS = ("trades", "trades_per_day", "gross_R", "charges_R", "net_R", "net_R_ci95", "win_rate",
        "years_positive", "net_bps_500k", "median_stop_pct", "median_rr")


def brief(t):
    s = summarise(t)
    return {k: s.get(k) for k in KEYS}


def portfolio(t: pd.DataFrame, account=SM.ACCOUNT, daily_stop=0.02, max_losses=2, leverage=5.0):
    """The blog's own risk rules, applied in time order within each day:
    1% risk per trade (already the sizing), stop for the day at -2% or after
    two losses in a row, gross exposure <= 5x. Equity is NOT compounded --
    risk stays Rs 5,000 -- so the curve reads directly in rupees."""
    t = t.sort_values(["d", "fill_bar", "s_exec"])
    taken = []
    for d, g in t.groupby("d", sort=True):
        pnl, streak, open_ = 0.0, 0, []      # open_: (exit_bar, notional)
        for r in g.itertuples(index=False):
            if pnl <= -daily_stop * account or streak >= max_losses:
                break
            # A loss is only KNOWN once it has exited. Count the exits that
            # happened before this fill; positions still open do not count yet.
            open_ = [x for x in open_ if x[0] >= r.fill_bar]
            if sum(x[1] for x in open_) + r.notional > leverage * account:
                continue
            taken.append(r)
            open_.append((r.exit_bar, r.notional))
            # Simplification, stated: the day's loss limit and the streak are
            # updated in FILL order, not exit order. Between overlapping
            # trades the true order of exits is ignored.
            pnl += r.net_R * account * 0.01
            streak = streak + 1 if r.net_R < 0 else 0
    p = pd.DataFrame(taken)
    if p.empty:
        return {}
    daily = p.groupby("d")["net_R"].sum() * account * 0.01
    eq = daily.cumsum()
    dd = (eq - eq.cummax()).min()
    years = len(np.unique(pd.to_datetime(p["date"]).dt.year))
    return {
        "trades": len(p), "trades_per_day": round(len(p) / p["d"].nunique(), 2),
        "total_pnl_rs": round(float(daily.sum())),
        "pnl_per_year_rs": round(float(daily.sum()) / years),
        "return_per_year_pct_of_5L": round(100 * float(daily.sum()) / years / account, 1),
        "max_drawdown_rs": round(float(dd)),
        "worst_day_rs": round(float(daily.min())),
        "best_day_rs": round(float(daily.max())),
        "days_positive_frac": round(float((daily > 0).mean()), 3),
    }


def main() -> int:
    t0 = time.time()
    w = World(holdout=False)
    base = Config()
    res = {}

    def run(label, cfg):
        t = w.trades(cfg)
        res[label] = brief(t)
        print(f"{label:<28} {res[label]['trades']:>6} trades  gross {res[label]['gross_R']:+.3f}  "
              f"net {res[label]['net_R']:+.3f} R {res[label]['net_R_ci95']}  "
              f"({time.time() - t0:.0f}s)", flush=True)
        return t

    bt = run("baseline (OB1)", base)

    # 1. first retest vs later retests
    anyr = run("any retest", replace(base, first_only=False))
    res["by_retest"] = {str(n): brief(anyr[anyr["retest"].clip(upper=3) == n]) for n in (1, 2, 3)}

    # 2. wait vs chase
    run("chase (market after break)", replace(base, chase=True))

    # 3. the four-point check
    for g in ("align4", "top20", "none"):
        run(f"gate={g}", replace(base, gate=g))

    # 4. exits
    run("target=R1", replace(base, target="R1"))
    run("target=time only", replace(base, target="time"))

    # 5. picks
    run("gate=picks", replace(base, gate="picks"))

    # 6. R:R floor off
    run("no R:R floor", replace(base, rr_min=None))

    # 7. rank checked at marking only
    run("gate at mark only", replace(base, gate_at_mark=True))

    # 7b. SENSITIVITY: the most generous reading of the fill bar (never the rule)
    run("fill-bar stop ignored", replace(base, fill_bar_stop=False))
    run("picks, fill-bar stop ignored", replace(base, gate="picks", fill_bar_stop=False))

    # 8. stop width
    q = pd.qcut(bt["risk_frac_px"], 5)
    res["by_stop_width_quintile"] = {f"{100 * iv.left:.2f}-{100 * iv.right:.2f}%": brief(g)
                                     for iv, g in bt.groupby(q, observed=True)}
    # 9. by exit time-of-entry and by impulse kind
    res["by_fill_hour"] = {str(h): brief(g) for h, g in
                           bt.groupby((9 * 60 + 15 + 5 * bt["fill_bar"]) // 60)}
    res["by_impulse"] = {("range" if k == 1 else "fvg"): brief(g) for k, g in bt.groupby("impulse")}
    # 10. the blog's portfolio rules on the baseline trades
    res["portfolio_baseline"] = portfolio(bt)
    print(json.dumps(res["portfolio_baseline"], indent=1))

    OUT.mkdir(exist_ok=True)
    (OUT / "diagnostics_dev.json").write_text(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
