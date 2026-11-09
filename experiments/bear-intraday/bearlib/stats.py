"""Statistics beyond islib.stats: episodes, concentration, month blocks."""
from __future__ import annotations

import numpy as np
import pandas as pd

from islib.stats import day_bootstrap, diff_bootstrap, summary, taken  # noqa: F401


def episode_table(t: pd.DataFrame, episodes: list[dict]) -> list[dict]:
    """Mean net per trade inside each episode's date span. No trades -> not positive."""
    t = taken(t).assign(date=lambda x: pd.to_datetime(x["date"]))
    rows = []
    for e in episodes:
        m = (t["date"] >= e["start"]) & (t["date"] <= e["end"])
        sub = t[m]
        mean = float(sub["net_ret"].mean()) if len(sub) else float("nan")
        rows.append({"start": e["start"], "end": e["end"], "trades": int(len(sub)),
                     "net_bps": round(1e4 * mean, 2) if len(sub) else None,
                     "positive": bool(len(sub) and mean > 0)})
    return rows


def drop_best_days(t: pd.DataFrame, n: int = 10) -> float:
    t = taken(t)
    by = t.groupby("d")["net_ret"].sum().sort_values(ascending=False)
    keep = t[~t["d"].isin(by.index[:n])]
    return round(1e4 * float(keep["net_ret"].mean()), 2) if len(keep) else float("nan")


def month_bootstrap(monthly: pd.Series, n_boot: int = 4000, seed: int = 0) -> dict:
    """Resample whole calendar months (the operator's unit) with replacement."""
    v = monthly.to_numpy(dtype=float)
    if len(v) == 0:
        return {}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(v), size=(n_boot, len(v)))
    means = v[idx].mean(axis=1)
    return {"mean_month": round(float(v.mean()), 5),
            "ci95": [round(float(np.quantile(means, q)), 5) for q in (0.025, 0.975)],
            "p_le_zero": round(float((means <= 0).mean()), 4)}


def pooled(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Pool control reps; day ids stay, so the day bootstrap still groups by day."""
    return pd.concat(frames, ignore_index=True)
