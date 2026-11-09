"""Backtest the DIAGONAL: sell the near expiry, buy further OTM expiry+N.

The structure asked for in CALENDARS.md, now measured rather than reasoned
about. Head-to-head against the same-expiry vertical on a matched window, then
swept over the two parameters that define it, then stressed on the assumption
that decides the answer -- the bid-ask on a leg trading two orders of magnitude
less than the one it hedges.

TWO THINGS TO KEEP IN MIND WHILE READING

  * Capital at risk is the CONSERVATIVE bound: max loss assumes the far leg has
    lost all time value by the near leg's expiry, which it has not. The real
    position is never worse than this, so return on capital at risk is
    UNDERSTATED for the diagonal and fairly stated for the vertical. The
    comparison is therefore biased AGAINST the diagonal, deliberately.

  * Margin is not reported for diagonals. The model has no inter-month spread
    charge and SEBI withdrew the index calendar benefit on expiry day from
    2025-02-10 (MARGIN.md, CALENDARS.md section 5). A margin number here would
    be invented.

Writes out/diagonals.txt.
"""
from __future__ import annotations

import json
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


HDR = (f"  {'variant':<34} {'n':>4} {'total P&L':>11} {'R/risk /yr':>11} "
       f"{'worst':>10} {'cr/maxL':>8} {'win':>6}")


JSON_ROWS: dict[str, dict] = {}


def row(label: str, conn, cfg, days, slip=None, group: str = "") -> dict:
    bt = Backtest(conn, cfg, slippage=slip or SlippageModel())
    trades = bt.run(days)
    s = trade_stats(trades)
    if s.n == 0:
        say(f"  {label:<34}    0   (no trades)")
        return {}
    done = [t for t in trades if t.exit_reason != "still_open_at_sample_end"]
    cr = np.mean([t.entry_credit for t in done]) if done else 0.0
    ml = np.mean([t.max_loss for t in done]) if done else 1.0
    say(f"  {label:<34} {s.n:>4} {s.total_pnl:>11,.0f} "
        f"{s.annualised_return_on_max_loss * 100:>10.2f}% {s.worst_trade:>10,.0f} "
        f"{cr / ml * 100:>7.1f}% {s.win_rate * 100:>5.1f}%")
    if group:
        JSON_ROWS.setdefault(group, {})[label.strip()] = {
            "n": s.n, "total_pnl": s.total_pnl,
            "return_on_risk_annual": s.annualised_return_on_max_loss,
            "worst": s.worst_trade, "worst_1pct": s.worst_1pct,
            "win_rate": s.win_rate, "skew": s.skew,
            "credit_over_maxloss": cr / ml if ml else 0.0,
        }
    return {"stats": s, "trades": trades, "bt": bt,
            "credit_over_maxloss": cr / ml if ml else 0.0}


