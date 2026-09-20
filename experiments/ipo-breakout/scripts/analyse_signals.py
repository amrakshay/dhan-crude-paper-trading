"""Do the signals beat what was available on the same days?

For every signal, the forward return from the entry close over several
horizons, against two baselines computed on the SAME dates (see
`ipolib/panel.py` for why there are two and which is the softer), with a block
bootstrap by calendar month for significance.

    python3 scripts/analyse_signals.py                    # every setup
    python3 scripts/analyse_signals.py --setup base

Writes out/analysis_<setup>_<profile>.txt and .json.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np

import _bootstrap  # noqa: F401

from ipolib import listings, panel as panel_mod

OUT = Path(__file__).resolve().parent.parent / "out"

# Other IPOs count as this listing's cohort when they listed within this many
# days of it. Six months either side: wide enough to give the average a
# population, narrow enough that 2021's listings are not averaged with 2018's.
COHORT_WINDOW_DAYS = 183


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setup", nargs="*", default=["base", "day1", "week1"])
    parser.add_argument("--profile", default="strict")
    parser.add_argument("--iterations", type=int, default=5000)
    args = parser.parse_args()

    print("loading the panel...", flush=True)
    panel = panel_mod.load_panel()
    print(f"panel: {len(panel.dates)} dates x {len(panel.symbols)} symbols",
          flush=True)

    matched, _unmatched = listings.build()
    listing_dates = {listing.symbol: listing.listing_date for listing in matched}
    column_of = {symbol: i for i, symbol in enumerate(panel.symbols)}

    for setup in args.setup:
        path = OUT / f"signals_{setup}_{args.profile}.json"
        if not path.exists():
            print(f"skipping {setup}: {path.name} not found")
            continue
        signals = json.loads(path.read_text())
        if not signals:
            continue

        rows = []
        dropped = 0
        for signal in signals:
            signal_date = date.fromisoformat(signal["signal_date"])
            row = panel.row(signal_date)
            column = column_of.get(signal["symbol"])
            listed = listing_dates.get(signal["symbol"])
            # A signals file can go stale against the universe: `listings.build`
            # claims each symbol greedily, so growing the database can hand an
            # IPO to a different ticker than it had when the scan ran. Dropped
            # and COUNTED rather than crashed on, and the count is printed --
            # a silent drop would quietly shrink the sample.
            if row is None or column is None or listed is None:
                dropped += 1
                continue
            cohort = np.array([
                column_of[symbol] for symbol, when in listing_dates.items()
                if symbol in column_of and symbol != signal["symbol"]
                and abs((when - listed).days) <= COHORT_WINDOW_DAYS])

            record = {
                "symbol": signal["symbol"],
                "signal_date": signal["signal_date"],
                "month": signal["signal_date"][:7],
                "score": signal["score"],
                "sessions_after_listing": signal["sessions_after_listing"],
                "lockin_regime": signal["lockin_regime"],
                "near_unlock": signal["near_unlock"],
            }
            for horizon in panel_mod.HORIZONS:
                returns = panel_mod.forward_returns(panel, row, horizon)
                record[f"r{horizon}"] = float(returns[column])
                record[f"mkt{horizon}"] = panel_mod.market_baseline(
                    panel, row, horizon, exclude=column)
                record[f"coh{horizon}"] = (
                    panel_mod.cohort_baseline(panel, row, horizon, cohort,
                                              exclude=column)
                    if len(cohort) else np.nan)
            rows.append(record)

        lines = [f"{setup} / {args.profile}: {len(rows)} signals",
                 "=" * 60, ""]
        if dropped:
            lines += [f"({dropped} signal(s) dropped: not resolvable against the "
                      f"current universe -- re-run scripts/scan_real.py)", ""]
        payload = {"setup": setup, "profile": args.profile, "n": len(rows),
                   "horizons": {}}
        months = np.array([r["month"] for r in rows])

        header = (f"{'horizon':>8s} {'n':>5s} {'signal':>9s} {'market':>9s} "
                  f"{'vs mkt':>9s} {'cohort':>9s} {'vs cohort':>10s} "
                  f"{'95% CI vs mkt':>22s} {'p<=0':>6s}")
        lines += [header, "-" * len(header)]

        for horizon in panel_mod.HORIZONS:
            signal_returns = np.array([r[f"r{horizon}"] for r in rows])
            market = np.array([r[f"mkt{horizon}"] for r in rows])
            cohort_returns = np.array([r[f"coh{horizon}"] for r in rows])
            excess_market = signal_returns - market
            excess_cohort = signal_returns - cohort_returns

            boot_market = panel_mod.block_bootstrap(
                excess_market, months, n_iterations=args.iterations)
            boot_cohort = panel_mod.block_bootstrap(
                excess_cohort, months, n_iterations=args.iterations)

            finite = np.isfinite(signal_returns)
            lines.append(
                f"{horizon:>8d} {int(finite.sum()):>5d} "
                f"{np.nanmean(signal_returns):>8.2%} {np.nanmean(market):>8.2%} "
                f"{boot_market['mean']:>8.2%} {np.nanmean(cohort_returns):>8.2%} "
                f"{boot_cohort['mean']:>9.2%} "
                f"[{boot_market['lo']:>+7.2%},{boot_market['hi']:>+7.2%}] "
                f"{boot_market['p_le_zero']:>6.3f}")
            payload["horizons"][horizon] = {
                "n": int(finite.sum()),
                "signal_mean": float(np.nanmean(signal_returns)),
                "market_mean": float(np.nanmean(market)),
                "cohort_mean": float(np.nanmean(cohort_returns)),
                "excess_vs_market": boot_market,
                "excess_vs_cohort": boot_cohort,
                "win_rate": float(np.nanmean(signal_returns > 0)),
                "median": float(np.nanmedian(signal_returns)),
            }

        lines += ["",
                  "'vs mkt' and 'vs cohort' are the block-bootstrap means of the",
                  "per-signal excess, resampled by calendar month. The cohort",
                  "baseline is the SOFTER one -- IPOs as a class underperform, so",
                  "beating other IPOs of the same vintage is a weaker claim than",
                  "beating the market. See ipolib/panel.py.", ""]

        # Distribution, because a mean over 100 trades hides everything.
        for horizon in (20, 60):
            values = np.array([r[f"r{horizon}"] for r in rows])
            values = values[np.isfinite(values)]
            if not len(values):
                continue
            lines += [
                f"forward {horizon} sessions: n={len(values)}  "
                f"win rate {np.mean(values > 0):.1%}  "
                f"median {np.median(values):+.2%}  mean {np.mean(values):+.2%}",
                f"  percentiles  5% {np.quantile(values, .05):+.1%}   "
                f"25% {np.quantile(values, .25):+.1%}   "
                f"75% {np.quantile(values, .75):+.1%}   "
                f"95% {np.quantile(values, .95):+.1%}", ""]

        # By lock-in regime and by proximity to an unlock: the causal feature.
        by_unlock = defaultdict(list)
        for record in rows:
            key = record["near_unlock"] or "not near an unlock"
            if np.isfinite(record["r20"]):
                by_unlock[key].append(record["r20"] - record["mkt20"])
        if by_unlock:
            lines += ["excess over market at 20 sessions, by unlock proximity:"]
            for key, values in sorted(by_unlock.items()):
                lines.append(f"  {key:<22s} n={len(values):3d}  "
                             f"mean {np.mean(values):+.2%}")
            lines += ["", "(a signal is 'near' an unlock when it lands within 3 "
                          "days of one; see ipolib/lockins.py for why 3)", ""]

        text = "\n".join(lines)
        print("\n" + text)
        (OUT / f"analysis_{setup}_{args.profile}.txt").write_text(text + "\n")
        (OUT / f"analysis_{setup}_{args.profile}.json").write_text(
            json.dumps(payload, indent=2, default=str))
        (OUT / f"rows_{setup}_{args.profile}.json").write_text(
            json.dumps(rows, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
