"""What the bars say: new bases, and what each open position now owes.

PURE. No database, no feed, no clock -- bars in, verdicts out -- so every rule
here is testable on fixed bars and so the execution service cannot quietly
apply a different version of one.

Each function mirrors a specific piece of the backtest, named in its
docstring. Where the live rule has to differ (it decides on a close and acts
at the next open), the difference is stated where it happens.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np

from src.vcp.database.db_models.vcp_models import (
    EXIT_CAP,
    EXIT_STOP,
    EXIT_TRAIL,
    SIGNAL_CANDIDATE,
    SIGNAL_IGNORED,
    SIGNAL_REJECTED,
)
from src.vcp.services.detector.bars import Bars, from_rows
from src.vcp.services.detector.base import overlap
from src.vcp.services.detector.indicators import atr
from src.vcp.services.detector.vcp import detect_vcp
from src.vcp.services.vcp_parameters import VcpParameters


def bars_from_daily(symbol: str, rows: Sequence) -> Bars:
    """`DailyBar` rows (oldest first) as the detector's container."""
    return from_rows(
        symbol,
        [(r.bar_date, r.open, r.high, r.low, r.close, r.volume or 0) for r in rows],
    )


def ema(close: np.ndarray, period: int) -> np.ndarray:
    """Seeded on the first close, as `scripts/vcp_final.py::ema` is."""
    a = 2.0 / (period + 1.0)
    out = np.empty(len(close))
    if len(close) == 0:
        return out
    out[0] = close[0]
    for i in range(1, len(close)):
        out[i] = a * close[i] + (1 - a) * out[i - 1]
    return out


def index_of(bars: Bars, day: Optional[date]) -> Optional[int]:
    """Position of a date in the series, or None when it has no bar."""
    if day is None:
        return None
    hits = np.flatnonzero(bars.date == np.datetime64(day.isoformat()))
    return int(hits[0]) if len(hits) else None


def to_date(value) -> date:
    return date.fromisoformat(str(value)[:10])


# --- new bases --------------------------------------------------------------


@dataclass
class AcceptedBase:
    """One base the detector accepted, and what this strategy makes of it."""

    t: int
    seen_date: date
    base_start_date: date
    start_idx: int
    pivot: float
    stop: float
    close: float
    atr: Optional[float]
    stop_atr: Optional[float]
    contractions: Optional[int]
    variant: str
    score: float
    trend_template: Optional[int]
    status: str            # CANDIDATE | REJECTED
    reason: str


def accepted_spans(bars: Bars, remembered: Iterable[Tuple[date, date]]) -> List[SimpleNamespace]:
    """Previously accepted bases as bar spans, for the overlap test.

    A base is remembered by its first and last DATES, because bar positions
    move whenever history is restated or extended. One whose start no longer
    has a bar is dropped: it cannot overlap anything on this series.
    """
    spans = []
    for start, seen in remembered:
        a, b = index_of(bars, start), index_of(bars, seen)
        if a is not None and b is not None:
            spans.append(SimpleNamespace(start_idx=a, end_idx=b))
    return spans


def scan(bars: Bars, ts: Iterable[int], memory: List[SimpleNamespace],
         params: VcpParameters) -> List[AcceptedBase]:
    """Every base accepted at the bars `ts`, oldest first.

    The loop is `scripts/vcp_entry_policies.py::_scan`, with `step` 1: for each
    bar, each FORMING detection that does not overlap an already accepted base
    by `overlap_threshold` is accepted -- and remembered BEFORE it is judged,
    so a 2T reading blocks a later 3T reading of the same base, as it did in
    the backtest. `memory` is extended in place.

    Then, in this order: the backtest's own validity check (the close must sit
    between the stop and the pivot), then the two FINAL_LOGIC gates -- three or
    more contractions, and a stop at least `min_stop_atr` ATR below the close.
    The ATR is the detector package's own Wilder ATR, the one the backtest's
    gate read.
    """
    a14 = atr(bars, params.atr_sessions)
    out: List[AcceptedBase] = []
    for t in ts:
        if t < params.minimum_sessions or t >= len(bars):
            continue
        for d in detect_vcp(bars, t):
            if d.state != "forming":
                continue
            if any(overlap(d, k) >= params.overlap_threshold for k in memory):
                continue
            memory.append(SimpleNamespace(start_idx=d.start_idx, end_idx=d.end_idx))
            close = float(bars.close[t])
            pivot, stop = float(d.pivot_price), float(d.stop_price)
            av = float(a14[t]) if np.isfinite(a14[t]) else None
            stop_atr = (close - stop) / av if av and av > 0 else None
            tcount = d.metrics.get("t_count")
            if stop <= 0 or pivot <= close or stop >= close:
                # Accepted, so REMEMBERED, but not a trade: already through the
                # pivot, or already under the stop. It must still be returned
                # and stored -- the backtest's memory held it, and forgetting
                # it between nights let a later reading of the same base pass
                # as new (found replaying Aug-Oct 2026 night by night).
                status = SIGNAL_IGNORED
                reason = ("Accepted with the close already above the pivot or under "
                          "the stop: remembered so the base is not taken later, "
                          "never traded.")
            elif tcount is None or tcount < params.min_contractions:
                status = SIGNAL_REJECTED
                reason = (f"{tcount}T base: fewer than {params.min_contractions} "
                          f"contractions. Detected and deliberately not traded "
                          f"(FINAL_LOGIC.md section 4).")
            elif stop_atr is None or stop_atr < params.min_stop_atr:
                status = SIGNAL_REJECTED
                shown = "unknown" if stop_atr is None else f"{stop_atr:.2f}"
                reason = (f"Stop {shown} ATR below the close, under the "
                          f"{params.min_stop_atr:g} ATR minimum: a stop inside "
                          f"half a day's range is a coin flip (VCP_LOSSES.md).")
            else:
                status = SIGNAL_CANDIDATE
                reason = (f"{d.variant}: pivot {pivot:,.2f}, stop {stop:,.2f} "
                          f"({(close - stop) / close:.1%} below the close, "
                          f"{stop_atr:.2f} ATR). Buy at the next open.")
            out.append(AcceptedBase(
                t=t, seen_date=to_date(bars.date[t]),
                base_start_date=to_date(bars.date[d.start_idx]), start_idx=d.start_idx,
                pivot=pivot, stop=stop, close=close, atr=av, stop_atr=stop_atr,
                contractions=tcount, variant=d.variant, score=float(d.score),
                trend_template=d.metrics.get("trend_template_passed"),
                status=status, reason=reason,
            ))
    return out


