"""Triangles, wedges, pennants and broadening formations -- one engine.

They are all the same object: TWO TRENDLINES FITTED TO ALTERNATING PIVOTS.
What separates them is only the pair of slopes, so it would be perverse to
write five detectors. This module fits the lines once and classifies:

    upper flat, lower rising          ->  ascending triangle
    upper falling, lower flat         ->  descending triangle
    upper falling, lower rising       ->  symmetrical triangle
                                          (pennant, if short and preceded
                                           by a near-vertical move)
    both rising, upper less steep     ->  rising wedge      (bearish)
    both falling, lower less steep    ->  falling wedge     (bullish)
    diverging                         ->  broadening formation

BULKOWSKI'S IDENTIFICATION GUIDELINES, MADE ARITHMETIC:

  "two converging trendlines"   width at the end < `converge_ratio` x width
                                at the start, and the apex ahead of us
  "touch one trendline at       >= 3 pivots within `touch_tol` x ATR of one
   least three times, the       line and >= 2 of the other
   other at least twice"
  "price must cross from side   `fill`: mean bar range as a fraction of the
   to side, filling the         channel width at that bar. A pattern that
   triangle, not white space"   drifts along one line has a low fill and is
                                a channel, not a triangle.
  "volume trends downward       slope of volume over the pattern
   78% to 86% of the time"
  "breaks out 61% to 75% of     `apex_progress` at the breakout bar; past
   the way to the apex"         85% the pattern has run out of room and is
                                scored down

The classifier's one genuinely arbitrary number is what counts as FLAT. It
is expressed as total drift across the whole pattern as a fraction of price
(`flat_drift`), not as a slope, so it means the same thing on a 12-bar
pennant and a 90-bar triangle.
"""
from __future__ import annotations

import numpy as np

from .bars import Bars
from .base import Detection, dedupe
from .indicators import atr, linreg, sma
from .pivots import zigzag
from .shapes import channel_fill, volume_trend

LENGTH = {
    "D": dict(min=15, max=120, pennant_max=20),
    "W": dict(min=4,  max=26,  pennant_max=4),
}


class TriangleParams:
    """Defaults chosen by the sweep in scripts/sweep.py, not by taste.

    Across the synthetic benchmark, triangle recall is flat at ~99% over
    every value of every threshold tried, while the false-alarm rate on the
    negative controls moves by a factor of 2.3. That is the whole story of
    this pattern: the geometry is easy to find and hard to find ONLY when it
    means something. The values below are the low-false-alarm end of each
    curve, taken because recall was not paying for them.
    """

    def __init__(self, timeframe: str = "D", **over):
        L = LENGTH[timeframe]
        self.timeframe = timeframe
        self.len_min = L["min"]
        self.len_max = L["max"]
        self.pennant_max = L["pennant_max"]
        self.min_pivots = 5            # 3 on one line + 2 on the other
        self.max_pivots = 9
        self.touch_tol_atr = 0.60      # a "touch" is within 0.6 ATR of the line
        self.min_touches_major = 3
        self.min_touches_minor = 2
        self.converge_ratio = 0.58     # end width vs start width
        self.diverge_ratio = 1.30      # for broadening formations
        self.flat_drift = 0.030        # total drift/price that still reads flat
        self.min_fill = 0.55
        self.min_r2 = 0.70             # how well each line actually fits
        self.leg_shrink_max = 0.55     # last swing leg vs first, as a ratio
        self.min_amplitude_atr = 3.0   # starting width, in ATRs
        self.min_legs = 5
        self.touch_ratio_min = 0.90    # selected pivots that must lie on a line
        self.apex_max_progress = 0.95
        self.volume_trend_max = 0.0
        self.breakout_volume_mult = 1.30
        self.pennant_prior_move = 0.15
        self.min_prior_move = 0.06     # a triangle consolidates SOMETHING
        self.min_score = 0.84
        self.detect_broadening = False
        for k, v in over.items():
            if not hasattr(self, k):
                raise AttributeError(f"unknown TriangleParams field {k!r}")
            setattr(self, k, v)


def _classify(sh_drift: float, sl_drift: float, p: TriangleParams,
              length: int, prior_move: float) -> str | None:
    """sh_drift / sl_drift: total drift of each line over the pattern,
    as a fraction of the pattern's mid price."""
    up_flat = abs(sh_drift) <= p.flat_drift
    lo_flat = abs(sl_drift) <= p.flat_drift
    if up_flat and lo_flat:
        return None                       # a rectangle, not a triangle
    if up_flat and sl_drift > 0:
        return "ascending"
    if lo_flat and sh_drift < 0:
        return "descending"
    if sh_drift < 0 and sl_drift > 0:
        if length <= p.pennant_max and prior_move >= p.pennant_prior_move:
            return "pennant"
        return "symmetrical"
    if sh_drift > 0 and sl_drift > 0:
        return "rising_wedge" if sh_drift < sl_drift else None
    if sh_drift < 0 and sl_drift < 0:
        return "falling_wedge" if abs(sh_drift) > abs(sl_drift) else None
    return None


