"""Minervini's Trend Template -- context, not a pattern.

None of the three patterns REQUIRES an uptrend to exist geometrically. All
three mean something different depending on whether the stock is in one, so
the template is computed alongside every detection and never inside it. The
detectors stay honest about geometry; the caller decides what to do with a
textbook cup in a downtrend.

Criterion 8 (IBD Relative Strength >= 70) needs a cross-sectional ranking and
so cannot be computed from one symbol's candles. `rs_rating` is therefore
optional and the template reports how many of the 7 computable criteria pass.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np

from .bars import Bars
from .indicators import sma


@dataclass
class TrendTemplate:
    passed: int
    total: int
    above_150_200: bool
    ma150_above_200: bool
    ma200_rising_1m: bool
    ma50_above_150_200: bool
    above_50: bool
    above_52w_low_pct: float
    within_52w_high_pct: float
    pct_from_52w_low_ok: bool
    pct_to_52w_high_ok: bool

    @property
    def ok(self) -> bool:
        return self.passed == self.total

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ok"] = self.ok
        return d


def trend_template(bars: Bars, i: int | None = None,
                   low_margin: float = 0.30, high_margin: float = 0.25) -> TrendTemplate:
    """Evaluate the 7 candle-computable criteria at bar `i` (default: last)."""
    i = len(bars) - 1 if i is None else i
    c = bars.close[: i + 1]
    ma50, ma150, ma200 = sma(c, 50), sma(c, 150), sma(c, 200)
    px = c[i]
    win = c[max(0, i - 251):i + 1]
    lo52, hi52 = float(win.min()), float(win.max())

    j = max(0, i - 21)  # "trending up for at least 1 month"
    above_150_200 = bool(px > ma150[i] and px > ma200[i])
    ma150_above_200 = bool(ma150[i] > ma200[i])
    ma200_rising = bool(ma200[i] > ma200[j])
    ma50_above = bool(ma50[i] > ma150[i] and ma50[i] > ma200[i])
    above_50 = bool(px > ma50[i])
    from_low = (px / lo52 - 1.0) if lo52 > 0 else 0.0
    to_high = (hi52 / px - 1.0) if px > 0 else 1.0
    low_ok = bool(from_low >= low_margin)
    high_ok = bool(to_high <= high_margin)

    flags = [above_150_200, ma150_above_200, ma200_rising, ma50_above,
             above_50, low_ok, high_ok]
    return TrendTemplate(
        passed=sum(flags), total=len(flags),
        above_150_200=above_150_200, ma150_above_200=ma150_above_200,
        ma200_rising_1m=ma200_rising, ma50_above_150_200=ma50_above,
        above_50=above_50, above_52w_low_pct=round(from_low, 4),
        within_52w_high_pct=round(to_high, 4),
        pct_from_52w_low_ok=low_ok, pct_to_52w_high_ok=high_ok,
    )


def prior_advance(bars: Bars, i: int, lookback: int) -> float:
    """Gain from the lowest low in `lookback` bars before i, up to close[i]."""
    a = max(0, i - lookback)
    lo = float(bars.low[a: i + 1].min())
    return (float(bars.close[i]) / lo - 1.0) if lo > 0 else 0.0
