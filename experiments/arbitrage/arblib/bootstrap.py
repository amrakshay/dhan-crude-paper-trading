"""Block bootstrap, for a series whose observations are not independent.

An ordinary bootstrap resamples single days and destroys autocorrelation,
which makes a strategy's returns look more independent -- and therefore more
significant -- than they are. A market-neutral book's returns are strongly
autocorrelated by construction: the same positions are open for days, so
consecutive days share a cause.

`stationary_bootstrap` (Politis and Romano) resamples blocks of geometrically
distributed length, which preserves short-range dependence while still
producing a valid resample. Mean block length is set from the strategy's own
mean holding period rather than picked, because that is the timescale the
dependence actually lives on.
"""
from __future__ import annotations

import numpy as np


def stationary_bootstrap(x: np.ndarray, n_boot: int, mean_block: float,
                         rng=None) -> np.ndarray:
    """`n_boot` resamples of `x`, each the same length. Returns (n_boot, len(x))."""
    rng = rng or np.random.default_rng(20260920)
    x = np.asarray(x, float)
    n = len(x)
    if n == 0:
        return np.zeros((n_boot, 0))
    p = 1.0 / max(1.0, mean_block)
    out = np.empty((n_boot, n))
    for b in range(n_boot):
        idx = np.empty(n, dtype=int)
        i = rng.integers(n)
        for t in range(n):
            idx[t] = i
            if rng.random() < p:
                i = rng.integers(n)
            else:
                i = (i + 1) % n
        out[b] = x[idx]
    return out


def mean_ci(x: np.ndarray, n_boot: int = 2000, mean_block: float = 10.0,
            level: float = 0.95, rng=None) -> dict:
    """Confidence interval for the MEAN of a dependent series.

    This is the test that matters for an excess-return series: is the mean
    distinguishable from zero once the dependence is accounted for? A CI that
    straddles zero means the strategy has not been shown to work, whatever the
    point estimate says.
    """
    x = np.asarray(x, float)
    boots = stationary_bootstrap(x, n_boot, mean_block, rng).mean(axis=1)
    lo, hi = np.quantile(boots, [(1 - level) / 2, 1 - (1 - level) / 2])
    return {"mean": float(x.mean()), "lo": float(lo), "hi": float(hi),
            "level": level, "n_boot": n_boot, "mean_block": mean_block,
            "p_two_sided": float(2 * min((boots <= 0).mean(), (boots >= 0).mean())),
            "excludes_zero": bool(lo > 0 or hi < 0)}


def trade_bootstrap(net_pnls: np.ndarray, n_boot: int = 5000, rng=None) -> dict:
    """Ordinary bootstrap over TRADES, which are closer to independent than
    days are -- different pairs, different dates. Reported alongside the
    block bootstrap over days because the two can disagree, and when they do
    the day-level one is the conservative answer."""
    rng = rng or np.random.default_rng(20260921)
    x = np.asarray(net_pnls, float)
    if len(x) == 0:
        return {"mean": 0.0, "lo": 0.0, "hi": 0.0, "excludes_zero": False}
    boots = rng.choice(x, size=(n_boot, len(x)), replace=True).mean(axis=1)
    lo, hi = np.quantile(boots, [0.025, 0.975])
    return {"mean": float(x.mean()), "lo": float(lo), "hi": float(hi),
            "n_boot": n_boot,
            "p_two_sided": float(2 * min((boots <= 0).mean(), (boots >= 0).mean())),
            "excludes_zero": bool(lo > 0 or hi < 0)}
