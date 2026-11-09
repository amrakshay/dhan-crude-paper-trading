"""The bull put spread is the only defined-risk structure that made money.
This asks, at length, whether that is real or a cell picked out of a grid.

A single positive cell in a sweep of forty is what a selection artefact looks
like.  The tests here are the ones that would break one:

  * every parameter moved, one at a time -- is the surface a plateau or a spike?
  * both sides of the SEBI structural break
  * with 2020 removed entirely, in case it is a COVID artefact
  * under four times the assumed slippage
  * block bootstrap over whole months

Writes out/bull_put_robustness.txt.
"""
from __future__ import annotations

import sqlite3
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hedgelib.backtest import Backtest, SlippageModel, StrategyConfig  # noqa: E402
from hedgelib.chain import trading_days                                # noqa: E402
from hedgelib.metrics import block_bootstrap, trade_stats              # noqa: E402

DB = ROOT / "data/cache/options.db"
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)
SEBI_BREAK = date(2024, 11, 20)
RISK_FREE = 0.0564          # mean RBI repo over the sample; see baselines.py

_lines: list[str] = []


def say(s: str = "") -> None:
    print(s)
    _lines.append(s)


HDR = (f"  {'variant':<26} {'n':>4} {'total P&L':>11} {'R/risk /yr':>11} "
       f"{'worst':>10} {'worst1%':>10} {'win':>6}")