# --- open positions ---------------------------------------------------------


@dataclass
class PositionVerdict:
    breakout_date: Optional[date]
    last_bar_date: date
    last_close: float
    last_ema: float
    sessions_held: int
    exit_due: Optional[str]           # STOP | TRAIL | CAP
    exit_reason: Optional[str]


def evaluate_position(bars: Bars, *, signal_date: date, entry_date: date,
                      stop: float, pivot: float, checked_through: Optional[date],
                      params: VcpParameters) -> Optional[PositionVerdict]:
    """What the closes since `checked_through` say about one open position.

    `scripts/vcp_live_fills.py::live_trades`, one night at a time:

      * BREAKOUT is the first close above the pivot within
        `breakout_window_sessions` of the signal bar, inclusive -- the
        backtest's `breakout_idx`. A base that breaks out later never arms the
        trail, exactly as measured.
      * STOP: any low at or under the stop on a bar the live monitor should
        have seen. Normally the monitor already sold it in the session; this
        catches a session the process was down for, and the exit is then at
        the next open.
      * TRAIL: a close under the EMA on a bar AFTER the breakout bar.
      * CAP: `max_hold_sessions` bars after the entry bar.

    Every bar after `checked_through` is examined in order and the FIRST due
    exit wins, so a missed night cannot skip a crossing. Returns None when the
    entry date has no bar (the series does not contain the position).
    """
    n = len(bars)
    entry_idx = index_of(bars, entry_date)
    signal_idx = index_of(bars, signal_date)
    if entry_idx is None or signal_idx is None or n == 0:
        return None
    ma = ema(bars.close, params.trail_ema_sessions)

    window_end = min(n - 1, signal_idx + params.breakout_window_sessions)
    bo_idx = None
    for j in range(signal_idx, window_end + 1):
        if float(bars.close[j]) > pivot:
            bo_idx = j
            break

    start = entry_idx
    done = index_of(bars, checked_through) if checked_through else None
    if done is not None and done >= entry_idx:
        start = done + 1

    due, reason = None, None
    for j in range(start, n):
        day = to_date(bars.date[j])
        if float(bars.low[j]) <= stop:
            due, reason = EXIT_STOP, (f"Low {float(bars.low[j]):,.2f} on {day} touched the "
                                      f"stop {stop:,.2f} and no live exit was recorded. "
                                      f"Selling at the next open.")
            break
        if bo_idx is not None and j > bo_idx and float(bars.close[j]) < ma[j]:
            due, reason = EXIT_TRAIL, (f"Closed {float(bars.close[j]):,.2f} on {day}, under "
                                       f"its {params.trail_ema_sessions} EMA "
                                       f"{ma[j]:,.2f}, after breaking out. Selling at "
                                       f"the next open.")
            break
        if j - entry_idx >= params.max_hold_sessions:
            due, reason = EXIT_CAP, (f"Held {j - entry_idx} sessions, the "
                                     f"{params.max_hold_sessions}-session cap. Selling at "
                                     f"the next open.")
            break

    return PositionVerdict(
        breakout_date=to_date(bars.date[bo_idx]) if bo_idx is not None else None,
        last_bar_date=to_date(bars.date[n - 1]),
        last_close=float(bars.close[n - 1]),
        last_ema=float(ma[n - 1]),
        sessions_held=(n - 1) - entry_idx,
        exit_due=due,
        exit_reason=reason,
    )
