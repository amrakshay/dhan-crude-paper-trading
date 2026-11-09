"""Round 2 (ROUND2.md), development period only. No holdout switch.

    .venv/bin/python scripts/run_round2.py          -> out/r2_<name>.json, out/r2_S5_index_momentum.json
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import json
from datetime import date, datetime

import numpy as np
import pandas as pd

from islib import panel as PN

from bearlib import OUT
from bearlib import hyp2
from bearlib import stats as St
from bearlib.engine import index_bars, simulate_index
from bearlib.run import Context, _ci, bps, evaluate
from bearlib.universe import Universe

DEV_END = date(2022, 12, 31)
SEBI_BREAK = date(2024, 11, 20)


def log_variant(name, result):
    path = OUT / "variant_log.json"
    log = json.loads(path.read_text()) if path.exists() else []
    log.append({"run": name, "regime": "B", "period": "dev", "round": 2,
                "at": datetime.now().isoformat(timespec="seconds"),
                "net": result.get("net_5bps") or result.get("net_1bps")})
    path.write_text(json.dumps(log, indent=1))


def s5(ctx: Context, period: np.ndarray, on: np.ndarray, eps) -> dict:
    """Gao et al.: first half-hour (prev close -> 10:00) < 0 => short NIFTY 15:00 -> 15:30."""
    c = ctx.ib["close"]
    last = np.array([r[np.flatnonzero(~np.isnan(r))[-1]] if np.isfinite(r).any() else np.nan for r in c])
    prev = np.concatenate([[np.nan], last[:-1]])
    first = c[:, 8] / prev - 1.0
    days = np.arange(len(ctx.p.dates))
    down = period & (first < 0)
    up = period & (first > 0)

    def run(mask, side, slip=0.0001):
        sig = pd.DataFrame({"d": days[mask], "k": 68, "side": side})
        return simulate_index(ctx.p, ctx.ib, sig, slippage=slip, exit_bar=74)

    t = run(down, -1)
    t = t[t["net_ret"].notna()]
    out = {"signals": int(down.sum()), "trades": int(len(t)),
           "gross_bps": bps(t["gross_ret"].mean()), "cost_bps": bps(t["cost_frac"].mean())}
    for slip in (0.0001, 0.0002, 0.0005):
        tt = run(down, -1, slip)
        out[f"net_{int(slip * 1e4)}bps"] = _ci(St.day_bootstrap(tt))
    out["drop_best_10_days_bps"] = St.drop_best_days(t, 10)
    on_mask = np.isin(t["d"], days[on])
    out["regime_on"] = {"trades": int(on_mask.sum()), "net_bps": bps(t.loc[on_mask, "net_ret"].mean())}
    out["regime_off"] = {"trades": int((~on_mask).sum()), "net_bps": bps(t.loc[~on_mask, "net_ret"].mean())}
    yr = pd.to_datetime(t["date"]).dt.year
    out["by_year_net_bps"] = (1e4 * t.groupby(yr)["net_ret"].mean()).round(2).to_dict()
    out["episodes"] = St.episode_table(t, eps)
    out["episodes_positive"] = f"{sum(e['positive'] for e in out['episodes'])}/{len(out['episodes'])}"
    # Controls.
    rng = np.random.default_rng(0)
    pool = []
    for _ in range(5):
        pick = np.zeros(len(days), bool)
        pick[rng.choice(days[period], size=int(down.sum()), replace=False)] = True
        pool.append(run(pick, -1))
    rnd = pd.concat(pool, ignore_index=True)
    mirror = run(down, +1)
    every = run(period, -1)
    longmom = run(up, +1)
    out["controls"] = {
        "random_day_short": {"net": _ci(St.day_bootstrap(rnd)), "diff": St.diff_bootstrap(t, rnd)},
        "mirror_long_same_days": {"net": _ci(St.day_bootstrap(mirror)), "diff": St.diff_bootstrap(t, mirror)},
        "short_every_day": {"net": _ci(St.day_bootstrap(every)), "diff": St.diff_bootstrap(t, every)},
    }
    out["diagnostic_long_side_first_half_up"] = _ci(St.day_bootstrap(longmom))
    out["beats_all_controls"] = all(v["diff"]["ci95_bps"][0] > 0 for v in out["controls"].values())
    return out


def main():
    p = PN.load()
    U = Universe(p)
    ctx = Context(p, U, index_bars(p))
    dates = np.array(p.dates)
    period = dates <= DEV_END
    on = U.regime_b & period
    eps = json.loads((OUT / "regime.json").read_text())["rules"]["B_breadth"]["episodes_dev"]
    summary = {}
    for h in hyp2.ROUND2:
        r = evaluate(ctx, h.name, on, period, eps)
        (OUT / f"r2_{h.name}.json").write_text(json.dumps(r, indent=1, default=str))
        log_variant(h.name, r)
        if "net_5bps" not in r:
            summary[h.name] = {"signals": r["signals"]}
            continue
        pf = r["portfolio"]["risk_0.005"]
        summary[h.name] = {
            "trades": r["summary_5bps"]["taken"], "gross": r["summary_5bps"]["gross_mean_bps"],
            "net5": r["net_5bps"], "drop10": r["drop_best_10_days_bps"], "no_covid": r["without_2020_02_05_bps"],
            "episodes": r["episodes_positive"], "beats_all": r["beats_all_controls"],
            "stop_pct": r["stop_distance_median_pct"], "cost_R": r["cost_in_R_median"],
            "ctl": {k: v["diff"]["ci95_bps"] for k, v in r["controls"].items()},
            "months": [pf["monthly_bar"][k] for k in ("share_positive", "worst_month", "longest_losing_streak")],
        }
        print(h.name, json.dumps(summary[h.name], default=str), flush=True)
    r5 = s5(ctx, period, on, eps)
    (OUT / "r2_S5_index_momentum.json").write_text(json.dumps(r5, indent=1, default=str))
    log_variant("S5_index_momentum", r5)
    print("S5", json.dumps({k: v for k, v in r5.items() if k != "episodes"}, default=str))


if __name__ == "__main__":
    main()
