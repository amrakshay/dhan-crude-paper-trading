"""Cup-and-Handle detection from candles alone.

THE SHAPE, IN WORDS (O'Neil, `How to Make Money in Stocks`; Bulkowski,
`Encyclopedia of Chart Patterns`):

    a prior advance -> a rounded U-shaped decline and recovery (the CUP)
    back to roughly the level it left -> a shallow drift in the upper half
    of that range (the HANDLE) -> a breakout through the handle's high.

THE SHAPE, AS ARITHMETIC. Every clause above becomes a measurement:

  "prior advance"      close at the left rim >= +30% off the low of the
                       preceding window (O'Neil's proper-base precondition)
  "rounded, not a V"   >= `min_bottom_third` of the cup's bars have their
                       low in the bottom third of the cup's range, AND a
                       parabola beats a V in least squares (shapes.py)
  "back to the level"  |left rim - right rim| / rim <= `rim_tolerance`
  "the cup"            depth in [12%, 35%] of the rim, 7..65 weeks long
  "upper half"         handle low > cup low + 50% of cup depth
  "shallow"            handle depth <= 15% of the right rim
  "drifts down"        slope of the handle's closes is not materially up
  "volume"             receding through the cup, drier still in the handle,
                       expanding on the breakout bar

WHAT MAKES THIS HARD, AND WHAT IS DONE ABOUT IT:

1. There is no such thing as "the" cup: every pair of swing highs is a
   candidate rim pair, and one real cup generates a dozen readings. The
   detector enumerates pairs, scores each, and `base.dedupe` keeps the best
   of each overlapping cluster.

2. A handle that has not broken out yet is indistinguishable from a handle
   that is about to fail. The detector reports STATE rather than pretending
   otherwise: `forming` while price sits under the pivot, `breakout` on the
   bar that closes through it.

3. Nothing may look forward. Every call takes bars 0..t and returns what was
   knowable at t. The zigzag's final pivot is flagged provisional precisely
   so the right rim of a live cup can be used without cheating.
"""
from __future__ import annotations

import numpy as np

from .bars import Bars
from .base import Detection, dedupe
from .indicators import atr, linreg, sma
from .pivots import Pivot, zigzag
from .shapes import (max_single_leg_fraction, time_in_bottom_third,
                     u_vs_v_fit, volume_dryup, volume_trend)
from .trend import prior_advance

# Bands expressed in BARS, per timeframe. O'Neil's 7-65 weeks; a trading
# week is 5 sessions, so the daily band is the same span counted differently.
DURATION = {
    "D": dict(cup_min=30, cup_max=325, handle_min=5, handle_max=60),
    "W": dict(cup_min=7,  cup_max=65,  handle_min=1, handle_max=12),
}


class CupParams:
    """One place to argue about thresholds. Every default is sourced."""

    def __init__(self, timeframe: str = "D", **over):
        d = DURATION[timeframe]
        self.timeframe = timeframe
        self.cup_min = d["cup_min"]
        self.cup_max = d["cup_max"]
        self.handle_min = d["handle_min"]
        self.handle_max = d["handle_max"]
        # cup
        self.depth_min = 0.12          # O'Neil: 12%..33% normal
        self.depth_max = 0.35
        self.depth_max_deep = 0.50     # tolerated, tagged "deep", scored down
        self.rim_tolerance = 0.06      # Bulkowski: "near the same level".
                                       # Chosen by the sweep, not by taste:
                                       # going 0.10 -> 0.06 costs ZERO recall
                                       # on the benchmark and removes two
                                       # thirds of the false alarms. Level
                                       # rims are what a real cup has by
                                       # construction and what noise does not.
        self.min_bottom_third = 0.28   # U-vs-V gate; ideal V is 0.33, so this
                                       # sits just under it and the fit test
                                       # does the rest of the work
        self.min_u_vs_v = 0.50
        self.max_single_leg = 0.85
        self.prior_advance_min = 0.30  # O'Neil: ~30% advance into the base
        self.prior_advance_lookback = 2.0   # x cup length
        # handle
        self.handle_depth_max = 0.15   # "low teens in percent" is the flaw line
        self.handle_retrace_max = 0.50 # of cup depth; <=0.33 is ideal
        self.handle_slope_max = 0.0015 # per bar, as a fraction of price
        self.handle_upper_half = True
        self.handle_max_tol = 1.5      # hard cap = handle_max x this
        # volume
        self.cup_volume_trend_max = 0.0
        self.handle_dryup_max = 1.00   # handle avg vs cup avg
        self.prior_advance_gate = 0.15 # a floor, below the scored ideal
        self.near_rim_max = 0.12       # close at t vs the pivot
        self.breakout_volume_mult = 1.40   # "40 to 50 percent above average"
        self.breakout_volume_lookback = 50
        # scoring / output
        self.min_score = 0.76
        self.allow_no_handle = False
        for k, v in over.items():
            if not hasattr(self, k):
                raise AttributeError(f"unknown CupParams field {k!r}")
            setattr(self, k, v)


