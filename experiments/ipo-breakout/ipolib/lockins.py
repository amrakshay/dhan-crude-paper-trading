"""India's statutory lock-in calendar, as a function of the IPO's own date.

This is the one genuinely causal, non-price feature in the project: the dates
are knowable in advance from the listing date alone, they are statutory rather
than contractual, and they are the same for every issue subject to the same
rule. See RESEARCH.md section 3.

THE TRAP THIS MODULE EXISTS TO AVOID. The rules changed twice inside the
sample window. Applying today's schedule to a 2019 listing is not a small
error -- before April 2022 there was no 90-day anchor tranche at all, and
before August 2021 pre-IPO holders were locked for twelve months, not six. A
constant calendar would place unlock dates that never existed and would then
happily measure returns around them.

WHAT THIS MODULE DOES NOT KNOW. Lock-in runs from the date of ALLOTMENT; this
project knows only the listing date. The gap is the allotment-to-listing leg,
which SEBI cut from T+6 to T+3 for issues from December 2023 -- so the error is
2-4 sessions AND it shifts mid-sample. Every window built here is therefore
wide enough to swallow it, and `DEFAULT_WINDOW` says so out loud.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

# SEBI ICDR (Second Amendment) Regulations 2021: notified 13 Aug 2021.
# Promoter minimum contribution 3y -> 18m; promoter excess and non-promoter
# pre-IPO holdings 12m -> 6m.
LOCKIN_HALVING_DATE = date(2021, 8, 13)

# The anchor allotment was a single flat 30-day lock from the concept's
# introduction until this date; for issues opening on or after it, 50% unlocks
# at 30 days and 50% at 90.
ANCHOR_SPLIT_DATE = date(2022, 4, 1)

# Sessions either side of a computed unlock date that a test must treat as
# "at the unlock". Three, because listing-minus-allotment is 2-4 sessions and
# is not known per issue. A tighter window would be measuring the approximation
# rather than the event.
DEFAULT_WINDOW = 3


@dataclass(frozen=True)
class Unlock:
    """One scheduled supply event."""

    date: date
    kind: str          # anchor | pre_ipo | promoter_excess | promoter_minimum
    fraction: float    # of that holder class released on this date, 0..1
    statutory: bool    # True when the stock BECOMES sellable by rule, rather
                       # than when a discretionary holder might choose to sell

    def __repr__(self) -> str:  # pragma: no cover -- debugging affordance
        return f"<Unlock {self.date} {self.kind} {self.fraction:.0%}>"


def _add_months(day: date, months: int) -> date:
    """Calendar months, clamped to the month's last day.

    31 Jan + 1 month is 28/29 Feb, not 3 March. Statutory periods are expressed
    in months, so counting 30-day blocks would drift by several days over 18.
    """
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    last_day = [31, 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0))
                else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, min(day.day, last_day))


def schedule(listing_date: date) -> list[Unlock]:
    """Every statutory unlock for an issue that listed on `listing_date`.

    Dates are approximated from listing rather than allotment; see the module
    docstring. Returned in chronological order.
    """
    anchor_split = listing_date >= ANCHOR_SPLIT_DATE
    halved = listing_date >= LOCKIN_HALVING_DATE

    unlocks: list[Unlock] = []

    if anchor_split:
        unlocks.append(Unlock(listing_date + timedelta(days=30), "anchor", 0.5, True))
        unlocks.append(Unlock(listing_date + timedelta(days=90), "anchor", 0.5, True))
    else:
        unlocks.append(Unlock(listing_date + timedelta(days=30), "anchor", 1.0, True))

    # Discretionary from here down: the stock becomes sellable, but whether a
    # promoter or a pre-IPO fund sells is a choice, not a rule. Flagged so the
    # analysis can weight the anchor tests above these.
    pre_ipo_months = 6 if halved else 12
    unlocks.append(Unlock(_add_months(listing_date, pre_ipo_months), "pre_ipo", 1.0, False))
    unlocks.append(
        Unlock(_add_months(listing_date, pre_ipo_months), "promoter_excess", 1.0, False))
    unlocks.append(
        Unlock(_add_months(listing_date, 18 if halved else 36), "promoter_minimum", 1.0, False))

    return sorted(unlocks, key=lambda u: (u.date, u.kind))


def regime(listing_date: date) -> str:
    """Which rulebook this listing lived under. For grouping results."""
    if listing_date >= ANCHOR_SPLIT_DATE:
        return "anchor-split"          # from 2022-04-01
    if listing_date >= LOCKIN_HALVING_DATE:
        return "halved-no-split"       # 2021-08-13 .. 2022-03-31
    return "pre-2021"                  # before 2021-08-13


def days_to_next_unlock(listing_date: date, as_of: date,
                        statutory_only: bool = True) -> tuple[int, str] | None:
    """Sessions-agnostic calendar distance to the next unlock at or after `as_of`.

    Returns (days, kind), or None when every unlock is behind us. Positive means
    the unlock is ahead. `statutory_only` keeps the anchor dates and drops the
    discretionary ones, which is the default because only the anchor dates are
    a rule rather than a possibility.
    """
    for unlock in schedule(listing_date):
        if statutory_only and not unlock.statutory:
            continue
        if unlock.date >= as_of:
            return (unlock.date - as_of).days, unlock.kind
    return None


def near_unlock(listing_date: date, as_of: date, window: int = DEFAULT_WINDOW,
                statutory_only: bool = True) -> Unlock | None:
    """The unlock `as_of` sits within `window` days of, if any.

    "A breakout three days before an anchor unlock is not the same trade as one
    three days after" -- this is the function that says which one you have.
    """
    for unlock in schedule(listing_date):
        if statutory_only and not unlock.statutory:
            continue
        if abs((unlock.date - as_of).days) <= window:
            return unlock
    return None
