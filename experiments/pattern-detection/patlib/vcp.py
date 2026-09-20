"""Volatility Contraction Pattern (Minervini) from candles alone.

THE SHAPE, IN WORDS (`Trade Like a Stock Market Wizard`):

    inside a base, each successive pullback is shallower than the last --
    20%, then 10%, then 5% -- while volume dries up, until price is coiled
    in a tight pivot area and breaks out on expanding volume.

Minervini writes it as a FOOTPRINT: `19W 19/7 4T` = 19 weeks long, largest
contraction 19%, smallest 7%, four contractions. That notation is itself the
specification, and this detector reproduces it verbatim in `variant`.

THE SHAPE, AS ARITHMETIC:

  a "contraction"      one zigzag high -> the next zigzag low. Its depth is
                       (high - low) / high.
  "each smaller"       depth[i+1] <= depth[i] x `tighten_ratio`. Strictly
                       decreasing is too brittle for real data -- two
                       contractions of 11.4% and 11.5% are one observation,
                       not a violation -- so the ratio has slack.
  "2 to 6 of them"     T count in [2, 6]
  "the last one tight" final depth <= `final_depth_max`
  "volume dries up"    average volume over the final contraction against
                       the FIRST contraction's, and the same ratio for ATR.
                       Both comparisons are made INSIDE the base. The
                       obvious alternative -- ATR(10) against ATR(50) --
                       is biased against exactly the bases the pattern is
                       about: a 2T base 30 bars long has an ATR(50) that
                       reaches 20 bars back past its own start, so the
                       "contraction" is measured partly against price action
                       that is not part of the pattern. It cost most of the
                       2T recall before it was found, and it is kept only as
                       a scored extra.
  "the pivot"          the high of the final contraction

WHY THE PIVOT ENGINE MATTERS MORE HERE THAN ANYWHERE ELSE. The last
contraction of a good VCP can be 3%. A zigzag coarse enough to draw the
cup will not see it at all, and one fine enough to see it will shred the
first contraction into four. The detector therefore runs the zigzag at
SEVERAL thresholds and keeps the reading that produces the best-formed
footprint -- which is, in effect, what a human does when they squint at a
chart and then zoom in.
"""
from __future__ import annotations

import numpy as np

from .bars import Bars
from .base import Detection, dedupe
from .indicators import atr, sma, true_range
from .pivots import zigzag
from .shapes import volume_trend
from .trend import prior_advance, trend_template

def _bump(trace, key):
    """Gate-rejection counter, for diagnosing recall. None in normal use."""
    if trace is not None:
        trace[key] += 1


LENGTH = {           # base length in bars
    "D": dict(min=15, max=150),
    "W": dict(min=3,  max=30),
}


class VCPParams:
    def __init__(self, timeframe: str = "D", **over):
        L = LENGTH[timeframe]
        self.timeframe = timeframe
        self.base_min = L["min"]
        self.base_max = L["max"]
        self.min_contractions = 2          # Minervini's 2T floor
        self.max_contractions = 6
        self.tighten_ratio = 0.85          # each <= 85% of the previous
        self.min_leg_bars = 3              # a 1-bar dip is not a contraction
        self.first_depth_min = 0.08
        self.first_depth_max = 0.40        # 20-25% typical, 30-35% in vol names
        self.final_depth_max = 0.12
        self.final_depth_ideal = 0.06
        self.undercut_tol = 0.03           # how far the base low may be broken
        self.volume_trend_max = 0.0
        self.final_dryup_max = 0.90        # final contraction vs base average
        self.atr_squeeze_max = 0.75        # range in the last contraction
                                           # vs the first. Sweep-chosen:
                                           # 0.85 -> 0.75 costs no recall
                                           # and cuts false alarms by 23%.
        self.atr_squeeze_ideal = 0.50
        self.prior_advance_min = 0.25
        self.breakout_volume_mult = 1.40
        self.near_high_max = 0.10          # close vs the base's own high
        self.trend_template_min = 6        # of the 7 candle-computable ones.
                                           # Demanding all 7 takes benchmark
                                           # recall to ZERO -- the template is
                                           # a screen for the market's few
                                           # strongest names, not a property
                                           # every good base has.
        self.min_score = 0.65
        for k, v in over.items():
            if not hasattr(self, k):
                raise AttributeError(f"unknown VCPParams field {k!r}")
            setattr(self, k, v)