def _bump(trace, key):
    """Gate-rejection counter, for diagnosing recall. None in normal use."""
    if trace is not None:
        trace[key] += 1


def _handle_region(bars: Bars, r_idx: int, t: int):
    """The handle: everything after the right rim, up to the as-of bar."""
    a, b = r_idx + 1, t + 1
    if b - a < 1:
        return None
    return a, b


def _score(checks: dict[str, tuple[bool, float]]) -> tuple[float, list[str]]:
    """Weighted pass-rate, plus the names of what failed."""
    total = sum(w for _, w in checks.values())
    got = sum(w for ok, w in checks.values() if ok)
    fails = [k for k, (ok, _) in checks.items() if not ok]
    return (got / total if total else 0.0), fails


ZZ_GRID = ((3.0, 0.060), (2.2, 0.045), (1.6, 0.032))


def detect_cup_and_handle(bars: Bars, t: int | None = None,
                          params: CupParams | None = None,
                          zz_grid=ZZ_GRID,
                          require_hard_gates: bool = True,
                          trace=None) -> list[Detection]:
    """Every cup-and-handle visible in bars[0..t].

    `require_hard_gates` keeps the structural impossibilities out (wrong
    duration, wrong depth, handle below the cup's midpoint). The soft
    criteria -- volume, shape quality, prior advance -- are scored instead
    of gated, because a cup that is textbook in every way except that
    volume rose slightly is still a cup, and pretending otherwise throws
    away most of the real ones.
    """
    t = len(bars) - 1 if t is None else t
    p = params or CupParams(bars.timeframe)
    if t < p.cup_min + p.handle_min:
        return []

    view = bars.head(t + 1)
    out_all: list[Detection] = []
    for zz_k, zz_min_pct in zz_grid:
        out_all.extend(_detect_one(view, bars, t, p, zz_k, zz_min_pct,
                                   require_hard_gates, trace))
    return dedupe(out_all)


