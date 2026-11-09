"""Daily series, every one opened read-only.

- F&O 210: `intraday-screener/data/clean/days.parquet`, one row per kept
  symbol-day. Its open/close are the RAW vendor prints; `adj` rescales them
  onto the adjusted series (CLEANING.md rule 7), so adjusted = raw * adj.
- Nifty 500 and NIFTY: the application's `daily_bars`, via a `mode=ro` URI.
- India VIX and other indices: `hedged-option-selling`'s `index_close`.
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from . import APP_DB, OPTIONS_DB, SCREENER


def _ro(path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def fno_daily() -> pd.DataFrame:
    """Wide adjusted closes [date x symbol] for the 210 F&O names, valid days only."""
    d = pd.read_parquet(SCREENER / "data" / "clean" / "days.parquet")
    d = d[d["valid"] & (d["symbol"] != "NIFTY50")]
    d = d.assign(close_adj=d["close"] * d["adj"], open_adj=d["open"] * d["adj"])
    d["date"] = pd.to_datetime(d["date"])
    return d


def wide(df: pd.DataFrame, col: str) -> pd.DataFrame:
    return df.pivot(index="date", columns="symbol", values=col).sort_index()


def nifty500_daily() -> tuple[pd.DataFrame, int]:
    """Wide closes for today's Nifty 500 from `daily_bars`, with isolated
    never-adjusted prints removed (CLEANING.md: BHARTIARTL 2018-04-20 reads 402
    between 346 and 349). A print is dropped only when it jumps > 15% AND the
    next day jumps back by > 15% the other way -- a one-day excursion no real
    stock makes and reverses on consecutive closes. Returns (wide, n_dropped)."""
    with _ro(APP_DB) as c:
        q = pd.read_sql(
            "select symbol, bar_date, close from daily_bars where exchange_segment='NSE_EQ'", c)
    q["bar_date"] = pd.to_datetime(q["bar_date"])
    w = q.pivot(index="bar_date", columns="symbol", values="close").sort_index().astype(float)
    r_in = w / w.ffill().shift(1) - 1.0
    r_out = w.ffill().shift(-1) / w - 1.0
    spike = (r_in.abs() > 0.15) & (r_out.abs() > 0.15) & (np.sign(r_in) != np.sign(r_out))
    n = int(spike.sum().sum())
    return w.mask(spike), n


def nifty_daily() -> pd.DataFrame:
    """NIFTY 50 OHLC from `daily_bars` (IDX_I), 2015-07 onward."""
    with _ro(APP_DB) as c:
        q = pd.read_sql(
            "select bar_date, open, high, low, close from daily_bars "
            "where exchange_segment='IDX_I' and symbol='NIFTY' order by bar_date", c)
    q["bar_date"] = pd.to_datetime(q["bar_date"])
    return q.set_index("bar_date").astype(float)


def index_close(name: str) -> pd.Series:
    with _ro(OPTIONS_DB) as c:
        q = pd.read_sql("select trade_date, close from index_close where index_name=? "
                        "order by trade_date", c, params=(name,))
    q["trade_date"] = pd.to_datetime(q["trade_date"])
    return q.set_index("trade_date")["close"].astype(float)
