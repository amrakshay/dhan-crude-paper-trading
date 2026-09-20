"""The charges engine, and the risk-free comparator.

Built FIRST, before any strategy, because this is a costs problem wearing a
strategy costume. A market-neutral spread is often worth under 1% and is paid
for with two round trips; if the cost model is wrong the alpha is decided
before it is measured.

Money is `Decimal` throughout, never `float`, matching the application's own
rule. Rates come from a YAML card in `conf/charges/` and nothing here is
hardcoded -- see `COSTS.md` for what each rate is and where it came from.

One thing this engine does that the application's does not: **rates may be
dated**. A rate_key may resolve to a scalar or to a list of
`{from: YYYY-MM-DD, value: x}` entries, and the entry in force on the trade
date is used. The sample spans eleven years and three of these rates changed
inside it; a single-rate card would charge 2026's taxes on a 2016 trade.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP
from functools import lru_cache
from pathlib import Path

import yaml

CONF = Path(__file__).resolve().parent.parent / "conf"
BUY, SELL = "BUY", "SELL"


def _d(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


@lru_cache(maxsize=None)
def load_card(name: str) -> dict:
    """`name` is a file stem under conf/charges, e.g. 'nse-equity-futures'."""
    return yaml.safe_load((CONF / "charges" / f"{name}.yaml").read_text())


def _resolve(card: dict, key: str, on: date):
    """Walk a dotted rate_key, then pick the dated entry in force on `on`.

    A KeyError here is a card error, not a data error, and is raised rather
    than defaulted -- a silently-zero tax is the worst failure this file has.
    """
    node = card
    for part in key.split("."):
        node = node[part]
    if isinstance(node, list):
        chosen = None
        for entry in node:
            frm = entry["from"]
            frm = frm if isinstance(frm, date) else date.fromisoformat(str(frm))
            if frm <= on:
                chosen = entry
        if chosen is None:
            raise KeyError(f"{key}: no entry in force on {on} (earliest is "
                           f"{node[0]['from']})")
        return _d(chosen["value"])
    return _d(node)


_ROUND = {
    "paise_half_even": (Decimal("0.01"), ROUND_HALF_EVEN),
    "paise_half_up": (Decimal("0.01"), ROUND_HALF_UP),
    "rupee_half_up": (Decimal("1"), ROUND_HALF_UP),
}


def _round(amount: Decimal, how: str | None) -> Decimal:
    if not how:
        return amount
    q, mode = _ROUND[how]
    return amount.quantize(q, rounding=mode)


@dataclass(frozen=True)
class Leg:
    """One side of one instrument. `turnover` is price x quantity, in rupees."""

    side: str                  # BUY or SELL
    turnover: Decimal
    on: date
    orders: int = 1

    def __post_init__(self):
        if self.side not in (BUY, SELL):
            raise ValueError(f"side must be BUY or SELL, got {self.side!r}")


@dataclass
class Charges:
    total: Decimal
    lines: dict = field(default_factory=dict)

    def __add__(self, other: "Charges") -> "Charges":
        lines = dict(self.lines)
        for k, v in other.lines.items():
            lines[k] = lines.get(k, Decimal(0)) + v
        return Charges(self.total + other.total, lines)

    def as_float(self) -> dict:
        return {k: float(v) for k, v in self.lines.items()} | {"total": float(self.total)}


def charge(card_name: str, leg: Leg) -> Charges:
    """Every line item for one leg, in the order the card declares them."""
    card = load_card(card_name)
    turnover, lines = _d(leg.turnover), {}

    for levy in card["levies"]:
        kind, name = levy["kind"], levy["name"]
        sides = levy.get("sides")
        if sides and leg.side not in sides:
            continue

        if kind == "brokerage":
            b = card["brokerage"]
            mode = b.get("mode", "flat")
            flat = _d(b.get("flat_per_order", 0)) * leg.orders
            if mode == "flat":
                amount = flat
            elif mode == "lower_of":
                pct = _d(b.get("percentage_of_turnover", 0)) * turnover
                amount = min(flat, pct) if flat > 0 else pct
            elif mode == "percentage":
                amount = _d(b.get("percentage_of_turnover", 0)) * turnover
            else:
                raise ValueError(f"unknown brokerage mode {mode!r}")
        elif kind == "percent_of_turnover":
            amount = _resolve(card, levy["rate_key"], leg.on) * turnover
        elif kind == "flat_per_order":
            base = (_d(levy["amount"]) if "amount" in levy
                    else _resolve(card, levy["rate_key"], leg.on))
            amount = base * leg.orders
        elif kind == "percent_of_components":
            base = sum((lines.get(c, Decimal(0)) for c in levy["applies_to"]), Decimal(0))
            amount = _resolve(card, levy["rate_key"], leg.on) * base
        else:
            raise ValueError(f"unknown levy kind {kind!r}")

        lines[name] = _round(amount, levy.get("intermediate_rounding"))

    dp = card.get("rounding", {}).get("final_total_decimal_places", 2)
    total = sum(lines.values(), Decimal(0)).quantize(
        Decimal(1).scaleb(-dp), rounding=ROUND_HALF_UP)
    return Charges(total, lines)


def round_trip(card_name: str, turnover_in: Decimal, turnover_out: Decimal,
               on_in: date, on_out: date, direction: str = "LONG") -> Charges:
    """A complete position: open and close, in that order.

    `direction` LONG opens with a BUY and closes with a SELL; SHORT is the
    reverse. It matters: STT on equity futures falls on the sell side only,
    so a short's tax is paid on the way IN, at the opening price, and a
    long's on the way OUT.
    """
    if direction not in ("LONG", "SHORT"):
        raise ValueError(direction)
    open_side, close_side = (BUY, SELL) if direction == "LONG" else (SELL, BUY)
    return (charge(card_name, Leg(open_side, _d(turnover_in), on_in))
            + charge(card_name, Leg(close_side, _d(turnover_out), on_out)))


def margin_fraction(card_name: str) -> Decimal:
    card = load_card(card_name)
    return _d(card.get("margin", {}).get("span_plus_exposure_fraction", 0))


# ---------------------------------------------------------------------------
# The risk-free comparator.
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _policy_rate_changes() -> list[tuple[date, float]]:
    card = yaml.safe_load((CONF / "rates" / "india-policy-rate.yaml").read_text())
    out = []
    for c in card["changes"]:
        frm = c["from"]
        out.append((frm if isinstance(frm, date) else date.fromisoformat(str(frm)),
                    float(c["rate"])))
    return sorted(out)


def risk_free_annual(on: date) -> float:
    """The policy rate in force, in percent. See the card for what it is NOT."""
    changes = _policy_rate_changes()
    rate = changes[0][1]
    for frm, r in changes:
        if frm <= on:
            rate = r
    return rate


def risk_free_growth(dates) -> list[float]:
    """Cumulative growth factor of cash rolled at the policy rate.

    The comparator a market-neutral book has to beat. Accrues on an ACT/365
    basis over the gaps between the dates given, so it is defined on exactly
    the trading calendar the strategy is measured on.
    """
    dates = [d if isinstance(d, date) else date.fromisoformat(str(d)[:10]) for d in dates]
    out, level = [], 1.0
    for i, d in enumerate(dates):
        if i:
            days = (d - dates[i - 1]).days
            level *= (1.0 + risk_free_annual(dates[i - 1]) / 100.0 * days / 365.0)
        out.append(level)
    return out