def detect_triangles(bars: Bars, t: int | None = None,
                     params: TriangleParams | None = None,
                     zz_grid=((2.0, 0.035), (1.4, 0.025), (1.0, 0.018))
                     ) -> list[Detection]:
    t = len(bars) - 1 if t is None else t
    p = params or TriangleParams(bars.timeframe)
    if t < p.len_min + 5:
        return []
    view = bars.head(t + 1)
    a = atr(view, 14)
    v50 = sma(view.volume, 50)
    out: list[Detection] = []

    for zz_k, zz_min in zz_grid:
        pivots = [q for q in zigzag(view, k=zz_k, min_pct=zz_min)
                  if q.idx >= t - p.len_max - 5]
        if len(pivots) < p.min_pivots:
            continue
        for npv in range(min(p.max_pivots, len(pivots)), p.min_pivots - 1, -1):
            sel = pivots[-npv:]
            s_idx, e_idx = sel[0].idx, sel[-1].idx
            length = t - s_idx
            if not (p.len_min <= length <= p.len_max):
                continue
            hi = [(q.idx, q.price) for q in sel if q.is_high]
            lo = [(q.idx, q.price) for q in sel if not q.is_high]
            if len(hi) < 2 or len(lo) < 2:
                continue
            if max(len(hi), len(lo)) < p.min_touches_major:
                continue

            sh, ih, r2h = linreg(np.array([x for x, _ in hi], float),
                                 np.array([y for _, y in hi], float))
            sl, il, r2l = linreg(np.array([x for x, _ in lo], float),
                                 np.array([y for _, y in lo], float))
            mid = float(view.close[s_idx: t + 1].mean())
            if mid <= 0:
                continue
            up_at = lambda x: sh * x + ih
            lo_at = lambda x: sl * x + il
            w0 = up_at(s_idx) - lo_at(s_idx)
            w1 = up_at(t) - lo_at(t)
            if w0 <= 0 or w1 <= 0:
                continue          # lines already crossed: the pattern is over

            converging = w1 <= w0 * p.converge_ratio
            broadening = w1 >= w0 * p.diverge_ratio
            if not converging and not (broadening and p.detect_broadening):
                continue

            sh_d = sh * length / mid
            sl_d = sl * length / mid
            prior = 0.0
            if s_idx > 10:
                w = view.close[max(0, s_idx - 20): s_idx + 1]
                prior = float(abs(w[-1] / w[0] - 1.0)) if w[0] > 0 else 0.0

            if broadening and not converging:
                kind = "broadening"
            else:
                kind = _classify(sh_d, sl_d, p, length, prior)
            if kind is None:
                continue

            # THE gate that random data fails. Two fitted lines converge by
            # chance all the time; what does not happen by chance is the
            # actual swings getting smaller, one after another. Measured on
            # the pivots themselves, not on the lines drawn through them.
            amps = [abs(b.price - a.price) / mid for a, b in zip(sel, sel[1:])]
            if len(amps) < p.min_legs:
                continue
            leg_slope, _, _ = linreg(np.arange(len(amps), dtype=float),
                                     np.array(amps, float))
            if amps[0] <= 0 or amps[-1] > amps[0] * p.leg_shrink_max:
                continue
            if leg_slope >= 0:
                continue
            # and it must be a real range, not a doodle inside the noise
            if w0 < p.min_amplitude_atr * float(a[s_idx]):
                continue

            # touches
            tol = p.touch_tol_atr
            th = sum(1 for x, y in hi if abs(y - up_at(x)) <= tol * a[x])
            tl = sum(1 for x, y in lo if abs(y - lo_at(x)) <= tol * a[x])
            if max(th, tl) < p.min_touches_major or min(th, tl) < p.min_touches_minor:
                continue
            if th + tl < 5:
                continue
            # Every selected pivot must sit ON one of the lines. Without this
            # the search happily reaches back past the start of the pattern,
            # picks up pivots from the move that preceded it, and reports a
            # triangle that begins twenty bars before the triangle does --
            # scoring well, because the extra pivots were never asked to fit.
            if (th + tl) / npv < p.touch_ratio_min:
                continue
            # NOTE: R^2 is NOT used as a gate. An ascending triangle's upper
            # line is horizontal by definition, so the variance it is being
            # asked to explain is zero and R^2 collapses towards zero however
            # perfectly the line fits. The touch test above already measures
            # fit, in ATRs, which is the scale that means something.

            # fill: does price actually traverse the channel?
            # apex
            apex_x = ((il - ih) / (sh - sl)) if abs(sh - sl) > 1e-12 else None
            apex_prog = (((t - s_idx) / (apex_x - s_idx))
                         if apex_x and apex_x > s_idx else None)

            xs = np.arange(s_idx, t + 1)
            fill = channel_fill(view.high[s_idx: t + 1], view.low[s_idx: t + 1],
                                up_at(xs), lo_at(xs))

            if fill < p.min_fill:
                continue
            vtrend = volume_trend(view.volume[s_idx: t + 1])
            # Bulkowski measures receding volume in 78% (ascending/descending)
            # to 86% (symmetrical) of real triangles. At those rates it is
            # not a tendency to score, it is part of the definition.
            if vtrend > p.volume_trend_max:
                continue
            if abs(prior) < p.min_prior_move:
                continue

            upper_now, lower_now = float(up_at(t)), float(lo_at(t))
            bias_up = kind in ("ascending", "falling_wedge", "pennant", "symmetrical")
            pivot_price = upper_now if bias_up else lower_now
            stop_price = lower_now if bias_up else upper_now

            checks = {
                "converging":       (converging, 1.5),
                "touches":          (th + tl >= 5, 1.5),
                "line_fit_upper":   (r2h >= p.min_r2 or len(hi) == 2, 0.75),
                "line_fit_lower":   (r2l >= p.min_r2 or len(lo) == 2, 0.75),
                "fills_the_space":  (fill >= p.min_fill * 1.15, 1.5),
                "legs_shrink_hard": (amps[-1] <= amps[0] * 0.55, 1.0),
                "volume_recedes":   (vtrend <= p.volume_trend_max, 1.0),
                "room_before_apex": (apex_prog is None or apex_prog <= p.apex_max_progress, 1.0),
                "duration":         (p.len_min <= length <= p.len_max, 0.5),
            }
            total = sum(w for _, w in checks.values())
            score = sum(w for ok, w in checks.values() if ok) / total
            fails = [k for k, (ok, _) in checks.items() if not ok]
            if score < p.min_score:
                continue

            # state: a close outside either line confirms (Bulkowski)
            state, bo_idx, bo_dir = "forming", None, None
            for j in range(e_idx + 1, t + 1):
                if view.close[j] > up_at(j):
                    bo_idx, bo_dir, state = j, "up", "breakout"
                    break
                if view.close[j] < lo_at(j):
                    bo_idx, bo_dir, state = j, "down", "breakout"
                    break
            bo_mult = (float(view.volume[bo_idx] / v50[bo_idx])
                       if bo_idx is not None and v50[bo_idx] > 0 else None)

            out.append(Detection(
                pattern="triangle", variant=kind, symbol=bars.symbol,
                timeframe=bars.timeframe, start_idx=s_idx, end_idx=t,
                pivot_price=pivot_price, stop_price=stop_price,
                state=state, score=round(score, 4), reasons=fails,
                start_date=str(view.date[s_idx]), end_date=str(view.date[t]),
                metrics=dict(
                    pivots=npv, highs=len(hi), lows=len(lo),
                    touches_upper=th, touches_lower=tl,
                    upper_drift=round(sh_d, 4), lower_drift=round(sl_d, 4),
                    r2_upper=round(r2h, 3), r2_lower=round(r2l, 3),
                    width_start=round(float(w0), 4), width_end=round(float(w1), 4),
                    convergence=round(float(w1 / w0), 3), fill=round(fill, 3),
                    apex_progress=round(apex_prog, 3) if apex_prog else None,
                    volume_trend=round(vtrend, 5), prior_move=round(prior, 3),
                    upper_now=round(upper_now, 4), lower_now=round(lower_now, 4),
                    upper_start=round(float(up_at(s_idx)), 4),
                    lower_start=round(float(lo_at(s_idx)), 4),
                    zz_k=zz_k, zz_min_pct=zz_min, length=int(length),
                    leg_amplitudes=[round(x, 4) for x in amps],
                    leg_shrink=round(amps[-1] / amps[0], 3),
                    breakout_idx=bo_idx, breakout_direction=bo_dir,
                    breakout_date=str(view.date[bo_idx]) if bo_idx is not None else None,
                    breakout_volume_mult=round(bo_mult, 2) if bo_mult else None,
                ),
            ))
            break
    return dedupe(out, threshold=0.5)
