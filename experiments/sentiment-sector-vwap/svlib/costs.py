"""Dhan NSE intraday charges, from conf/intraday_costs.yaml. No rate lives here.

Same arithmetic as experiments/intraday-screener/islib/costs.py, scalar
instead of numpy."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class RateCard:
    flat: float
    brokerage_pct: float
    stt_sell: float
    exchange: float
    sebi: float
    ipft: float
    stamp_buy: float
    gst: float

    @classmethod
    def load(cls, path: Path) -> "RateCard":
        c = yaml.safe_load(path.read_text())
        return cls(
            flat=c["brokerage"]["per_order_flat"], brokerage_pct=c["brokerage"]["per_order_pct"],
            stt_sell=c["stt_sell_pct"], exchange=c["exchange_txn_pct"], sebi=c["sebi_fee_pct"],
            ipft=c["ipft_pct"], stamp_buy=c["stamp_buy_pct"], gst=c["gst_pct"],
        )

    def round_trip(self, buy_value: float, sell_value: float) -> float:
        """Rupees for one buy and one sell, in either order (a short sells first
        and still pays STT on the sell and stamp duty on the buy)."""
        brokerage = (min(self.flat, self.brokerage_pct * buy_value)
                     + min(self.flat, self.brokerage_pct * sell_value))
        turnover = buy_value + sell_value
        exchange, sebi, ipft = (self.exchange * turnover, self.sebi * turnover,
                                self.ipft * turnover)
        gst = self.gst * (brokerage + exchange + sebi + ipft)
        return (brokerage + self.stt_sell * sell_value + exchange + sebi + ipft
                + self.stamp_buy * buy_value + gst)
