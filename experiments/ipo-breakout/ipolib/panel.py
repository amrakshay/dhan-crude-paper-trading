"""The cross-section: forward returns, and the baselines they are measured against.

WHY A BASELINE AT ALL. 2016-2026 mostly went up. Any long strategy shows a
positive average forward return over it, so a raw number is evidence of the
decade, not of the rule. The only interpretable figure is the EXCESS over what
was available on the same dates.

TWO BASELINES, AND THEY ANSWER DIFFERENT QUESTIONS.

  market   For a signal on date D, the mean forward return of every OTHER
           symbol with data on D. This is "would picking at random on that day
           have done as well".

  cohort   The same, restricted to other IPOs that listed within a window
           either side of this one's listing. This controls for the IPO cohort
           effect -- 2021's listings all rose together and all fell together,
           and a strategy that only trades 2021 listings would beat a market
           baseline on that alone.

           NOTE THE DIRECTION. Ritter's result is that IPOs as a class
           UNDERPERFORM (RESEARCH.md section 0), so the cohort baseline is
           LOWER than the market one and beating it is EASIER. It is the more
           relevant comparison and the softer one at the same time. Both are
           reported, and every table says which it is.

SURVIVORSHIP. Both baselines are drawn from the rebuilt universe, which holds
every NSE equity Dhan still serves. It excludes anything delisted since, so
both baselines are biased UP by an unknown amount -- which makes the excess a
LOWER bound, the conservative direction. README.md section 1 has the size of
the hole.
"""
from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent.parent
LISTINGS_DB = HERE / "data" / "listings.db"
MASTER_CSV = HERE / "data" / "master.csv"

# Dhan's master marks an equity share "ES" and a fund "ETF" in
# INSTRUMENT_TYPE, though both carry SERIES=EQ. The pull therefore swept up
# roughly a hundred index and gilt ETFs alongside the companies.
#
# They are excluded from the BASELINE, because the baseline answers "would
# picking something else that day have done as well" and an index ETF is not
# something else -- it is the market, so including it pulls the comparison
# towards the index and understates the dispersion a stock picker faced.
EQUITY_INSTRUMENT_TYPE = "ES"

# Forward horizons in SESSIONS. 20 is the VCP work's headline horizon and is
# kept so the two experiments are comparable.
HORIZONS = (5, 10, 20, 40, 60)


@dataclass
class Panel:
    """Aligned closes for the whole universe: dates x symbols."""

    dates: np.ndarray            # datetime64[D], ascending
    symbols: list[str]
    close: np.ndarray            # (n_dates, n_symbols), NaN where not listed
    _index: dict = None          # date -> row

    def row(self, day: date | np.datetime64) -> int | None:
        return self._index.get(np.datetime64(str(day)[:10]))

    def column(self, symbol: str) -> int | None:
        try:
            return self.symbols.index(symbol)
        except ValueError:
            return None


def equity_symbols(master_path: Path = MASTER_CSV) -> set[str]:
    """Tickers the master marks as equity SHARES, not funds."""
    out = set()
    if not master_path.exists():
        return out
    with master_path.open(encoding="utf-8", errors="replace") as handle:
        for row in csv.DictReader(handle):
            if (row.get("EXCH_ID") == "NSE" and row.get("SEGMENT") == "E"
                    and row.get("INSTRUMENT_TYPE") == EQUITY_INSTRUMENT_TYPE):
                symbol = (row.get("UNDERLYING_SYMBOL") or "").strip()
                if symbol:
                    out.add(symbol)
    return out


