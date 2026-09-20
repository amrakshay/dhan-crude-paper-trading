"""Read-only access to the paper-trading application's daily bars.

The database is opened with `mode=ro` on a URI, which makes a write a
programming error rather than a policy that can be forgotten. Nothing in this
package holds a writable handle.

Two things about the data that callers must know, both established in
`FEASIBILITY.md` rather than assumed:

* Prices ARE back-adjusted for splits and bonuses, and NOT for demergers or
  re-listings. `EXTREME_MOVES` names every unadjusted break found.
* Volume is in shares and is NOT adjusted, so a pre-split turnover in rupees
  computed as close x volume is understated by the split factor.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT.parent.parent / "backend" / "data" / "paper_trading.db"

#: Overnight close-to-close moves outside [0.6x, 1.75x] over the whole
#: NSE_EQ panel. Established by `scripts/feasibility.py`. Each is a real
#: corporate event or a re-listing, NOT a split the vendor adjusted -- so a
#: spread spanning one of these dates is an artefact, not an opportunity.
EXTREME_MOVES = {
    "HEG": ["2026-07-13"],
    "HEXT": ["2025-02-19"],
    "JSL": ["2015-11-19"],
    "PATANJALI": ["2020-01-27"],
    "TATACHEM": ["2020-03-04"],
    "YESBANK": ["2020-03-06"],
}


@dataclass(frozen=True)
class Series:
    """One symbol's daily bars, as plain numpy columns."""

    symbol: str
    date: np.ndarray      # datetime64[D]
    open: np.ndarray      # float64
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray

    def __len__(self) -> int:
        return len(self.close)

    @property
    def turnover(self) -> np.ndarray:
        """Rupee turnover proxy. See the volume caveat in the module docstring."""
        return self.close * self.volume


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or DB).resolve()
    if not path.exists():                                # pragma: no cover
        raise FileNotFoundError(f"application database not found at {path}")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def load(symbol: str, db_path=None, segment: str = "NSE_EQ") -> Series:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT bar_date, open, high, low, close, volume FROM daily_bars "
            "WHERE symbol = ? AND exchange_segment = ? ORDER BY bar_date",
            (symbol, segment),
        ).fetchall()
    finally:
        conn.close()
    return Series(
        symbol=symbol,
        date=np.array([np.datetime64(str(r[0])[:10]) for r in rows], dtype="datetime64[D]"),
        open=np.asarray([float(r[1]) for r in rows]),
        high=np.asarray([float(r[2]) for r in rows]),
        low=np.asarray([float(r[3]) for r in rows]),
        close=np.asarray([float(r[4]) for r in rows]),
        volume=np.asarray([float(r[5] or 0) for r in rows]),
    )


def symbols(db_path=None, segment: str = "NSE_EQ", min_bars: int = 1) -> list[str]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT symbol, COUNT(*) c FROM daily_bars WHERE exchange_segment = ? "
            "GROUP BY symbol HAVING c >= ? ORDER BY symbol",
            (segment, min_bars),
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def fno_eligible(db_path=None) -> set[str]:
    """Underlyings that carry single-stock futures, per the instrument master.

    This is the ONLY set in which a short position can be carried overnight
    (through the futures leg). See `FEASIBILITY.md` section 3.
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT underlying_symbol FROM instruments "
            "WHERE exchange_segment = 'NSE_EQ' AND fno_eligible = 1"
        ).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


@lru_cache(maxsize=1)
def panel(min_bars: int = 1500, segment: str = "NSE_EQ", db_path=None):
    """Every symbol with at least `min_bars` bars, on one shared date axis.

    Returns `(dates, {symbol: close}, {symbol: volume})` where each array is
    aligned to `dates` and carries NaN where the symbol did not trade. A
    shared axis is what makes a pair's spread well defined; forward-filling
    is deliberately NOT done, because a stale price in a spread reads as a
    divergence that was never tradeable.

    `panel_ohlc` additionally returns the OPEN, which is what fills are
    priced at: a signal read off the close of day t is executed at the open
    of t+1. Filling at the same close the signal was computed on is a
    one-bar lookahead, and for a mean-reversion strategy it is the worst
    possible one -- the signal fires precisely at the extreme price, so the
    fill would always be at the best price of the move.
    """
    conn = _connect(db_path)
    try:
        keep = [
            r[0] for r in conn.execute(
                "SELECT symbol, COUNT(*) c FROM daily_bars WHERE exchange_segment = ? "
                "GROUP BY symbol HAVING c >= ? ORDER BY symbol", (segment, min_bars),
            )
        ]
        rows = conn.execute(
            "SELECT symbol, bar_date, close, volume FROM daily_bars "
            "WHERE exchange_segment = ? ORDER BY bar_date", (segment,),
        ).fetchall()
    finally:
        conn.close()

    keepset = set(keep)
    all_dates = sorted({str(r[1])[:10] for r in rows if r[0] in keepset})
    idx = {d: i for i, d in enumerate(all_dates)}
    n = len(all_dates)
    close = {s: np.full(n, np.nan) for s in keep}
    vol = {s: np.full(n, np.nan) for s in keep}
    for sym, d, c, v in rows:
        if sym not in keepset:
            continue
        i = idx[str(d)[:10]]
        close[sym][i] = float(c)
        vol[sym][i] = float(v or 0)
    return np.array(all_dates, dtype="datetime64[D]"), close, vol


@lru_cache(maxsize=1)
def panel_ohlc(min_bars: int = 1500, segment: str = "NSE_EQ", db_path=None):
    """`(dates, close, open, volume)` on one shared axis. See `panel`."""
    conn = _connect(db_path)
    try:
        keep = [
            r[0] for r in conn.execute(
                "SELECT symbol, COUNT(*) c FROM daily_bars WHERE exchange_segment = ? "
                "GROUP BY symbol HAVING c >= ? ORDER BY symbol", (segment, min_bars),
            )
        ]
        rows = conn.execute(
            "SELECT symbol, bar_date, open, close, volume FROM daily_bars "
            "WHERE exchange_segment = ? ORDER BY bar_date", (segment,),
        ).fetchall()
    finally:
        conn.close()

    keepset = set(keep)
    all_dates = sorted({str(r[1])[:10] for r in rows if r[0] in keepset})
    idx = {d: i for i, d in enumerate(all_dates)}
    n = len(all_dates)
    close = {s: np.full(n, np.nan) for s in keep}
    opn = {s: np.full(n, np.nan) for s in keep}
    vol = {s: np.full(n, np.nan) for s in keep}
    for sym, d, o, c, v in rows:
        if sym not in keepset:
            continue
        i = idx[str(d)[:10]]
        close[sym][i] = float(c)
        opn[sym][i] = float(o)
        vol[sym][i] = float(v or 0)
    return np.array(all_dates, dtype="datetime64[D]"), close, opn, vol
