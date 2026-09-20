"""Synthetic OHLCV with known ground truth.

Why bother, when real charts exist: on real data nobody can say where the
cup starts, so precision and recall cannot be computed, only argued about.
Here the answer is in the generator. If a detector cannot find a cup that
was DRAWN as a cup, no amount of tuning against real charts will save it.

The negative controls matter more than the positives. A detector that fires
on everything scores 100% recall on the positive set; only V-bottoms,
rectangles, random walks and expanding-volatility bases reveal that.

Bars are built the same way for every pattern: a close path, then an
intrabar range around it scaled by `noise`, then volume from a per-pattern
profile. So a detector cannot learn the generator instead of the shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .bars import Bars


@dataclass
class Sample:
    bars: Bars
    label: str                   # "cup_and_handle" | "vcp" | "triangle" | "none"
    variant: str = ""
    truth: dict = field(default_factory=dict)   # e.g. pivot index, depth


def _ohlc_from_close(close: np.ndarray, rng: np.random.Generator,
                     noise: float | np.ndarray, base_price: float) -> tuple:
    n = len(close)
    span = np.abs(close) * np.asarray(noise, dtype=float)
    span = np.maximum(span, base_price * 0.002)
    up = np.abs(rng.normal(0, 1, n)) * span
    dn = np.abs(rng.normal(0, 1, n)) * span
    high = close + up
    low = close - dn
    op = np.empty(n)
    op[0] = close[0]
    op[1:] = close[:-1] + rng.normal(0, 1, n - 1) * span[1:] * 0.4
    op = np.clip(op, low, high)
    high = np.maximum.reduce([high, op, close])
    low = np.minimum.reduce([low, op, close])
    return op, high, low


def _assemble(name: str, close: np.ndarray, volume: np.ndarray,
              rng: np.random.Generator, noise) -> Bars:
    close = np.asarray(close, float)
    o, h, l = _ohlc_from_close(close, rng, noise, float(close[0]))
    dates = np.arange(np.datetime64("2015-01-01"),
                      np.datetime64("2015-01-01") + np.timedelta64(len(close) * 2, "D"),
                      dtype="datetime64[D]")
    dates = np.array([d for d in dates
                      if (d.astype(int) + 3) % 7 < 5][:len(close)], dtype="datetime64[D]")
    return Bars(name, dates, o, h, l, close, np.maximum(volume, 1.0), "D")


def _noise_path(n: int, rng, vol: float, phi: float = 0.90) -> np.ndarray:
    """Stationary AR(1) wobble, as a fractional multiplier on the shape.

    NOT a cumulative random walk. A cumsum of N(0, 1%) over 120 bars drifts
    10-20%, which silently tilts a drawn cup's right rim well away from its
    left one -- so the generator would be emitting samples that are not the
    pattern it labelled them, and the detector would be blamed for it. An
    AR(1) with phi=0.9 gives noise that looks serially correlated, like real
    price does, while its standard deviation stays at `vol` forever.
    """
    sigma = vol * np.sqrt(1.0 - phi * phi)
    e = np.empty(n)
    e[0] = rng.normal(0, vol)
    for i in range(1, n):
        e[i] = phi * e[i - 1] + rng.normal(0, sigma)
    return e


def _range_profile(n: int, scale: np.ndarray | None, noise: float) -> np.ndarray:
    """Per-bar intrabar range, scaled by a profile.

    A constant intrabar range across a tightening base is not a tightening
    base -- it is a tightening CLOSE path with the same daily volatility all
    the way through, and ATR would never contract. Generating it that way
    quietly made the synthetic VCPs untestable against the one criterion the
    pattern is named for, so the range is scaled bar by bar instead.
    """
    if scale is None:
        return np.full(n, noise)
    sc = np.asarray(scale, float)
    sc = sc / max(sc.max(), 1e-9)
    return noise * np.clip(sc, 0.22, 1.0)


# --------------------------------------------------------------- positives

def make_cup_and_handle(rng: np.random.Generator, *, lead=40, cup_len=90,
                        handle_len=14, depth=0.25, handle_retrace=0.33,
                        u_power=2.0, run_after=25, price=100.0,
                        noise=0.010, vol_noise=0.010) -> Sample:
    """`u_power` is the shape dial: 2.0 is a parabola (U), 1.0 is a V.

    `handle_retrace` is the handle's depth as a fraction of the CUP's depth,
    not of price. That is how O'Neil states it, and it matters: a 13%-deep
    handle on a 14%-deep cup is not a shallow handle, it is a handle below
    the cup's midpoint, which his own rule forbids. Parameterised the other
    way round, the generator emitted samples labelled cup-and-handle that
    were not cup-and-handles, and the detector was blamed for rejecting them.
    """
    handle_depth = min(depth * handle_retrace, 0.15)
    adv = price * 0.45
    lead_p = price - adv + adv * np.linspace(0, 1, lead) ** 0.85
    rim = lead_p[-1]
    x = np.linspace(-1, 1, cup_len)
    cup = rim - rim * depth * (1 - np.abs(x) ** u_power)
    h = rim * handle_depth * np.sin(np.linspace(0, np.pi, handle_len)) ** 0.7
    handle = rim - h - np.linspace(0, rim * handle_depth * 0.25, handle_len)
    after = handle[-1] + (rim * 0.30) * np.linspace(0, 1, run_after) ** 0.8
    close = np.concatenate([lead_p, cup, handle, after])
    close = close * (1 + _noise_path(len(close), rng, vol_noise))

    v = np.empty(len(close))
    v[:lead] = rng.lognormal(13.8, 0.25, lead)
    # volume recedes through the cup and dries up in the handle
    v[lead:lead + cup_len] = rng.lognormal(
        13.8 - 0.55 * (1 - np.abs(np.linspace(-1, 1, cup_len))), 0.25, cup_len)
    s = lead + cup_len
    v[s:s + handle_len] = rng.lognormal(13.1, 0.20, handle_len)
    v[s + handle_len:] = rng.lognormal(14.2, 0.30, run_after)
    v[s + handle_len] *= 2.2                     # the breakout bar

    b = _assemble("SYN_CUP", close, v, rng, noise)
    return Sample(b, "cup_and_handle", "with_handle", truth=dict(
        cup_start=lead, cup_low=lead + cup_len // 2, right_rim=lead + cup_len - 1,
        handle_start=s, breakout=s + handle_len, depth=depth,
        handle_retrace=handle_retrace, handle_depth=handle_depth,
        pivot=float(max(close[lead], close[s - 1])),
    ))


def make_vcp(rng: np.random.Generator, *, lead=40, depths=(0.22, 0.12, 0.06),
             leg_len=18, run_after=25, price=100.0, noise=0.010,
             vol_noise=0.008) -> Sample:
    adv = price * 0.5
    close = list(price - adv + adv * np.linspace(0, 1, lead) ** 0.85)
    vol = list(rng.lognormal(13.9, 0.25, lead))
    top = close[-1]
    starts = []
    scale = [1.0] * lead
    d0 = depths[0]
    for i, d in enumerate(depths):
        n = max(6, int(leg_len * (0.85 ** i)))
        starts.append(len(close))
        lo = top * (1 - d)
        close += list(np.linspace(close[-1], lo, n // 2))
        close += list(np.linspace(lo, top * (1 - d * 0.12), n - n // 2))
        # daily range and volume both taper with the contraction's own depth
        vol += list(rng.lognormal(13.9 + np.log(max(d / d0, 0.20)) * 0.55, 0.20, n))
        scale += [max(d / d0, 0.22)] * n
        top = max(top, close[-1])
    bo = len(close)
    close += list(close[-1] * (1 + 0.28 * np.linspace(0, 1, run_after) ** 0.8))
    vol += list(rng.lognormal(14.3, 0.30, run_after))
    scale += [1.0] * run_after
    vol[bo] *= 2.4
    prof = _range_profile(len(close), np.asarray(scale), 1.0)
    close = np.asarray(close) * (1 + _noise_path(len(close), rng, vol_noise) * prof)
    b = _assemble("SYN_VCP", close, np.asarray(vol),
                  rng, _range_profile(len(close), np.asarray(scale), noise))
    return Sample(b, "vcp", f"{len(depths)}T", truth=dict(
        base_start=starts[0], breakout=bo, depths=list(depths), t_count=len(depths)))


def make_triangle(rng: np.random.Generator, kind="ascending", *, lead=30,
                  length=60, height=0.22, price=100.0, legs=6, run_after=25,
                  noise=0.009, vol_noise=0.007) -> Sample:
    """kind: ascending | descending | symmetrical | rising_wedge |
    falling_wedge | broadening"""
    close = list(price * (1 + 0.30 * np.linspace(0, 1, lead) ** 0.9))
    p0 = close[-1]
    hi0, lo0 = p0, p0 * (1 - height)
    if kind == "ascending":
        hi1, lo1 = hi0, p0 * (1 - height * 0.12)
    elif kind == "descending":
        hi1, lo1 = p0 * (1 - height * 0.88), lo0
    elif kind == "symmetrical":
        hi1, lo1 = p0 * (1 - height * 0.40), p0 * (1 - height * 0.60)
    elif kind == "rising_wedge":
        hi0, lo0 = p0, p0 * (1 - height)
        hi1, lo1 = p0 * 1.10, p0 * 1.04
    elif kind == "falling_wedge":
        hi1, lo1 = p0 * (1 - height * 1.5), p0 * (1 - height * 1.9)
    elif kind == "broadening":
        hi0, lo0 = p0 * 0.98, p0 * 0.94
        hi1, lo1 = p0 * 1.12, p0 * 0.80
    else:
        raise ValueError(kind)

    seg = max(4, length // legs)
    tpos, cur, up = 0, close[-1], False
    while tpos < length:
        f0, f1 = tpos / length, min(1.0, (tpos + seg) / length)
        tgt = (hi0 + (hi1 - hi0) * f1) if up else (lo0 + (lo1 - lo0) * f1)
        close += list(np.linspace(cur, tgt, seg))
        cur, up, tpos = tgt, not up, tpos + seg
    bo = len(close)
    direction = 1 if kind in ("ascending", "symmetrical", "falling_wedge") else -1
    close += list(close[-1] * (1 + direction * 0.25 * np.linspace(0, 1, run_after) ** 0.8))
    n = len(close)
    vol = list(rng.lognormal(13.9, 0.25, lead))
    vol += list(rng.lognormal(13.9, 0.22, bo - lead)
                * np.linspace(1.0, 0.55, bo - lead))
    vol += list(rng.lognormal(14.2, 0.30, n - bo))
    vol[bo] *= 2.2
    close = np.asarray(close) * (1 + _noise_path(n, rng, vol_noise))
    b = _assemble(f"SYN_TRI_{kind.upper()}", close, np.asarray(vol), rng, noise)
    return Sample(b, "triangle", kind, truth=dict(start=lead, breakout=bo))


# --------------------------------------------------------------- negatives

def make_random_walk(rng, n=200, price=100.0, vol=0.016, noise=0.010) -> Sample:
    close = price * np.exp(np.cumsum(rng.normal(0.0002, vol, n)))
    vol_s = rng.lognormal(13.9, 0.35, n)
    return Sample(_assemble("NEG_RW", close, vol_s, rng, noise), "none", "random_walk")


def make_trend(rng, n=200, price=100.0, slope=0.0035, noise=0.010) -> Sample:
    close = price * np.exp(np.cumsum(rng.normal(slope, 0.012, n)))
    return Sample(_assemble("NEG_TREND", close, rng.lognormal(13.9, 0.3, n), rng, noise),
                  "none", "trend")


def make_v_bottom(rng, **kw) -> Sample:
    """A V is the classic false positive for a cup, so it is a NEGATIVE."""
    s = make_cup_and_handle(rng, u_power=1.0, **kw)
    s.bars = Bars("NEG_V", *[getattr(s.bars, f) for f in
                             ("date", "open", "high", "low", "close", "volume")], "D")
    return Sample(s.bars, "none", "v_bottom", truth=s.truth)


def make_rectangle(rng, lead=30, length=70, height=0.14, price=100.0,
                   legs=6, run_after=20, noise=0.009) -> Sample:
    """Parallel lines, not converging: must NOT read as a triangle."""
    close = list(price * (1 + 0.25 * np.linspace(0, 1, lead) ** 0.9))
    p0 = close[-1]
    hi, lo = p0, p0 * (1 - height)
    seg = max(4, length // legs)
    cur, up, tpos = close[-1], False, 0
    while tpos < length:
        tgt = hi if up else lo
        close += list(np.linspace(cur, tgt, seg))
        cur, up, tpos = tgt, not up, tpos + seg
    close += list(close[-1] * (1 + 0.20 * np.linspace(0, 1, run_after)))
    close = np.asarray(close) * (1 + _noise_path(len(close), rng, 0.007))
    v = rng.lognormal(13.9, 0.25, len(close))
    return Sample(_assemble("NEG_RECT", close, v, rng, noise), "none", "rectangle")


def make_expanding_vol(rng, lead=30, depths=(0.05, 0.11, 0.22), leg_len=18,
                       price=100.0, noise=0.010) -> Sample:
    """A VCP run backwards: contractions WIDENING. Must not read as a VCP."""
    s = make_vcp(rng, lead=lead, depths=depths, leg_len=leg_len, price=price, noise=noise)
    return Sample(Bars("NEG_EXPAND", *[getattr(s.bars, f) for f in
                                       ("date", "open", "high", "low", "close", "volume")], "D"),
                  "none", "expanding_volatility")


def make_head_and_shoulders(rng, lead=30, price=100.0, noise=0.010) -> Sample:
    """Three peaks, middle highest -- a top, not a base."""
    p = [price * (1 + 0.3 * f ** 0.9) for f in np.linspace(0, 1, lead)]
    base = p[-1]
    for pk, n in ((1.08, 16), (0.94, 14), (1.16, 18), (0.93, 14), (1.07, 16), (0.82, 22)):
        p += list(np.linspace(p[-1], base * pk, n))
    close = np.asarray(p) * (1 + _noise_path(len(p), rng, 0.008))
    v = rng.lognormal(13.9, 0.3, len(close))
    return Sample(_assemble("NEG_HNS", close, v, rng, noise), "none", "head_and_shoulders")


NEGATIVE_MAKERS = [make_random_walk, make_trend, make_v_bottom, make_rectangle,
                   make_expanding_vol, make_head_and_shoulders]