def _detect_one(view, bars, t, p, zz_k, zz_min_pct, require_hard_gates, trace):
    """One reading of the chart, at one zigzag resolution.

    A single threshold cannot see both ends of the problem: coarse enough to
    draw a 30%-deep cup's rims is too coarse to notice the 6% handle that
    confirms the right rim, and fine enough for the handle shatters the cup
    into a dozen little ones. So the chart is read three times and the
    readings are merged by score, which is what a human does with the zoom.
    """
    pivots = zigzag(view, k=zz_k, min_pct=zz_min_pct)
    # A cup whose left rim is older than cup_max + a tolerated handle cannot
    # still be forming at t, so it is not a candidate. Bounding the search
    # this way is what keeps a bar-by-bar scan of 2,800 bars cheap.
    oldest = t - int(p.cup_max + p.handle_max * p.handle_max_tol) - 2
    highs = [q for q in pivots if q.is_high and q.idx >= oldest]
    if len(highs) < 2:
        _bump(trace, 'few_pivots'); return []

    vol50 = sma(view.volume, p.breakout_volume_lookback)
    out: list[Detection] = []

    for li in range(len(highs) - 1):
        L = highs[li]
        for ri in range(li + 1, len(highs)):
            R = highs[ri]
            cup_len = R.idx - L.idx
            if cup_len < p.cup_min:
                _bump(trace, "cup_too_short"); continue
            if cup_len > p.cup_max:
                break

            lows = view.low[L.idx: R.idx + 1]
            hs = view.high[L.idx: R.idx + 1]
            b_off = int(lows.argmin())
            b_idx = L.idx + b_off
            bottom = float(lows.min())
            rim = max(L.price, R.price)
            if rim <= 0:
                continue
            depth = (rim - bottom) / rim

            # --- hard gates: geometry that would make it a different shape
            if depth < p.depth_min or depth > p.depth_max_deep:
                _bump(trace, "depth"); continue
            rim_gap = abs(L.price - R.price) / rim
            if rim_gap > p.rim_tolerance:
                _bump(trace, "rim_gap"); continue
            # The bottom must be interior. A low pinned to either rim is a
            # decline or a rally, not a cup.
            if b_off < 0.15 * cup_len or b_off > 0.85 * cup_len:
                _bump(trace, "bottom_not_interior"); continue

            hr = _handle_region(view, R.idx, t)
            hlen_raw = (hr[1] - hr[0]) if hr else 0
            # A handle that has run on for longer than the tolerance is not a
            # handle any more -- it is a second base, and calling it a handle
            # is how a detector ends up reporting a six-year one.
            if hlen_raw > p.handle_max * p.handle_max_tol:
                _bump(trace, "handle_too_long"); continue
            has_handle = hr is not None and hlen_raw >= p.handle_min
            if not has_handle and not p.allow_no_handle:
                _bump(trace, "no_handle"); continue

            if has_handle:
                ha, hb = hr
                # THE PIVOT AND THE HANDLE END TOGETHER, AND BOTH END BEFORE
                # THE BREAKOUT. Taking the pivot as the highest high over the
                # whole handle region -- which runs to `t` -- silently
                # includes the breakout bar, so the pivot is lifted to that
                # bar's own high and no close can ever exceed it. Every cup
                # then reads `forming` forever and the detector never
                # produces a signal. Walk it instead: the pivot is the
                # highest high made while price was still below the pivot,
                # and the breakout is the first close above that.
                pivot_run = float(R.price)
                bo_idx = None
                for j in range(ha, t + 1):
                    if view.close[j] > pivot_run:
                        bo_idx = j
                        break
                    pivot_run = max(pivot_run, float(view.high[j]))
                pivot_price = pivot_run
                h_end = bo_idx if bo_idx is not None else hb   # exclusive
                if h_end <= ha:
                    _bump(trace, "handle_empty"); continue
                hlen = h_end - ha
                if hlen < p.handle_min:
                    _bump(trace, "handle_too_short"); continue
                h_low = float(view.low[ha:h_end].min())
                h_high = float(view.high[ha:h_end].max())
                h_depth = (R.price - h_low) / R.price
                h_retrace = (R.price - h_low) / (rim - bottom) if rim > bottom else 1.0
                mid = bottom + 0.5 * (rim - bottom)
                if p.handle_upper_half and h_low <= mid:
                    _bump(trace, "handle_below_midpoint"); continue
                if h_depth > p.handle_depth_max * 1.6:   # far past the flaw line
                    _bump(trace, "handle_too_deep"); continue
                if h_low <= bottom:
                    _bump(trace, "handle_undercuts_cup"); continue
                hsl, _, _ = linreg(np.arange(hlen, dtype=float), view.close[ha:h_end])
                h_slope = hsl / max(view.close[ha:h_end].mean(), 1e-9)
            else:
                ha, hb, hlen = R.idx, R.idx + 1, 0
                h_end = R.idx + 1
                h_low = h_high = float(R.price)
                h_depth = h_retrace = 0.0
                h_slope = 0.0
                pivot_price = float(R.price)
                bo_idx = next((j for j in range(R.idx + 1, t + 1)
                               if view.close[j] > pivot_price), None)

            # --- shape. Rounded-ness is a GATE, not a score: "U-shaped,
            # not V-shaped" is the whole difference between a base being
            # built and a panic that bounced, and a detector that merely
            # docks points for it will report every V-bottom in the market.
            bottom_third = time_in_bottom_third(lows)
            uv = u_vs_v_fit(lows)
            leg = max_single_leg_fraction(lows, hs)
            if bottom_third < p.min_bottom_third or uv < p.min_u_vs_v:
                _bump(trace, "not_rounded"); continue

            # --- volume
            cup_v = view.volume[L.idx: R.idx + 1]
            vtrend = volume_trend(cup_v)

            # --- context
            adv = prior_advance(view, L.idx, int(p.prior_advance_lookback * cup_len))
            if adv < p.prior_advance_gate:
                _bump(trace, "no_prior_advance"); continue      # no advance, no base: a cup needs something to
                              # consolidate. This is O'Neil's precondition,
                              # gated at half his 30% so the score can still
                              # discriminate above it.

            stop_price = h_low if has_handle else bottom
            # The handle is where price waits just under the pivot. If it has
            # wandered far below, the base has broken and this is a leftover
            # shape, not a setup.
            if (pivot_price - view.close[t]) / pivot_price > p.near_rim_max:
                _bump(trace, "far_below_pivot"); continue

            # As in the VCP detector: measure the handle's dryness over the
            # bars still UNDER the pivot. Including the breakout bar makes
            # the volume expansion that confirms the pattern look like the
            # handle failing to dry up.
            dry = volume_dryup(view.volume[ha:h_end], cup_v) if has_handle else 1.0

            checks = {
                "depth_in_band":     (p.depth_min <= depth <= p.depth_max, 1.0),
                "rims_level":        (rim_gap <= p.rim_tolerance * 0.6, 1.0),
                "rounded_bottom":    (bottom_third >= 0.38, 2.0),
                "u_beats_v":         (uv >= 0.70, 1.5),
                "no_single_leg":     (leg <= p.max_single_leg, 0.5),
                "prior_advance":     (adv >= p.prior_advance_min, 1.0),
                "handle_present":    (has_handle, 0.5),
                "cup_volume_recedes": (vtrend <= p.cup_volume_trend_max, 1.0),
            }
            if has_handle:
                checks.update({
                    "handle_shallow":  (h_depth <= p.handle_depth_max, 1.5),
                    "handle_upper_half": (h_retrace <= p.handle_retrace_max, 1.0),
                    "handle_ideal_retrace": (h_retrace <= 0.33, 0.5),
                    "handle_drifts_down": (h_slope <= p.handle_slope_max, 1.0),
                    "handle_duration": (p.handle_min <= hlen <= p.handle_max, 0.5),
                    "handle_volume_dry": (dry <= p.handle_dryup_max, 1.0),
                })

            score, fails = _score(checks)
            if require_hard_gates and score < p.min_score:
                _bump(trace, "low_score"); continue

            # --- state: has it gone yet?  (bo_idx was found with the pivot)
            state = "breakout" if bo_idx is not None else "forming"
            bo_vol_mult = (float(view.volume[bo_idx] / vol50[bo_idx])
                           if bo_idx is not None and vol50[bo_idx] > 0 else None)
            if bo_idx is not None:
                ok = bo_vol_mult is not None and bo_vol_mult >= p.breakout_volume_mult
                checks["breakout_volume"] = (ok, 1.0)
                score, fails = _score(checks)

            variant = "with_handle" if has_handle else "no_handle"
            tags = []
            if depth > p.depth_max:
                tags.append("deep")
            if depth <= 0.20 and cup_len >= 1.5 * p.cup_min:
                tags.append("saucer")
            if bottom_third < 0.35:
                tags.append("v_ish")
            if tags:
                variant += ":" + "+".join(tags)

            out.append(Detection(
                pattern="cup_and_handle", variant=variant, symbol=bars.symbol,
                timeframe=bars.timeframe, start_idx=L.idx, end_idx=t,
                pivot_price=float(pivot_price), stop_price=float(stop_price),
                state=state, score=round(score, 4), reasons=fails,
                start_date=str(view.date[L.idx]), end_date=str(view.date[t]),
                metrics=dict(
                    left_rim=float(L.price), right_rim=float(R.price),
                    cup_low=bottom, cup_low_date=str(view.date[b_idx]),
                    cup_len=int(cup_len), depth=round(depth, 4),
                    rim_gap=round(rim_gap, 4), bottom_third=round(bottom_third, 3),
                    u_vs_v=round(uv, 3), max_leg=round(leg, 3),
                    prior_advance=round(adv, 3), cup_volume_trend=round(vtrend, 5),
                    handle_len=int(hlen), handle_low=float(h_low),
                    handle_depth=round(h_depth, 4), handle_retrace=round(h_retrace, 3),
                    handle_slope=round(h_slope, 5), handle_volume_ratio=round(dry, 3),
                    breakout_idx=bo_idx,
                    breakout_date=str(view.date[bo_idx]) if bo_idx is not None else None,
                    breakout_volume_mult=(round(bo_vol_mult, 2)
                                          if bo_vol_mult is not None else None),
                ),
            ))
    return out


