"""Causal indicators. Every value at index i uses bars 0..i only."""
from __future__ import annotations

import numpy as np

from .bars import Bars


def true_range(bars: Bars) -> np.ndarray:
    h, l, c = bars.high, bars.low, bars.close
    prev = np.empty_like(c)
    prev[0] = c[0]
    prev[1:] = c[:-1]
    return np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))


def atr(bars: Bars, period: int = 14) -> np.ndarray:
    """Wilder's ATR. Seeded with a simple mean of the first `period` ranges."""
    tr = true_range(bars)
    n = len(tr)
    out = np.full(n, np.nan)
    if n == 0:
        return out
    p = min(period, n)
    out[p - 1] = tr[:p].mean()
    for i in range(p, n):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    # Before the seed there is no ATR; back-fill with the seed so callers that
    # index early bars get something finite rather than a nan that silently
    # poisons a comparison.
    out[: p - 1] = out[p - 1]
    return out


def sma(x: np.ndarray, period: int) -> np.ndarray:
    n = len(x)
    out = np.full(n, np.nan)
    if n == 0:
        return out
    cs = np.concatenate([[0.0], np.cumsum(x)])
    for i in range(n):
        a = max(0, i - period + 1)
        out[i] = (cs[i + 1] - cs[a]) / (i + 1 - a)
    return out


def rolling_max(x: np.ndarray, period: int) -> np.ndarray:
    return np.array([x[max(0, i - period + 1): i + 1].max() for i in range(len(x))])


def rolling_min(x: np.ndarray, period: int) -> np.ndarray:
    return np.array([x[max(0, i - period + 1): i + 1].min() for i in range(len(x))])


def linreg(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    """Least-squares fit. Returns (slope, intercept, r2)."""
    if len(x) < 2:
        return 0.0, float(y[0]) if len(y) else 0.0, 0.0
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    xm, ym = x.mean(), y.mean()
    sxx = ((x - xm) ** 2).sum()
    if sxx == 0:
        return 0.0, ym, 0.0
    slope = ((x - xm) * (y - ym)).sum() / sxx
    intercept = ym - slope * xm
    resid = y - (slope * x + intercept)
    sst = ((y - ym) ** 2).sum()
    r2 = 1.0 - (resid ** 2).sum() / sst if sst > 0 else 1.0
    return float(slope), float(intercept), float(r2)
