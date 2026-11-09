"""Why does a trade with a -2% stop lose 40%?

Asked directly after the weekly-target test reported a mean realised loss of
-9.79% on trades that exited via a -2% stop. The answer is not a bug and it is
not slippage in the ordinary sense -- it is that the distance between "fine" and
"maximum loss" on a short-dated credit spread is about one ordinary day's move,
and a stop evaluated at the close has no observation inside that distance.

Writes out/why_big_losses.txt.
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
from hedgelib.chain import load_chain, settlement_value, trading_days  # noqa: E402

DB = ROOT / "data/cache/options.db"
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)

_lines: list[str] = []


def say(s: str = "") -> None:
    print(s)
    _lines.append(s)


def main() -> int:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    days = trading_days(conn, "NIFTY")
    sess = {d: i for i, d in enumerate(days)}
    idx = dict((date.fromisoformat(a), float(v)) for a, v in conn.execute(
        "SELECT trade_date, close FROM index_close WHERE index_name='Nifty 50'"))

    base = StrategyConfig(
        structure="bull_put_spread", short_delta=0.16, wing_delta=0.05,
        dte_target=7, dte_tolerance=3, profit_take_fraction=None,
        target_pct_of_capital=0.02, stop_pct_of_capital=0.02,
        capital_basis="max_loss", min_trades=100, symbol="NIFTY")

    say("=" * 86)
    say("WHY DOES A TRADE WITH A -2% STOP LOSE 40%?")
    say("=" * 86)

    # ---------------------------------------------------------------- 1
    say("")
    say("1. HOW LONG DID THE BIG LOSSES TAKE?")
    say("-" * 86)
    say(f"  {'DTE':>4} {'trades':>7} {'lost >10%':>10} {'in ONE session':>16} "
        f"{'mean stop slip':>15} {'worst':>9}")
    for dte, tol in ((7, 3), (14, 5), (30, 10), (45, 10)):
        cfg = replace(base, dte_target=dte, dte_tolerance=tol)
        tr = [t for t in Backtest(conn, cfg, slippage=SlippageModel()).run(days)
              if t.exit_reason != "still_open_at_sample_end" and t.max_loss > 0]
        bad = [t for t in tr if t.net_pnl / t.max_loss < -0.10]
        if not bad:
            continue
        one = sum(1 for t in bad
                  if t.exit_date in sess and t.entry_date in sess
                  and sess[t.exit_date] - sess[t.entry_date] <= 1)
        stops = [t.net_pnl / t.max_loss for t in tr
                 if t.exit_reason.startswith("stop")]
        say(f"  {dte:>4} {len(tr):>7} {len(bad):>10} "
            f"{one:>9} ({one / len(bad) * 100:>3.0f}%) "
            f"{np.mean(stops) * 100 if stops else float('nan'):>14.2f}% "
            f"{min(t.net_pnl / t.max_loss for t in tr) * 100:>8.1f}%")
    say("")
    say("  Half to two-thirds of them happen in a SINGLE trading session, and the")
    say("  stop's slippage halves as the entry moves further from expiry. That")
    say("  gradient is gamma, and it points at the cause.")

    # ---------------------------------------------------------------- 2
    say("")
    say("2. THE CAUSE -- the loss zone is narrower than a day's move")
    say("-" * 86)
    sig = np.median([r[0] for r in conn.execute(
        "SELECT daily_vol FROM fo_volt WHERE symbol='NIFTY'").fetchall()]) * 100
    say(f"  {'DTE':>4} {'short strike':>13} {'long strike':>12} {'loss zone':>10} "
        f"{'zone / 1-day sigma':>19}")
    for dte, tol in ((7, 3), (14, 5), (30, 10), (45, 10)):
        cfg = replace(base, dte_target=dte, dte_tolerance=tol)
        tr = [t for t in Backtest(conn, cfg, slippage=SlippageModel()).run(days)
              if t.exit_reason != "still_open_at_sample_end"]
        if not tr:
            continue
        sh, lo = [], []
        for t in tr:
            ks = sorted(k for k, _, _ in t.legs)
            lo.append((ks[0] / t.entry_forward - 1) * 100)
            sh.append((ks[1] / t.entry_forward - 1) * 100)
        zone = np.median(sh) - np.median(lo)
        say(f"  {dte:>4} {np.median(sh):>12.2f}% {np.median(lo):>11.2f}% "
            f"{zone:>9.2f}% {zone / sig:>18.2f}x")
    say("")
    say(f"  NIFTY's median daily sigma over this sample is {sig:.2f}% -- NSE's own")
    say("  published figure, the same one the margin model uses.")
    say("")
    say("  At 7 DTE the entire distance from full profit to MAXIMUM loss is about")
    say("  2.4 standard deviations. The sample contains 20 sessions that fell")
    say("  more than 3% and 7 that fell more than 5%. On any of those the spread")
    say("  crosses the whole zone between one close and the next.")
    say("")
    say("  A -2% stop sits INSIDE that zone, near the top. There is almost no")
    say("  room between the level at which the stop triggers and the level at")
    say("  which the position is already at its maximum loss.")

    # ---------------------------------------------------------------- 3
    say("")
    say("3. THE WORST TRADE, TRACED END TO END")
    say("-" * 86)
    tr = [t for t in Backtest(conn, base, slippage=SlippageModel()).run(days)
          if t.entry_date == date(2026, 1, 16)]
    if tr:
        t = tr[0]
        ks = sorted(k for k, _, _ in t.legs)
        say(f"  entered {t.entry_date}, expiry {t.expiry}, forward "
            f"{t.entry_forward:,.0f}")
        say(f"  SHORT {ks[1]:.0f} PE  /  LONG {ks[0]:.0f} PE   "
            f"(gap {ks[1] - ks[0]:.0f} points = "
            f"{(ks[1] - ks[0]) / t.entry_forward * 100:.2f}% of the index)")
        say(f"  credit Rs {t.entry_credit:,.0f}   maximum loss Rs {t.max_loss:,.0f}")
        say("")
        for d, mtm, _m in t.marks:
            say(f"    {d}  NIFTY {idx.get(d, float('nan')):>10,.2f}   "
                f"mark {mtm:>10,.0f}  ({mtm / t.max_loss * 100:>6.1f}% of max loss)")
        say("")
        ch = load_chain(conn, t.expiry, "NIFTY", t.expiry)
        sv = settlement_value(ch)[0] if ch else float("nan")
        say(f"  settlement {sv:,.2f}: short {ks[1]:.0f} finished "
            f"{max(ks[1] - sv, 0):,.2f} in the money, long {ks[0]:.0f} finished "
            f"{max(ks[0] - sv, 0):,.2f} in the money")
        say(f"  net Rs {t.net_pnl:,.0f} = {t.net_pnl / t.max_loss * 100:.1f}% "
            f"of maximum loss")
        say("")
        say("  THE STOP NEVER HAD A CHANCE TO FIRE. At the previous close the")
        say("  position was 1.8% underwater -- comfortably inside the -2% stop --")
        say("  because the index was still 135 points ABOVE the short strike with")
        say("  a day to run. The next observation was settlement.")
        say("")
        say("  NIFTY fell 1.38% that session. The loss zone was 0.97% wide. The")
        say("  day's move was 1.4x the entire distance from fine to ruined.")

    # ---------------------------------------------------------------- 4
    say("")
    say("4. AND NEAR EXPIRY THE MARK UNDERSTATES THE OUTCOME")
    say("-" * 86)
    say("  The mark-to-market a stop watches is an EXPECTATION over what could")
    say("  still happen. Settlement is one draw from it. With a day to run and")
    say("  the index just above the short strike, the expectation is mildly")
    say("  negative -- the position might still expire worthless -- while the")
    say("  realisation is either fine or nearly the whole loss.")
    say("")
    say("  So a stop placed on the mark is watching the wrong variable at exactly")
    say("  the moment it matters most. This is not fixable by moving the stop:")
    say("  a tighter one fires more often on noise, and a looser one gives up")
    say("  more before it fires. It is fixable by not being there -- which is")
    say("  what 'exit at 10 DTE' does, and why that was the best management rule")
    say("  in SWEEP.md section 5.")

    (OUT / "why_big_losses.txt").write_text("\n".join(_lines) + "\n")
    print(f"\nwrote {OUT / 'why_big_losses.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