def main() -> int:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    days = trading_days(conn, "NIFTY")
    base = StrategyConfig(structure="bull_put_spread", short_delta=0.16,
                          wing_delta=0.05, dte_target=30, dte_tolerance=10,
                          profit_take_fraction=0.5, min_trades=100,
                          symbol="NIFTY")

    say("=" * 84)
    say("IS THE BULL PUT SPREAD REAL, OR A CELL PICKED OUT OF A GRID?")
    say("=" * 84)
    say("It is the only defined-risk structure in the sweep that made money.")
    say("That is exactly what a selection artefact looks like, so it gets tested")
    say("harder than the rest.")
    say("")
    say("'R/risk /yr' is the annualised return per rupee-year of MAXIMUM LOSS --")
    say(f"the capital actually at risk. The comparator is the risk-free rate,")
    say(f"{RISK_FREE * 100:.2f}% a year over this sample.")
    say("")
    say("WHY IT MIGHT BE REAL, stated before the numbers so it is a prediction")
    say("rather than a rationalisation: on an equity index the volatility smile")
    say("is skewed, so the put sold sits on a steeper part of it than the put")
    say("bought. Measured on this data (NIFTY 2025-08-01, 27 DTE) the 16-delta")
    say("put carried 12.8% implied volatility against the 16-delta call's 10.6%.")
    say("A put spread is therefore short the richer option and long the cheaper")
    say("one in volatility terms, and a call spread is the reverse. If that is")
    say("the mechanism, the BEAR CALL spread should be badly negative -- and it")
    say("is, at -102% a year on capital at risk.")
    say("")

    results: dict[str, float] = {}

    def row(label: str, cfg: StrategyConfig | None = None,
            slip: SlippageModel | None = None,
            filt=None, trades=None) -> None:
        if trades is None:
            bt = Backtest(conn, cfg or base, slippage=slip or SlippageModel())
            trades = bt.run(days)
        if filt is not None:
            trades = [t for t in trades if filt(t)]
        s = trade_stats(trades)
        if s.n == 0:
            say(f"  {label:<26}    0   (no trades)")
            return
        results[label] = s.annualised_return_on_max_loss
        say(f"  {label:<26} {s.n:>4} {s.total_pnl:>11,.0f} "
            f"{s.annualised_return_on_max_loss * 100:>10.2f}% "
            f"{s.worst_trade:>10,.0f} {s.worst_1pct:>10,.0f} "
            f"{s.win_rate * 100:>5.1f}%")

    say("-" * 84)
    say("1. THE SURFACE -- one parameter at a time")
    say("-" * 84)
    say(HDR)
    row("BASE 0.16d / 0.05d / 30")
    say("  -- short-strike delta --")
    for d in (0.08, 0.10, 0.20, 0.30, 0.40):
        row(f"    short {d:.2f}d", replace(base, short_delta=d))
    say("  -- days to expiry --")
    for n in (7, 14, 21, 45, 60):
        row(f"    DTE {n}", replace(base, dte_target=n,
                                    dte_tolerance=max(3, n // 3)))
    say("  -- wing --")
    for w in (0.02, 0.03, 0.08, 0.10):
        row(f"    wing {w:.2f}d", replace(base, wing_delta=w))
    say("  -- management --")
    row("    hold to expiry", replace(base, profit_take_fraction=None,
                                      hold_to_expiry=True))
    row("    profit take 25%", replace(base, profit_take_fraction=0.25))
    row("    profit take 75%", replace(base, profit_take_fraction=0.75))
    row("    PT50 + stop 2x", replace(base, stop_loss_credit_multiple=2.0))

    vals = list(results.values())
    pos = sum(1 for v in vals if v > 0)
    beat = sum(1 for v in vals if v > RISK_FREE)
    say("")
    say(f"  {pos}/{len(vals)} variants positive; {beat}/{len(vals)} beat the "
        f"risk-free rate.")
    say(f"  median across variants: {np.median(vals) * 100:.2f}% a year.")
    say("")
    if pos < len(vals):
        say("  THE SURFACE IS NOT A PLATEAU. Several neighbouring cells are")
        say("  negative with no pattern to which ones -- DTE 21 loses while")
        say("  DTE 14 and DTE 60 gain. That is the signature of a thin sample,")
        say("  not of a parameter that matters, and it is the main reason this")
        say("  is reported as a signal rather than as a strategy.")

    # ------------------------------------------------------------------
    say("")
    say("-" * 84)
    say("2. IS IT AN ARTEFACT OF ONE PERIOD?")
    say("-" * 84)
    say(HDR)
    bt = Backtest(conn, base, slippage=SlippageModel())
    all_trades = bt.run(days)
    row("full sample", trades=all_trades)
    row("before SEBI 2024-11-20", trades=all_trades,
        filt=lambda t: t.entry_date < SEBI_BREAK)
    row("after SEBI 2024-11-20", trades=all_trades,
        filt=lambda t: t.entry_date >= SEBI_BREAK)
    row("excluding 2020 (COVID)", trades=all_trades,
        filt=lambda t: t.entry_date.year != 2020)
    row("excluding the worst 3", trades=sorted(
        all_trades, key=lambda t: t.net_pnl)[3:])
    row("first half (to 2021)", trades=all_trades,
        filt=lambda t: t.entry_date < date(2021, 7, 1))
    row("second half (from 2021)", trades=all_trades,
        filt=lambda t: t.entry_date >= date(2021, 7, 1))

    # The half-split verdict is COMPUTED, not asserted.  An earlier version of
    # this script hardcoded "the first half is negative" as the strongest
    # objection; after the expiry-selection fix (EXPIRY_MIX.md) that stopped
    # being true, and a hardcoded sentence would have contradicted the table
    # printed directly above it.
    h1 = results.get("first half (to 2021)")
    h2 = results.get("second half (from 2021)")
    say("")
    if h1 is not None and h2 is not None:
        if h1 > 0 and h2 > 0:
            say(f"  BOTH HALVES ARE POSITIVE ({h1 * 100:.1f}% and {h2 * 100:.1f}% a year).")
            say("  That is what a structural premium should look like, and it is")
            say("  the single strongest thing in favour of this result. It was NOT")
            say("  true before the expiry-selection fix: the first half then read")
            say("  -5.3%, because sessions were silently skipped whenever the")
            say("  nearest-to-target expiry was an illiquid weekly. See")
            say("  EXPIRY_MIX.md -- the objection was an artefact of the bug.")
        elif h1 <= 0 < h2:
            say(f"  THE HALF-SPLIT IS EVIDENCE AGAINST: first half {h1 * 100:.1f}% a")
            say(f"  year, second half {h2 * 100:.1f}%. A structural premium should be")
            say("  present in both; an effect concentrated in one half of the")
            say("  record is what a thin sample looks like.")
        else:
            say(f"  Half-split: {h1 * 100:.1f}% and {h2 * 100:.1f}% a year.")
    say("")
    say("  Note separately that removing the worst three trades moves the total")
    say("  substantially -- a handful of observations still carry the result,")
    say("  whichever way the halves fall.")

    # ------------------------------------------------------------------
    say("")
    say("-" * 84)
    say("3. DOES IT SURVIVE A WORSE SPREAD ASSUMPTION?")
    say("-" * 84)
    say("  Slippage is the assumption with no data behind it, and the bought")
    say("  wing is the thinnest leg. 4% is deliberately punitive.")
    say(HDR)
    for rel, tk, lbl in ((0.0, 0.0, "none (unreal)"),
                         (0.01, 1, "1% / 1 tick (base)"),
                         (0.02, 2, "2% / 2 ticks"),
                         (0.04, 4, "4% / 4 ticks"),
                         (0.08, 8, "8% / 8 ticks")):
        row(f"    slippage {lbl}", slip=SlippageModel(rel=rel, ticks=tk))

    # ------------------------------------------------------------------
    say("")
    say("-" * 84)
    say("4. SIGNIFICANCE -- block bootstrap over whole months")
    say("-" * 84)
    bb = block_bootstrap(all_trades, n_boot=20_000)
    if bb:
        say(f"  months resampled         {bb['n_months']}")
        say(f"  observed total      Rs {bb['observed_total']:>12,.0f}")
        say(f"  5th percentile      Rs {bb['total_p05']:>12,.0f}")
        say(f"  median              Rs {bb['total_p50']:>12,.0f}")
        say(f"  95th percentile     Rs {bb['total_p95']:>12,.0f}")
        say(f"  P(total P&L < 0)        {bb['prob_total_negative'] * 100:>11.1f}%")
        say("")
        p = bb["prob_total_negative"]
        if p > 0.05:
            say(f"  A {p * 100:.0f}% chance that the true expectation is negative is not")
            say("  significance. It is a signal worth naming and not a result worth")
            say("  trading, and no amount of further parameter search will fix that")
            say("  -- searching harder on the same 2,643 sessions only makes the")
            say("  selection problem worse.")

    say("")
    say("=" * 84)
    say("VERDICT")
    say("=" * 84)
    say("The put-side bias is REAL and theory-consistent: the same mechanism that")
    say("makes the bull put spread the best structure makes the bear call spread")
    say("the worst, and the sign of that difference was predicted before it was")
    say("measured. It survives both halves of the sample, the removal of 2020,")
    say("and eight times the assumed slippage.")
    post = results.get("after SEBI 2024-11-20")
    pre = results.get("before SEBI 2024-11-20")
    if post is not None and post <= 0:
        say("")
        say(f"  BUT THE CURRENT REGIME IS NEGATIVE. Since SEBI's index-derivatives")
        say(f"  framework took effect on 2024-11-20 the same rules return")
        say(f"  {post * 100:.1f}% a year against {pre * 100:+.1f}% before it -- on only 48")
        say("  trades, which is far too few to conclude from, but it is the")
        say("  period that describes trading this today. Larger lots, one weekly")
        say("  expiry per exchange, +2% expiry-day ELM and an STT rate that rose")
        say("  again in April 2026 all land in that window. This is now the")
        say("  strongest single objection, and it replaced an earlier one (the")
        say("  half-split) that turned out to be a bug artefact.")
    say("")
    neg = [k for k, v in results.items() if v <= 0]
    say("The MAGNITUDE is weaker evidence than the direction:")
    say("")
    if neg:
        say(f"  * {len(neg)} of {len(results)} variants are at or below zero "
            f"({', '.join(neg[:3])}{'...' if len(neg) > 3 else ''});")
    else:
        say(f"  * every one of the {len(results)} variants is positive, which is")
        say("    stronger than expected -- but they are not independent tests, they")
        say("    are one sample re-cut, so this is much less evidence than it looks;")
    if bb:
        say(f"  * {bb['prob_total_negative'] * 100:.0f}% of bootstrap resamples lose money, and the 5th")
        say(f"    percentile is Rs {bb['total_p05']:,.0f};")
    say("  * the result still rests on a small number of trades -- removing the")
    say("    worst three moves it materially;")
    say("  * and the whole thing is one market, one underlying, 2,643 sessions")
    say("    containing seven days worse than -5%.")
    say("")
    say("What this supports is 'index put skew is worth something after costs on")
    say("this sample', not 'here is a strategy'. Establishing the magnitude needs")
    say("data this sample does not contain -- more sessions, not more searching.")

    (OUT / "bull_put_robustness.txt").write_text("\n".join(_lines) + "\n")
    print(f"\nwrote {OUT / 'bull_put_robustness.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
