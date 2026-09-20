"""Is a lock-in expiry a tradable event? The causal, non-price test.

WHY THIS IS THE MOST DEFENSIBLE THING HERE. Every other feature in this project
is derived from price and could be a coincidence of a decade that mostly went
up. An unlock date is not: it is statutory, it is fixed at allotment, it is
knowable years in advance, and it has a mechanism -- Aggarwal, Krigman and
Womack's account of managers underpricing to build momentum they then sell into
at expiry (RESEARCH.md section 3.4).

THREE THINGS THAT MAKE THIS A TEST RATHER THAN A CHART.

1. A PLACEBO. Returns around day 30 being poor proves nothing on its own: IPOs
   may simply drift down in their second month. So the same measurement is run
   at offsets where NO unlock falls, and the unlock result is only interesting
   if it differs from them.

2. A REGIME PREDICTION. The rules changed on 1 April 2022: before it the whole
   anchor tranche unlocked at day 30, after it half unlocks at 30 and half at
   90. If the effect is supply, the day-30 effect should be LARGER before the
   split than after, and a day-90 effect should appear only after. That is a
   falsifiable prediction the data can refuse, which a single pooled average
   cannot be.

3. A BASELINE. Every return is net of the market's on the same dates.

    python3 scripts/lockin_study.py

Writes out/lockin_study.txt and .json.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import numpy as np

import _bootstrap  # noqa: F401

from ipolib import listings, lockins, panel as panel_mod

OUT = Path(__file__).resolve().parent.parent / "out"

# Sessions measured either side of the event bar.
BEFORE = 10
AFTER = 10

# Calendar-day offsets from listing at which NO statutory unlock falls. Chosen
# to sit between the real ones (30, 90, ~182) and away from their +-3 day
# windows, so a placebo can never overlap a genuine event.
PLACEBO_OFFSETS = (55, 125, 150, 220)

# kind -> (period before the 13 Aug 2021 halving, period after it), in
# calendar months. See ipolib/lockins.py for the amendment.
MONTHS_BY_KIND = {
    "pre_ipo/promoter_excess": (12, 6),
    "promoter_minimum": (36, 18),
}


def _row_at_or_after(panel, day):
    """First panel row on or after `day`. Unlocks land on weekends and holidays."""
    index = int(np.searchsorted(panel.dates, np.datetime64(day.isoformat())))
    return index if index < len(panel.dates) else None


def _window_excess(panel, row, column, sessions, forward: bool):
    """Excess over the market for the `sessions` bars before or after `row`."""
    start, end = (row, row + sessions) if forward else (row - sessions, row)
    if start < 0 or end >= len(panel.dates):
        return np.nan
    price_start, price_end = panel.close[start, column], panel.close[end, column]
    if not (np.isfinite(price_start) and np.isfinite(price_end)) or price_start <= 0:
        return np.nan
    own = price_end / price_start - 1.0
    market = panel_mod.market_baseline(panel, start, end - start, exclude=column)
    return own - market if np.isfinite(market) else np.nan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=5000)
    args = parser.parse_args()

    print("loading the panel...", flush=True)
    panel = panel_mod.load_panel()
    column_of = {symbol: i for i, symbol in enumerate(panel.symbols)}
    matched, _ = listings.build()
    usable = [listing for listing in matched
              if listing.symbol in column_of and listing.n_bars >= 120]
    print(f"{len(usable)} listings with a column in the panel", flush=True)

    # event key -> list of (excess_before, excess_after, month, regime)
    events: dict[str, list] = defaultdict(list)

    for listing in usable:
        column = column_of[listing.symbol]
        regime = lockins.regime(listing.listing_date)

        # `pre_ipo` and `promoter_excess` share a date and a duration, so
        # they produce identical rows. Merged into one event rather than
        # printed twice, which would read as two pieces of evidence.
        scheduled = []
        seen_dates = set()
        for unlock in lockins.schedule(listing.listing_date):
            kind = ("pre_ipo/promoter_excess"
                    if unlock.kind in ("pre_ipo", "promoter_excess")
                    else unlock.kind)
            if (kind, unlock.date) in seen_dates:
                continue
            seen_dates.add((kind, unlock.date))
            scheduled.append((kind, unlock.fraction, unlock.date))
        placebos = [("placebo", 0.0, listing.listing_date + timedelta(days=offset))
                    for offset in PLACEBO_OFFSETS]

        for kind, fraction, when in scheduled + placebos:
            row = _row_at_or_after(panel, when)
            if row is None:
                continue
            before = _window_excess(panel, row, column, BEFORE, forward=False)
            after = _window_excess(panel, row, column, AFTER, forward=True)
            if not (np.isfinite(before) or np.isfinite(after)):
                continue

            days = (when - listing.listing_date).days
            if kind == "anchor":
                key = f"anchor +{days}d ({fraction:.0%})"
            elif kind == "placebo":
                key = f"placebo +{days}d"
            else:
                # Label by the STATUTORY period, not by days/30: a 36-month
                # lock-in measured in days and divided by 30 rounds to 36 or 37
                # depending on which months it crossed, and then one event
                # prints as two.
                months = MONTHS_BY_KIND[kind][
                    lockins.regime(listing.listing_date) != "pre-2021"]
                key = f"{kind} +{months}m"
            events[key].append((before, after, when.isoformat()[:7], regime))

    lines = ["Lock-in expiry: is it a tradable event?",
             "=" * 39, "",
             f"{len(usable)} matched listings. Every figure is EXCESS over the",
             f"market on the same dates. 'before' is the {BEFORE} sessions into the",
             f"unlock, 'after' is the {AFTER} sessions out of it.", "",
             "Placebo rows use offsets from listing at which no statutory",
             "unlock falls. If the unlock rows do not differ from these, there",
             "is no event here -- only the drift every new listing has.", ""]

    # The BEFORE window is bootstrapped too, and shown first, because that is
    # where the supply story predicts the effect: selling into a date everyone
    # can see coming. Reporting only the AFTER window would have measured the
    # relief and missed the event.
    header = (f"{'event':<28s} {'n':>4s} {'before':>8s} "
              f"{'before 95% CI':>20s} {'after':>8s} {'after 95% CI':>20s}")
    lines += [header, "-" * len(header)]

    payload: dict = {}

    def emit(key, records, indent=""):
        before = np.array([r[0] for r in records], dtype=float)
        after = np.array([r[1] for r in records], dtype=float)
        months = np.array([r[2] for r in records])
        boot_before = panel_mod.block_bootstrap(
            before, months, n_iterations=args.iterations)
        boot_after = panel_mod.block_bootstrap(
            after, months, n_iterations=args.iterations)
        lines.append(
            f"{indent + key:<28s} {len(records):>4d} "
            f"{np.nanmean(before):>7.2%} "
            f"[{boot_before['lo']:>+6.2%},{boot_before['hi']:>+6.2%}] "
            f"{np.nanmean(after):>7.2%} "
            f"[{boot_after['lo']:>+6.2%},{boot_after['hi']:>+6.2%}]")
        return {"n": len(records), "before": float(np.nanmean(before)),
                "after": float(np.nanmean(after)),
                "bootstrap_before": boot_before, "bootstrap_after": boot_after}

    for key in sorted(events, key=lambda k: ("placebo" in k, k)):
        payload[key] = emit(key, events[key])

    # --- the regime prediction --------------------------------------------
    lines += ["", "The regime prediction (see the module docstring)", "-" * 47,
              "Before 1 Apr 2022 the WHOLE anchor tranche unlocked at day 30.",
              "After it, half unlocks at 30 and half at 90. If this is supply,",
              "the day-30 effect should be larger in the earlier regime.", "",
              header, "-" * len(header)]
    payload["regimes"] = {}
    for key in sorted(k for k in events if k.startswith("anchor")):
        for regime in ("pre-2021", "halved-no-split", "anchor-split"):
            records = [r for r in events[key] if r[3] == regime]
            if len(records) >= 5:
                payload["regimes"][f"{key} / {regime}"] = emit(
                    f"{regime}", records, indent=f"{key[:12]} ")

    text = "\n".join(lines)
    print("\n" + text)
    OUT.mkdir(exist_ok=True)
    (OUT / "lockin_study.txt").write_text(text + "\n")
    (OUT / "lockin_study.json").write_text(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
