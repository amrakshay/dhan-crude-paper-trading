"""Dhan NSE intraday charges, from conf/intraday_costs.yaml. No rate lives here."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

CONF = Path(__file__).resolve().parent.parent / "conf" / "intraday_costs.yaml"


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
    def load(cls, path: Path = CONF) -> "RateCard":
        c = yaml.safe_load(path.read_text())
        return cls(
            flat=c["brokerage"]["per_order_flat"], brokerage_pct=c["brokerage"]["per_order_pct"],
            stt_sell=c["stt_sell_pct"], exchange=c["exchange_txn_pct"], sebi=c["sebi_fee_pct"],
            ipft=c["ipft_pct"], stamp_buy=c["stamp_buy_pct"], gst=c["gst_pct"],
        )

    def round_trip(self, buy_value, sell_value):
        """Rupees of charges for one buy and one sell (either order: a short
        sells first, and pays STT on the sell and stamp on the buy all the
        same). Vectorised over numpy arrays. No rupee rounding -- the
        statutory rounding is per contract note, not per trade, and at these
        sizes it is noise."""
        buy_value = np.asarray(buy_value, dtype=float)
        sell_value = np.asarray(sell_value, dtype=float)
        brokerage = (np.minimum(self.flat, self.brokerage_pct * buy_value)
                     + np.minimum(self.flat, self.brokerage_pct * sell_value))
        turnover = buy_value + sell_value
        exchange = self.exchange * turnover
        sebi = self.sebi * turnover
        ipft = self.ipft * turnover
        gst = self.gst * (brokerage + exchange + sebi + ipft)
        return (brokerage + self.stt_sell * sell_value + exchange + sebi + ipft
                + self.stamp_buy * buy_value + gst)
