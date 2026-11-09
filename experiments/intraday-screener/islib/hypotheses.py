"""Pre-registered ideas (README.md) as signal generators, and their controls.

A hypothesis supplies two things, kept apart on purpose:

  candidates(F, days)  -> DataFrame s, d, k, side   WHEN it fires, and which way
  stops(F, sig)        -> stop, target arrays       HOW it would be managed

The controls replace only one ingredient of `candidates` -- the direction,
the time of day, or the stock -- keeping the same RISK per trade, so a control
that does as well as the hypothesis says that ingredient carried nothing.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .features import OPENING_RANGE_BARS, Features
from .panel import FIRST_DECISION_BAR

# Harness defaults that apply to EVERY hypothesis, fixed before any result:
LAST_DECISION_BAR = 56     # decide no later than the close of 13:55-14:00
ONE_TRADE_PER_NAME_PER_DAY = True


class Hypothesis:
    name = "base"
    first_bar = FIRST_DECISION_BAR

    def candidates(self, F: Features, days: np.ndarray) -> pd.DataFrame:
        raise NotImplementedError

    def stops(self, F: Features, sig: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        raise NotImplementedError

    def signals(self, F: Features, days: np.ndarray) -> pd.DataFrame:
        sig = self.candidates(F, days)
        stop, target = self.stops(F, sig)
        return sig.assign(stop=stop, target=target)


def first_true(mask: np.ndarray, first_bar: int, last_bar: int) -> pd.DataFrame:
    """[S, D, B] bool -> the first bar in [first_bar, last_bar] per (s, d)."""
    window = mask[:, :, first_bar:last_bar + 1]
    any_ = window.any(axis=2)
    k = first_bar + np.argmax(window, axis=2)
    s, d = np.nonzero(any_)
    return pd.DataFrame({"s": s, "d": d, "k": k[s, d]})


class H1OpeningRangeBreakout(Hypothesis):
    """H1: close beyond the 09:15-09:30 range, with cumulative volume at least
    1.5x this name's normal for the same time of day. Stop: the other side of
    the opening range. Target: none (time exit) -- risk/reward is studied
    separately from the MFE/MAE this produces."""

    name = "H1_orb_volume"
    first_bar = OPENING_RANGE_BARS          # bar 3 closes at 09:35: first possible break
    relvol_min = 1.5

    def candidates(self, F, days):
        orh, orl = F.opening_range
        c = F.p.close
        ok = F.liquid[..., None] & days[None, :, None] & (F.relvol >= self.relvol_min)
        with np.errstate(invalid="ignore"):
            up = ok & (c > orh[..., None])
            dn = ok & (c < orl[..., None])
        a = first_true(up, self.first_bar, LAST_DECISION_BAR).assign(side=1)
        b = first_true(dn, self.first_bar, LAST_DECISION_BAR).assign(side=-1)
        sig = pd.concat([a, b], ignore_index=True)
        if ONE_TRADE_PER_NAME_PER_DAY:   # whichever side broke first
            sig = sig.sort_values("k").drop_duplicates(["s", "d"], keep="first")
        return sig.sort_values(["d", "k", "s"]).reset_index(drop=True)

    def stops(self, F, sig):
        orh, orl = F.opening_range
        s, d = sig["s"].to_numpy(), sig["d"].to_numpy()
        stop = np.where(sig["side"].to_numpy() > 0, orl[s, d], orh[s, d])
        return stop, np.full(len(sig), np.nan)


ATR_STOP_MULT = 3.0


def _atr_stop(F: Features, sig: pd.DataFrame, mult: float = ATR_STOP_MULT) -> np.ndarray:
    s, d, k = (sig[c].to_numpy() for c in ("s", "d", "k"))
    ref = F.close_ff[s, d, k]
    return ref - sig["side"].to_numpy() * mult * F.atr5[s, d, k]


class H2RelativeStrength(Hypothesis):
    """H2: at 10:00, top/bottom decile of (return since open - Nifty's), on
    the right side of VWAP. Stop 3 x ATR5. Time exit."""

    name = "H2_relative_strength"
    decision_bar = 8          # 09:55-10:00, closes at 10:00
    first_bar = 8
    decile = 0.10

    def candidates(self, F, days):
        k = self.decision_bar
        rs = F.ret_since_open[:, :, k] - F.index_ret_since_open[None, :, k]
        ok = F.liquid & days[None, :] & ~np.isnan(rs) & ~np.isnan(F.p.close[:, :, k])
        rs = np.where(ok, rs, np.nan)
        df = pd.DataFrame(rs.T)                       # [D, S]
        pct = df.rank(axis=1, pct=True).to_numpy().T   # [S, D], NaN stays NaN
        px, vw = F.close_ff[:, :, k], F.vwap[:, :, k]
        with np.errstate(invalid="ignore"):
            long_ = ok & (pct > 1 - self.decile) & (px > vw)
            short = ok & (pct <= self.decile) & (px < vw)
        rows = [pd.DataFrame({"s": s, "d": d, "k": k, "side": side})
                for side, m in ((1, long_), (-1, short)) for s, d in [np.nonzero(m)]]
        return pd.concat(rows, ignore_index=True).sort_values(["d", "s"]).reset_index(drop=True)

    def stops(self, F, sig):
        return _atr_stop(F, sig), np.full(len(sig), np.nan)


class _H3Gap(Hypothesis):
    first_bar = 2
    min_gap = 0.01
    mode = None    # "continue" | "fade"

    def candidates(self, F, days):
        k = 2
        g = F.gap
        ok = (F.liquid & days[None, :] & ~F.p.gap_flag & (np.abs(np.nan_to_num(g)) >= self.min_gap)
              & ~np.isnan(F.p.close[:, :, k]))
        gap_dir = np.sign(np.nan_to_num(g))
        held = np.sign(F.close_ff[:, :, k] - F.day_open) == gap_dir    # 09:30 close beyond the open, gap's way
        failed = np.sign(F.close_ff[:, :, k] - F.day_open) == -gap_dir
        if self.mode == "continue":
            m, side = ok & held, gap_dir
        else:
            m, side = ok & failed, -gap_dir
        s, d = np.nonzero(m)
        return pd.DataFrame({"s": s, "d": d, "k": k, "side": side[s, d].astype(int)})

    def stops(self, F, sig):
        orh, orl = F.opening_range
        s, d = sig["s"].to_numpy(), sig["d"].to_numpy()
        return np.where(sig["side"].to_numpy() > 0, orl[s, d], orh[s, d]), np.full(len(sig), np.nan)


class H3aGapContinue(_H3Gap):
    """H3a: gap >= 1% whose 09:30 close is still beyond the open: go with it."""
    name = "H3a_gap_continue"
    mode = "continue"


class H3bGapFade(_H3Gap):
    """H3b: gap >= 1% whose 09:30 close is back through the open: fade it."""
    name = "H3b_gap_fade"
    mode = "fade"


class H3cGapFadeTop3(H3bGapFade):
    """H3c: H3b, keeping only the 3 largest |gap| names per day.

    NOT pre-registered. Found by diagnosing H3b on the development period
    (2026-09-27): H3b's per-trade edge came from a dozen market-wide reversal
    days with 50-97 simultaneous signals; ranked by gap size and capped, it
    held in every year. Caps of 1, 3 and 5 were looked at; 3 is fixed here.
    Because the rule was chosen on development data, only the HOLDOUT can
    validate it, and it gets one run. Ranking uses |gap|, known at 09:15.
    """

    name = "H3c_gap_fade_top3"
    per_day = 3

    def candidates(self, F, days):
        sig = super().candidates(F, days)
        sig["absgap"] = np.abs(F.gap[sig["s"].to_numpy(), sig["d"].to_numpy()])
        sig = (sig.sort_values(["d", "absgap", "s"], ascending=[True, False, True])
               .groupby("d").head(self.per_day))
        return sig.drop(columns="absgap").sort_values(["d", "s"]).reset_index(drop=True)


class H4VwapPullback(Hypothesis):
    """H4: above (below) VWAP on every close 09:30-10:30; after 10:30 a low
    (high) touches VWAP within the last 3 bars and a bar then closes beyond
    the previous bar's high (low) and on the trend side of VWAP. Stop: the
    3-bar extreme. Time exit."""

    name = "H4_vwap_pullback"
    trend_bars = (2, 14)       # closes 09:30 .. 10:30
    first_bar = 15
    lookback = 3

    def candidates(self, F, days):
        c, h, l, vw = F.p.close, F.p.high, F.p.low, F.vwap
        base = F.liquid & days[None, :]
        a, b = self.trend_bars
        with np.errstate(invalid="ignore"):
            above = c > vw
            below = c < vw
            up_trend = base & np.all(above[:, :, a:b + 1], axis=2)
            dn_trend = base & np.all(below[:, :, a:b + 1], axis=2)
            n = self.lookback
            touch_up = np.zeros_like(above)
            touch_dn = np.zeros_like(above)
            for j in range(n):
                sl = slice(n - 1 - j, c.shape[2] - j)
                touch_up[:, :, n - 1:] |= (l[:, :, sl] <= vw[:, :, sl])
                touch_dn[:, :, n - 1:] |= (h[:, :, sl] >= vw[:, :, sl])
            prev_h = np.full_like(h, np.nan); prev_h[:, :, 1:] = h[:, :, :-1]
            prev_l = np.full_like(l, np.nan); prev_l[:, :, 1:] = l[:, :, :-1]
            go_up = up_trend[..., None] & touch_up & (c > prev_h) & above
            go_dn = dn_trend[..., None] & touch_dn & (c < prev_l) & below
        x = first_true(go_up, self.first_bar, LAST_DECISION_BAR).assign(side=1)
        y = first_true(go_dn, self.first_bar, LAST_DECISION_BAR).assign(side=-1)
        return pd.concat([x, y], ignore_index=True).sort_values(["d", "k", "s"]).reset_index(drop=True)

    def stops(self, F, sig):
        s, d, k = (sig[c].to_numpy() for c in ("s", "d", "k"))
        n = self.lookback
        idx = k[:, None] - np.arange(n)[None, :]
        lows = F.p.low[s[:, None], d[:, None], idx]
        highs = F.p.high[s[:, None], d[:, None], idx]
        with np.errstate(all="ignore"):
            stop = np.where(sig["side"].to_numpy() > 0, np.nanmin(lows, axis=1), np.nanmax(highs, axis=1))
        return stop, np.full(len(sig), np.nan)


class H5BullishBreakoutChecklist(Hypothesis):
    """H5: the owner's six-condition "bullish breakout" checklist (README.md,
    fixed 2026-11-09 before running). Long only. First bar whose close is
    above the previous day's high and today's first candle's high, with
    EMA10 > EMA20 > EMA50, ADX(14) > 25, close above VWAP and bar volume at
    least 1.5x the previous 20 bars' mean. Stop: the signal bar's low.
    Target: 2R from the signal close."""

    name = "H5_bullish_breakout_checklist"
    adx_min = 25.0
    vol_mult = 1.5
    reward_risk = 2.0

    def candidates(self, F, days):
        c, l = F.p.close, F.p.low
        with np.errstate(invalid="ignore"):
            m = (F.liquid[..., None] & days[None, :, None]
                 & (c > F.prev_day_high[..., None])
                 & (c > F.first_bar_high[..., None])
                 & (F.ema(10) > F.ema(20)) & (F.ema(20) > F.ema(50))
                 & (F.adx14 > self.adx_min)
                 & (c > F.vwap)
                 & (F.p.volume >= self.vol_mult * F.prior_bar_vol_mean20)
                 & (c > l))                       # zero-risk bars cannot be sized
        sig = first_true(m, self.first_bar, LAST_DECISION_BAR).assign(side=1)
        return sig.sort_values(["d", "k", "s"]).reset_index(drop=True)

    def stops(self, F, sig):
        s, d, k = (sig[c].to_numpy() for c in ("s", "d", "k"))
        stop = F.p.low[s, d, k]
        ref = F.p.close[s, d, k]
        return stop, ref + self.reward_risk * (ref - stop)


# ---------------------------------------------------------------------------
# Controls. Each returns a signal frame of the same length.
#
# A control keeps the hypothesis's stop DISTANCE (as a fraction of the price
# at the decision), not its stop RULE. The first version re-applied the rule,
# and on a planted-edge panel the random-side control earned exactly what H1
# did: H1's stop is the far side of the opening range, so a coin that flipped
# a long into a short put the stop where price already was, the engine
# skipped it as "stop already breached", and the only trades the control ever
# took were the ones where the coin had agreed with H1. The stop rule was
# carrying the direction. Caught by tests/test_end_to_end.py.
# ---------------------------------------------------------------------------

def _stop_fraction(F: Features, sig: pd.DataFrame) -> np.ndarray:
    ref = F.close_ff[sig["s"].to_numpy(), sig["d"].to_numpy(), sig["k"].to_numpy()]
    return np.abs(ref - sig["stop"].to_numpy()) / ref


def _target_fraction(F: Features, sig: pd.DataFrame) -> np.ndarray:
    """Target distance as a fraction of the decision price; NaN when the
    hypothesis has no target (H1-H4), so their controls are unchanged."""
    if "target" not in sig:
        return np.full(len(sig), np.nan)
    ref = F.close_ff[sig["s"].to_numpy(), sig["d"].to_numpy(), sig["k"].to_numpy()]
    return np.abs(sig["target"].to_numpy() - ref) / ref


def _place(F: Features, c: pd.DataFrame, frac: np.ndarray,
           tfrac: np.ndarray | None = None) -> pd.DataFrame:
    """Re-place the stop -- and the target, when the hypothesis has one -- at
    the same DISTANCE from this control's own decision price. A control
    without H5's 2R target would be a different exit, not a control."""
    ref = F.close_ff[c["s"].to_numpy(), c["d"].to_numpy(), c["k"].to_numpy()]
    side = c["side"].to_numpy()
    target = np.nan if tfrac is None else ref * (1 + side * tfrac)
    return c.assign(stop=ref * (1 - side * frac), target=target)


