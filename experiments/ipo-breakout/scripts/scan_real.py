"""Scan the real listing universe, causally, and record every signal.

Causal means: for each listing the as-of bar walks forward from the earliest
bar the setup could fire on, and `detect` sees bars 0..t only. Nothing about
bar t+1 reaches the decision.

    python3 scripts/scan_real.py                       # all setups, strict
    python3 scripts/scan_real.py --setup base --profile relaxed

Writes out/signals_<setup>_<profile>.json and a funnel to out/scan_funnel.txt.
The FUNNEL is as much the result as the signals: how many listings exist, how
many had a base at all, and how many of those ever triggered.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict
from datetime import date
from pathlib import Path

import _bootstrap  # noqa: F401

from ipolib import ipo_base, listings, lockins

OUT = Path(__file__).resolve().parent.parent / "out"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setup", nargs="*", default=["base", "day1", "week1"])
    parser.add_argument("--profile", default="strict")
    parser.add_argument("--min-bars", type=int, default=90,
                        help="a listing needs enough history to measure a "
                             "forward return at all")
    args = parser.parse_args()

    matched, unmatched = listings.build()
    usable = [listing for listing in matched if listing.n_bars >= args.min_bars]
    params = ipo_base.params(args.profile)

    OUT.mkdir(exist_ok=True)
    funnel_lines = [
        "IPO breakout scan -- the funnel",
        "=" * 31,
        "",
        f"IPO calendar entries                  {len(matched) + len(unmatched):5d}",
        f"  matched to a tradable symbol        {len(matched):5d}",
        f"  NOT matched (the survivorship hole) {len(unmatched):5d}",
        f"  with >= {args.min_bars} sessions of history       {len(usable):5d}",
        "",
        f"profile: {args.profile}",
        "",
    ]

    for setup in args.setup:
        signals = []
        had_base = 0
        for listing in usable:
            bars = listings.load_bars(listing.symbol)
            if len(bars) < args.min_bars:
                continue

            forming = ipo_base.scan(bars, params, setup=setup, step=1)
            if forming:
                had_base += 1
            breakout = next((d for d in forming if d.state == "breakout"), None)
            if breakout is None:
                continue

            signal_date = date.fromisoformat(str(bars.date[breakout.end_idx]))
            unlock = lockins.near_unlock(listing.listing_date, signal_date)
            next_unlock = lockins.days_to_next_unlock(
                listing.listing_date, signal_date)
            signals.append({
                "symbol": listing.symbol,
                "company": listing.company,
                "setup": setup,
                "listing_date": listing.listing_date.isoformat(),
                "signal_date": signal_date.isoformat(),
                "sessions_after_listing": breakout.end_idx,
                "entry_close": float(bars.close[breakout.end_idx]),
                "pivot_price": breakout.pivot_price,
                "stop_price": breakout.stop_price,
                "score": breakout.score,
                "reasons": breakout.reasons,
                "issue_price": listing.issue_price,
                "listing_return_pct": listing.listing_return_pct,
                "lockin_regime": lockins.regime(listing.listing_date),
                "near_unlock": unlock.kind if unlock else None,
                "days_to_next_unlock": next_unlock[0] if next_unlock else None,
                "metrics": {k: (None if v is None else float(v))
                            if isinstance(v, (int, float)) else v
                            for k, v in breakout.metrics.items()},
            })

        path = OUT / f"signals_{setup}_{args.profile}.json"
        path.write_text(json.dumps(signals, indent=2, default=str))

        by_year = Counter(s["signal_date"][:4] for s in signals)
        funnel_lines += [
            f"setup {setup!r}",
            f"  listings where a base formed at all {had_base:5d}"
            f"  ({had_base / len(usable):.0%} of usable)",
            f"  listings that triggered a breakout  {len(signals):5d}"
            f"  ({len(signals) / len(usable):.0%} of usable)",
            "  signals by year: " + ", ".join(
                f"{year} {count}" for year, count in sorted(by_year.items())),
            "",
        ]
        print(f"{setup:6s} base formed {had_base:4d}   triggered {len(signals):4d}"
              f"   -> {path.name}", flush=True)

    text = "\n".join(funnel_lines)
    (OUT / "scan_funnel.txt").write_text(text + "\n")
    print("\n" + text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
