"""Bar series: the columnar container the detector reads.

Ported from experiments/pattern-detection/patlib/bars.py with the loaders
(SQLite, CSV, weekly resampling) removed: the server reads `daily_bars`
through its own repository and hands rows to `from_rows`.

A `Bars` is a plain columnar container of numpy arrays. Deliberately not a
DataFrame: every detector indexes positionally and needs `bars.high[a:b]` to
be a cheap view, and positional slicing of a DatetimeIndex is a wart.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Bars:
    symbol: str
    date: np.ndarray      # datetime64[D]
    open: np.ndarray      # float64
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    timeframe: str = "D"  # "D" or "W"

    def __len__(self) -> int:
        return len(self.close)

    def slice(self, a: int, b: int) -> "Bars":
        return Bars(
            self.symbol, self.date[a:b], self.open[a:b], self.high[a:b],
            self.low[a:b], self.close[a:b], self.volume[a:b], self.timeframe,
        )

    def head(self, n: int) -> "Bars":
        """The first n bars -- i.e. everything knowable at bar n-1."""
        return self.slice(0, n)


def from_rows(symbol: str, rows, timeframe: str = "D") -> Bars:
    """rows: iterable of (date_str_or_date, o, h, l, c, v)."""
    rows = list(rows)
    return Bars(
        symbol=symbol,
        date=np.array([np.datetime64(str(r[0])[:10]) for r in rows], dtype="datetime64[D]"),
        open=np.asarray([float(r[1]) for r in rows]),
        high=np.asarray([float(r[2]) for r in rows]),
        low=np.asarray([float(r[3]) for r in rows]),
        close=np.asarray([float(r[4]) for r in rows]),
        volume=np.asarray([float(r[5] or 0) for r in rows]),
        timeframe=timeframe,
    )