def control_random_side(h: Hypothesis, F: Features, sig: pd.DataFrame, rng) -> pd.DataFrame:
    """Same name, same moment, same risk, a coin for the direction."""
    c = sig[["s", "d", "k"]].copy()
    c["side"] = rng.choice([-1, 1], size=len(c))
    return _place(F, c, _stop_fraction(F, sig), _target_fraction(F, sig))


def control_random_time(h: Hypothesis, F: Features, sig: pd.DataFrame, rng) -> pd.DataFrame:
    """Same name, same day, same direction, same risk, a random decision bar
    AT OR AFTER the signal's -- never before it. Tests whether the edge lives
    in the trigger moment or survives a late entry.

    Drawing from the whole window was the second leak these controls found:
    the direction is only known once the break has happened, so entering long
    at 10:00 because the name WILL break out at 11:30 trades on the future. On
    a pure random walk that "control" earned +9 bps."""
    c = sig[["s", "d", "side"]].copy()
    k = sig["k"].to_numpy()
    c["k"] = k + np.floor(rng.random(len(c)) * (LAST_DECISION_BAR + 1 - k)).astype(int)
    return _place(F, c, _stop_fraction(F, sig), _target_fraction(F, sig))


def control_random_name(h: Hypothesis, F: Features, sig: pd.DataFrame, rng) -> pd.DataFrame:
    """Same moment, same direction, same risk, a random OTHER liquid name that
    had a bar then. Tests whether the SELECTION carried anything."""
    liquid = F.liquid
    has_bar = ~np.isnan(F.p.close)
    out_s = np.empty(len(sig), dtype=int)
    for i, (s, d, k) in enumerate(sig[["s", "d", "k"]].itertuples(index=False, name=None)):
        pool = np.flatnonzero(liquid[:, d] & has_bar[:, d, k])
        pool = pool[pool != s]
        out_s[i] = rng.choice(pool) if pool.size else s
    c = sig[["d", "k", "side"]].copy()
    c["s"] = out_s
    return _place(F, c, _stop_fraction(F, sig), _target_fraction(F, sig))


CONTROLS = {
    "random_side": control_random_side,
    "random_time": control_random_time,
    "random_name": control_random_name,
}

HYPOTHESES = {h.name: h for h in (H1OpeningRangeBreakout(), H2RelativeStrength(),
                                   H3aGapContinue(), H3bGapFade(), H3cGapFadeTop3(),
                                   H4VwapPullback(), H5BullishBreakoutChecklist())}
