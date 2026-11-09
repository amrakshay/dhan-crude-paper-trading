"""The real-data backtest: the hedged structure, its unhedged twin, and the
baselines it has to beat.

Reports are arranged so the LEFT TAIL is unavoidable.  The win rate is printed
once, immediately labelled meaningless, and never used as evidence.

  python scripts/run_backtest.py [--from 2016-01-01] [--to 2026-09-17]
                                 [--structure iron_condor] [--json out/...]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hedgelib.backtest import Backtest, SlippageModel, StrategyConfig  # noqa: E402
from hedgelib.baselines import RiskFree, buy_and_hold                   # noqa: E402
from hedgelib.chain import trading_days                                 # noqa: E402
from hedgelib.metrics import (block_bootstrap, equity_metrics,          # noqa: E402
                              tail_events, trade_stats)

DB = ROOT / "data/cache/options.db"
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)

# SEBI's index-derivatives framework: lot size tripled, one weekly expiry per
# exchange, +2% expiry-day ELM.  A backtest spanning this is two markets.
SEBI_BREAK = date(2024, 11, 20)

_lines: list[str] = []


def say(s: str = "") -> None:
    print(s)
    _lines.append(s)


def index_closes(conn, start: date, end: date) -> list[tuple[date, float]]:
    rows = conn.execute(
        "SELECT trade_date, close FROM index_close WHERE index_name='Nifty 50' "
        "  AND trade_date>=? AND trade_date<=? ORDER BY trade_date",
        (start.isoformat(), end.isoformat())).fetchall()
    return [(date.fromisoformat(d), float(c)) for d, c in rows]


def vix_series(conn, start: date, end: date) -> list[tuple[date, float]]:
    rows = conn.execute(
        "SELECT trade_date, close FROM index_close WHERE index_name='India VIX' "
        "  AND trade_date>=? AND trade_date<=? ORDER BY trade_date",
        (start.isoformat(), end.isoformat())).fetchall()
    return [(date.fromisoformat(d), float(c)) for d, c in rows]


def report(name: str, trades: list, note: str = "") -> dict:
    s = trade_stats(trades)
    say("")
    say("-" * 78)
    say(f"{name}")
    if note:
        say(f"  {note}")
    say("-" * 78)
    if s.n == 0:
        say("  NO COMPLETED TRADES")
        return {}
    say(f"  trades {s.n}   (still open at sample end: {s.n_open_at_end})")
    say(f"  mean days held {s.mean_days_held:.1f}")
    say("")
    say(f"  net P&L total          Rs {s.total_pnl:>14,.0f}")
    say(f"    of which gross       Rs {s.gross_pnl:>14,.0f}")
    say(f"    of which charges     Rs {-s.total_charges:>14,.0f}   "
        f"({s.total_charges / abs(s.gross_pnl) * 100 if s.gross_pnl else 0:.1f}% of gross)")
    say(f"  mean per trade         Rs {s.mean_pnl:>14,.0f}")
    say(f"  median per trade       Rs {s.median_pnl:>14,.0f}")
    say("")
    say(f"  >>> THE LEFT TAIL <<<")
    say(f"  worst single trade     Rs {s.worst_trade:>14,.0f}")
    say(f"  mean of worst 1%       Rs {s.worst_1pct:>14,.0f}")
    say(f"  mean of worst 5%       Rs {s.worst_5pct:>14,.0f}")
    say(f"  worst month            Rs {s.worst_month:>14,.0f}   ({s.worst_month_label})")
    say(f"  max loss / margin         {s.max_loss_per_margin * 100:>13.1f}%")
    say(f"  mean loss / mean win      {s.loss_tail_ratio:>13.2f}x")
    say(f"  skew of trade P&L         {s.skew:>13.2f}")
    say("")
    say(f"  mean margin blocked    Rs {s.mean_margin:>14,.0f}")
    say(f"  return on margin, mean    {s.mean_return_on_margin * 100:>13.2f}%  per trade")
    say(f"  return on margin, total   {s.total_return_on_margin * 100:>13.2f}%  "
        f"(sum P&L / sum margin)")
    say(f"  return on margin, /yr     {s.annualised_return_on_margin * 100:>13.2f}%  "
        f"per rupee-YEAR of margin -- LEVERAGED, see below")
    if s.max_loss_rupee_years > 0:
        say(f"  return on CAPITAL AT RISK {s.annualised_return_on_max_loss * 100:>13.2f}%  "
            f"per rupee-YEAR of MAX LOSS <- compare this with the risk-free rate")
    else:
        say(f"  return on CAPITAL AT RISK     (unbounded)   this structure has no "
            f"bounded maximum loss")
    say("")
    say(f"  win rate                  {s.win_rate * 100:>13.1f}%   <- MEANINGLESS on its own.")
    say(f"                            Selling OTM options produces this by construction.")
    say(f"  exits: {s.exit_reasons}")
    return asdict(s)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="2016-01-01")
    ap.add_argument("--to", dest="end", default="2026-09-17")
    ap.add_argument("--structure", default="iron_condor")
    ap.add_argument("--short-delta", type=float, default=0.16)
    ap.add_argument("--wing-delta", type=float, default=0.05)
    ap.add_argument("--dte", type=int, default=30)
    ap.add_argument("--profit-take", type=float, default=0.5)
    ap.add_argument("--capital", type=float, default=2_000_000)
    ap.add_argument("--max-concurrent", type=int, default=1)
    ap.add_argument("--json", default=str(OUT / "backtest.json"))
    ap.add_argument("--txt", default=str(OUT / "backtest_report.txt"))
    a = ap.parse_args()

    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    days = [d for d in trading_days(conn, "NIFTY") if start <= d <= end]
    if not days:
        say("no sessions in range -- has the cache been fetched?")
        return 1

    idx = index_closes(conn, days[0], days[-1])
    vix = vix_series(conn, days[0], days[-1])

    say("=" * 78)
    say("HEDGED OPTION SELLING -- NIFTY index options")
    say("=" * 78)
    say(f"  sample      {days[0]} .. {days[-1]}   ({len(days)} sessions)")
    say(f"  structure   {a.structure}  short {a.short_delta:.2f}d  "
        f"wing {a.wing_delta:.2f}d  DTE~{a.dte}  "
        f"PT {int(a.profit_take * 100) if a.profit_take else 'none'}%")
    if vix:
        vv = [v for _, v in vix]
        say(f"  India VIX   min {min(vv):.1f}  median "
            f"{sorted(vv)[len(vv) // 2]:.1f}  max {max(vv):.1f}")

    # ---- how many disasters does this sample actually contain? ----------
    te = tail_events(idx)
    say("")
    say("=" * 78)
    say("WHAT THE SAMPLE CONTAINS  -- read this before any performance number")
    say("=" * 78)
    say(f"  sessions                       {te.get('sessions', 0):,}")
    say(f"  single-day falls over 3%       {te.get('down_moves_over_3pct', 0)}")
    say(f"  single-day falls over 5%       {te.get('down_moves_over_5pct', 0)}")
    say(f"  worst single session           {te.get('worst_session', 0) * 100:.2f}%  "
        f"({te.get('worst_session_date', '')})")
    say(f"  worst 20-session stretch       {te.get('worst_20_session_move', 0) * 100:.2f}%  "
        f"(to {te.get('worst_20_session_end', '')})")
    if te.get("down_moves_over_5pct", 0) == 0:
        say("")
        say("  *** THE SAMPLE CONTAINS NO 5% DOWN DAY. That is the headline, not a")
        say("  *** footnote: this backtest has not tested the event that decides")
        say("  *** whether a premium seller survives.")

    cfg = StrategyConfig(
        structure=a.structure, short_delta=a.short_delta,
        wing_delta=a.wing_delta, dte_target=a.dte, dte_tolerance=10,
        profit_take_fraction=a.profit_take, hold_to_expiry=not a.profit_take,
        min_trades=100, max_concurrent=a.max_concurrent,
        capital=a.capital, symbol="NIFTY")

    bt = Backtest(conn, cfg, slippage=SlippageModel())
    trades = bt.run(days)
    results = {"config": cfg.describe(), "sample": [str(days[0]), str(days[-1])],
               "tail_events": te}

    results["hedged"] = report(
        f"HEDGED -- {cfg.describe()}",
        trades,
        f"data faults {len(bt.data_faults)}, "
        f"entries refused for margin {bt.rejected_for_margin}")

    # ---- the unhedged twin: same engine, wing removed -------------------
    unhedged_name = {"iron_condor": "naked_strangle",
                     "bull_put_spread": "naked_put"}.get(a.structure)
    un_trades = []
    if unhedged_name:
        ucfg = StrategyConfig(**{**cfg.__dict__, "structure": unhedged_name})
        ubt = Backtest(conn, ucfg, slippage=SlippageModel())
        un_trades = ubt.run(days)
        results["unhedged"] = report(
            f"UNHEDGED -- {unhedged_name}, identical in every other respect",
            un_trades,
            "The wing must justify its own cost. If this line is better on "
            "return AND survivable on the tail, the hedge does not pay.")

    # ---- split at the structural break ----------------------------------
    say("")
    say("=" * 78)
    say(f"SPLIT AT THE SEBI BREAK ({SEBI_BREAK}) -- these are two different markets")
    say("=" * 78)
    for label, sel in (("BEFORE", lambda t: t.entry_date < SEBI_BREAK),
                       ("AFTER ", lambda t: t.entry_date >= SEBI_BREAK)):
        sub = [t for t in trades if sel(t)]
        s = trade_stats(sub)
        if s.n:
            say(f"  {label}  trades {s.n:>4}  mean Rs {s.mean_pnl:>9,.0f}  "
                f"total Rs {s.total_pnl:>12,.0f}  worst Rs {s.worst_trade:>10,.0f}  "
                f"RoM {s.total_return_on_margin * 100:>6.2f}%  win {s.win_rate * 100:.0f}%")
            results[f"split_{label.strip().lower()}"] = asdict(s)
        else:
            say(f"  {label}  no completed trades")

    # ---- baselines -------------------------------------------------------
    rf = RiskFree()
    curve = [(d, e) for d, e, _, _ in bt.equity_curve]
    say("")
    say("=" * 78)
    say("AGAINST THE BASELINES")
    say("=" * 78)
    if curve:
        em = equity_metrics(curve)
        results["equity"] = em

        # THE BOOK IS MOSTLY IDLE, AND A RAW CAGR MISREPRESENTS THAT.
        # One position at a time on Rs 20 lakh leaves ~97% of the capital
        # uncommitted, and the raw equity curve implicitly earns NOTHING on it.
        # Comparing that against the repo rate charges the strategy for cash it
        # never used. A real operator's uncommitted margin sits in liquid funds,
        # so the un-deployed balance is credited at the same repo rate the
        # baseline uses, and BOTH curves are reported.
        credited: list[tuple[date, float]] = []
        eq = a.capital
        prev_d = None
        for d, raw, mgn, _u in bt.equity_curve:
            if prev_d is not None:
                idle = max(eq - mgn, 0.0)
                dt = (d - prev_d).days / 365.0
                eq += idle * rf.rate_on(d) * dt
            prev_d = d
            credited.append((d, eq + (raw - a.capital)))
        em_c = equity_metrics(credited)
        results["equity_idle_credited"] = em_c

        rfc = rf.grow(a.capital, [d for d, _ in curve])
        bh = buy_and_hold(idx, a.capital)
        rf_m = equity_metrics(rfc)
        bh_m = equity_metrics(bh) if len(bh) > 1 else {}
        results["risk_free"] = rf_m
        results["buy_hold"] = bh_m
        say(f"  {'':22} {'CAGR':>9} {'maxDD':>9} {'MAR':>8} {'Sharpe':>8}")
        say(f"  {'hedged seller (raw)':22} {em['cagr'] * 100:>8.2f}% "
            f"{em['max_drawdown'] * 100:>8.2f}% {em['mar']:>8.2f} {em['sharpe']:>8.2f}")
        say(f"  {'  + idle cash at repo':22} {em_c['cagr'] * 100:>8.2f}% "
            f"{em_c['max_drawdown'] * 100:>8.2f}% {em_c['mar']:>8.2f} "
            f"{em_c['sharpe']:>8.2f}")
        say(f"  {'risk-free (repo)':22} {rf_m['cagr'] * 100:>8.2f}% "
            f"{rf_m['max_drawdown'] * 100:>8.2f}% {'-':>8} {'-':>8}")
        if bh_m:
            say(f"  {'buy and hold NIFTY':22} {bh_m['cagr'] * 100:>8.2f}% "
                f"{bh_m['max_drawdown'] * 100:>8.2f}% {bh_m['mar']:>8.2f} "
                f"{bh_m['sharpe']:>8.2f}")
        peak_u = max((u for _, _, _, u in bt.equity_curve), default=0.0)
        mean_u = (sum(u for _, _, _, u in bt.equity_curve)
                  / len(bt.equity_curve)) if bt.equity_curve else 0.0
        say("")
        say(f"  peak margin utilisation  {peak_u * 100:.1f}%    "
            f"mean {mean_u * 100:.1f}%")
        say(f"  capital Rs {a.capital:,.0f}; the strategy is constrained by MARGIN,")
        say(f"  not by cash -- a credit structure receives money at entry.")
        results["margin_utilisation"] = {"peak": peak_u, "mean": mean_u}
        excess = em_c["cagr"] - rf_m["cagr"]
        say("")
        say(f"  EXCESS OVER RISK-FREE: {excess * 100:+.2f}% a year")
        say("  (measured on the idle-cash-credited curve, which is the fair")
        say("   comparison: the raw curve earns nothing on ~97% of the book and")
        say("   would charge the strategy for capital it never deployed.)")
        if excess <= 0:
            say("  The strategy did not beat leaving the money in bills.")
        say("")
        say("  The scale-free figure is RETURN ON CAPITAL AT RISK above, which")
        say("  does not depend on how much idle cash happens to sit beside the")
        say("  position. CAGR here is a property of the book, not the strategy.")

    # ---- significance ----------------------------------------------------
    say("")
    say("=" * 78)
    say("BLOCK BOOTSTRAP -- whole months resampled, 10,000 draws")
    say("=" * 78)
    say("Months, not trades: losses arrive CLUSTERED here, because one adverse")
    say("move hits every open position and the next is sold into the same regime.")
    bb = block_bootstrap(trades)
    results["bootstrap"] = bb
    if bb:
        say(f"  months resampled           {bb['n_months']}")
        say(f"  observed total        Rs {bb['observed_total']:>14,.0f}")
        say(f"  bootstrap 5th pct     Rs {bb['total_p05']:>14,.0f}")
        say(f"  bootstrap median      Rs {bb['total_p50']:>14,.0f}")
        say(f"  bootstrap 95th pct    Rs {bb['total_p95']:>14,.0f}")
        say(f"  P(total P&L < 0)          {bb['prob_total_negative'] * 100:>13.1f}%")
        say(f"  median worst trade    Rs {bb['worst_trade_median']:>14,.0f}")

    # ---- per-trade rows for the dashboard -------------------------------
    results["trades"] = [t.as_row() for t in trades]
    results["unhedged_trades"] = [t.as_row() for t in un_trades]
    results["equity_curve"] = [[d.isoformat(), e, m, u]
                               for d, e, m, u in bt.equity_curve]
    results["index"] = [[d.isoformat(), c] for d, c in idx]
    results["vix"] = [[d.isoformat(), v] for d, v in vix]

    Path(a.json).write_text(json.dumps(results, default=str))
    Path(a.txt).write_text("\n".join(_lines) + "\n")
    print(f"\nwrote {a.json}\nwrote {a.txt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
