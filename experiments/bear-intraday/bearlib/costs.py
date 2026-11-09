"""Charges. MIS stock card is the screener's (read-only); index futures card is ours."""
from __future__ import annotations

import sqlite3
from datetime import date
from functools import lru_cache

import numpy as np
import pandas as pd
import yaml

from . import EXP, OPTIONS_DB

from islib.costs import RateCard  # noqa: F401  (re-exported: the MIS card)

FUT_CARD = EXP / "conf" / "index_futures_costs.yaml"


class FuturesCard:
    def __init__(self, path=FUT_CARD):
        c = yaml.safe_load(path.read_text())
        self.flat = c["brokerage"]["per_order_flat"]
        self.pct = c["brokerage"]["per_order_pct"]
        self._stt = [(date.fromisoformat(r["from"]), r["value"]) for r in c["stt_sell"]]
        self._exch = [(date.fromisoformat(r["from"]), r["value"]) for r in c["exchange_txn"]]
        self.sebi, self.ipft, self.stamp, self.gst = c["sebi_fee"], c["ipft"], c["stamp_buy"], c["gst"]

    @staticmethod
    def _at(table, on: date) -> float:
        v = table[0][1]
        for d, x in table:
            if on >= d:
                v = x
        return v

    def round_trip(self, buy_value: float, sell_value: float, on: date) -> float:
        brk = min(self.flat, self.pct * buy_value) + min(self.flat, self.pct * sell_value)
        turn = buy_value + sell_value
        exch = self._at(self._exch, on) * turn
        sebi, ipft = self.sebi * turn, self.ipft * turn
        return (brk + self._at(self._stt, on) * sell_value + exch + sebi + ipft
                + self.stamp * buy_value + self.gst * (brk + exch + sebi + ipft))


@lru_cache(maxsize=None)
def nifty_lot_sizes() -> pd.Series:
    """Near-month NIFTY futures lot size per session, from the bhavcopy's own
    rows (UDiFF in-band; legacy derived by hedged-option-selling)."""
    with sqlite3.connect(f"file:{OPTIONS_DB}?mode=ro", uri=True) as c:
        q = pd.read_sql("select trade_date, expiry, lot_size from fo_bhav "
                        "where symbol='NIFTY' and kind='IDF' and lot_size is not null", c)
    q = q.sort_values(["trade_date", "expiry"]).drop_duplicates("trade_date", keep="first")
    s = q.set_index(pd.to_datetime(q["trade_date"]))["lot_size"].astype(int)
    return s


def nifty_lot(on: date) -> int:
    s = nifty_lot_sizes()
    i = s.index.searchsorted(pd.Timestamp(on), side="right") - 1
    return int(s.iloc[max(i, 0)])