def main() -> int:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    alldays = trading_days(conn, "NIFTY")

    # Diagonals need at least far_expiry_offset+1 expiries listed. NIFTY did not
    # carry four liquid expiries until weeklies matured, so the comparison window
    # starts where the structure is actually constructible.
    days = [d for d in alldays if d >= date(2024, 7, 1)]

    say("=" * 96)
    say("DIAGONALS -- sell the near expiry, buy further OTM at expiry+N")
    say("=" * 96)
    say(f"  window {days[0]} .. {days[-1]}  ({len(days)} sessions)")
    say("  Short window by necessity: the structure needs 4+ expiries listed and")
    say("  liquid, which NIFTY did not have earlier. Everything below rests on")
    say("  about two years, which is not many independent observations.")
    say("")
    say("  'cr/maxL' is the credit as a share of maximum loss -- the risk/reward")
    say("  the structure is actually offering.")
    say("  Capital at risk is the CONSERVATIVE bound (far leg assumed worthless")
    say("  at the near expiry), so the diagonal is flattered by nothing here.")

    base_diag = StrategyConfig(
        structure="put_diagonal", short_delta=0.16, dte_target=7,
        dte_tolerance=3, far_expiry_offset=3, far_wing_delta=0.05,
        far_min_trades=1, profit_take_fraction=0.5, min_trades=100,
        symbol="NIFTY")

    # ------------------------------------------------------------------ 1
    say("")
    say("-" * 96)
    say("1. HEAD TO HEAD -- diagonal against the same-expiry vertical")
    say("-" * 96)
    say("Same near-leg delta, same DTE, same window, same charges and slippage.")
    say("")
    say(HDR)
    res = {}
    G = "head_to_head"
    res["vertical"] = row("bull put spread (same expiry)", conn,
                          replace(base_diag, structure="bull_put_spread",
                                  wing_delta=0.05), days, group=G)
    res["diagonal"] = row("put diagonal (expiry+3)", conn, base_diag, days,
                          group=G)
    res["condor"] = row("iron condor (same expiry)", conn,
                        replace(base_diag, structure="iron_condor",
                                wing_delta=0.05), days, group=G)
    res["double"] = row("double diagonal (expiry+3)", conn,
                        replace(base_diag, structure="double_diagonal"), days,
                        group=G)

    d = res.get("diagonal", {}).get("bt")
    if d is not None:
        say("")
        say(f"  far leg untradeable when the trade ended: {d.far_leg_untradeable}")
        say(f"  margin figures produced for a 2-expiry structure "
            f"(not defensible, not reported): {d.multi_expiry_margin_calls:,}")

    # ------------------------------------------------------------------ 2
    say("")
    say("-" * 96)
    say("2. HOW FAR OUT SHOULD THE BOUGHT LEG SIT?")
    say("-" * 96)
    say(HDR)
    for off in (1, 2, 3, 4):
        row(f"expiry+{off}", conn, replace(base_diag, far_expiry_offset=off),
            days, group="far_offset")
    say("")
    say("  Further out = more time value retained in a crash, and a thinner")
    say("  contract to buy it in.")

    say("")
    say(HDR)
    for fd in (0.02, 0.05, 0.10, 0.20):
        row(f"far leg {fd:.2f} delta", conn,
            replace(base_diag, far_wing_delta=fd), days, group="far_delta")
    say("")
    say("  A nearer-the-money far leg costs more but narrows the strike gap,")
    say("  which is what the maximum loss actually is.")

    # ------------------------------------------------------------------ 3
    say("")
    say("-" * 96)
    say("3. THE ASSUMPTION THAT DECIDES IT -- slippage on the thin leg")
    say("-" * 96)
    say("The far leg trades ~400x less than the near one (CALENDARS.md section 3).")
    say("The bhavcopy has no quotes, so this is modelled. If the answer flips")
    say("inside a plausible range, the backtest has not answered the question.")
    say("")
    say(HDR)
    for rel, tk, mult, lbl in (
            (0.00, 0, 1.0, "none (unreal)"),
            (0.01, 1, 2.0, "1% / 1 tick, 2x illiquid (base)"),
            (0.02, 2, 3.0, "2% / 2 ticks, 3x illiquid"),
            (0.04, 4, 4.0, "4% / 4 ticks, 4x illiquid"),
            (0.08, 8, 4.0, "8% / 8 ticks, 4x illiquid")):
        row(f"  {lbl}", conn, base_diag, days,
            slip=SlippageModel(rel=rel, ticks=tk, illiquid_multiple=mult,
                               illiquid_trades_below=5000), group="slippage")

    # ------------------------------------------------------------------ 4
    say("")
    say("-" * 96)
    say("4. THE TAIL -- did the long-dated hedge actually help?")
    say("-" * 96)
    for name in ("vertical", "diagonal"):
        r = res.get(name)
        if not r:
            continue
        s = r["stats"]
        done = [t for t in r["trades"]
                if t.exit_reason != "still_open_at_sample_end"]
        pnl = np.array([t.net_pnl for t in done])
        ml = np.array([t.max_loss for t in done])
        frac = np.abs(np.minimum(pnl, 0)) / np.maximum(ml, 1)
        say(f"  {name:<10} worst {s.worst_trade:>10,.0f}   "
            f"mean worst 1% {s.worst_1pct:>10,.0f}   "
            f"skew {s.skew:>6.2f}   "
            f"worst as share of its own max loss {frac.max() * 100:>5.1f}%")
    say("")
    say("  The diagonal's claim is that the long-dated leg holds value in a")
    say("  spike where a near-dated wing can expire worthless. If that is true")
    say("  here, its worst trade should be a SMALLER fraction of its own")
    say("  maximum loss.")

    # ------------------------------------------------------------------ 5
    say("")
    say("-" * 96)
    say("5. SIGNIFICANCE")
    say("-" * 96)
    for name in ("vertical", "diagonal"):
        r = res.get(name)
        if not r:
            continue
        bb = block_bootstrap(r["trades"], n_boot=20000)
        if bb:
            say(f"  {name:<10} observed {bb['observed_total']:>10,.0f}   "
                f"5-95% [{bb['total_p05']:>10,.0f}, {bb['total_p95']:>10,.0f}]   "
                f"P(<0) {bb['prob_total_negative'] * 100:>5.1f}%")

    # ------------------------------------------------------------------ 6
    say("")
    say("=" * 96)
    say("WHAT THIS SAYS")
    say("=" * 96)
    v, dg = res.get("vertical", {}).get("stats"), res.get("diagonal", {}).get("stats")
    if v and dg:
        say("")
        say(f"  risk/reward offered: vertical {res['vertical']['credit_over_maxloss'] * 100:.1f}% "
            f"of max loss as credit, diagonal "
            f"{res['diagonal']['credit_over_maxloss'] * 100:.1f}%.")
        if res['diagonal']['credit_over_maxloss'] < res['vertical']['credit_over_maxloss']:
            say("  The diagonal is paid LESS per unit of capital at risk, because a")
            say("  5-delta strike three expiries out is far away, and the strike")
            say("  gap IS the maximum loss. Buying further out in TIME does not")
            say("  narrow the gap in PRICE -- and the gap is what you can lose.")
        say("")
        better_ret = dg.annualised_return_on_max_loss > v.annualised_return_on_max_loss
        better_tail = dg.worst_trade > v.worst_trade
        say(f"  return: diagonal {'beats' if better_ret else 'loses to'} the vertical "
            f"({dg.annualised_return_on_max_loss * 100:+.2f}% vs "
            f"{v.annualised_return_on_max_loss * 100:+.2f}% a year)")
        say(f"  tail:   diagonal {'better' if better_tail else 'worse'} on the worst trade "
            f"({dg.worst_trade:,.0f} vs {v.worst_trade:,.0f})")
        say("")
        say(f"  risk-free over this sample: {RISK_FREE * 100:.2f}% a year.")

    payload = {"window": [str(days[0]), str(days[-1])], "groups": JSON_ROWS}
    d0 = res.get("diagonal")
    if d0:
        done = [t for t in d0["trades"]
                if t.exit_reason != "still_open_at_sample_end"]
        payload["diagonal_trades"] = [t.as_row() for t in done]
        payload["far_leg_untradeable"] = d0["bt"].far_leg_untradeable
    (OUT / "diagonals.json").write_text(json.dumps(payload, default=str))
    (OUT / "diagonals.txt").write_text("\n".join(_lines) + "\n")
    print(f"\nwrote {OUT / 'diagonals.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
