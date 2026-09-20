"""How different is the listing bar, really?

RESEARCH.md section 4 asserts, from NSE's rules, that the listing-day bar is not
a normal bar: a shorter session, a +-5% or +-20% band measured from an auction
price, and volume that never recurs. Those are sourced. What is NOT sourced is
how OFTEN the band binds -- and that is the number that decides whether "break
above the listing-day high" is a supply level or an arithmetic fact about the
rulebook.

So it is measured rather than assumed.

    python3 scripts/listing_bar_stats.py

Writes out/listing_bar_stats.txt and .json.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import numpy as np

import _bootstrap  # noqa: F401

from ipolib import listings

OUT = Path(__file__).resolve().parent.parent / "out"


def main() -> int:
    matched, _ = listings.build()
    rows = []
    for listing in matched:
        bars = listings.load_bars(listing.symbol)
        stats = listings.listing_bar_stats(bars)
        if not stats:
            continue
        stats["symbol"] = listing.symbol
        stats["year"] = listing.year
        stats["listing_return_pct"] = listing.listing_return_pct
        rows.append(stats)

    if not rows:
        return print("no listings -- run scripts/scan_real.py's prerequisites") or 1

    def column(key):
        return np.array([r[key] for r in rows if np.isfinite(r.get(key, np.nan))])

    volume_ratio = column("volume_vs_next20_median")
    high_move = column("high_vs_open_pct")
    low_move = column("low_vs_open_pct")
    hit_5 = sum(1 for r in rows if r["hit_up_5"] or r["hit_down_5"])
    hit_20 = sum(1 for r in rows if r["hit_up_20"] or r["hit_down_20"])
    closed_high = sum(1 for r in rows if r["closed_at_high"])
    closed_low = sum(1 for r in rows if r["closed_at_low"])

    lines = [
        "The listing bar, measured",
        "=" * 25,
        "",
        f"{len(rows)} matched listings.",
        "",
        "VOLUME. The listing bar against the median of the twenty sessions",
        "after it:",
        f"  median {np.median(volume_ratio):.1f}x   "
        f"25th {np.quantile(volume_ratio, .25):.1f}x   "
        f"75th {np.quantile(volume_ratio, .75):.1f}x   "
        f"max {volume_ratio.max():.0f}x",
        "",
        "  This is why the listing bar is excluded from every average. A",
        "  50-session mean volume that includes it is dominated by it for",
        "  fifty sessions, and every 'volume 40% above average' test then",
        "  fails by construction.",
        "",
        "THE BAND. Moves from the listing (auction) price on day one:",
        f"  high above open   median {np.median(high_move):+.2f}%   "
        f"max {high_move.max():+.1f}%",
        f"  low below open    median {np.median(low_move):+.2f}%   "
        f"min {low_move.min():+.1f}%",
        "",
        f"  closed exactly at the day's high   {closed_high:4d}  "
        f"({closed_high / len(rows):.0%})",
        f"  closed exactly at the day's low    {closed_low:4d}  "
        f"({closed_low / len(rows):.0%})",
        f"  an extreme sitting on a +-5% band  {hit_5:4d}  "
        f"({hit_5 / len(rows):.0%})",
        f"  an extreme sitting on a +-20% band {hit_20:4d}  "
        f"({hit_20 / len(rows):.0%})",
        "",
        "  A bar that closes exactly on its high, on a band, is frozen at a",
        "  circuit. Its high is the rulebook, not a price at which supply",
        "  met demand -- which is what makes the 'above the listing-day high'",
        "  setup (RESEARCH.md section 2, setup A) suspect before any return is",
        "  measured.",
        "",
    ]

    by_year = Counter()
    frozen_by_year = Counter()
    for row in rows:
        by_year[row["year"]] += 1
        if row["closed_at_high"] or row["closed_at_low"]:
            frozen_by_year[row["year"]] += 1
    lines += ["Listings closing at an extreme, by year:"]
    for year in sorted(by_year):
        lines.append(f"  {year}  {frozen_by_year[year]:3d} / {by_year[year]:3d}")

    text = "\n".join(lines)
    print(text)
    OUT.mkdir(exist_ok=True)
    (OUT / "listing_bar_stats.txt").write_text(text + "\n")
    (OUT / "listing_bar_stats.json").write_text(json.dumps(rows, indent=2,
                                                           default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
