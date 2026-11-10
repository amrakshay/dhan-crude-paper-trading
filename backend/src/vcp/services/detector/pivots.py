"""Swing pivots -- the substrate every pattern detector is built on.

Why a zigzag and not fractals: a fractal (a high with N lower highs either
side) has a fixed time width, so on a quiet stock it marks noise as structure
and on a violent one it misses the turn that matters. A zigzag is defined by
PRICE travelled, which is what the patterns are actually described in.

Why ATR-scaled and not a fixed percentage: 5% is a shrug for one name and a
crash for another. The threshold here is `k x ATR14 / price`, floored at a
minimum percentage so a dead-flat stock does not produce a pivot per bar.

Causality: pivot k is confirmed only at `confirm_idx`, the bar on which price
had retraced far enough from the extreme. A detector that treats `idx` as the
moment it knew about the pivot is peeking. Everything downstream uses
`confirm_idx` for that question, and `idx` only for geometry.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .bars import Bars
from .indicators import atr


@dataclass(frozen=True)
class Pivot:
    idx: int          # bar index of the extreme itself
    price: float
    kind: int         # +1 = swing high, -1 = swing low
    confirm_idx: int  # bar index at which the reversal was large enough to confirm
    provisional: bool = False

    @property
    def is_high(self) -> bool:
        return self.kind > 0


def swing_threshold(bars: Bars, k: float = 2.0, min_pct: float = 0.03,
                    max_pct: float = 0.15, atr_period: int = 14) -> np.ndarray:
    """Fractional retracement needed to confirm a reversal, per bar.

    `max_pct` is a CEILING and it matters more than it looks. Scaling the
    threshold by ATR is right until volatility spikes, at which point the
    requirement can reach a quarter of the price and the zigzag simply stops
    marking swings -- exactly over the crash-and-recover stretch where the
    interesting bases are built. Capping it at 15% costs a few spurious
    pivots in violent markets and buys back every pivot in March 2020.
    """
    a = atr(bars, atr_period)
    with np.errstate(divide="ignore", invalid="ignore"):
        frac = k * a / np.where(bars.close > 0, bars.close, np.nan)
    frac = np.nan_to_num(frac, nan=min_pct)
    return np.clip(frac, min_pct, max_pct)


def zigzag(bars: Bars, k: float = 2.0, min_pct: float = 0.03,
           max_pct: float = 0.15, include_provisional: bool = True) -> list[Pivot]:
    """Confirmed swing pivots, oldest first, alternating high/low.

    The final element may be provisional: the running extreme since the last
    confirmed pivot, not yet retraced from. For a base that is still forming
    -- which is the only kind worth trading -- that provisional pivot IS the
    pattern's right edge, so it is returned rather than discarded, flagged.
    """
    n = len(bars)
    if n < 3:
        return []
    thr = swing_threshold(bars, k, min_pct, max_pct)
    h, l = bars.high, bars.low

    pivots: list[Pivot] = []
    # Seed: run forward from bar 0 until price has moved `thr` from the open
    # extremes in one direction or the other.
    hi_i, hi_p = 0, h[0]
    lo_i, lo_p = 0, l[0]
    direction = 0
    start = 0
    for i in range(1, n):
        if h[i] > hi_p:
            hi_i, hi_p = i, h[i]
        if l[i] < lo_p:
            lo_i, lo_p = i, l[i]
        if (hi_p - l[i]) / hi_p >= thr[hi_i] and hi_i > lo_i:
            direction, ext_i, ext_p, start = -1, hi_i, hi_p, i
            pivots.append(Pivot(hi_i, hi_p, +1, i))
            ext_i, ext_p = i, l[i]
            break
        if (h[i] - lo_p) / lo_p >= thr[lo_i] and lo_i > hi_i:
            direction, start = +1, i
            pivots.append(Pivot(lo_i, lo_p, -1, i))
            ext_i, ext_p = i, h[i]
            break
    else:
        return []

    for i in range(start + 1, n):
        if direction > 0:                       # tracking a running HIGH
            if h[i] > ext_p:
                ext_i, ext_p = i, h[i]
            elif (ext_p - l[i]) / ext_p >= thr[ext_i]:
                pivots.append(Pivot(ext_i, ext_p, +1, i))
                direction, ext_i, ext_p = -1, i, l[i]
        else:                                   # tracking a running LOW
            if l[i] < ext_p:
                ext_i, ext_p = i, l[i]
            elif (h[i] - ext_p) / ext_p >= thr[ext_i]:
                pivots.append(Pivot(ext_i, ext_p, -1, i))
                direction, ext_i, ext_p = +1, i, h[i]

    if include_provisional and (not pivots or pivots[-1].idx != ext_i):
        pivots.append(Pivot(ext_i, ext_p, direction, n - 1, provisional=True))
    return pivots


def fractal_pivots(bars: Bars, width: int = 5) -> list[Pivot]:
    """Fixed-width fractals. Kept for comparison against the zigzag, and used
    by nothing in the detectors."""
    n = len(bars)
    out: list[Pivot] = []
    for i in range(width, n - width):
        win_h = bars.high[i - width: i + width + 1]
        win_l = bars.low[i - width: i + width + 1]
        if bars.high[i] == win_h.max() and (win_h.argmax() == width):
            out.append(Pivot(i, float(bars.high[i]), +1, i + width))
        elif bars.low[i] == win_l.min() and (win_l.argmin() == width):
            out.append(Pivot(i, float(bars.low[i]), -1, i + width))
    return out
