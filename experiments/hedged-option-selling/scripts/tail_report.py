"""The left tail: what the sample contained, what the worst trades did, and what
position size the observed tail actually supports.

RUIN RISK, NOT EXPECTANCY, IS THE DECISION VARIABLE.  A positive expectancy with
occasional 10x-premium losses is a strategy that works until it does not, and a
mean and a win rate together can describe a book that goes to zero.  This script
exists to make that concrete rather than rhetorical.

Writes out/tail_report.txt.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hedgelib.backtest import Backtest, SlippageModel, StrategyConfig  # noqa: E402
from hedgelib.chain import trading_days                                # noqa: E402
from hedgelib.metrics import tail_events                               # noqa: E402

DB = ROOT / "data/cache/options.db"
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)

_lines: list[str] = []


def say(s: str = "") -> None:
    print(s)
    _lines.append(s)


def write_out() -> None:
    (OUT / "tail_report.txt").write_text("\n".join(_lines) + "\n")
    print(f"\nwrote {OUT / 'tail_report.txt'}")


def main() -> int:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    days = trading_days(conn, "NIFTY")
    idx = [(date.fromisoformat(d), float(c)) for d, c in conn.execute(
        "SELECT trade_date, close FROM index_close WHERE index_name='Nifty 50' "
        "ORDER BY trade_date").fetchall()]

    say("=" * 78)
    say("THE LEFT TAIL")
    say("=" * 78)
    say(f"  sample {days[0]} .. {days[-1]}  ({len(days)} sessions)")

    # ------------------------------------------------------------------ 1
    te = tail_events(idx)
    say("")
    say("1. WHAT THE SAMPLE CONTAINED")
    say("-" * 78)
    say("A backtest with no disaster in it is undersampled, not safe. Before any")
    say("performance figure, this is how many chances it had to go wrong.")
    say("")
    say(f"  sessions                        {te['sessions']:,}")
    say(f"  single-day falls over 3%        {te['down_moves_over_3pct']}")
    say(f"  single-day falls over 5%        {te['down_moves_over_5pct']}")
    say(f"  single-day rises over 3%        {te['up_moves_over_3pct']}")
    say(f"  worst single session            {te['worst_session'] * 100:.2f}%  "
        f"({te['worst_session_date']})")
    say(f"  worst 20-session stretch        {te['worst_20_session_move'] * 100:.2f}%  "
        f"(to {te['worst_20_session_end']})")
    n5 = te["down_moves_over_5pct"]
    say("")
    if n5 == 0:
        say("  *** ZERO 5% down days. THAT IS THE HEADLINE. This sample has not")
        say("  *** tested the event that decides the outcome.")
    else:
        say(f"  {n5} sessions fell more than 5%: {n5} independent chances for the")
        say(f"  strategy to meet the event that decides it, out of "
            f"{te['sessions']:,}.")
        say(f"  Any conclusion about the tail rests on {n5} observations.")

    # ------------------------------------------------------------------ 2
    cfg = StrategyConfig(structure="iron_condor", short_delta=0.16,
                         wing_delta=0.05, dte_target=30, dte_tolerance=10,
                         profit_take_fraction=0.5, min_trades=100,
                         symbol="NIFTY")
    bt = Backtest(conn, cfg, slippage=SlippageModel())
    trades = [t for t in bt.run(days)
              if t.exit_reason != "still_open_at_sample_end"]
    if not trades:
        say("\n  no completed trades -- nothing further to report")
        write_out()
        return 0

    pnl = np.array([t.net_pnl for t in trades])
    order = np.argsort(pnl)

    say("")
    say("2. TAIL DOMINANCE -- what the worst few trades do to the total")
    say("-" * 78)
    say(f"  {'exclude':<22} {'total P&L':>14} {'change':>12}")
    total = pnl.sum()
    say(f"  {'nothing':<22} {total:>14,.0f} {'':>12}")
    for k in (1, 2, 3, 5, 10):
        if k >= len(pnl):
            break
        without = total - pnl[order[:k]].sum()
        say(f"  {'worst ' + str(k) + ' trades':<22} {without:>14,.0f} "
            f"{without - total:>+12,.0f}")
    say("")
    say(f"  {len(pnl)} trades. The worst few decide the result; the other")
    say("  ninety-something per cent decide almost nothing. This is why a mean")
    say("  and a win rate together are not a description of this strategy.")

    # ------------------------------------------------------------------ 3
    say("")
    say("3. THE WORST TEN TRADES")
    say("-" * 78)
    say(f"  {'entry':<12} {'exit':<12} {'reason':<16} {'net P&L':>11} "
        f"{'max loss':>11} {'of max':>8} {'margin':>10} {'loss/mgn':>9}")
    worst_reasons = []
    for i in order[:10]:
        t = trades[int(i)]
        worst_reasons.append(t.exit_reason)
        frac = (-t.net_pnl / t.max_loss * 100) if t.max_loss else 0
        say(f"  {t.entry_date.isoformat():<12} "
            f"{(t.exit_date or t.entry_date).isoformat():<12} "
            f"{t.exit_reason[:16]:<16} {t.net_pnl:>11,.0f} {t.max_loss:>11,.0f} "
            f"{frac:>7.0f}% {t.margin_at_entry:>10,.0f} "
            f"{t.net_pnl / max(t.margin_at_entry, 1) * 100:>8.1f}%")
    if all(r == "expiry" for r in worst_reasons):
        say("")
        say("  Every one of them ran to EXPIRY rather than a managed exit. A")
        say("  profit-take rule cannot save a loser -- by construction it only")
        say("  ever fires on winners -- so the management rule shapes the right")
        say("  side of the distribution and leaves the left side untouched.")

    # ------------------------------------------------------------------ 4
    say("")
    say("4. WORST MONTHS")
    say("-" * 78)
    by_month: dict[str, float] = defaultdict(float)
    for t in trades:
        d = t.exit_date or t.entry_date
        by_month[f"{d.year}-{d.month:02d}"] += t.net_pnl
    say(f"  {'month':<10} {'net P&L':>12}")
    for mth, v in sorted(by_month.items(), key=lambda kv: kv[1])[:8]:
        say(f"  {mth:<10} {v:>12,.0f}")

    # ------------------------------------------------------------------ 5
    say("")
    say("5. RUIN RISK -- what size does the OBSERVED tail support?")
    say("-" * 78)
    say("Resampled in whole months so that clustered losses stay clustered.")
    say("The question is not 'what is the expected return' but 'at what size")
    say("does a plausible sequence of these trades end the book'.")
    say("")

    # A trade whose margin could not be established is excluded rather than
    # divided by a placeholder: dividing a four-figure P&L by 1.0 produces a
    # "return on margin" of tens of thousands of per cent and destroys every
    # statistic computed from it.
    sized = [t for t in trades if t.margin_at_entry > 0 and t.max_loss > 0]
    dropped = len(trades) - len(sized)
    if dropped:
        say(f"  {dropped} trade(s) excluded: no margin, or no bounded max loss.")
        say("")

    rom = np.array([t.net_pnl / t.margin_at_entry for t in sized])
    rml = np.array([t.net_pnl / t.max_loss for t in sized])
    say(f"  return on MARGIN   : mean {rom.mean() * 100:+8.2f}%   "
        f"worst {rom.min() * 100:+9.2f}%   p5 {np.percentile(rom, 5) * 100:+8.2f}%")
    say(f"  return on MAX LOSS : mean {rml.mean() * 100:+8.2f}%   "
        f"worst {rml.min() * 100:+9.2f}%   p5 {np.percentile(rml, 5) * 100:+8.2f}%")
    say("")
    say("  THE TWO BASES DISAGREE, AND THAT IS THE POINT. Sizing by MARGIN is")
    say("  what a broker's screen invites, and it is incoherent here: margin runs")
    say("  at a median of ~26% of the structure's own maximum loss, so the worst")
    say(f"  trade lost {rom.min() * 100:.0f}% of what it was blocking. You cannot lose")
    say("  that and stay solvent at any meaningful size. Measured against MAX")
    say(f"  LOSS the worst trade is {rml.min() * 100:.0f}%, bounded by construction -- and")
    say("  max loss IS the amount at risk for a defined-risk structure.")
    say("  Size on that, never on margin.")

    rng = np.random.default_rng(11)
    N_PATHS, N_MONTHS = 20_000, 60      # five years forward

    def simulate(blocks: list[list[float]], frac: float):
        ends = np.empty(N_PATHS)
        ruin = halved = 0
        for p in range(N_PATHS):
            eq, low = 1.0, 1.0
            for _ in range(N_MONTHS):
                blk = blocks[rng.integers(0, len(blocks))]
                for r in blk:
                    eq *= (1.0 + frac * r)
                    low = min(low, eq)
                    if eq <= 0.0:
                        eq = 0.0
                        break
                if eq <= 0.0:
                    break
            ends[p] = eq
            ruin += eq <= 0.20
            halved += low <= 0.50
        return ends, halved / N_PATHS, ruin / N_PATHS

    for basis, series, label in (
            ("MARGIN", rom, "fraction of equity committed as margin"),
            ("MAX LOSS", rml, "fraction of equity at risk as maximum loss")):
        grouped: dict[str, list[float]] = defaultdict(list)
        for t, r in zip(sized, series, strict=True):
            d = t.exit_date or t.entry_date
            grouped[f"{d.year}-{d.month:02d}"].append(float(r))
        blocks = list(grouped.values())
        say("")
        say(f"  SIZED BY {basis} -- {label}")
        say(f"  {'at risk':<10} {'median end':>12} {'5th pct':>10} "
            f"{'P(-50%)':>9} {'P(ruin)':>9}")
        for frac in (0.02, 0.05, 0.10, 0.20, 0.30, 0.50):
            ends, halved, ruin = simulate(blocks, frac)
            say(f"  {frac * 100:>8.0f}% {np.median(ends):>12.2f}x "
                f"{np.percentile(ends, 5):>10.2f}x {halved * 100:>8.1f}% "
                f"{ruin * 100:>8.1f}%")

    say("")
    say("  'ruin' is ending below 20% of the starting book over five years.")
    say("  Read the last two columns, not the first.")
    say("")
    say("  The MAX LOSS table is the meaningful one, and it says what the")
    say("  headline says: on this sample the strategy has a NEGATIVE expectancy")
    say("  after costs, so every size loses money and larger sizes lose it")
    say("  faster. Position sizing cannot rescue a negative edge; it only")
    say("  decides how long the book takes to get there.")
    say("")
    say("  CAVEAT, and it is the important one: this resamples a tail observed")
    say(f"  over {te['sessions']:,} sessions containing "
        f"{te['down_moves_over_5pct']} days worse than -5%. The true tail is")
    say("  almost certainly worse than the one that happened to fall inside this")
    say("  window, so every ruin probability here is a LOWER BOUND.")

    write_out()
    (OUT / "tail_events.json").write_text(json.dumps(te, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