def _contractions(view: Bars, pivots, t: int, min_leg: int = 3):
    """Turn an alternating pivot list into (high_idx, high, low_idx, low, depth).

    `min_leg` discards pullbacks that begin and end within a bar or two of
    each other. A fine zigzag emits a lot of these, and because a VCP is
    read as a SEQUENCE, one of them landing between two genuine contractions
    breaks the monotone chain and the whole pattern is lost. A pullback that
    took two days is not one of Minervini's contractions under any reading.
    """
    out = []
    for a, b in zip(pivots, pivots[1:]):
        if a.is_high and not b.is_high and b.price < a.price and b.idx - a.idx >= min_leg:
            out.append((a.idx, float(a.price), b.idx, float(b.price),
                        (a.price - b.price) / a.price))
    return out


def _footprint(view: Bars, cs, tf: str) -> str:
    weeks = (cs[-1][2] - cs[0][0]) / (5.0 if tf == "D" else 1.0)
    return f"{int(round(weeks))}W {cs[0][4]*100:.0f}/{cs[-1][4]*100:.0f} {len(cs)}T"


def detect_vcp(bars: Bars, t: int | None = None, params: VCPParams | None = None,
               zz_grid=((2.5, 0.05), (1.8, 0.035), (1.2, 0.022), (0.9, 0.015)),
               trace=None) -> list[Detection]:
    """Every VCP visible in bars[0..t], best zigzag reading per base."""
    t = len(bars) - 1 if t is None else t
    p = params or VCPParams(bars.timeframe)
    if t < p.base_min + 10:
        return []
    view = bars.head(t + 1)
    a10, a50 = atr(view, 10), atr(view, 50)
    # Raw true range, NOT a smoothed ATR, for the within-base squeeze: a
    # Wilder ATR(14) has barely finished adapting to a 12-bar contraction
    # by the time that contraction is over, so the ratio it reports is a
    # lagged blend of both legs rather than a comparison of them.
    tr14 = true_range(view) / np.where(view.close > 0, view.close, np.nan)
    v50 = sma(view.volume, 50)
    out: list[Detection] = []

    for zz_k, zz_min in zz_grid:
        pivots = zigzag(view, k=zz_k, min_pct=zz_min)
        if len(pivots) < 3:
            _bump(trace, 'few_pivots'); continue
        cs_all = _contractions(view, pivots, t, p.min_leg_bars)
        if len(cs_all) < p.min_contractions:
            _bump(trace, 'few_contractions'); continue

        # Try every tail of the contraction list: a VCP is the LAST n
        # contractions of a base, and n is not known in advance.
        for n in range(p.max_contractions, p.min_contractions - 1, -1):
            if len(cs_all) < n:
                continue
            cs = cs_all[-n:]
            start_idx, end_low_idx = cs[0][0], cs[-1][2]
            base_len = t - start_idx
            if not (p.base_min <= base_len <= p.base_max):
                _bump(trace, "base_len"); continue

            depths = [c[4] for c in cs]
            # each contraction shallower than the one before it
            if any(depths[i + 1] > depths[i] * p.tighten_ratio
                   for i in range(len(depths) - 1)):
                _bump(trace, "not_tightening"); continue
            if not (p.first_depth_min <= depths[0] <= p.first_depth_max):
                _bump(trace, "first_depth"); continue
            if depths[-1] > p.final_depth_max:
                _bump(trace, "final_depth"); continue

            base_low = min(c[3] for c in cs)
            first_low = cs[0][3]
            # The base must hold: a VCP that undercuts its own start is a
            # failed base, not a tightening one.
            if base_low < first_low * (1 - p.undercut_tol):
                _bump(trace, "base_undercut"); continue

            # the tail after the final low: still inside the pivot area?
            final_low = float(cs[-1][3])
            tail_lo = float(view.low[cs[-1][2]: t + 1].min())
            if tail_lo < final_low * (1 - p.undercut_tol):
                _bump(trace, "tail_undercut"); continue

            # The dry-up window must STOP at the base. Running it to `t`
            # swallows the breakout bar, whose volume is 2-3x by definition,
            # and the very expansion that confirms the pattern then reads as
            # a failure to contract. Cost a lot of recall before it was found.
            pivot_price = float(max(c[1] for c in cs))
            inside = [j for j in range(cs[-1][0], t + 1)
                      if view.close[j] <= pivot_price]
            last_inside = inside[-1] if inside else cs[-1][2]
            base_v = view.volume[start_idx: last_inside + 1]
            fin_v = view.volume[cs[-1][0]: last_inside + 1]
            first_v = view.volume[cs[0][0]: cs[0][2] + 1]
            vtrend = volume_trend(base_v)
            dry = float(fin_v.mean() / base_v.mean()) if base_v.mean() > 0 else 1.0
            dry_vs_first = (float(fin_v.mean() / first_v.mean())
                            if first_v.mean() > 0 else 1.0)
            # the squeeze, measured inside the base
            fin_a = tr14[cs[-1][0]: last_inside + 1]
            first_a = tr14[cs[0][0]: cs[0][2] + 1]
            squeeze_in_base = (float(np.nanmean(fin_a) / np.nanmean(first_a))
                               if len(first_a) and np.nanmean(first_a) > 0 else 1.0)
            squeeze = float(a10[t] / a50[t]) if a50[t] > 0 else 1.0
            adv = prior_advance(view, start_idx, min(start_idx, 3 * base_len))

            # --- gates, not scores. Each of these is structural: a base
            # that fails one is not a tightening base, it is something else
            # that happens to contain two smaller pullbacks than the first.
            base_high = float(view.high[start_idx: t + 1].max())
            if base_high <= 0 or (base_high - view.close[t]) / base_high > p.near_high_max:
                _bump(trace, "near_high"); continue
            if adv < p.prior_advance_min:
                _bump(trace, "prior_advance"); continue
            if min(dry, dry_vs_first) > p.final_dryup_max:
                _bump(trace, "dryup"); continue
            if squeeze_in_base > p.atr_squeeze_max:
                _bump(trace, "atr_squeeze"); continue
            tt = trend_template(view, t)
            if tt.passed < p.trend_template_min:
                _bump(trace, "trend_template"); continue

            checks = {
                "t_count_2_to_6":       (p.min_contractions <= n <= p.max_contractions, 0.5),
                "first_depth_band":     (0.10 <= depths[0] <= 0.35, 1.0),
                "final_tight":          (depths[-1] <= p.final_depth_ideal, 1.5),
                "halving":              (all(depths[i+1] <= depths[i] * 0.65
                                             for i in range(len(depths)-1)), 1.0),
                "volume_recedes":       (vtrend <= p.volume_trend_max, 1.0),
                "final_volume_dry":     (min(dry, dry_vs_first) <= p.final_dryup_max * 0.85, 1.5),
                "atr_squeeze_in_base":  (squeeze_in_base <= 0.70, 1.0),
                "atr_squeeze_vs_50d":   (squeeze <= p.atr_squeeze_ideal, 0.5),
                "prior_advance":        (adv >= p.prior_advance_min * 1.5, 1.0),
                "trend_template":       (tt.ok, 1.0),
            }
            total = sum(w for _, w in checks.values())
            score = sum(w for ok, w in checks.values() if ok) / total
            fails = [k for k, (ok, _) in checks.items() if not ok]
            if score < p.min_score:
                _bump(trace, "low_score"); continue

            state, bo_idx = "forming", None
            for j in range(cs[-1][2] + 1, t + 1):
                if view.close[j] > pivot_price:
                    bo_idx, state = j, "breakout"
                    break
            bo_mult = (float(view.volume[bo_idx] / v50[bo_idx])
                       if bo_idx is not None and v50[bo_idx] > 0 else None)
            if bo_idx is not None:
                checks["breakout_volume"] = (bo_mult is not None
                                             and bo_mult >= p.breakout_volume_mult, 1.0)
                total = sum(w for _, w in checks.values())
                score = sum(w for ok, w in checks.values() if ok) / total
                fails = [k for k, (ok, _) in checks.items() if not ok]

            out.append(Detection(
                pattern="vcp", variant=_footprint(view, cs, bars.timeframe),
                symbol=bars.symbol, timeframe=bars.timeframe,
                start_idx=start_idx, end_idx=t,
                pivot_price=pivot_price, stop_price=final_low,
                state=state, score=round(score, 4), reasons=fails,
                start_date=str(view.date[start_idx]), end_date=str(view.date[t]),
                metrics=dict(
                    t_count=n, depths=[round(d, 4) for d in depths],
                    base_len=int(base_len), base_low=float(base_low),
                    zz_k=zz_k, zz_min_pct=zz_min,
                    volume_trend=round(vtrend, 5), final_volume_ratio=round(dry, 3),
                    dryup_window_end=int(last_inside),
                    atr_squeeze_vs_50d=round(squeeze, 3),
                    atr_squeeze_in_base=round(squeeze_in_base, 3),
                    final_volume_vs_first=round(dry_vs_first, 3),
                    prior_advance=round(adv, 3),
                    trend_template_passed=tt.passed, trend_template_ok=tt.ok,
                    contraction_lows=[str(view.date[c[2]]) for c in cs],
                    breakout_idx=bo_idx,
                    breakout_date=str(view.date[bo_idx]) if bo_idx is not None else None,
                    breakout_volume_mult=round(bo_mult, 2) if bo_mult else None,
                ),
            ))
            break   # deepest tail that works wins for this zigzag reading
    return dedupe(out, threshold=0.5)
