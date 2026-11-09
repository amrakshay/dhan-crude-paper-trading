"""Date-aware charge engine for NSE index options.

Every rate in conf/charges/nse-index-options.yaml is a list of
`{from: date, value: rate}`; a charge is always resolved at the TRADE's own
date, never at "today". Five STT regimes live inside the 2016-2026 sample and
getting that wrong is worth more than most of the strategy parameters.

Two shapes of cost, deliberately kept apart:

  charge_trade()   a real transaction -- brokerage, STT, transaction charge,
                   SEBI fee, stamp duty, GST.
  charge_expiry()  a contract that expired.  NOT a transaction: no brokerage,
                   no GST, no STT on the premium.  A long leg exercised in the
                   money pays STT on INTRINSIC value; everything else is free.

The second is why letting a spread expire is cheaper than closing it, and the
backtest must not charge for a trade that never happened.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

Q = Decimal("0.01")


def _d(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


@dataclass(frozen=True)
class Leg:
    """One option leg of a transaction.

    premium is per unit; qty is in UNITS (lots x lot_size), matching how the
    bhavcopy reports volume.  side is 'BUY' or 'SELL'.
    """
    side: str
    premium: Decimal
    qty: int

    @property
    def turnover(self) -> Decimal:
        return _d(self.premium) * self.qty


@dataclass
class ChargeBreakdown:
    brokerage: Decimal = Decimal("0")
    stt: Decimal = Decimal("0")
    stt_exercise: Decimal = Decimal("0")
    exchange_transaction_charge: Decimal = Decimal("0")
    sebi_turnover_fee: Decimal = Decimal("0")
    stamp_duty: Decimal = Decimal("0")
    gst: Decimal = Decimal("0")
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        t = (self.brokerage + self.stt + self.stt_exercise
             + self.exchange_transaction_charge + self.sebi_turnover_fee
             + self.stamp_duty + self.gst)
        return t.quantize(Q, rounding=ROUND_HALF_UP)

    def as_dict(self) -> dict[str, float]:
        return {
            "brokerage": float(self.brokerage),
            "stt": float(self.stt),
            "stt_exercise": float(self.stt_exercise),
            "exchange_transaction_charge": float(self.exchange_transaction_charge),
            "sebi_turnover_fee": float(self.sebi_turnover_fee),
            "stamp_duty": float(self.stamp_duty),
            "gst": float(self.gst),
            "total": float(self.total),
        }

    def __add__(self, o: "ChargeBreakdown") -> "ChargeBreakdown":
        return ChargeBreakdown(
            brokerage=self.brokerage + o.brokerage,
            stt=self.stt + o.stt,
            stt_exercise=self.stt_exercise + o.stt_exercise,
            exchange_transaction_charge=(self.exchange_transaction_charge
                                         + o.exchange_transaction_charge),
            sebi_turnover_fee=self.sebi_turnover_fee + o.sebi_turnover_fee,
            stamp_duty=self.stamp_duty + o.stamp_duty,
            gst=self.gst + o.gst,
            notes=self.notes + o.notes,
        )


DEFAULT_CARD = Path(__file__).resolve().parents[1] / "conf/charges/nse-index-options.yaml"


class ChargeCard:
    def __init__(self, path: str | Path = DEFAULT_CARD):
        self.path = Path(path)
        with open(self.path) as f:
            self.raw = yaml.safe_load(f)
        self.version = self.raw["version"]

    # -- rate resolution ---------------------------------------------------

    def rate(self, dotted: str, on: date) -> Decimal:
        """Resolve a possibly date-keyed rate at `on`.

        A plain scalar resolves to itself.  A list resolves to the last entry
        whose `from` is on or before `on`, which is why the lists must stay in
        ascending date order -- asserted below.
        """
        node = self.raw
        for part in dotted.split("."):
            node = node[part]
        if not isinstance(node, list):
            return _d(node)
        chosen = None
        prev: date | None = None
        for entry in node:
            f = entry["from"]
            f = f if isinstance(f, date) else date.fromisoformat(str(f))
            if prev is not None and f < prev:
                raise ValueError(f"{dotted}: rate list not in ascending date order")
            prev = f
            if f <= on:
                chosen = entry["value"]
        if chosen is None:
            raise ValueError(f"{dotted}: no rate in force on {on} "
                             f"(earliest is {node[0]['from']})")
        return _d(chosen)

    # -- the two shapes ----------------------------------------------------

    def charge_trade(self, legs: list[Leg], on: date,
                     orders: int | None = None) -> ChargeBreakdown:
        """Charge a real transaction.

        `orders` defaults to one per leg -- a four-legged condor is four
        executed orders on the way in and four on the way out.  Leg count is a
        real cost, not a presentation detail.
        """
        b = ChargeBreakdown()
        n_orders = len(legs) if orders is None else orders

        b.brokerage = _d(self.raw["brokerage"]["flat_per_order"]) * n_orders

        sell_turnover = sum((l.turnover for l in legs if l.side == "SELL"),
                            Decimal("0"))
        buy_turnover = sum((l.turnover for l in legs if l.side == "BUY"),
                           Decimal("0"))
        all_turnover = sell_turnover + buy_turnover

        b.stt = sell_turnover * self.rate("stt.sell_option_premium_rate", on)
        b.exchange_transaction_charge = all_turnover * self.rate(
            "exchange_transaction_charge.index_options_total", on)
        b.sebi_turnover_fee = all_turnover * self.rate("sebi_turnover_fee.rate", on)
        b.stamp_duty = buy_turnover * self.rate("stamp_duty.option_premium_rate", on)

        b.gst = self.rate("gst.rate", on) * (
            b.brokerage + b.exchange_transaction_charge + b.sebi_turnover_fee)
        return b

    def charge_expiry(self, legs: list[Leg], intrinsics: list[Decimal],
                      on: date) -> ChargeBreakdown:
        """Charge contracts that reached expiry.

        Not a transaction.  The only levy is STT on the intrinsic value of a
        LONG leg that finishes in the money and is therefore exercised -- paid
        by the purchaser.  A short leg that is assigned pays none (it already
        paid STT on the premium when it was sold), and a leg that expires
        worthless costs nothing at all.
        """
        b = ChargeBreakdown()
        r = self.rate("stt.exercise_intrinsic_rate", on)
        for leg, iv in zip(legs, intrinsics, strict=True):
            if leg.side == "BUY" and _d(iv) > 0:
                b.stt_exercise += _d(iv) * leg.qty * r
                b.notes.append(
                    f"exercise STT on long leg, intrinsic {iv} x {leg.qty} @ {r}")
        return b


__all__ = ["ChargeCard", "ChargeBreakdown", "Leg"]
