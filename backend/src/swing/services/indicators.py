"""The strategy's indicators, in pure Python.

Deliberately no pandas and no numpy. This backend carries no heavyweight data
dependency and there is no latency pressure here -- the specification measures
the whole computation at 1.44 s in pandas, and signals are taken after the
close for orders that fill at the next open. Seconds are fine.

**The guarantee is the parity test, not the library.** Every function here
reproduces a specific pandas expression from `research2/swing_backtest.py`, and
`tests/test_swing_parity.py` pins each one against golden values taken from the
research project's own panel. Where a pandas behaviour is load-bearing -- how
`max(axis=1)` treats the NaN in the first true range, what `ewm(adjust=False)`
seeds itself with -- it is spelled out below and asserted there.

Indicators are **float**. They are ratios and averages, not money: a price is a
`Decimal` everywhere it is a rupee, and is converted at the boundary here.
Mixing the two would be worse than either, and `Decimal` division would make
the parity test compare against a number pandas cannot produce.

Everything returns a list the same length as its input, with `None` where the
indicator is not yet defined. `None` is not zero, and a filter that treats an
undefined SMA200 as "price is above it" would buy the whole universe on day
one.
"""
from typing import List, Optional, Sequence

# Wilder's smoothing constant. ATR14 is an EWM with alpha = 1/14 and
# adjust=False -- NOT a rolling mean. The specification warns that small
# differences here move the stop and therefore every result.
WILDER_PERIOD = 14


def sma(values: Sequence[Optional[float]], window: int) -> List[Optional[float]]:
    """`series.rolling(window).mean()`.

    `None` until `window` observations exist, matching pandas' NaN. A running
    sum would drift over a 2,700-session series, so each window is summed; at
    500 symbols x 2,700 sessions that is still well under a second.
    """
    if window <= 0:
        raise ValueError("window must be positive")

    out: List[Optional[float]] = []
    total = 0.0
    start = 0
    for index, value in enumerate(values):
        if value is None:
            # A gap makes every window containing it undefined, exactly as a
            # NaN does in pandas. The window restarts after it rather than
            # straddling it.
            total = 0.0
            start = index + 1
            out.append(None)
            continue
        total += float(value)
        while index - start + 1 > window:
            total -= float(values[start])
            start += 1
        out.append(total / window if index - start + 1 == window else None)
    return out


def true_range(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]
) -> List[float]:
    """`concat([h-l, |h-pc|, |l-pc|], axis=1).max(axis=1)`.

    The first bar has no previous close, so two of the three candidates are
    NaN. `DataFrame.max(axis=1)` skips NaN, so the first true range is simply
    `high - low` rather than NaN. Reproducing that is what keeps the ATR series
    aligned with the research panel from the very first bar; seeding with NaN
    instead would shift the whole ATR and therefore every stop.
    """
    out: List[float] = []
    for index in range(len(highs)):
        high = float(highs[index])
        low = float(lows[index])
        if index == 0:
            out.append(high - low)
            continue
        previous_close = float(closes[index - 1])
        out.append(
            max(high - low, abs(high - previous_close), abs(low - previous_close))
        )
    return out


def wilder_ewm(values: Sequence[float], period: int = WILDER_PERIOD) -> List[float]:
    """`series.ewm(alpha=1/period, adjust=False).mean()`.

    With `adjust=False` the recursion is seeded with the FIRST observation --
    not with a mean of the first `period` values, which is the other common
    spelling of Wilder smoothing and produces different numbers forever.
    """
    alpha = 1.0 / float(period)
    out: List[float] = []
    current: Optional[float] = None
    for value in values:
        value = float(value)
        current = value if current is None else current + alpha * (value - current)
        out.append(current)
    return out


def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = WILDER_PERIOD,
) -> List[float]:
    """ATR14, Wilder-smoothed. Specification section 4."""
    return wilder_ewm(true_range(highs, lows, closes), period)


def average_daily_value(
    closes: Sequence[float], volumes: Sequence[Optional[float]], window: int = 20
) -> List[Optional[float]]:
    """ADV20 as RUPEE TURNOVER: `(close * volume).rolling(20).mean()`.

    Not average volume in shares. The liquidity floor (P2) is Rs 10 crore of
    turnover, and comparing a share count against it would pass every penny
    stock and fail every expensive one.
    """
    turnover: List[Optional[float]] = [
        None if volume is None else float(close) * float(volume)
        for close, volume in zip(closes, volumes)
    ]
    return sma(turnover, window)