def mirror(bars: Bars) -> Bars:
    """Reflect a series in log price: p -> C^2 / p.

    Turns a top into a bottom exactly, so the bullish detector finds the
    bearish pattern. The one caveat worth stating: percentage thresholds are
    not perfectly symmetric under this map (a 20% fall reflects to a 25%
    rise), so a mirrored detection's depths read a little larger than the
    original's. In log terms they are identical, and the bands here are wide
    enough that it changes no verdict in the 12-35% range.
    """
    c = float(np.sqrt(bars.close.max() * bars.close.min()))
    k = c * c
    return Bars(bars.symbol, bars.date, k / bars.open, k / bars.low,
                k / bars.high, k / bars.close, bars.volume, bars.timeframe)


def detect_inverted_cup_and_handle(bars: Bars, t: int | None = None,
                                   params: CupParams | None = None,
                                   **kw) -> list[Detection]:
    """The bearish mirror: a rounded TOP with a weak rebound that breaks down."""
    m = mirror(bars)
    p = params or CupParams(bars.timeframe)
    p.prior_advance_min = min(p.prior_advance_min, 0.20)  # a prior DECLINE, mirrored
    dets = detect_cup_and_handle(m, t, p, **kw)
    t = len(bars) - 1 if t is None else t
    c = float(np.sqrt(bars.close.max() * bars.close.min()))
    k = c * c
    for d in dets:
        d.pattern = "inverted_cup_and_handle"
        d.pivot_price = k / d.pivot_price
        d.stop_price = k / d.stop_price
        for key in ("left_rim", "right_rim", "cup_low", "handle_low"):
            if key in d.metrics:
                d.metrics[key] = k / d.metrics[key]
        d.metrics["cup_high"] = d.metrics.pop("cup_low", None)
        d.metrics["handle_high"] = d.metrics.pop("handle_low", None)
    return dets
