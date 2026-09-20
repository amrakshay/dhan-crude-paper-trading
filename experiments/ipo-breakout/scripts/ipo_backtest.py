"""Cash-accounted backtest of the IPO breakout rule set, and its dashboard.

WHAT IS REUSED AND WHAT IS NOT. The dashboard's HTML/JS (`TEMPLATE`) and the
summary arithmetic (`summarise`) are IMPORTED from
`pattern-detection/scripts/vcp_backtest.py`, not copied: the KPI tiles, the
equity and drawdown charts, the positions-held plot, the monthly heatmap and
the filterable ledger are already right, and a forked copy would drift the
moment either was edited. The subtitle is still generated from the flags so it
cannot go stale. Headings and caveats are rewritten here for this strategy.

`simulate` IS reimplemented, because the original reads prices from the
pattern-detection database and this experiment has its own.

SIZING is fixed notional per position, so the number of positions the book can
hold is whatever the cash supports -- an OUTPUT, not a setting, which is why
the dashboard plots it. That matters more here than it did for the VCP,
because an IPO breakout strategy can only trade when IPOs are listing: 2020 has
almost no signals and 2024 has many. A fixed slot count would have idled
through the first and rationed the second.

EXPOSURE, NOT POSITION SIZE, IS THE COMPARISON. `scripts/exit_grid.py` shows
"stop only" earning far more per trade than any trail -- while holding for 142
sessions instead of 17. Those are not the same bet. Only a cash-accounted book
with a finite number of slots can price the difference, and it is the reason
this file exists rather than a table of per-trade expectancies.

    python3 scripts/ipo_backtest.py
    python3 scripts/ipo_backtest.py --exit "chandelier 4.5" --max-hold 80

Writes out/ipo_dashboard.html and out/ipo_backtest.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np

import _bootstrap  # noqa: F401

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE.parent / "pattern-detection" / "scripts"))

from vcp_backtest import TEMPLATE, summarise  # noqa: E402

from ipolib import listings, trades as trades_mod  # noqa: E402

OUT = HERE / "out"


def simulate(trades, price_of, capital=1_000_000.0, per_trade=100_000.0,
             cost=0.003, compound=False, slots=10):
    """Walk the calendar, exiting before entering, marking to market daily.

    Exits are processed first so the cash they release is available the same
    day -- otherwise the book holds fewer positions than the rules allow and
    every return is understated.
    """
    by_entry = defaultdict(list)
    for trade in trades:
        by_entry[trade.entry_date].append(trade)

    symbols = sorted({t.symbol for t in trades})
    prices = {s: price_of(s) for s in symbols}
    all_dates = sorted({d for s in symbols for d in prices[s]})
    first = min(t.entry_date for t in trades)
    all_dates = [d for d in all_dates if d >= first]

    cash = capital
    open_positions: list[dict] = []
    ledger: list[dict] = []
    series: list[dict] = []
    skipped = 0

    for day in all_dates:
        still_open = []
        for position in open_positions:
            if position["exit_date"] <= day:
                proceeds = position["qty"] * position["exit"]
                fee = (position["invested"] + proceeds) * (cost / 2.0)
                net = proceeds - position["invested"] - fee
                cash += proceeds - fee
                ledger.append(dict(
                    symbol=position["symbol"], entry_date=position["entry_date"],
                    exit_date=position["exit_date"], days=position["bars"],
                    reason=position["reason"], net=round(net, 2),
                    entry=round(position["entry"], 2),
                    exit=round(position["exit"], 2), qty=position["qty"],
                    invested=int(round(position["invested"])),
                    ret_pct=round(100.0 * net / position["invested"], 2)))
            else:
                still_open.append(position)
        open_positions = still_open

        equity_now = cash + sum(
            p["qty"] * prices[p["symbol"]].get(day, p["entry"])
            for p in open_positions)
        stake = (equity_now / slots) if compound else per_trade

        for trade in by_entry.get(day, []):
            if trade.entry <= 0:
                continue
            quantity = int(stake // trade.entry)
            invested = quantity * trade.entry
            fee = invested * (cost / 2.0)
            if quantity < 1 or invested + fee > cash:
                skipped += 1
                continue
            cash -= invested + fee
            open_positions.append(dict(
                symbol=trade.symbol, entry_date=trade.entry_date,
                exit_date=trade.exit_date, entry=trade.entry, exit=trade.exit,
                qty=quantity, invested=invested, bars=trade.bars,
                reason=trade.reason))

        invested_now = sum(p["qty"] * prices[p["symbol"]].get(day, p["entry"])
                           for p in open_positions)
        equity = cash + invested_now
        peak = max([s["peak"] for s in series[-1:]] + [equity, capital])
        series.append(dict(d=day, e=equity, n=len(open_positions),
                           inv=invested_now, peak=peak,
                           dd=100.0 * (equity / peak - 1.0)))

    return series, ledger, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capital", type=float, default=1_000_000)
    parser.add_argument("--per-trade", type=float, default=100_000)
    parser.add_argument("--cost", type=float, default=0.003)
    parser.add_argument("--setup", default="base")
    parser.add_argument("--profile", default="strict")
    parser.add_argument("--exit", default="stop only",
                        choices=sorted(trades_mod.EXIT_RULES))
    parser.add_argument("--max-hold", type=int, default=80)
    parser.add_argument("--min-score", type=float, default=0.0)
    parser.add_argument("--skip-before-unlock", action="store_true",
                        help="refuse a signal landing in the 10 sessions before "
                             "an anchor unlock (see LOCKINS.md)")
    parser.add_argument("--compound", action="store_true")
    parser.add_argument("--slots", type=int, default=10)
    parser.add_argument("--out", default="ipo_dashboard.html")
    args = parser.parse_args()

    signals = json.loads(
        (OUT / f"signals_{args.setup}_{args.profile}.json").read_text())
    n_signals = len(signals)

    if args.min_score > 0:
        signals = [s for s in signals if s["score"] >= args.min_score]
    if args.skip_before_unlock:
        signals = [s for s in signals
                   if not (s.get("days_to_next_unlock") is not None
                           and 0 <= s["days_to_next_unlock"] <= 14)]

    bars_cache = {symbol: listings.load_bars(symbol)
                  for symbol in sorted({s["symbol"] for s in signals})}
    rule = trades_mod.EXIT_RULES[args.exit]
    built = []
    for signal in signals:
        bars = bars_cache.get(signal["symbol"])
        if bars is None:
            continue
        trade = trades_mod.build_trade(bars, signal, rule, max_hold=args.max_hold)
        if trade is not None:
            built.append(trade)

    if not built:
        sys.exit("no trades -- run scripts/scan_real.py first")

    def price_of(symbol: str) -> dict:
        bars = bars_cache[symbol]
        return {str(d): float(c) for d, c in zip(bars.date, bars.close)}

    print(f"{len(built)} trades from {n_signals} signals; exit={args.exit!r}, "
          f"max hold {args.max_hold}; Rs {args.capital:,.0f} book, "
          f"Rs {args.per_trade:,.0f} a slot, {args.cost:.1%} round trip")

    series, ledger, skipped = simulate(
        built, price_of, args.capital, args.per_trade, args.cost,
        args.compound, args.slots)
    summary, months, yearly = summarise(series, ledger, args.capital, skipped,
                                        len(built))

    thin = [s for i, s in enumerate(series) if i % 5 == 0 or i == len(series) - 1]
    data = dict(summary=summary, series=thin, months=months, yearly=yearly,
                trades=ledger)

    html = TEMPLATE.replace("__DATA__", json.dumps(data, separators=(",", ":")))
    html = html.replace("__GENERATED__", date.today().isoformat())
    html = html.replace("Experiment · pattern-detection",
                        "Experiment · ipo-breakout")
    html = html.replace("NSE VCP — backtest ledger",
                        "NSE IPO breakout — backtest ledger")
    html = html.replace("NSE VCP — Backtest Ledger",
                        "NSE IPO Breakout — Backtest Ledger")
    html = html.replace(
        "Volatility Contraction Pattern, entered at the forming bar, "
        "trailed on the 50 EMA after breakout.",
        f"IBD IPO base ({args.setup} setup, {args.profile} profile), entered at "
        f"the open after the breakout close, exited on {args.exit!r}.")
    html = html.replace(
        "Ten at the start by construction. It rises as the book compounds and "
        "falls in drawdowns — the count is an output of the cash rule, not a "
        "setting.",
        "An output of the cash rule, not a setting — and here it is also an "
        "output of the IPO calendar: the book cannot hold ten positions in a "
        "year when nothing listed.")

    # Built from the flags rather than written out, because the two hand-edited
    # versions of the equivalent sentence in the VCP work both went stale
    # without anything failing.
    bits = [f"Fixed &#8377;{args.per_trade:,.0f} a position from a "
            f"&#8377;{args.capital:,.0f} book, so the number of open trades "
            f"floats with the cash.",
            f"Held at most {args.max_hold} sessions."]
    if args.min_score > 0:
        bits.append(f"Only bases scoring {args.min_score:g} or better.")
    if args.skip_before_unlock:
        bits.append("Signals landing within 14 days before an anchor lock-in "
                    "expiry are refused.")
    bits.append("SURVIVORSHIP: listings delisted since are absent, so every "
                "figure here is an upper bound.")
    html = html.replace("__SUBTITLE__", " ".join(bits))

    OUT.mkdir(exist_ok=True)
    path = OUT / args.out
    path.write_text(html)
    (OUT / "ipo_backtest.json").write_text(json.dumps(
        {"summary": summary, "yearly": yearly, "args": vars(args),
         "n_signals": n_signals, "n_trades": len(built)}, indent=2, default=str))

    for key in ("start", "final", "cagr", "maxdd", "mar", "trades", "win", "pf",
                "avg_hold", "max_pos", "avg_pos", "exposure", "skipped"):
        print(f"   {key:<10}{summary[key]}")
    print(f"\nwrote {path}  ({path.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
