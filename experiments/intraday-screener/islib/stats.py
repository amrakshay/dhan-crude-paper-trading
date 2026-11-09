"""Summaries and the day-block bootstrap.

Trades on the same day are not independent -- a market-wide move hits every
breakout at once -- so uncertainty is estimated by resampling whole DAYS,
not trades. Treating 40,000 trades as 40,000 independent draws would shrink
the confidence interval by roughly the square root of trades-per-day and
manufacture significance.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def taken(t: pd.DataFrame) -> pd.DataFrame:
    return t[t["net_ret"].notna()]


def day_bootstrap(t: pd.DataFrame, col: str = "net_ret", n_boot: int = 2000, seed: int = 0):
    """Mean per trade, with a 95% CI from resampling days with replacement."""
    t = taken(t)
    by_day = t.groupby("d")[col].agg(["sum", "count"])
    sums, counts = by_day["sum"].to_numpy(), by_day["count"].to_numpy()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(sums), size=(n_boot, len(sums)))
    means = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    return {
        "mean": float(sums.sum() / counts.sum()),
        "ci95": [float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))],
        "p_le_zero": float((means <= 0).mean()),
        "days": int(len(sums)),
    }


def summary(t: pd.DataFrame) -> dict:
    all_ = len(t)
    skipped = t.loc[t["net_ret"].isna(), "exit_reason"].value_counts().to_dict()
    t = taken(t)
    if t.empty:
        return {"signals": all_, "taken": 0}
    yr = pd.to_datetime(t["date"]).dt.year
    return {
        "signals": all_,
        "taken": len(t),
        "not_taken": {k: int(v) for k, v in skipped.items()},
        "trades_per_day": round(len(t) / t["d"].nunique(), 2),
        "gross_mean_bps": round(1e4 * t["gross_ret"].mean(), 2),
        "cost_mean_bps": round(1e4 * t["cost_frac"].mean(), 2),
        "net_mean_bps": round(1e4 * t["net_ret"].mean(), 2),
        "net_median_bps": round(1e4 * t["net_ret"].median(), 2),
        "win_rate_net": round(float((t["net_ret"] > 0).mean()), 4),
        "avg_r": round(float(t["r_multiple"].mean()), 3),
        "exit_mix": t["exit_reason"].value_counts(normalize=True).round(3).to_dict(),
        "by_side_net_bps": (1e4 * t.groupby("side")["net_ret"].mean()).round(2).to_dict(),
        "by_year_net_bps": (1e4 * t.groupby(yr)["net_ret"].mean()).round(2).to_dict(),
        "by_year_trades": t.groupby(yr).size().to_dict(),
        "years_positive": int((t.groupby(yr)["net_ret"].mean() > 0).sum()),
        "years": int(yr.nunique()),
        "mfe_q_bps": (1e4 * t["mfe"].quantile([0.25, 0.5, 0.75, 0.9])).round(1).to_dict(),
        "mae_q_bps": (1e4 * t["mae"].quantile([0.1, 0.25, 0.5, 0.75])).round(1).to_dict(),
    }


def diff_bootstrap(a: pd.DataFrame, b: pd.DataFrame, n_boot=2000, seed=0) -> dict:
    """Mean per trade of a minus b, resampling the SAME days for both."""
    a, b = taken(a), taken(b)
    days = np.union1d(a["d"].unique(), b["d"].unique())
    pos = {d: i for i, d in enumerate(days)}

    def per_day(t):
        g = t.groupby("d")["net_ret"].agg(["sum", "count"])
        s = np.zeros(len(days)); c = np.zeros(len(days))
        idx = g.index.map(pos).to_numpy()
        s[idx], c[idx] = g["sum"], g["count"]
        return s, c

    sa, ca = per_day(a)
    sb, cb = per_day(b)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(days), size=(n_boot, len(days)))
    diffs = sa[idx].sum(1) / ca[idx].sum(1) - sb[idx].sum(1) / cb[idx].sum(1)
    point = sa.sum() / ca.sum() - sb.sum() / cb.sum()
    return {"diff_bps": round(float(1e4 * point), 2),
            "ci95_bps": [round(float(1e4 * np.quantile(diffs, q)), 2) for q in (0.025, 0.975)]}
