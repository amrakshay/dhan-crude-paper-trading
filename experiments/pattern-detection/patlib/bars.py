"""Bar series: loading, weekly resampling, slicing.

A `Bars` is a plain columnar container of numpy arrays. Deliberately not a
DataFrame: every detector indexes positionally and needs `bars.high[a:b]` to
be a cheap view, and positional slicing of a DatetimeIndex is a wart.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

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


def load_sqlite(db_path: str | Path, symbol: str, exchange_segment: str = "NSE_EQ") -> Bars:
    """Read one symbol's daily bars out of the paper-trading app's database.

    Read-only: opened with mode=ro so an experiment can never write to it.
    """
    uri = f"file:{Path(db_path).resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute(
            "SELECT bar_date, open, high, low, close, volume FROM daily_bars "
            "WHERE symbol = ? AND exchange_segment = ? ORDER BY bar_date",
            (symbol, exchange_segment),
        ).fetchall()
    finally:
        conn.close()
    return from_rows(symbol, rows)


def list_sqlite_symbols(db_path: str | Path, exchange_segment: str = "NSE_EQ",
                        min_bars: int = 400) -> list[str]:
    uri = f"file:{Path(db_path).resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute(
            "SELECT symbol, COUNT(*) c FROM daily_bars WHERE exchange_segment = ? "
            "GROUP BY symbol HAVING c >= ? ORDER BY symbol",
            (exchange_segment, min_bars),
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def load_csv(path: str | Path, symbol: str | None = None) -> Bars:
    """Yahoo/Stooq-shaped CSV: Date,Open,High,Low,Close[,Adj Close],Volume."""
    import csv

    path = Path(path)
    with path.open() as fh:
        reader = csv.DictReader(fh)
        cols = {c.lower().strip(): c for c in (reader.fieldnames or [])}

        def col(*names):
            for n in names:
                if n in cols:
                    return cols[n]
            raise KeyError(f"{path.name}: none of {names} in {reader.fieldnames}")

        cd, co, ch = col("date"), col("open"), col("high")
        cl, cc = col("low"), col("close")
        cv = col("volume") if "volume" in cols else None
        rows = []
        for r in reader:
            if not r[cc] or r[cc] in ("null", "nan", ""):
                continue
            rows.append((r[cd], r[co], r[ch], r[cl], r[cc], r[cv] if cv else 0))
    return from_rows(symbol or path.stem.upper(), rows)


def to_weekly(bars: Bars) -> Bars:
    """Resample daily bars to weekly, grouped on the ISO week.

    The final week is included even when partial -- a detector running on
    Wednesday must see the week in progress, because that is what a human
    reading a weekly chart on Wednesday sees.
    """
    if len(bars) == 0:
        return Bars(bars.symbol, bars.date, bars.open, bars.high, bars.low,
                    bars.close, bars.volume, "W")
    # Monday of each bar's week.
    week_start = bars.date.astype("datetime64[D]") - (
        (bars.date.astype("datetime64[D]").astype(int) + 3) % 7
    ).astype("timedelta64[D]")
    _, starts = np.unique(week_start, return_index=True)
    starts = np.sort(starts)
    ends = np.append(starts[1:], len(bars))
    o, h, l, c, v, d = [], [], [], [], [], []
    for a, b in zip(starts, ends):
        o.append(bars.open[a])
        h.append(bars.high[a:b].max())
        l.append(bars.low[a:b].min())
        c.append(bars.close[b - 1])
        v.append(bars.volume[a:b].sum())
        d.append(bars.date[b - 1])  # label the week by its last traded day
    return Bars(bars.symbol, np.array(d, dtype="datetime64[D]"),
                np.asarray(o), np.asarray(h), np.asarray(l), np.asarray(c),
                np.asarray(v), "W")