def load_panel(db_path: Path = LISTINGS_DB, min_bars: int = 60,
               equities_only: bool = True) -> Panel:
    """Every symbol's closes on a common date axis.

    `min_bars` drops names too short to contribute a forward return, which
    keeps the matrix from being mostly NaN. `equities_only` drops the ETFs --
    see EQUITY_INSTRUMENT_TYPE above.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT symbol, bar_date, close FROM daily_bars WHERE symbol IN "
            "(SELECT symbol FROM securities WHERE status='ok' AND n_bars >= ?)",
            (min_bars,)).fetchall()
    finally:
        conn.close()

    if equities_only:
        keep = equity_symbols()
        if keep:
            rows = [r for r in rows if r[0] in keep]

    symbols = sorted({r[0] for r in rows})
    dates = np.array(sorted({r[1] for r in rows}), dtype="datetime64[D]")
    sym_index = {s: i for i, s in enumerate(symbols)}
    date_index = {d: i for i, d in enumerate(dates)}

    close = np.full((len(dates), len(symbols)), np.nan)
    for symbol, bar_date, value in rows:
        close[date_index[np.datetime64(bar_date)], sym_index[symbol]] = value

    return Panel(dates=dates, symbols=symbols, close=close, _index=date_index)


def forward_returns(panel: Panel, row: int, horizon: int) -> np.ndarray:
    """Every symbol's return from `row` to `row + horizon`, NaN where unavailable.

    Uses POSITIONAL offsets on the panel's own date axis, so a horizon is
    `horizon` sessions in which the market was open -- not calendar days, and
    not this symbol's own sessions, which would drift for a name that was
    suspended.
    """
    end = row + horizon
    if end >= len(panel.dates):
        return np.full(len(panel.symbols), np.nan)
    start_prices = panel.close[row]
    end_prices = panel.close[end]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(start_prices > 0, end_prices / start_prices - 1.0, np.nan)


def market_baseline(panel: Panel, row: int, horizon: int,
                    exclude: int | None = None) -> float:
    """Mean forward return of every symbol with data on that date."""
    returns = forward_returns(panel, row, horizon)
    if exclude is not None and 0 <= exclude < len(returns):
        returns = returns.copy()
        returns[exclude] = np.nan
    valid = returns[np.isfinite(returns)]
    return float(np.mean(valid)) if len(valid) else np.nan


def cohort_baseline(panel: Panel, row: int, horizon: int,
                    cohort_columns: np.ndarray,
                    exclude: int | None = None) -> float:
    """Mean forward return of the IPO cohort, on the same date."""
    returns = forward_returns(panel, row, horizon)
    mask = np.zeros(len(returns), dtype=bool)
    mask[cohort_columns] = True
    if exclude is not None and 0 <= exclude < len(returns):
        mask[exclude] = False
    valid = returns[mask & np.isfinite(returns)]
    return float(np.mean(valid)) if len(valid) else np.nan


# --------------------------------------------------------------------------
def block_bootstrap(values: np.ndarray, blocks: np.ndarray,
                    n_iterations: int = 5000, seed: int = 11,
                    alpha: float = 0.05) -> dict:
    """Confidence interval for a mean, resampling whole BLOCKS.

    Overlapping forward windows are not independent: a signal on Monday and one
    on Tuesday share 19 of their 20 forward sessions, so a naive t-statistic
    counts the same market move many times and is far too generous. Resampling
    whole calendar months keeps signals that shared a market inside the same
    draw.

    Returns the observed mean, the interval, and the share of resamples at or
    below zero -- a bootstrap p-value for "the mean is positive".
    """
    values = np.asarray(values, dtype=float)
    finite = np.isfinite(values)
    values, blocks = values[finite], np.asarray(blocks)[finite]
    if not len(values):
        return {"n": 0, "mean": np.nan, "lo": np.nan, "hi": np.nan,
                "p_le_zero": np.nan, "n_blocks": 0}

    unique = np.unique(blocks)
    grouped = [values[blocks == block] for block in unique]
    rng = np.random.default_rng(seed)

    means = np.empty(n_iterations)
    for i in range(n_iterations):
        picked = rng.integers(0, len(grouped), len(grouped))
        means[i] = np.mean(np.concatenate([grouped[j] for j in picked]))

    return {
        "n": int(len(values)),
        "n_blocks": int(len(unique)),
        "mean": float(np.mean(values)),
        "lo": float(np.quantile(means, alpha / 2)),
        "hi": float(np.quantile(means, 1 - alpha / 2)),
        "p_le_zero": float(np.mean(means <= 0)),
    }
