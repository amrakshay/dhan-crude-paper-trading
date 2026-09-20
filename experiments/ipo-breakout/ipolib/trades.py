"""Turning signals into trades: entry, stop, trail, exit.

ENTRY IS THE NEXT SESSION'S OPEN, not the signal bar's close. The signal is
known only once that bar has closed, so closing on it is lookahead worth about
a day of the move -- and on a breakout bar that is the most expensive day there
is. The VCP work entered at the close and said so; this one does not, and the
difference is reported in `EXITS.md`.

THE STOP HAS AN ATR FLOOR. A base 1.5% deep gives a 1.5% stop, which under
fixed-notional sizing is not a small bet -- it is the same bet with a stop so
tight that ordinary noise takes it out. The VCP work found requiring the stop
to sit at least 0.5 ATR from entry moved the win rate from 20% to 27% and the
drawdown from -21% to -17%. The same floor is applied here, and
`scripts/exit_grid.py` re-measures it rather than inheriting it on faith.

ATR IS RAW, NOT WILDER-SMOOTHED. A new listing has 30-60 bars of history at the
breakout; a Wilder ATR(14) seeded on bar 14 has barely adapted. `raw_atr` is a
simple mean of true range over whatever window exists.

R AND RUPEES ARE DIFFERENT CURRENCIES, and every field says which. Under
fixed-notional sizing a trade with a 1% stop earning 10R makes 10% in cash; one
with a 5% stop earning 3R makes 15%. `r_multiple` is risk-relative;
`return_pct` is what the book felt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from patlib.bars import Bars
from patlib.indicators import true_range

# Sessions of true range averaged for the stop floor and the trail. Short,
# because the instrument is short.
ATR_WINDOW = 14

# Minimum distance from entry to stop, in ATR. Inherited from the VCP work and
# re-measured in scripts/exit_grid.py.
MIN_STOP_ATR = 0.5

# Nothing is held past this. An IPO breakout that has neither stopped out nor
# trailed out after a year is no longer an IPO trade.
MAX_HOLD_SESSIONS = 250


@dataclass
class Trade:
    symbol: str
    entry_date: str
    entry: float
    exit_date: str
    exit: float
    bars: int
    reason: str              # stop | trail | time | end-of-data
    initial_stop: float
    risk_pct: float          # (entry - stop) / entry
    r_multiple: float        # in units of initial risk
    return_pct: float        # in cash terms, before costs
    score: float
    sessions_after_listing: int
    listing_date: str
    near_unlock: str | None
    days_to_next_unlock: int | None
    max_favourable_pct: float
    max_adverse_pct: float


def raw_atr(bars: Bars, index: int, window: int = ATR_WINDOW) -> float:
    """Mean true range over the `window` sessions ending at `index`.

    Excludes bar 0: the listing bar's range is set by an auction and a circuit,
    not by the instrument's volatility, and it would dominate a 14-bar mean.
    """
    tr = true_range(bars)
    start = max(1, index - window + 1)
    piece = tr[start:index + 1]
    return float(np.mean(piece)) if len(piece) else float("nan")


def ema(values: np.ndarray, period: int) -> np.ndarray:
    """Standard EMA, seeded on the first value so it is defined everywhere."""
    alpha = 2.0 / (period + 1.0)
    out = np.empty(len(values))
    if not len(values):
        return out
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


# --------------------------------------------------------------------------
# exit rules. Each takes (bars, entry_idx, i, state) and returns a stop level.
# --------------------------------------------------------------------------
def chandelier(multiple: float, window: int = ATR_WINDOW) -> Callable:
    """`highest close since entry - multiple x ATR`, ratcheting UP only.

    The ratchet is the rule, not a refinement. ATR widens after a violent day,
    so the naive formula LOWERS the stop on exactly the session the position
    became more dangerous. The application's own swing strategy carries the
    same note; it is repeated here because it is easy to drop in a rewrite.
    """
    def rule(bars: Bars, entry_idx: int, i: int, state: dict) -> float:
        highest = float(np.max(bars.close[entry_idx:i + 1]))
        level = highest - multiple * raw_atr(bars, i, window)
        state["stop"] = max(state.get("stop", -np.inf), level)
        return state["stop"]
    return rule


def ema_trail(period: int) -> Callable:
    """Exit on a close below an EMA. Ratchets by construction in an uptrend."""
    def rule(bars: Bars, entry_idx: int, i: int, state: dict) -> float:
        if "ema" not in state:
            state["ema"] = ema(bars.close, period)
        return float(state["ema"][i])
    return rule


def fixed_stop_only() -> Callable:
    """No trail at all: the initial stop, held. The control."""
    def rule(bars: Bars, entry_idx: int, i: int, state: dict) -> float:
        return -np.inf
    return rule


EXIT_RULES = {
    "stop only": fixed_stop_only(),
    "chandelier 2.5": chandelier(2.5),
    "chandelier 3.0": chandelier(3.0),
    "chandelier 3.5": chandelier(3.5),
    "chandelier 4.5": chandelier(4.5),
    "EMA 10": ema_trail(10),
    "EMA 20": ema_trail(20),
    "EMA 50": ema_trail(50),
}


# --------------------------------------------------------------------------
def build_trade(bars: Bars, signal: dict, exit_rule: Callable,
                min_stop_atr: float = MIN_STOP_ATR,
                max_hold: int = MAX_HOLD_SESSIONS) -> Trade | None:
    """One signal -> one trade, walked forward bar by bar.

    Everything the exit consults at bar `i` comes from bars `<= i`. The exit
    price for a stop is the stop LEVEL when the bar gapped through it, and the
    bar's open when it gapped below -- filling at the level in that case would
    be inventing a price nobody could have got.
    """
    signal_idx = signal["sessions_after_listing"]
    entry_idx = signal_idx + 1
    if entry_idx >= len(bars):
        return None

    entry = float(bars.open[entry_idx])
    if entry <= 0:
        return None

    atr = raw_atr(bars, signal_idx)
    stop = float(signal["stop_price"])
    if np.isfinite(atr) and atr > 0:
        stop = min(stop, entry - min_stop_atr * atr)
    if stop >= entry:                       # a base low above the entry open
        return None
    risk = entry - stop

    state: dict = {"stop": stop}
    highest = entry
    lowest = entry
    exit_idx, exit_price, reason = None, None, None

    for i in range(entry_idx, min(len(bars), entry_idx + max_hold)):
        highest = max(highest, float(bars.high[i]))
        lowest = min(lowest, float(bars.low[i]))

        level = max(stop, exit_rule(bars, entry_idx, i, state))
        if float(bars.low[i]) <= level:
            # Gapped through: fill at the open, not at the level.
            exit_price = min(level, float(bars.open[i]))
            exit_idx = i
            reason = "stop" if level <= stop + 1e-9 else "trail"
            break

    if exit_idx is None:
        exit_idx = min(len(bars) - 1, entry_idx + max_hold - 1)
        exit_price = float(bars.close[exit_idx])
        reason = "time" if exit_idx == entry_idx + max_hold - 1 else "end-of-data"

    return Trade(
        symbol=bars.symbol,
        entry_date=str(bars.date[entry_idx]),
        entry=entry,
        exit_date=str(bars.date[exit_idx]),
        exit=float(exit_price),
        bars=exit_idx - entry_idx,
        reason=reason,
        initial_stop=stop,
        risk_pct=risk / entry,
        r_multiple=(exit_price - entry) / risk if risk > 0 else float("nan"),
        return_pct=(exit_price / entry - 1.0),
        score=signal["score"],
        sessions_after_listing=signal_idx,
        listing_date=signal["listing_date"],
        near_unlock=signal.get("near_unlock"),
        days_to_next_unlock=signal.get("days_to_next_unlock"),
        max_favourable_pct=(highest / entry - 1.0),
        max_adverse_pct=(lowest / entry - 1.0),
    )