def rolling_max_prior(
    values: Sequence[float], window: int
) -> List[Optional[float]]:
    """`series.rolling(window).max().shift(1)`. BTST B4's `hi55`.

    THE SHIFT IS THE RULE, not a detail. `high.rolling(55).max()` including
    today compares today's price against a window today is itself setting, so
    a breakout can never happen: on the day a stock makes a new high, its own
    high IS the maximum. Shifting by one is what makes it "the PRIOR day's
    55-day high", which is what B4 says.

    `None` until a full prior window exists, matching pandas' NaN.
    """
    if window <= 0:
        raise ValueError("window must be positive")
    out: List[Optional[float]] = []
    for index in range(len(values)):
        start = index - window
        if start < 0:
            out.append(None)
            continue
        out.append(max(float(one) for one in values[start:index]))
    return out


def average_share_volume(
    volumes: Sequence[Optional[float]], window: int = 20
) -> List[Optional[float]]:
    """`advq`: `volume.rolling(20).mean()`, in SHARES.

    The sibling of `average_daily_value`, and the specification warns about the
    pair by name (BTST section 4): `advq` is share volume and gates the volume
    surge (B5), `adv20` is rupee turnover and gates liquidity (B2). They are
    different columns with different roles and conflating them silently changes
    which stocks qualify.

    Lives here rather than in a BTST-only module because it is an indicator,
    and this is where the indicators are -- the rotation simply never needed
    this one.
    """
    return sma(volumes, window)


def close_location_value(
    price: float, low: float, high: float
) -> Optional[float]:
    """`CLV = (price - low) / (high - low)`, 0..1. BTST B6.

    One bar rather than a series, because the only caller measures the session
    SO FAR from the live book: `high` and `low` are the running extremes, not a
    finished bar's.

    `None` on a zero range, matching the backtest's
    `(df.high - df.low).replace(0, np.nan)`. A stock that has not moved all day
    has no close location, and treating that as 1.0 would buy every untraded
    name -- undefined is not one, the same way it is not zero.

    ** THE INPUTS ARE UNVERIFIED WHERE THEY COME FROM THE FEED. ** Root
    `CLAUDE.md` section 5 records that the Quote/Full packet's four price
    fields are mapped open/close/high/low per the SDK and have never been
    checked against a live feed. If high and low are transposed this function
    is not merely wrong, it is INVERTED. `scan_service` refuses a quote whose
    high is below its low rather than computing through it; the standing
    verification is `scripts/verify_feed_session_fields.py`.
    """
    span = float(high) - float(low)
    if span <= 0:
        return None
    return (float(price) - float(low)) / span


def momentum(
    closes: Sequence[float], lookback: int = 126, skip: int = 5
) -> List[Optional[float]]:
    """`close.shift(skip) / close.shift(lookback) - 1`. Specification P5.

    Six-month momentum with the most recent week skipped, which is what keeps
    a one-week spike out of a six-month ranking.
    """
    out: List[Optional[float]] = []
    for index in range(len(closes)):
        recent_index = index - skip
        old_index = index - lookback
        if old_index < 0 or recent_index < 0:
            out.append(None)
            continue
        old = float(closes[old_index])
        if old == 0:
            out.append(None)
            continue
        out.append(float(closes[recent_index]) / old - 1.0)
    return out


def percent_change(closes: Sequence[float], periods: int) -> List[Optional[float]]:
    """`series.pct_change(periods)`. Used for the NIFTY 63-day return (P9)."""
    out: List[Optional[float]] = []
    for index in range(len(closes)):
        previous_index = index - periods
        if previous_index < 0:
            out.append(None)
            continue
        previous = float(closes[previous_index])
        if previous == 0:
            out.append(None)
            continue
        out.append(float(closes[index]) / previous - 1.0)
    return out


def score(momentum_value: Optional[float], atr_percent: Optional[float]) -> Optional[float]:
    """P7: `mom / (ATR14 / close)`, the volatility-adjusted rank score.

    The divisor is the differentiating idea of this strategy -- it penalises
    momentum that was bought with volatility. The specification measures
    removing it at 29% CAGR and an unacceptable -31% drawdown.

    `None` when either input is undefined or the ATR percentage is not
    positive, which is the engine's own `if P.vol_adj and r.atrpct > 0` guard.
    """
    if momentum_value is None or atr_percent is None or atr_percent <= 0:
        return None
    return momentum_value / atr_percent
