"""Breadth and the three pre-registered regime rules (REGIME.md).

Every series here is stamped on the session whose CLOSE produced it. A rule
evaluated at the close of day t is the regime for trading day t+1: `for_trading`
does that shift, and it is the only way a backtest may read a regime.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SMA_FAST = 50
SMA_SLOW = 200
SLOPE_LAG = 20          # "falling" 200-day SMA: below its value 20 sessions earlier
HIGH_WINDOW = 252
BREADTH_THRESHOLD = 0.60
DRAWDOWN_THRESHOLD = -0.10


def sma(w: pd.DataFrame | pd.Series, n: int) -> pd.DataFrame | pd.Series:
    # Missing sessions (a name not kept that day) are skipped, not filled; a
    # mean needs 80% of its window to exist.
    return w.rolling(n, min_periods=int(n * 0.8)).mean()


def breadth(w: pd.DataFrame) -> pd.DataFrame:
    """Per session: share of names below SMA50 / SMA200, and the median
    drawdown from each name's own 252-session high. Only names with the
    statistic defined that day are counted."""
    s50, s200 = sma(w, SMA_FAST), sma(w, SMA_SLOW)
    hi = w.rolling(HIGH_WINDOW, min_periods=200).max()
    below50 = (w < s50).where(s50.notna() & w.notna())
    below200 = (w < s200).where(s200.notna() & w.notna())
    dd = (w / hi - 1.0).where(hi.notna() & w.notna())
    return pd.DataFrame({
        "below50": below50.mean(axis=1),
        "below200": below200.mean(axis=1),
        "median_dd": dd.median(axis=1),
        "n50": below50.notna().sum(axis=1),
        "n200": below200.notna().sum(axis=1),
    })


def index_state(close: pd.Series) -> pd.DataFrame:
    s50, s200 = sma(close, SMA_FAST), sma(close, SMA_SLOW)
    hi = close.rolling(HIGH_WINDOW, min_periods=HIGH_WINDOW).max()
    return pd.DataFrame({
        "close": close, "sma50": s50, "sma200": s200,
        "sma200_falling": s200 < s200.shift(SLOPE_LAG),
        "dd252": close / hi - 1.0,
    })


def rules(nifty: pd.DataFrame, fno_breadth: pd.DataFrame) -> pd.DataFrame:
    """The three rules, at the close of each session. NaN inputs -> off."""
    st = index_state(nifty["close"])
    b = fno_breadth["below50"].reindex(st.index)
    out = pd.DataFrame(index=st.index)
    out["A_sma200"] = (st["close"] < st["sma200"]) & st["sma200_falling"].fillna(False)
    out["B_breadth"] = (b > BREADTH_THRESHOLD).fillna(False)
    out["C_drawdown"] = (st["dd252"] < DRAWDOWN_THRESHOLD).fillna(False)
    return out


def for_trading(at_close: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    """Regime known at the close of t applies to t+1. The first session is off."""
    return at_close.shift(1).fillna(False).astype(bool)


def episodes(on: pd.Series, bridge: int = 20, min_on: int = 10) -> list[dict]:
    """Group on-days into episodes: on-runs separated by <= `bridge` off
    sessions are one episode; an episode needs >= `min_on` on-days. Fixed in
    REGIME.md before any strategy result."""
    on = on.astype(bool)
    idx = np.flatnonzero(on.to_numpy())
    if len(idx) == 0:
        return []
    groups, start, prev = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - prev - 1 > bridge:
            groups.append((start, prev))
            start = i
        prev = i
    groups.append((start, prev))
    dates = on.index
    out = []
    for a, b in groups:
        n_on = int(on.iloc[a:b + 1].sum())
        if n_on >= min_on:
            out.append({"start": dates[a].date().isoformat(), "end": dates[b].date().isoformat(),
                        "sessions": b - a + 1, "on_days": n_on})
    return out


def flips(on: pd.Series) -> dict:
    on = on.astype(bool)
    switches = int((on != on.shift(1)).iloc[1:].sum())
    runs = (on != on.shift(1)).cumsum()
    lens = on.groupby(runs).agg(["first", "size"])
    on_runs = lens.loc[lens["first"], "size"]
    years = (on.index[-1] - on.index[0]).days / 365.25
    return {
        "share_on": round(float(on.mean()), 3),
        "switches": switches,
        "switches_per_year": round(switches / years, 1),
        "on_runs": int(len(on_runs)),
        "median_on_run": float(on_runs.median()) if len(on_runs) else 0.0,
        "on_runs_le_5_sessions": int((on_runs <= 5).sum()),
    }


def drawdowns(close: pd.Series, threshold: float = 0.10) -> list[dict]:
    """Ex-post peak-to-trough declines of at least `threshold` (hindsight; used
    only to MEASURE how late a causal rule was, never as a rule)."""
    out, peak_i, i = [], 0, 0
    v = close.to_numpy()
    n = len(v)
    while i < n:
        if v[i] >= v[peak_i]:
            peak_i = i
            i += 1
            continue
        if v[i] / v[peak_i] - 1.0 <= -threshold:
            # In a drawdown: the trough is the minimum before a new high.
            j = i
            while j < n and v[j] < v[peak_i]:
                j += 1
            seg = v[peak_i:j]
            t = peak_i + int(np.argmin(seg))
            out.append({"peak": close.index[peak_i], "trough": close.index[t],
                        "recovered": close.index[j] if j < n else None,
                        "decline": float(v[t] / v[peak_i] - 1.0)})
            peak_i, i = j if j < n else n, j
            continue
        i += 1
    return out
