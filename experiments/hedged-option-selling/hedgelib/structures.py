"""Option structures: their legs, their arithmetic, and their payoff.

Every structure here is DEFINED RISK -- each short leg has a bought wing further
out.  Undefined-risk naked selling is out of scope, and the one place it appears
is as an explicit BASELINE (`naked_put`, `naked_strangle`), because the handoff
requires the wing to justify its own cost on every metric.  Those two are never
traded by the strategy; they exist to be beaten or not beaten.

All quantities are in UNITS (lots x lot_size), matching the bhavcopy.  A short
leg carries a NEGATIVE qty throughout, so a structure's value is just the sum of
qty x price and needs no special-casing anywhere.

MULTI-EXPIRY
------------
A leg carries its own `expiry`.  For a vertical or a condor every leg shares one,
and `expiry` stays None; for a DIAGONAL the short and long legs sit in different
expiries and each names its own.

That is why a price dictionary is keyed on `(strike, is_call, expiry)` rather
than `(strike, is_call)`: two legs of a diagonal can differ only in expiry, and a
two-part key would silently collapse them into one.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

LegKey = tuple[float, bool, "date | None"]


@dataclass(frozen=True)
class StructLeg:
    strike: float
    is_call: bool
    qty: int                      # signed, in units; negative = short
    expiry: date | None = None    # None = the structure's single expiry

    @property
    def is_short(self) -> bool:
        return self.qty < 0

    @property
    def key(self) -> LegKey:
        return (self.strike, self.is_call, self.expiry)


@dataclass(frozen=True)
class Structure:
    name: str
    legs: tuple[StructLeg, ...]
    lot_size: int

    # -- shape -------------------------------------------------------------

    @property
    def expiries(self) -> tuple[date | None, ...]:
        seen: list[date | None] = []
        for l in self.legs:
            if l.expiry not in seen:
                seen.append(l.expiry)
        return tuple(seen)

    @property
    def is_multi_expiry(self) -> bool:
        return len([e for e in self.expiries if e is not None]) > 1

    @property
    def near_expiry(self) -> date | None:
        es = [e for e in self.expiries if e is not None]
        return min(es) if es else None

    @property
    def far_expiry(self) -> date | None:
        es = [e for e in self.expiries if e is not None]
        return max(es) if es else None

    @property
    def short_legs(self) -> tuple[StructLeg, ...]:
        return tuple(l for l in self.legs if l.is_short)

    @property
    def n_legs(self) -> int:
        return len(self.legs)

    # -- arithmetic --------------------------------------------------------

    def value_at(self, prices: dict[LegKey, float]) -> float:
        return sum(l.qty * prices[l.key] for l in self.legs)

    def net_credit(self, prices: dict[LegKey, float]) -> float:
        """Positive when the structure is opened for a net CREDIT.

        Also equals what it costs to CLOSE at those prices, so the sign is never
        flipped a second time.
        """
        return -sum(l.qty * prices[l.key] for l in self.legs)

    def payoff_at_expiry(self, spot: float) -> float:
        """Total rupee payoff with EVERY leg valued at intrinsic, premium aside.

        For a single-expiry structure this is the payoff at its expiry.

        For a DIAGONAL it is a CONSERVATIVE BOUND rather than the payoff: it
        assumes the far leg has lost all remaining time value by the near leg's
        expiry, which it has not.  The real position is therefore never worse
        than this, and `max_loss` inherits that property -- which is exactly what
        a capital-at-risk denominator should do.
        """
        v = 0.0
        for l in self.legs:
            intrinsic = (max(spot - l.strike, 0.0) if l.is_call
                         else max(l.strike - spot, 0.0))
            v += l.qty * intrinsic
        return v

    def _grid(self) -> list[float]:
        ks = sorted({l.strike for l in self.legs})
        return [0.0] + ks + [ks[-1] * 2.0]

    def max_loss(self, prices: dict[LegKey, float]) -> float:
        """Worst payoff over the structure's own breakpoints, plus the credit.

        Computed numerically rather than from a closed form, so it is correct
        for condors, flies and diagonals without a special case per shape.
        Returns +inf for an unbounded structure, which is how the naked
        baselines announce themselves.

        For a diagonal this is an UPPER BOUND on the loss (see
        `payoff_at_expiry`), and it is used as the capital-at-risk denominator
        precisely because it cannot understate the risk.
        """
        if self.is_unbounded():
            return float("inf")
        credit = self.net_credit(prices)
        return -min(self.payoff_at_expiry(s) + credit for s in self._grid())

    def max_profit(self, prices: dict[LegKey, float]) -> float:
        credit = self.net_credit(prices)
        return max(self.payoff_at_expiry(s) + credit for s in self._grid())

    def is_unbounded(self) -> bool:
        """True when a tail is not covered by a bought leg of the same type."""
        call_qty = sum(l.qty for l in self.legs if l.is_call)
        put_qty = sum(l.qty for l in self.legs if not l.is_call)
        return call_qty < 0 or put_qty < 0

    def breakevens(self, prices: dict[LegKey, float], lo: float = 0.0,
                   hi: float | None = None, steps: int = 20000) -> list[float]:
        credit = self.net_credit(prices)
        ks = sorted({l.strike for l in self.legs})
        hi = hi if hi is not None else ks[-1] * 1.6
        out: list[float] = []
        prev_s, prev_v = lo, self.payoff_at_expiry(lo) + credit
        for i in range(1, steps + 1):
            s = lo + (hi - lo) * i / steps
            v = self.payoff_at_expiry(s) + credit
            if (prev_v < 0) != (v < 0) and v != prev_v:
                out.append(prev_s + (s - prev_s) * (-prev_v) / (v - prev_v))
            prev_s, prev_v = s, v
        return out


# --------------------------------------------------------------------------
# builders -- single expiry
# --------------------------------------------------------------------------

def bull_put_spread(short_k: float, long_k: float, lot: int, lots: int = 1,
                    expiry: date | None = None,
                    long_ratio: float = 1.0) -> Structure:
    """Sell the higher put, buy `long_ratio` of the lower.  Bullish/neutral.

    `long_ratio` = 1.0 is the ordinary vertical.

    ABOVE 1.0 it is a PUT BACKSPREAD, and the payoff changes shape rather than
    degree.  With two longs against one short:

        above K_s          both worthless, keep the credit
        K_l .. K_s         the short bleeds, the longs are still worthless
        at K_l             MAXIMUM LOSS -- and it is a point, not a plateau
        below K_l          the two longs outrun the one short, so the loss
                           SHRINKS and eventually turns into profit

    So a crash stops being the disaster case and becomes the good case.  What
    is paid for that is a bigger loss in a MODERATE move and a smaller credit
    (often a net debit), which is theta bleeding every day nothing happens.

    BELOW 1.0 would be a ratio spread with a naked short below the long strike.
    That is undefined risk and out of scope -- `is_unbounded()` catches it and
    `max_loss` returns infinity, so it can never be sized or reported as though
    it were bounded.
    """
    assert long_k < short_k, "the bought wing must be BELOW the sold put"
    q = lot * lots
    return Structure("bull_put_spread" if long_ratio == 1.0 else "put_backspread",
                     (StructLeg(short_k, False, -q, expiry),
                      StructLeg(long_k, False, int(round(q * long_ratio)), expiry)),
                     lot)


def bear_call_spread(short_k: float, long_k: float, lot: int, lots: int = 1,
                     expiry: date | None = None,
                     long_ratio: float = 1.0) -> Structure:
    """Sell the lower call, buy `long_ratio` of the higher.  Bearish/neutral."""
    assert long_k > short_k, "the bought wing must be ABOVE the sold call"
    q = lot * lots
    return Structure("bear_call_spread" if long_ratio == 1.0 else "call_backspread",
                     (StructLeg(short_k, True, -q, expiry),
                      StructLeg(long_k, True, int(round(q * long_ratio)), expiry)),
                     lot)


def iron_condor(short_put: float, long_put: float, short_call: float,
                long_call: float, lot: int, lots: int = 1,
                expiry: date | None = None) -> Structure:
    """Both spreads at once.  Two credits, one margin, four legs of brokerage."""
    assert long_put < short_put < short_call < long_call
    q = lot * lots
    return Structure("iron_condor",
                     (StructLeg(short_put, False, -q, expiry),
                      StructLeg(long_put, False, q, expiry),
                      StructLeg(short_call, True, -q, expiry),
                      StructLeg(long_call, True, q, expiry)), lot)


def iron_fly(atm: float, long_put: float, long_call: float, lot: int,
             lots: int = 1, expiry: date | None = None) -> Structure:
    """Short straddle at one strike with both wings bought."""
    assert long_put < atm < long_call
    q = lot * lots
    return Structure("iron_fly",
                     (StructLeg(atm, False, -q, expiry),
                      StructLeg(long_put, False, q, expiry),
                      StructLeg(atm, True, -q, expiry),
                      StructLeg(long_call, True, q, expiry)), lot)


# --------------------------------------------------------------------------
# builders -- DIAGONAL (two expiries)
# --------------------------------------------------------------------------

def put_diagonal(short_k: float, short_expiry: date,
                 long_k: float, long_expiry: date,
                 lot: int, lots: int = 1) -> Structure:
    """Sell a near-expiry put, buy a further-OTM put in a LATER expiry.

    The long leg is both further out of the money AND longer dated.  It hedges
    differently from a same-expiry wing: it holds time value at the short leg's
    expiry and its vega rises in a volatility spike, so it does not go to zero
    the way a near-dated wing can (CALENDARS.md §2).

    Risk is bounded, and the bound is no worse than the equivalent vertical --
    see `payoff_at_expiry` for why `max_loss` is a conservative bound here.
    """
    assert long_k < short_k, "the bought put must be BELOW the sold put"
    assert long_expiry > short_expiry, "the bought leg must be LATER dated"
    q = lot * lots
    return Structure("put_diagonal",
                     (StructLeg(short_k, False, -q, short_expiry),
                      StructLeg(long_k, False, q, long_expiry)), lot)


def call_diagonal(short_k: float, short_expiry: date,
                  long_k: float, long_expiry: date,
                  lot: int, lots: int = 1) -> Structure:
    """Sell a near-expiry call, buy a further-OTM call in a LATER expiry."""
    assert long_k > short_k, "the bought call must be ABOVE the sold call"
    assert long_expiry > short_expiry, "the bought leg must be LATER dated"
    q = lot * lots
    return Structure("call_diagonal",
                     (StructLeg(short_k, True, -q, short_expiry),
                      StructLeg(long_k, True, q, long_expiry)), lot)


def double_diagonal(short_put: float, long_put: float,
                    short_call: float, long_call: float,
                    short_expiry: date, long_expiry: date,
                    lot: int, lots: int = 1) -> Structure:
    """Both diagonals at once -- the two-expiry analogue of an iron condor."""
    assert long_put < short_put < short_call < long_call
    assert long_expiry > short_expiry
    q = lot * lots
    return Structure("double_diagonal",
                     (StructLeg(short_put, False, -q, short_expiry),
                      StructLeg(long_put, False, q, long_expiry),
                      StructLeg(short_call, True, -q, short_expiry),
                      StructLeg(long_call, True, q, long_expiry)), lot)


# -- the unhedged baselines.  NOT traded; they exist to be compared against. --

def naked_put(short_k: float, lot: int, lots: int = 1,
              expiry: date | None = None) -> Structure:
    return Structure("naked_put",
                     (StructLeg(short_k, False, -lot * lots, expiry),), lot)


def naked_strangle(short_put: float, short_call: float, lot: int, lots: int = 1,
                   expiry: date | None = None) -> Structure:
    q = lot * lots
    return Structure("naked_strangle",
                     (StructLeg(short_put, False, -q, expiry),
                      StructLeg(short_call, True, -q, expiry)), lot)


BUILDERS = {
    "bull_put_spread": bull_put_spread,
    "bear_call_spread": bear_call_spread,
    "iron_condor": iron_condor,
    "iron_fly": iron_fly,
    "put_diagonal": put_diagonal,
    "call_diagonal": call_diagonal,
    "double_diagonal": double_diagonal,
    "naked_put": naked_put,
    "naked_strangle": naked_strangle,
}

MULTI_EXPIRY = {"put_diagonal", "call_diagonal", "double_diagonal"}
RATIOABLE = {"bull_put_spread", "bear_call_spread"}

__all__ = ["Structure", "StructLeg", "LegKey", "BUILDERS", "MULTI_EXPIRY",
           "RATIOABLE",
           "bull_put_spread", "bear_call_spread", "iron_condor", "iron_fly",
           "put_diagonal", "call_diagonal", "double_diagonal",
           "naked_put", "naked_strangle"]
