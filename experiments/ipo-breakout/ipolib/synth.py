"""Synthetic post-listing series with known ground truth.

WHY. On real charts nobody can say where an IPO base starts, so recall and
precision cannot be computed, only argued about. Here the answer is in the
generator: if the detector cannot find a base that was DRAWN as a base, no
amount of tuning against real listings will rescue it.

THE NEGATIVE CONTROLS MATTER MORE THAN THE POSITIVES. A detector that fires on
everything scores 100% recall on the positive set. Only the things that LOOK
like an IPO base and are not -- a listing that gaps up and bleeds, a flat
post-listing drift, a random walk from a listing price, a base whose volume
expands, a base that never clears its pivot -- can show that.

THE GENERATOR IS VALIDATED, NOT TRUSTED. `pattern-detection/METHOD.md` records
that its generator was wrong three times and the detector was blamed each time.
So every positive drawn here is checked against its own truth dict before it is
returned -- the base really is that deep, that long, and the breakout bar really
does close above the pivot -- and `build_benchmark` refuses to hand back a
sample that fails its own specification. A generator bug now fails loudly at
construction instead of quietly as a recall number.

WHAT IS MODELLED THAT A GENERIC GENERATOR WOULD MISS. Bar 0 is a listing bar:
volume 10-40x what follows, and a close that frequently sits exactly on a
+-5% / +-20% circuit (RESEARCH.md 4.2). Without those the detector's
listing-bar exclusions are never exercised and would pass by not mattering.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from patlib.bars import Bars
# Domain-neutral bar assembly, reused rather than reimplemented: a close path,
# an intrabar range around it, a weekday date axis.
from patlib.synth import _assemble


@dataclass
class Sample:
    bars: Bars
    label: str                   # "ipo_base" | "none"
    variant: str = ""
    truth: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
def _listing_bar(bars: Bars, rng: np.random.Generator,
                 band_pct: float | None = None) -> Bars:
    """Turn bar 0 into a listing bar: huge volume, and often a circuit close.

    `band_pct` is the listing-day band (5 for issues under 250 crore, 20 above
    it). When the drawn day-one move would exceed it, the bar is clipped to the
    band and closes exactly on it -- which is what the real data does, and what
    makes "the listing-day high is not a supply level" testable.
    """
    open_ = float(bars.open[0])
    high, low, close = float(bars.high[0]), float(bars.low[0]), float(bars.close[0])

    if band_pct is not None:
        ceiling, floor = open_ * (1 + band_pct / 100), open_ * (1 - band_pct / 100)
        if high >= ceiling:
            high = close = ceiling
        if low <= floor:
            low = floor
            close = max(close, floor)
        close = min(max(close, low), high)

    volume = bars.volume.copy()
    volume[0] = float(np.median(volume[1:21])) * rng.uniform(10, 40)

    high_arr, low_arr, close_arr = bars.high.copy(), bars.low.copy(), bars.close.copy()
    high_arr[0], low_arr[0], close_arr[0] = high, low, close
    return Bars(bars.symbol, bars.date, bars.open, high_arr, low_arr, close_arr,
                volume, bars.timeframe)


def _path(rng: np.random.Generator, n: int, vol: float) -> np.ndarray:
    """Mean-reverting noise, zero-centred, in log space."""
    out = np.zeros(n)
    for i in range(1, n):
        out[i] = 0.90 * out[i - 1] + rng.normal(0, vol)
    return out


# --------------------------------------------------------------------------
# positives
# --------------------------------------------------------------------------
def make_ipo_base(rng: np.random.Generator, *, listing_price: float = 300.0,
                  pop_pct: float = 12.0, run_len: int = 14, run_pct: float = 22.0,
                  base_len: int = 16, depth_pct: float = 14.0,
                  after: int = 40, noise: float = 0.012,
                  band_pct: float | None = 20.0) -> Sample:
    """Listing -> run to a left-side high -> base -> breakout.

    The base drifts down into its low at roughly two-thirds of its length and
    recovers towards the pivot, with volume contracting through it and
    expanding on the breakout bar. That volume shape is the pattern's evidence,
    not decoration: a base with the right outline and rising volume is a
    different thing that looks similar.
    """
    close = [listing_price]
    close.append(listing_price * (1 + pop_pct / 100))          # bar 0 -> 1 pop

    # run to the left-side high
    peak = close[-1] * (1 + run_pct / 100)
    for i in range(1, run_len + 1):
        close.append(close[1] + (peak - close[1]) * (i / run_len))
    pivot_idx = len(close) - 1
    pivot_high = close[-1]

    # the base: down to the low at ~2/3, back up towards the pivot
    low_price = pivot_high * (1 - depth_pct / 100)
    low_at = max(2, int(base_len * 0.62))
    for i in range(1, base_len + 1):
        if i <= low_at:
            close.append(pivot_high + (low_price - pivot_high) * (i / low_at))
        else:
            frac = (i - low_at) / max(1, base_len - low_at)
            close.append(low_price + (pivot_high * 0.985 - low_price) * frac)
    breakout_idx = len(close)

    # the breakout and what follows
    close.append(pivot_high * 1.035)
    for _ in range(after):
        close.append(close[-1] * (1 + rng.normal(0.0016, 0.013)))

    close = np.asarray(close, float)
    close *= np.exp(_path(rng, len(close), noise * 0.45))

    # volume: heavy on the run, contracting through the base, expanding on the
    # breakout bar and decaying after it.
    volume = np.full(len(close), 1.0)
    volume[1:pivot_idx + 1] = rng.uniform(1.6, 2.4, pivot_idx)
    taper = np.linspace(1.15, 0.55, base_len)
    volume[pivot_idx + 1:breakout_idx] = taper[:breakout_idx - pivot_idx - 1]
    volume[breakout_idx] = rng.uniform(2.4, 4.0)
    volume[breakout_idx + 1:] = rng.uniform(0.8, 1.6, len(close) - breakout_idx - 1)
    volume = volume * rng.uniform(0.85, 1.15, len(close)) * 1e6

    bars = _assemble("SYNTH_IPO", close, volume, rng, noise)
    bars = _listing_bar(bars, rng, band_pct)
    return _enforce(Sample(bars, "ipo_base", f"d{int(depth_pct)}_l{base_len}", {
        "listing": 0, "pivot_idx": pivot_idx, "base_start": pivot_idx,
        "breakout": breakout_idx, "depth_pct": depth_pct, "base_len": base_len,
    }))


def _enforce(sample: Sample, breakout_pct: float = 3.5) -> Sample:
    """Make the drawn geometry exactly true of the ASSEMBLED bars.

    The close path is drawn first and the intrabar range is added afterwards,
    so a noise draw can put the pivot's INTRABAR HIGH above the breakout bar's
    close -- and then a sample labelled "ipo_base" contains no breakout. That
    is the generator being wrong, and on the first run it was wrong for 28 of
    150 positives. Rather than shrink the noise until it stops happening, the
    geometry is imposed after assembly:

      * nothing inside the base closes above the pivot high, so the breakout
        is the FIRST clearance and the label means what it says;
      * the breakout bar closes a fixed fraction above the pivot high;
      * the tail is re-based onto that close so the post-breakout drift is
        unchanged in shape.

    Depth is then MEASURED rather than asserted -- the drawn depth is a target,
    the measured one is the truth, and the benchmark reports cells by the
    measured value.
    """
    bars, truth = sample.bars, sample.truth
    pivot_idx, breakout_idx = truth["pivot_idx"], truth["breakout"]

    o, h, l, c = (bars.open.copy(), bars.high.copy(),
                  bars.low.copy(), bars.close.copy())
    pivot_high = float(np.max(h[1:pivot_idx + 1]))

    ceiling = pivot_high * 0.99
    span = slice(pivot_idx + 1, breakout_idx)
    for arr in (o, h, l, c):
        arr[span] = np.minimum(arr[span], ceiling)
    l[span] = np.minimum(l[span], c[span])
    h[span] = np.maximum(h[span], np.maximum(o[span], c[span]))

    target_close = pivot_high * (1 + breakout_pct / 100)
    scale = target_close / c[breakout_idx] if c[breakout_idx] > 0 else 1.0
    tail = slice(breakout_idx, len(c))
    for arr in (o, h, l, c):
        arr[tail] = arr[tail] * scale
    o[breakout_idx] = min(max(o[breakout_idx], l[breakout_idx]), h[breakout_idx])
    h[breakout_idx] = max(h[breakout_idx], c[breakout_idx])
    l[breakout_idx] = min(l[breakout_idx], o[breakout_idx], c[breakout_idx])

    base_low = float(np.min(l[pivot_idx + 1:breakout_idx]))
    truth = dict(truth)
    truth["pivot_high"] = pivot_high
    truth["measured_depth_pct"] = (pivot_high - base_low) / pivot_high * 100.0
    bars = Bars(bars.symbol, bars.date, o, h, l, c, bars.volume, bars.timeframe)
    return Sample(bars, sample.label, sample.variant, truth)


def make_ipo_base_with_shakeout(rng, *, undercut_pct: float = 4.0,
                                **kwargs) -> Sample:
    """A base whose LOW arrives near its end, as a shakeout before the breakout.

    The harder positive. `make_ipo_base` puts the low at ~62% of the base, which
    the detector's `low_not_at_the_end` check rewards; real bases frequently
    undercut late, shaking out the last holders one bar before the move. If the
    detector can only find the tidy version, recall on the clean set is
    measuring the generator rather than the rule.
    """
    sample = make_ipo_base(rng, **kwargs)
    pivot_idx, breakout_idx = sample.truth["pivot_idx"], sample.truth["breakout"]
    if breakout_idx - pivot_idx < 6:
        return sample

    o, h, l, c = (sample.bars.open.copy(), sample.bars.high.copy(),
                  sample.bars.low.copy(), sample.bars.close.copy())
    existing_low = float(np.min(l[pivot_idx + 1:breakout_idx]))
    at = breakout_idx - 2                       # two bars before the breakout
    new_low = existing_low * (1 - undercut_pct / 100)
    l[at] = new_low
    c[at] = min(c[at], new_low * 1.01)
    o[at] = min(max(o[at], l[at]), h[at])
    h[at] = max(h[at], o[at], c[at])

    truth = dict(sample.truth)
    pivot_high = truth["pivot_high"]
    truth["measured_depth_pct"] = (pivot_high - new_low) / pivot_high * 100.0
    truth["shakeout_at"] = at
    return Sample(Bars(sample.bars.symbol, sample.bars.date, o, h, l, c,
                       sample.bars.volume, sample.bars.timeframe),
                  "ipo_base", sample.variant + "_shake", truth)


# --------------------------------------------------------------------------
# negative controls: the things that look like one and are not
# --------------------------------------------------------------------------
def make_gap_and_bleed(rng, *, listing_price=300.0, pop_pct=45.0, n=140,
                       noise=0.014, band_pct=20.0) -> Sample:
    """The classic IPO failure: pops on listing, then bleeds for months.

    It HAS a left-side high -- the listing pop -- so a detector that only looks
    for a peak followed by a pullback will find one. What it does not have is a
    base: the low keeps arriving at the end of the window, and the pivot is
    never approached again.
    """
    close = [listing_price, listing_price * (1 + pop_pct / 100)]
    for i in range(n - 2):
        close.append(close[-1] * (1 - abs(rng.normal(0.004, 0.006))))
    close = np.asarray(close) * np.exp(_path(rng, n, noise * 0.5))
    volume = rng.uniform(0.6, 1.5, n) * 1e6
    volume[1] *= 2.5
    bars = _listing_bar(_assemble("SYNTH_BLEED", close, volume, rng, noise),
                        rng, band_pct)
    return Sample(bars, "none", "gap_and_bleed", {})


def make_flat_drift(rng, *, listing_price=300.0, n=140, noise=0.008,
                    band_pct=5.0) -> Sample:
    """Lists and goes nowhere. No run, no high worth the name, no breakout."""
    close = listing_price * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    volume = rng.uniform(0.5, 1.2, n) * 1e6
    bars = _listing_bar(_assemble("SYNTH_FLAT", close, volume, rng, noise),
                        rng, band_pct)
    return Sample(bars, "none", "flat_drift", {})


def make_random_walk(rng, *, listing_price=300.0, n=140, vol=0.017,
                     noise=0.013, band_pct=20.0) -> Sample:
    """A random walk from a listing price. The honest null."""
    close = listing_price * np.exp(np.cumsum(rng.normal(0, vol, n)))
    volume = rng.uniform(0.5, 1.8, n) * 1e6
    bars = _listing_bar(_assemble("SYNTH_RW", close, volume, rng, noise),
                        rng, band_pct)
    return Sample(bars, "none", "random_walk", {})


def make_expanding_base(rng, *, listing_price=300.0, run_len=14, run_pct=22.0,
                        base_len=18, depth_pct=16.0, n_after=40, noise=0.012,
                        band_pct=20.0) -> Sample:
    """The right OUTLINE with the wrong story: volume and range EXPAND.

    This is the control that the shape-only criteria cannot catch, and the
    reason volume contraction is scored rather than decorative.
    """
    sample = make_ipo_base(rng, listing_price=listing_price, run_len=run_len,
                           run_pct=run_pct, base_len=base_len,
                           depth_pct=depth_pct, after=n_after, noise=noise,
                           band_pct=band_pct)
    pivot_idx, breakout_idx = sample.truth["pivot_idx"], sample.truth["breakout"]
    volume = sample.bars.volume.copy()
    span = breakout_idx - pivot_idx - 1
    volume[pivot_idx + 1:breakout_idx] = (
        np.linspace(0.6, 2.2, span) * 1e6 * rng.uniform(0.9, 1.1, span))
    volume[breakout_idx] = 0.7e6                      # and no confirmation
    bars = Bars(sample.bars.symbol, sample.bars.date, sample.bars.open,
                sample.bars.high, sample.bars.low, sample.bars.close, volume,
                sample.bars.timeframe)
    return Sample(bars, "none", "expanding_base", {})


def make_base_that_fails(rng, *, listing_price=300.0, run_len=14, run_pct=22.0,
                         base_len=20, depth_pct=15.0, n_after=50, noise=0.012,
                         band_pct=20.0) -> Sample:
    """A genuine base that never clears its pivot.

    NOT a detector failure if it reports `forming` here -- that is the correct
    answer, and it is why `state` exists. It IS a failure to report `breakout`.
    Scored separately in `evaluate.py`.
    """
    sample = make_ipo_base(rng, listing_price=listing_price, run_len=run_len,
                           run_pct=run_pct, base_len=base_len,
                           depth_pct=depth_pct, after=n_after, noise=noise,
                           band_pct=band_pct)
    pivot_idx, breakout_idx = sample.truth["pivot_idx"], sample.truth["breakout"]
    pivot_high = float(np.max(sample.bars.high[1:pivot_idx + 1]))
    close = sample.bars.close.copy()
    high = sample.bars.high.copy()
    # Cap everything after the base at 97% of the pivot: the stock rolls over
    # instead of breaking out.
    ceiling = pivot_high * 0.97
    tail = slice(breakout_idx, len(close))
    close[tail] = np.minimum(close[tail], ceiling) * np.linspace(
        1.0, 0.85, len(close) - breakout_idx)
    high[tail] = np.maximum(np.minimum(high[tail], ceiling), close[tail])
    low = np.minimum(sample.bars.low, close)
    bars = Bars(sample.bars.symbol, sample.bars.date, sample.bars.open, high,
                low, close, sample.bars.volume, sample.bars.timeframe)
    return Sample(bars, "none", "base_that_fails",
                  {"pivot_idx": pivot_idx, "forming_is_correct": True})


def make_deep_collapse(rng, *, listing_price=300.0, run_len=12, run_pct=18.0,
                       n=140, noise=0.016, band_pct=20.0) -> Sample:
    """Runs, then falls more than 50% and stays there.

    Exceeds the pattern's own depth limit, so the hard gate should refuse it.
    Included because a detector that scores rather than gates would give this
    partial credit and call it a deep base.
    """
    close = [listing_price]
    peak = listing_price * (1 + run_pct / 100)
    for i in range(1, run_len + 1):
        close.append(listing_price + (peak - listing_price) * (i / run_len))
    remaining = n - len(close)
    floor = peak * 0.42
    for i in range(1, remaining + 1):
        close.append(peak + (floor - peak) * (i / remaining) ** 0.7)
    close = np.asarray(close) * np.exp(_path(rng, n, noise * 0.5))
    volume = rng.uniform(0.6, 1.8, n) * 1e6
    bars = _listing_bar(_assemble("SYNTH_DEEP", close, volume, rng, noise),
                        rng, band_pct)
    return Sample(bars, "none", "deep_collapse", {})


# --------------------------------------------------------------------------
def _validate_positive(sample: Sample) -> list[str]:
    """Check a drawn positive against its own truth dict.

    See the module docstring: the generator is the thing most likely to be
    wrong, and a wrong generator is indistinguishable from a wrong detector
    unless it is checked separately.
    """
    bars, truth = sample.bars, sample.truth
    pivot_idx, breakout_idx = truth["pivot_idx"], truth["breakout"]
    problems = []

    if not (1 <= pivot_idx < breakout_idx < len(bars)):
        problems.append(f"index order: pivot={pivot_idx} breakout={breakout_idx} "
                        f"len={len(bars)}")
        return problems

    pivot_high = float(np.max(bars.high[1:pivot_idx + 1]))
    if float(bars.close[breakout_idx]) <= pivot_high:
        problems.append("breakout bar does not close above the pivot")
    prior = bars.close[pivot_idx + 1:breakout_idx]
    if len(prior) and float(np.max(prior)) > pivot_high:
        problems.append("an earlier close in the base already cleared the pivot")

    # Depth is a TARGET, not an assertion -- `_enforce` measures what was
    # actually drawn and stores it. What must hold is that a positive stays
    # inside the depth limit the detector gates on, or it is not a positive.
    measured = truth.get("measured_depth_pct")
    if measured is None or not (0.5 < measured < 50.0):
        problems.append(f"measured depth {measured} is outside the pattern's own limit")

    if float(bars.volume[0]) < float(np.median(bars.volume[1:21])) * 5:
        problems.append("listing bar volume is not a listing bar's volume")
    return problems


NEGATIVE_MAKERS = (
    make_gap_and_bleed, make_flat_drift, make_random_walk,
    make_expanding_base, make_base_that_fails, make_deep_collapse,
)


def build_benchmark(seed: int = 7, n_per_cell: int = 6,
                    n_negative_each: int = 18) -> list[Sample]:
    """Positives across the published parameter ranges, plus negative controls.

    Raises if any positive fails its own specification -- see `_validate_positive`.
    """
    rng = np.random.default_rng(seed)
    samples: list[Sample] = []

    for base_len in (8, 12, 16, 22, 30):
        for depth_pct in (6.0, 12.0, 20.0, 32.0, 45.0):
            for _ in range(n_per_cell):
                samples.append(make_ipo_base(
                    rng,
                    listing_price=float(rng.uniform(60, 1200)),
                    pop_pct=float(rng.uniform(-5, 35)),
                    run_len=int(rng.integers(8, 22)),
                    run_pct=float(rng.uniform(12, 45)),
                    base_len=base_len,
                    depth_pct=depth_pct,
                    after=int(rng.integers(30, 70)),
                    noise=float(rng.uniform(0.008, 0.018)),
                    band_pct=float(rng.choice([5.0, 20.0])),
                ))

    # The harder positive family: the same cells, shaken out late.
    for base_len in (12, 16, 22, 30):
        for depth_pct in (10.0, 20.0, 32.0):
            for _ in range(max(1, n_per_cell // 2)):
                samples.append(make_ipo_base_with_shakeout(
                    rng,
                    listing_price=float(rng.uniform(60, 1200)),
                    pop_pct=float(rng.uniform(-5, 35)),
                    run_len=int(rng.integers(8, 22)),
                    run_pct=float(rng.uniform(12, 45)),
                    base_len=base_len,
                    depth_pct=depth_pct,
                    after=int(rng.integers(30, 70)),
                    noise=float(rng.uniform(0.008, 0.018)),
                    band_pct=float(rng.choice([5.0, 20.0])),
                ))

    broken = [(s.variant, _validate_positive(s)) for s in samples]
    broken = [(v, p) for v, p in broken if p]
    if broken:
        lines = "\n".join(f"  {v}: {'; '.join(p)}" for v, p in broken[:10])
        raise AssertionError(
            f"the GENERATOR is wrong, not the detector -- {len(broken)} of "
            f"{len(samples)} drawn positives fail their own truth dict:\n{lines}")

    for maker in NEGATIVE_MAKERS:
        for _ in range(n_negative_each):
            samples.append(maker(rng, listing_price=float(rng.uniform(60, 1200))))
    return samples
