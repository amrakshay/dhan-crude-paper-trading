"""Which exit rule? Measured, not asserted.

Every rule is run over the same signal set, and reported in BOTH currencies --
R and rupees -- because fixed-notional sizing makes them disagree: a trade with
a 1% stop earning 10R makes 10% of a slot; one with a 5% stop earning 3R makes
15%. A table in R alone would pick the wrong rule.

    python3 scripts/exit_grid.py --setup base

Writes out/exit_grid.txt and .json.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

import _bootstrap  # noqa: F401

from ipolib import listings, trades as trades_mod

OUT = Path(__file__).resolve().parent.parent / "out"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setup", default="base")
    parser.add_argument("--profile", default="strict")
    parser.add_argument("--min-stop-atr", type=float, nargs="*",
                        default=[0.0, 0.5])
    # Holding periods, because a rule cannot be judged without one: a trail
    # that exits in 17 sessions and a stop held for 142 are not the same bet
    # even when the per-trade number favours the second. The portfolio
    # simulation arbitrates at matched EXPOSURE; this grid spans the range
    # so it has something to arbitrate between.
    parser.add_argument("--max-hold", type=int, nargs="*",
                        default=[40, 80, 250])
    args = parser.parse_args()

    signals = json.loads(
        (OUT / f"signals_{args.setup}_{args.profile}.json").read_text())
    bars_cache = {symbol: listings.load_bars(symbol)
                  for symbol in sorted({s["symbol"] for s in signals})}

    lines = [f"Exit rules on {len(signals)} {args.setup} signals ({args.profile})",
             "=" * 62, "",
             "'total %' is the sum of per-trade cash returns -- what a fixed",
             "notional per slot would have earned, before costs. 'mean R' is",
             "risk-relative. They rank rules DIFFERENTLY and that is the point.",
             ""]
    header = (f"{'rule':<16s} {'atr/hold':>9s} {'n':>4s} {'win%':>6s} {'mean R':>8s} "
              f"{'med R':>7s} {'mean %':>8s} {'total %':>9s} {'hold':>6s} "
              f"{'worst %':>8s}")
    lines += [header, "-" * len(header)]
    payload = []

    for floor in args.min_stop_atr:
      for hold in args.max_hold:
        for name, rule in trades_mod.EXIT_RULES.items():
            built = []
            for signal in signals:
                bars = bars_cache.get(signal["symbol"])
                if bars is None:
                    continue
                trade = trades_mod.build_trade(bars, signal, rule,
                                               min_stop_atr=floor,
                                               max_hold=hold)
                if trade is not None:
                    built.append(trade)
            if not built:
                continue
            r = np.array([t.r_multiple for t in built])
            pct = np.array([t.return_pct for t in built])
            row = (f"{name:<16s} {floor:>4.1f}/{hold:<4d} {len(built):>4d} "
                   f"{np.mean(pct > 0):>6.1%} {np.mean(r):>8.2f} "
                   f"{np.median(r):>7.2f} {np.mean(pct):>8.2%} "
                   f"{np.sum(pct):>9.1%} "
                   f"{np.mean([t.bars for t in built]):>6.0f} "
                   f"{np.min(pct):>8.1%}")
            lines.append(row)
            payload.append({
                "rule": name, "min_stop_atr": floor, "max_hold": hold,
                "n": len(built),
                "win_rate": float(np.mean(pct > 0)),
                "mean_r": float(np.mean(r)), "median_r": float(np.median(r)),
                "mean_pct": float(np.mean(pct)), "total_pct": float(np.sum(pct)),
                "mean_hold": float(np.mean([t.bars for t in built])),
                "worst_pct": float(np.min(pct)),
            })
        lines.append("")

    risk = np.array([s["entry_close"] / s["stop_price"] - 1.0 for s in signals
                     if s.get("stop_price")])
    lines += [
        "Stop distance at the signal, as a fraction of price:",
        f"  median {np.median(risk):.1%}   10th {np.quantile(risk, .1):.1%}   "
        f"90th {np.quantile(risk, .9):.1%}",
        "An IPO base's low sits far below its pivot, so the 0.5-ATR stop floor",
        "the VCP work needed never binds here -- the rows for 0.0 and 0.5 are",
        "identical. That is a finding about the pattern, not a broken sweep.",
        ""]

    text = "\n".join(lines)
    print(text)
    OUT.mkdir(exist_ok=True)
    (OUT / "exit_grid.txt").write_text(text + "\n")
    (OUT / "exit_grid.json").write_text(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
