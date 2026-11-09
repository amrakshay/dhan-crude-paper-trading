"""Indicators, cost model and panel loader.

Pure functions COPIED (not imported) so this experiment is self-contained:
  - atr, buy_cost, sell_cost and the constants: from
    pullback/backend/intrday_test_strategy/research2/swing_backtest.py
  - load / prep: from research2/gate_run/run_gates.py, keeping only the
    columns the C4 engine reads (sma200, atr14, adv20, mom126) plus ret63 for
    the pot. rsi/adx/supertrend were computed there but never read by the
    engine; dropping them changes nothing (asserted by the V0 regression).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

DATA_EXT = Path("/Users/akshay/Workarea/local/pullback/backend/intrday_test_strategy/research2/gate_run/data_ext")

START_EQUITY = 1_000_000.0  # 10 slots x Rs 1L
MAX_POS = 10
SLIP = 0.0005
ADV_MIN = 10e7
PRICE_MIN = 50.0


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df.close.shift(1)
    tr = pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


# spec section 5, verbatim
def buy_cost(turnover: float) -> float:
    stt = turnover * 0.001
    txn = turnover * 0.0000297
    sebi = turnover * 1e-6
    stamp = turnover * 0.00015
    gst = 0.18 * txn
    return stt + txn + sebi + stamp + gst


def sell_cost(turnover: float) -> float:
    stt = turnover * 0.001
    txn = turnover * 0.0000297
    sebi = turnover * 1e-6
    gst = 0.18 * txn
    dp = 16.0
    return stt + txn + sebi + gst + dp


def prep(d: pd.DataFrame) -> pd.DataFrame:
    d = d.sort_values("date").reset_index(drop=True)
    d["sma200"] = d.close.rolling(200).mean()
    d["atr14"] = atr(d, 14)
    d["adv20"] = (d.close * d.volume).rolling(20).mean()
    d["mom126"] = d.close.shift(5) / d.close.shift(126) - 1
    d["ret63"] = d.close / d.close.shift(63) - 1          # pot RS-63 (new)
    return d


def load(path: Path = DATA_EXT) -> tuple[dict, pd.DataFrame]:
    syms, nifty = {}, None
    for p in sorted(path.glob("*.csv")):
        d = pd.read_csv(p)
        if "volume" not in d.columns or len(d) < 260:
            continue
        if p.stem == "NIFTY50":
            nifty = d.sort_values("date").reset_index(drop=True)
            continue
        syms[p.stem] = prep(d)
    return syms, nifty
