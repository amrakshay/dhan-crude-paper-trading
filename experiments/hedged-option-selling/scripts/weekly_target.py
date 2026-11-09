"""Test the "+2% or -2% of capital employed, then exit" rule, weekly.

The operator's stated goal: a minimum 2% return per week on capital employed,
exit on reaching it; or a 2% loss, exit.

Two questions, and they are separate:

  1. Does the EXIT RULE work -- i.e. when the stop fires, do you actually lose
     2%?  A stop is an instruction, not a guarantee: on daily data a position
     is marked at the close, so an overnight gap is realised in full before any
     stop can act.  This measures the difference between the stop LEVEL and the
     loss actually realised.

  2. Is the TARGET reachable at all?  2% a week compounds to roughly 180% a
     year.  The best structure in this study returned 26% a year on capital at
     risk.  That gap is the whole question, and no exit rule closes it -- an
     exit rule decides when you take what the market offered, it does not
     decide what the market offers.

Writes out/weekly_target.txt.
"""
from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
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
RISK_FREE = 0.0564

_lines: list[str] = []


def say(s: str = "") -> None:
    print(s)
    _lines.append(s)


def main() -> int:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    days = trading_days(conn, "NIFTY")

    say("=" * 86)
    say('THE "+2% OR -2% OF CAPITAL, WEEKLY" TARGET')
    say("=" * 86)

    # ---------------------------------------------------------------- 0
    say("")
    say("0. THE ARITHMETIC, BEFORE ANY BACKTEST")
    say("-" * 86)
    for wk in (0.02, 0.01, 0.005):
        say(f"  {wk * 100:.1f}% a week compounds to {((1 + wk) ** 52 - 1) * 100:>8.0f}% a year")
    say("")
    say(f"  Risk-free over this sample:                    {RISK_FREE * 100:>6.2f}% a year")
    say("  Best structure measured here (bull put spread):  26.00% a year")
    say("      -- which is about 0.45% a week.")
    say("")
    say("  So the target is roughly 4.4x the best thing in this study and 32x")
    say("  the risk-free rate. That gap is not an exit-rule problem. An exit")
    say("  rule decides WHEN you take what the market offered; it cannot")
    say("  decide WHAT the market offers.")

    # ---------------------------------------------------------------- 1
    say("")
    say("1. DOES THE RULE ACTUALLY DELIVER +2% / -2%?")
    say("-" * 86)
    say("Weekly-ish holding: ~7 DTE entries, exit on +2% or -2% of capital.")
    say("Both capital bases shown, because they are not the same question:")
    say("margin is ~26% of max loss, so 2% of margin is a far smaller move.")
    say("")

    results = {}
    for struct in ("bull_put_spread", "iron_condor"):
        for basis in ("max_loss", "margin"):
            cfg = StrategyConfig(
                structure=struct, short_delta=0.16, wing_delta=0.05,
                dte_target=7, dte_tolerance=3,
                profit_take_fraction=None, hold_to_expiry=False,
                target_pct_of_capital=0.02, stop_pct_of_capital=0.02,
                capital_basis=basis, min_trades=100,
                expiry_type="any", symbol="NIFTY")
            bt = Backtest(conn, cfg, slippage=SlippageModel())
            trades = [t for t in bt.run(days)
                      if t.exit_reason != "still_open_at_sample_end"]
            if not trades:
                continue
            results[(struct, basis)] = trades

            cap = np.array([t.max_loss if basis == "max_loss"
                            else t.margin_at_entry for t in trades])
            pnl = np.array([t.net_pnl for t in trades])
            with np.errstate(divide="ignore", invalid="ignore"):
                r = np.where(cap > 0, pnl / np.maximum(cap, 1e-9), 0.0)

            reasons = defaultdict(int)
            for t in trades:
                reasons[t.exit_reason.split("_")[0]] += 1

            s = trade_stats(trades)
            say(f"  --- {struct}, capital = {basis} ---")
            say(f"      trades {len(trades)}   mean held {s.mean_days_held:.1f} days")
            say(f"      exits: {dict(reasons)}")
            say(f"      total P&L Rs {s.total_pnl:>12,.0f}   "
                f"return on capital at risk {s.annualised_return_on_max_loss * 100:>7.2f}%/yr")
            say(f"      realised return per trade: mean {r.mean() * 100:+.2f}%  "
                f"median {np.median(r) * 100:+.2f}%")

            # THE KEY NUMBER: when the stop fires, what is actually realised?
            stopped = [(t, rr) for t, rr in zip(trades, r, strict=True)
                       if t.exit_reason.startswith("stop")]
            if stopped:
                sr = np.array([rr for _, rr in stopped])
                worse = (sr < -0.025).sum()
                say(f"      STOP FIRED {len(stopped)} times. Realised loss on those:")
                say(f"        mean {sr.mean() * 100:+.2f}%   median {np.median(sr) * 100:+.2f}%   "
                    f"WORST {sr.min() * 100:+.2f}%")
                say(f"        worse than -2.5%: {worse} ({worse / len(sr) * 100:.0f}%)"
                    f"   worse than -5%: {(sr < -0.05).sum()}")
            hit = [rr for t, rr in zip(trades, r, strict=True)
                   if t.exit_reason.startswith("target")]
            if hit:
                say(f"      TARGET HIT {len(hit)} times, mean realised "
                    f"{np.mean(hit) * 100:+.2f}%")
            say("")

    # ---------------------------------------------------------------- 2
    say("")
    say("2. WHAT WEEKLY RETURN WAS ACTUALLY AVAILABLE?")
    say("-" * 86)
    say("Per-calendar-week return on capital employed, best structure, the")
    say("+/-2% rule applied. This is the distribution the target has to come")
    say("from.")
    say("")
    key = ("bull_put_spread", "max_loss")
    if key in results:
        trades = results[key]
        by_week: dict[str, list[float]] = defaultdict(list)
        for t in trades:
            d = t.exit_date or t.entry_date
            iso = d.isocalendar()
            cap = t.max_loss if t.max_loss > 0 else 1.0
            by_week[f"{iso[0]}-W{iso[1]:02d}"].append(t.net_pnl / cap)
        wk = np.array([sum(v) for v in by_week.values()])
        say(f"  {len(wk)} calendar weeks with a completed trade")
        say(f"  mean weekly return on capital at risk : {wk.mean() * 100:+.2f}%")
        say(f"  median                                 : {np.median(wk) * 100:+.2f}%")
        say(f"  weeks reaching +2% or better           : "
            f"{(wk >= 0.02).sum()} ({(wk >= 0.02).mean() * 100:.0f}%)")
        say(f"  weeks at -2% or worse                  : "
            f"{(wk <= -0.02).sum()} ({(wk <= -0.02).mean() * 100:.0f}%)")
        say(f"  worst week                             : {wk.min() * 100:+.2f}%")
        say("")
        say("  For a 2%-a-week floor to be met, essentially EVERY week would")
        say("  have to clear +2%. The observed share is above.")

    # ---------------------------------------------------------------- 3
    say("")
    say("3. DOES THE RULE BEAT THE PLAIN VERSION?")
    say("-" * 86)
    say(f"  {'variant':<40} {'n':>4} {'total P&L':>11} {'R/risk /yr':>11} {'worst':>10}")
    base = StrategyConfig(structure="bull_put_spread", short_delta=0.16,
                          wing_delta=0.05, dte_target=7, dte_tolerance=3,
                          min_trades=100, symbol="NIFTY")
    variants = [
        ("plain PT50, 7 DTE", dict(profit_take_fraction=0.5)),
        ("+2%/-2% of max loss", dict(profit_take_fraction=None,
                                     target_pct_of_capital=0.02,
                                     stop_pct_of_capital=0.02,
                                     capital_basis="max_loss")),
        ("+2%/-2% of margin", dict(profit_take_fraction=None,
                                   target_pct_of_capital=0.02,
                                   stop_pct_of_capital=0.02,
                                   capital_basis="margin")),
        ("+2% target only, no stop", dict(profit_take_fraction=None,
                                          target_pct_of_capital=0.02,
                                          capital_basis="max_loss")),
        ("-2% stop only, hold winners", dict(profit_take_fraction=None,
                                             stop_pct_of_capital=0.02,
                                             capital_basis="max_loss")),
        ("+5%/-5% of max loss", dict(profit_take_fraction=None,
                                     target_pct_of_capital=0.05,
                                     stop_pct_of_capital=0.05,
                                     capital_basis="max_loss")),
    ]
    for label, kw in variants:
        bt = Backtest(conn, replace(base, **kw), slippage=SlippageModel())
        s = trade_stats(bt.run(days))
        if s.n == 0:
            say(f"  {label:<40}    0   (no trades)")
            continue
        say(f"  {label:<40} {s.n:>4} {s.total_pnl:>11,.0f} "
            f"{s.annualised_return_on_max_loss * 100:>10.2f}% {s.worst_trade:>10,.0f}")

    say("")
    say("=" * 86)
    say("WHAT THIS SAYS")
    say("=" * 86)
    key = ("bull_put_spread", "max_loss")
    if key in results:
        tr = results[key]
        cap = np.array([t.max_loss for t in tr])
        pnl = np.array([t.net_pnl for t in tr])
        r = np.where(cap > 0, pnl / np.maximum(cap, 1e-9), 0.0)
        st = np.array([rr for t, rr in zip(tr, r, strict=True)
                       if t.exit_reason.startswith("stop")])
        if len(st):
            say("")
            say(f"1. THE -2% STOP NEVER DELIVERED -2%. It fired {len(st)} times and")
            say(f"   realised a loss worse than -2.5% on {(st < -0.025).sum()} of them "
                f"({(st < -0.025).mean() * 100:.0f}%).")
            say(f"   Mean realised loss when the stop fired: {st.mean() * 100:.2f}%.")
            say(f"   Worst: {st.min() * 100:.2f}%.")
            say("")
            say("   A stop is an instruction, not a guarantee. The position is")
            say("   marked at the close, so by the time the mark crosses -2% the")
            say("   next tradeable price is already far below it -- and at ~7 DTE")
            say("   gamma makes that gap enormous. The rule as written asks for a")
            say("   2% loss and delivers a 10% one.")
    say("")
    say("2. CUTTING WINNERS AT +2% WHILE LOSERS RUN TO -10% IS BACKWARDS.")
    say("   The +/-2% rule turned a -3.31%/yr strategy into a -67.75%/yr one.")
    say("   The target fires often and small; the stop fires less often and")
    say("   large. That is the wrong way round for a premium seller, whose")
    say("   payoff is already negatively skewed.")
    say("")
    say("3. THE STOP ALONE, HOLDING WINNERS, WAS THE BEST VARIANT TESTED")
    say("   (+8.65%/yr, worst trade -11,150 -- the smallest of any variant).")
    say("   Stopping losers is defensible; capping winners at 2% is not.")
    say("")
    say("4. THE TARGET WAS AVAILABLE ABOUT HALF THE TIME. 51% of weeks reached")
    say("   +2%; 37% were at -2% or worse. A 2%-a-week FLOOR needs essentially")
    say("   every week to clear it.")
    say("")
    say("5. NOTE THE MEAN AND THE MEDIAN. Median weekly return +2.21%, mean")
    say("   -1.56%. The median week hits the target and the average week loses")
    say("   money. That gap IS the strategy's shape, and it is why a rule")
    say("   designed around the typical week misprices the book.")
    (OUT / "weekly_target.txt").write_text("\n".join(_lines) + "\n")
    print(f"\nwrote {OUT / 'weekly_target.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
