"""The IPO base detector: IBD's rules (RESEARCH.md section 1) as arithmetic
over post-listing candles.

CAUSALITY. `detect(bars, t)` sees bars `0..t` and nothing else. Bar 0 is the
listing bar. Every window is closed at or before `t`, and the two windows that
decide the outcome -- the pivot and the volume baseline -- are closed strictly
BEFORE it. Both of those were bugs in the VCP work and both are re-made easily:

  * THE PIVOT MUST NOT SEE BAR t. Defining it as "the highest high so far"
    includes the breakout bar, so no close can exceed it and the detector
    emits nothing, forever, silently. The IPO base is structurally immune
    because its pivot is the LEFT-side high -- formed early, by definition in
    the past -- which is one reason to implement the published rule rather
    than a plausible variant of it.
  * THE VOLUME BASELINE MUST NOT SEE BAR t. A breakout bar is 2-3x average by
    definition; include it and the confirming expansion reads as a failure to
    contract.

AND ONE SPECIFIC TO THIS PROBLEM. Bar 0 is not a normal bar (RESEARCH.md 4.3):
a shorter session, a +-5%/+-20% band measured off an auction price, and volume
that never recurs. It is excluded from every average and, by default, from the
pivot search -- a "breakout above the listing-day high" is often a breakout
above a circuit limit, which is a fact about the rulebook and not about supply.
`allow_listing_bar_pivot` flips that so the alternative can be measured rather
than assumed; see SETUPS below.

NO SMOOTHED INDICATORS. Ranges inside the base are raw true range averaged over
the window measured, never a Wilder ATR: a 14-period Wilder average has barely
adapted to a 12-bar base by the time the base is over. That single fix took VCP
recall from 52% to 89%.

SETUPS. RESEARCH.md section 2 finds only one of the three candidate breakouts is
published. All three are implemented so the other two can be measured:

    "base"     C. above the left-side high of a 2-5 week base   <- IBD's rule
    "day1"     A. above the listing-day high
    "week1"    B. above the high of the first five sessions
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from patlib.base import Detection
from patlib.bars import Bars
from patlib.indicators import true_range

PATTERN = "ipo_base"


@dataclass(frozen=True)
class IPOBaseParams:
    """Every threshold, in one place, so `scripts/sweep.py` can move one."""

    # --- history -----------------------------------------------------------
    # The STRUCTURAL minimum: one bar for the pivot (bar 0 is the listing bar
    # and is excluded) plus a minimum base. Nothing more.
    #
    # This was 30 for one benchmark run, on the reasoning in RESEARCH.md
    # section 7 that ~30 sessions are needed before any criterion can be
    # computed. That reasoning was wrong, and wrong in the same SHAPE as the
    # VCP pivot bug: a window that silently excludes the thing being looked
    # for. A 14-bar run into an 8-bar base breaks out around bar 23, so a
    # floor of 30 refused exactly the short bases IBD calls typical --
    # recall was 17% at 8 bars and 100% at 30, and all 52 misses carried the
    # reason "breakout before min_history". The volume baseline does not need
    # the floor either: it takes whatever sessions exist.
    min_history: int = 0     # 0 means "derive it", see effective_min_history

    # --- the left-side high (the pivot) ------------------------------------
    # "New issues tend to carve out a left-side high within the first 25 days
    # of trading" -- IBD.
    left_high_window: int = 25
    # Bar 0's high is an auction price plus a circuit, not a supply level.
    allow_listing_bar_pivot: bool = False
    # Clearance above the pivot that counts as through it. IBD says 10 cents on
    # a US quote; as a fraction that is a few basis points, so this is the
    # same idea expressed in a way that survives a 6-rupee stock and a
    # 4,000-rupee one.
    pivot_buffer_pct: float = 0.1

    # --- the base ----------------------------------------------------------
    # "usually two to five weeks", "as short as seven days" -- IBD. The upper
    # bound is deliberately looser than five weeks so a sweep can find where it
    # stops paying instead of being told.
    min_base_len: int = 7
    max_base_len: int = 40
    # "most of the time less than 20%"; "as deep as 50%" when volatile.
    normal_depth_pct: float = 20.0
    max_depth_pct: float = 50.0

    # --- the deadline ------------------------------------------------------
    # An IPO setup has to expire, and finding that out was the first thing the
    # detector taught. Left unbounded, "above the listing-day high" fired on
    # WINDLAS in January 2024 -- two years and five months after its August
    # 2021 listing. That is a trade in a two-year-old stock, not an IPO
    # breakout, and it would have quietly entered the comparison as one.
    # Twelve months is the outer edge of what IBD calls a new issue.
    max_age_sessions: int = 250

    # --- confirmation ------------------------------------------------------
    # O'Neil's 40% above average, as a ratio against a trailing MEDIAN rather
    # than a mean: one listing-adjacent spike drags a mean for fifty sessions.
    breakout_volume_ratio: float = 1.4
    volume_lookback: int = 30
    # Volume in the base should be quieter than the run-up into its high.
    dryup_ratio: float = 0.85

    # --- scoring -----------------------------------------------------------
    require_hard_gates: bool = True
    # O'Neil states the breakout volume rule as a requirement, not a
    # preference, and the benchmark agrees: scored rather than gated, the
    # detector signalled a breakout on 18 of 18 `expanding_base` controls --
    # the shape of a base with the volume story inverted, which is the one
    # negative control shape alone cannot catch.
    require_breakout_volume: bool = True
    min_score: float = 0.0


PROFILES = {
    # The textbook, as written.
    "strict": IPOBaseParams(),
    # How the pattern is labelled in practice: longer bases tolerated, depth
    # to the 50% limit, volume confirmation wanted but not demanded.
    "relaxed": IPOBaseParams(
        left_high_window=35, min_base_len=5, max_base_len=60,
        max_age_sessions=250,
        normal_depth_pct=30.0, breakout_volume_ratio=1.0, dryup_ratio=1.10,
        require_hard_gates=False, require_breakout_volume=False,
    ),
}


def effective_min_history(p: IPOBaseParams) -> int:
    """Earliest bar at which a base can structurally exist.

    `min_history=0` means "derive it": bar 0 is the listing bar, the pivot
    needs at least bar 1, and the base needs `min_base_len` bars after the
    pivot. An explicit non-zero value overrides, so a sweep can ask what a
    floor costs.
    """
    return p.min_history or (1 + p.min_base_len)


def params(profile: str = "strict", **overrides) -> IPOBaseParams:
    base = PROFILES.get(profile)
    if base is None:
        raise KeyError(f"unknown profile {profile!r}; have {sorted(PROFILES)}")
    return replace(base, **overrides) if overrides else base


# --------------------------------------------------------------------------
def _score(checks: dict[str, tuple[bool, float]]) -> tuple[float, list[str]]:
    """Weighted pass fraction, plus the names of everything that failed."""
    total = sum(weight for _passed, weight in checks.values())
    earned = sum(weight for passed, weight in checks.values() if passed)
    failed = [name for name, (passed, _w) in checks.items() if not passed]
    return (earned / total if total else 0.0), failed


def _pivot_index(bars: Bars, t: int, p: IPOBaseParams) -> int | None:
    """Index of the left-side high: the highest high early in the listing's life.

    Searched over bars `first .. min(left_high_window, t - min_base_len)`, so
    the window closes `min_base_len` bars BEFORE `t`. That is what makes the
    pivot knowable at `t` without containing it.
    """
    first = 0 if p.allow_listing_bar_pivot else 1
    last = min(p.left_high_window, t - p.min_base_len)
    if last < first:
        return None
    window = bars.high[first:last + 1]
    if not len(window):
        return None
    return first + int(np.argmax(window))


def _volume_baseline(bars: Bars, t: int, p: IPOBaseParams) -> float:
    """Median volume over the sessions before `t`, excluding the listing bar.

    Excludes `t` itself -- see the module docstring -- and bar 0, whose volume
    is a multiple of everything that follows and would otherwise set a
    threshold no ordinary breakout could clear.
    """
    start = max(1, t - p.volume_lookback)
    window = bars.volume[start:t]
    window = window[window > 0]
    return float(np.median(window)) if len(window) else 0.0


def detect(bars: Bars, t: int | None = None, p: IPOBaseParams | None = None,
           setup: str = "base") -> Detection | None:
    """The IPO base as of bar `t`, or None if there is not one.

    Returns a Detection whose `state` is "forming" (a base exists, price has
    not cleared the pivot) or "breakout" (this bar's close cleared it). Never a
    bare boolean: "there is a base here and it has not triggered" is the state
    the strategy spends most of its time in, and collapsing it into False
    throws away the only warning the pattern gives.
    """
    p = p or params()
    t = len(bars) - 1 if t is None else t
    if t < 0 or t >= len(bars):
        return None
    # `t` counts sessions since the listing bar, so the deadline is a plain
    # index comparison -- no calendar arithmetic and no listing date needed.
    if t > p.max_age_sessions:
        return None

    if setup in ("day1", "week1"):
        return _detect_simple_breakout(bars, t, p, setup)

    # --- hard gates: structural impossibilities ----------------------------
    if t < effective_min_history(p):
        return None
    pivot_idx = _pivot_index(bars, t, p)
    if pivot_idx is None:
        return None

    pivot_high = float(bars.high[pivot_idx])
    if pivot_high <= 0:
        return None
    base_len = t - pivot_idx
    if not (p.min_base_len <= base_len <= p.max_base_len):
        return None

    base_low = float(np.min(bars.low[pivot_idx + 1:t + 1]))
    depth_pct = (pivot_high - base_low) / pivot_high * 100.0
    if p.require_hard_gates and depth_pct > p.max_depth_pct:
        return None

    pivot_price = pivot_high * (1.0 + p.pivot_buffer_pct / 100.0)

    # The first close through the pivot IS the breakout. If an earlier close in
    # this base already cleared it, this bar is a continuation of a move that
    # already happened, and signalling it would book an entry days late at a
    # price the rule never offered.
    prior = bars.close[pivot_idx + 1:t]
    if len(prior) and float(np.max(prior)) > pivot_price:
        return None

    close_t = float(bars.close[t])
    state = "breakout" if close_t > pivot_price else "forming"

    # --- soft criteria -----------------------------------------------------
    baseline = _volume_baseline(bars, t, p)
    breakout_volume_ratio = (float(bars.volume[t]) / baseline) if baseline else np.nan

    # Same boundary as the range window below: the base ends where the
    # breakout begins.
    base_volume = bars.volume[pivot_idx + 1:t]
    base_volume = base_volume[base_volume > 0]
    run_up = bars.volume[max(1, pivot_idx - 5):pivot_idx + 1]
    run_up = run_up[run_up > 0]
    dryup = (float(np.median(base_volume)) / float(np.median(run_up))
             if len(base_volume) and len(run_up) else np.nan)

    # Tightening near the highs: raw true range, not a smoothed ATR, because
    # the window is a dozen bars long.
    #
    # THE LATE WINDOW ENDS AT t-1, NOT t. The breakout bar's range is wide by
    # definition -- that is what a breakout is -- so measuring "has the range
    # contracted?" through it reads the confirming expansion as a failure to
    # contract. It is the volume-dry-up trap in its range form, and it was
    # made here before it was caught. The base is bars pivot_idx+1 .. t-1; the
    # breakout bar is not part of the base it breaks out of.
    tr = true_range(bars)
    base_end = t - 1 if state == "breakout" else t
    span = base_end - pivot_idx
    third = max(2, span // 3) if span >= 2 else 0
    if third:
        early_tr = float(np.mean(tr[pivot_idx + 1:pivot_idx + 1 + third]))
        late_tr = float(np.mean(tr[base_end - third + 1:base_end + 1]))
        early_ref = float(np.mean(bars.close[pivot_idx + 1:pivot_idx + 1 + third]))
        late_ref = float(np.mean(bars.close[base_end - third + 1:base_end + 1]))
        tighten = ((late_tr / late_ref) / (early_tr / early_ref)
                   if early_tr > 0 and early_ref > 0 and late_ref > 0 else np.nan)
    else:
        tighten = np.nan

    # Where the base's low sits. A base whose low is its last bar is not a
    # base, it is a downtrend that has not finished.
    low_idx = pivot_idx + 1 + int(np.argmin(bars.low[pivot_idx + 1:t + 1]))
    low_position = (low_idx - pivot_idx) / base_len if base_len else 1.0

    checks: dict[str, tuple[bool, float]] = {
        "depth_normal": (depth_pct <= p.normal_depth_pct, 2.0),
        "volume_dryup": (not np.isnan(dryup) and dryup <= p.dryup_ratio, 2.0),
        "tightening": (not np.isnan(tighten) and tighten <= 1.0, 1.5),
        "low_not_at_the_end": (low_position <= 0.75, 1.5),
        "base_length_typical": (p.min_base_len <= base_len <= 25, 1.0),
    }
    if state == "breakout":
        confirmed = (not np.isnan(breakout_volume_ratio)
                     and breakout_volume_ratio >= p.breakout_volume_ratio)
        # A breakout without its volume is not a weaker breakout, it is a
        # different event: the pivot cleared with nobody behind it. Demoted
        # to `forming` rather than dropped, so the base stays on the
        # watchlist and the caller can see why it did not trigger.
        if p.require_breakout_volume and not confirmed:
            state = "forming"
        checks["breakout_volume"] = (confirmed, 2.0)

    score, failed = _score(checks)
    if score < p.min_score:
        return None

    return Detection(
        pattern=PATTERN,
        variant=f"base{base_len}",
        symbol=bars.symbol,
        timeframe=bars.timeframe,
        start_idx=pivot_idx,
        end_idx=t,
        pivot_price=pivot_price,
        # Stop: the base low. The pattern's claim is that supply above the
        # pivot is exhausted; a return through the base low says it was not.
        # FINAL_LOGIC.md section 3 has the full specification, including the
        # ATR floor that stops a 1%-wide base sizing an absurd position.
        stop_price=base_low,
        state=state,
        score=score,
        reasons=failed,
        metrics={
            "setup": "base",
            "pivot_idx": pivot_idx,
            "pivot_high": pivot_high,
            "pivot_session": pivot_idx,          # sessions after listing
            "base_len": base_len,
            "depth_pct": depth_pct,
            "base_low": base_low,
            "low_position": low_position,
            "dryup": dryup,
            "tighten": tighten,
            "breakout_volume_ratio": breakout_volume_ratio,
            "close": close_t,
            "distance_to_pivot_pct": (pivot_price / close_t - 1) * 100.0,
        },
        start_date=str(bars.date[pivot_idx]),
        end_date=str(bars.date[t]),
    )


def _detect_simple_breakout(bars: Bars, t: int, p: IPOBaseParams,
                            setup: str) -> Detection | None:
    """Setups A and B: above the listing-day high, or above the first week's.

    Neither is a published setup (RESEARCH.md section 2) and both are
    mechanically contaminated by the listing-day band. They exist so that
    claim can be measured rather than asserted.
    """
    reference_end = 0 if setup == "day1" else 4
    if t <= reference_end + p.min_base_len or reference_end >= len(bars):
        return None

    pivot_high = float(np.max(bars.high[:reference_end + 1]))
    if pivot_high <= 0:
        return None
    pivot_price = pivot_high * (1.0 + p.pivot_buffer_pct / 100.0)

    prior = bars.close[reference_end + 1:t]
    if len(prior) and float(np.max(prior)) > pivot_price:
        return None

    close_t = float(bars.close[t])
    state = "breakout" if close_t > pivot_price else "forming"
    low = float(np.min(bars.low[reference_end + 1:t + 1]))
    baseline = _volume_baseline(bars, t, p)
    volume_ratio = (float(bars.volume[t]) / baseline) if baseline else np.nan

    return Detection(
        pattern=PATTERN, variant=setup, symbol=bars.symbol,
        timeframe=bars.timeframe, start_idx=reference_end, end_idx=t,
        pivot_price=pivot_price, stop_price=low, state=state,
        score=1.0, reasons=[],
        metrics={
            "setup": setup,
            "pivot_high": pivot_high,
            "base_len": t - reference_end,
            "depth_pct": (pivot_high - low) / pivot_high * 100.0,
            "base_low": low,
            "breakout_volume_ratio": volume_ratio,
            "close": close_t,
            "distance_to_pivot_pct": (pivot_price / close_t - 1) * 100.0,
        },
        start_date=str(bars.date[reference_end]),
        end_date=str(bars.date[t]),
    )


def earliest_bar(p: IPOBaseParams, setup: str) -> int:
    """First index at which `setup` could produce a detection.

    Setups A and B need only their reference window plus a minimum hold,
    so starting them at `min_history` like the base setup would skip the
    bars on which they actually fire -- and then, because an earlier close
    above the pivot disqualifies every later bar, report nothing at all.
    That is a silent zero, which is the worst kind.
    """
    if setup == "day1":
        return p.min_base_len + 1
    if setup == "week1":
        return p.min_base_len + 5
    return effective_min_history(p)


def scan(bars: Bars, p: IPOBaseParams | None = None, setup: str = "base",
         start: int | None = None, stop: int | None = None,
         step: int = 1) -> list[Detection]:
    """Walk the as-of bar forward and report every detection, causally."""
    p = p or params()
    start = earliest_bar(p, setup) if start is None else start
    limit = min(len(bars), p.max_age_sessions + 1)
    stop = limit if stop is None else min(stop, limit)
    out = []
    for t in range(start, stop, step):
        detection = detect(bars, t, p, setup=setup)
        if detection is not None:
            out.append(detection)
    return out


def first_breakout(bars: Bars, p: IPOBaseParams | None = None,
                   setup: str = "base") -> Detection | None:
    """The first bar whose close clears the pivot -- the tradable signal.

    One per listing by construction: the pivot is fixed by the left-side high,
    and `detect` refuses once an earlier close has already cleared it.
    """
    p = p or params()
    for t in range(earliest_bar(p, setup),
                   min(len(bars), p.max_age_sessions + 1)):
        detection = detect(bars, t, p, setup=setup)
        if detection is not None and detection.state == "breakout":
            return detection
    return None
