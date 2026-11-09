"""The things the strategy has to beat.

A market-neutral premium seller is not competing with the index, so reporting
it against NIFTY alone is the wrong comparison in one direction -- and
reporting it against nothing at all is the wrong comparison in every direction.
Three comparators, and the third is the one that decides whether this project
has a subject:

  risk_free     the opportunity cost.  Money blocked as margin could have sat
                in treasury bills.  A 7% return while bills paid 6.5% is not
                a strategy.
  buy_and_hold  what the money would have done with no cleverness at all.
  UNHEDGED      the SAME strategy without the bought wing.  This is the real
                question: the wing costs premium and the wing releases margin,
                and if the tail it removes is worth less than it costs then the
                honest answer is that the hedged structure does not work at
                these prices.  That is a finding, not a failure.

The unhedged comparator is not built here -- it is the same engine with
`structure='naked_put'` or `'naked_strangle'`, so it shares every line of
accounting, charge and slippage code with the hedged version and the two
cannot drift apart.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

RATE_CARD = Path(__file__).resolve().parents[1] / "conf/rates/india-policy-rate.yaml"


class RiskFree:
    """The RBI policy repo rate, as a dated step function.

    A SUBSTITUTE for the 91-day treasury bill, which is the more correct
    instrument; see the card's own header for why and for the size of the
    error it introduces (roughly 25 bps either way, with no consistent sign).
    """

    def __init__(self, path: str | Path = RATE_CARD):
        with open(path) as f:
            raw = yaml.safe_load(f)
        self.name = raw["name"]
        self.changes = []
        for c in raw["changes"]:
            d = c["from"]
            d = d if isinstance(d, date) else date.fromisoformat(str(d))
            self.changes.append((d, float(c["rate"]) / 100.0))
        self.changes.sort()

    def rate_on(self, d: date) -> float:
        out = self.changes[0][1]
        for when, r in self.changes:
            if when <= d:
                out = r
            else:
                break
        return out

    def grow(self, capital: float, days: list[date]) -> list[tuple[date, float]]:
        """Compound `capital` daily at the prevailing rate, ACT/365."""
        curve = []
        v = capital
        prev = None
        for d in days:
            if prev is not None:
                v *= (1.0 + self.rate_on(d)) ** ((d - prev).days / 365.0)
            curve.append((d, v))
            prev = d
        return curve

    def annualised_over(self, start: date, end: date) -> float:
        """Average annualised rate across a span, weighted by days."""
        if end <= start:
            return self.rate_on(start)
        total, d = 0.0, start
        n = (end - start).days
        while d < end:
            total += self.rate_on(d)
            d = date.fromordinal(d.toordinal() + 1)
        return total / n


def buy_and_hold(index_closes: list[tuple[date, float]],
                 capital: float) -> list[tuple[date, float]]:
    if not index_closes:
        return []
    base = index_closes[0][1]
    return [(d, capital * c / base) for d, c in index_closes]


__all__ = ["RiskFree", "buy_and_hold"]
