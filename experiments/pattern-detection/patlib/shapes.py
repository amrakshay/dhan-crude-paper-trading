"""Shape measurements shared by the detectors.

These are the bits that turn a sentence in a trading book into a number:
"U-shaped, not V-shaped", "volume trends downward", "each pullback smaller
than the last". Keeping them here means one definition per phrase, and one
place to argue about it.
"""
from __future__ import annotations

import numpy as np

from .indicators import linreg


def normalise(y: np.ndarray) -> np.ndarray:
    y = np.asarray(y, dtype=float)
    lo, hi = y.min(), y.max()
    return (y - lo) / (hi - lo) if hi > lo else np.zeros_like(y)


def time_in_bottom_third(lows: np.ndarray) -> float:
    """Fraction of bars whose low sits in the lowest third of the range.

    The workhorse U-vs-V test. For an ideal parabola this is ~0.58; for an
    ideal V (two straight lines) it is ~0.33. A cup that spends a third of
    its life at the bottom is rounded; one that spends a tenth is a spike.
    """
    lo, hi = float(lows.min()), float(lows.max())
    if hi <= lo:
        return 1.0
    return float((lows <= lo + (hi - lo) / 3.0).mean())


def u_vs_v_fit(lows: np.ndarray) -> float:
    """Does a parabola explain the shape better than a V?

    Fits both a quadratic and a symmetric V (|x - vertex|) to the normalised
    lows and returns sse_v / (sse_u + sse_v): above 0.5 means the U wins.
    Scale-free, so it survives being handed a 40-rupee stock or a 4,000-one.
    """
    y = normalise(lows)
    n = len(y)
    if n < 5:
        return 0.5
    x = np.linspace(0.0, 1.0, n)
    # quadratic
    A = np.vstack([x ** 2, x, np.ones(n)]).T
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    sse_u = float(((A @ coef - y) ** 2).sum())
    # best symmetric V, vertex searched over the interior
    best = np.inf
    for v in np.linspace(0.15, 0.85, 29):
        B = np.vstack([np.abs(x - v), np.ones(n)]).T
        cf, *_ = np.linalg.lstsq(B, y, rcond=None)
        best = min(best, float(((B @ cf - y) ** 2).sum()))
    tot = sse_u + best
    return 0.5 if tot <= 0 else float(best / tot)


def max_single_leg_fraction(lows: np.ndarray, highs: np.ndarray) -> float:
    """Largest uninterrupted one-way move, as a fraction of the total range.

    A V-bottom is one long leg down and one long leg up; a rounded bottom is
    made of many short ones.
    """
    rng = float(highs.max() - lows.min())
    if rng <= 0:
        return 1.0
    c = (highs + lows) / 2.0
    d = np.diff(c)
    best = run = 0.0
    sign = 0
    for step in d:
        s = 1 if step > 0 else (-1 if step < 0 else sign)
        if s == sign:
            run += step
        else:
            sign, run = s, step
        best = max(best, abs(run))
    return float(best / rng)


def volume_trend(volume: np.ndarray) -> float:
    """Slope of a least-squares fit to volume, normalised by mean volume.

    Negative means volume is receding across the formation, which every one
    of these patterns wants. Reported as 'change per bar as a fraction of
    average volume' so the number is comparable across symbols.
    """
    v = np.asarray(volume, dtype=float)
    if len(v) < 3 or v.mean() <= 0:
        return 0.0
    slope, _, _ = linreg(np.arange(len(v), dtype=float), v)
    return float(slope / v.mean())


def volume_dryup(volume: np.ndarray, reference: np.ndarray) -> float:
    """Average volume over a window against a reference window. <1 is dry."""
    v, r = np.asarray(volume, float), np.asarray(reference, float)
    if len(v) == 0 or len(r) == 0 or r.mean() <= 0:
        return 1.0
    return float(v.mean() / r.mean())


def channel_fill(high: np.ndarray, low: np.ndarray, upper: np.ndarray,
                 lower: np.ndarray, slices: int = 5) -> float:
    """Does price TRAVERSE the channel, or drift along one edge?

    Bulkowski's "price must cross the pattern from side to side, filling the
    triangle with price movement, not white space". The obvious reading --
    average bar height over channel width -- is wrong: it measures how wide
    individual candles are, which is a property of the instrument, not of
    the pattern. What the guideline is about is COVERAGE, so this cuts the
    pattern into vertical slices and asks what fraction of the channel each
    slice's price range spans, then averages.

    A triangle price crosses repeatedly scores ~0.5-0.9. A channel price
    hugs the top of scores ~0.2.
    """
    n = len(high)
    if n < slices * 2:
        slices = max(1, n // 2)
    out = []
    for k in range(slices):
        a = k * n // slices
        b = max(a + 1, (k + 1) * n // slices)
        w = float(np.nanmean(upper[a:b] - lower[a:b]))
        if not np.isfinite(w) or w <= 0:
            continue
        out.append(float(high[a:b].max() - low[a:b].min()) / w)
    return float(np.mean(out)) if out else 0.0
