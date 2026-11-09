"""Parameter sweep, ONE DIMENSION AT A TIME.

The output is the SHAPE of each trade-off, not a best cell.  A best cell picked
from a grid this size is a selection artefact, and on a strategy whose result is
decided by two trades in sixty it is close to meaningless.

Every row carries its left tail beside its return, because a cell that looks
best on total P&L and worst on maximum loss per unit of margin has not won
anything.

  python scripts/sweep.py [--from 2016-01-01] [--to 2026-09-17]
                          [--dims dte,short_delta,wing,management,structure]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hedgelib.backtest import Backtest, SlippageModel, StrategyConfig  # noqa: E402
from hedgelib.chain import trading_days                                # noqa: E402
from hedgelib.metrics import trade_stats                               # noqa: E402

DB = ROOT / "data/cache/options.db"
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)

_lines: list[str] = []


def say(s: str = "") -> None:
    print(s)
    _lines.append(s)


# `R/risk /yr` is return per rupee-YEAR of MAX LOSS deployed -- the capital
# actually at risk.  Return on MARGIN is a leveraged number here, because the
# exchange collateralises a median of only ~26% of a structure's maximum loss.
HEADER = (f"  {'cell':<22} {'n':>4} {'total P&L':>12} {'R/risk /yr':>11} "
          f"{'RoM /yr':>9} {'worst':>11} {'worst1%':>11} {'maxL/mgn':>9} "
          f"{'win':>6} {'held':>6}")


def run_cell(conn, cfg: StrategyConfig, days: list[date]) -> dict:
    bt = Backtest(conn, cfg, slippage=SlippageModel())
    trades = bt.run(days)
    s = trade_stats(trades)
    return {"stats": s, "settlement": bt.settlement_sources,
            "faults": len(bt.data_faults) + len(bt.settlement_faults)}


def line(label: str, r: dict) -> str:
    s = r["stats"]
    if s.n == 0:
        return f"  {label:<22}    0   (no completed trades)"
    rr = (f"{s.annualised_return_on_max_loss * 100:>10.2f}%"
          if s.max_loss_rupee_years > 0 else f"{'unbounded':>11}")
    return (f"  {label:<22} {s.n:>4} {s.total_pnl:>12,.0f} {rr} "
            f"{s.annualised_return_on_margin * 100:>8.2f}% {s.worst_trade:>11,.0f} "
            f"{s.worst_1pct:>11,.0f} {s.max_loss_per_margin * 100:>8.1f}% "
            f"{s.win_rate * 100:>5.1f}% {s.mean_days_held:>6.1f}")


def sweep(conn, base: StrategyConfig, days: list[date], name: str,
          variants: list[tuple[str, dict]], note: str = "") -> dict:
    say("")
    say("=" * 108)
    say(f"SWEEP: {name}")
    if note:
        say(f"  {note}")
    say("=" * 108)
    say(HEADER)
    out = {}
    for label, kw in variants:
        cfg = replace(base, **kw)
        r = run_cell(conn, cfg, days)
        say(line(label, r))
        st = r["stats"]
        out[label] = {"n": st.n, "total_pnl": st.total_pnl,
                      "mean_pnl": st.mean_pnl, "worst": st.worst_trade,
                      "worst_1pct": st.worst_1pct,
                      "rom_total": st.total_return_on_margin,
                      "rom_annual": st.annualised_return_on_margin,
                      "return_on_risk_annual": st.annualised_return_on_max_loss,
                      "max_loss_rupee_years": st.max_loss_rupee_years,
                      "max_loss_per_margin": st.max_loss_per_margin,
                      "win_rate": st.win_rate, "days_held": st.mean_days_held,
                      "mean_margin": st.mean_margin}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="2016-01-01")
    ap.add_argument("--to", dest="end", default="2026-09-17")
    ap.add_argument("--dims", default="structure,dte,short_delta,wing,management,slippage")
    ap.add_argument("--json", default=str(OUT / "sweep.json"))
    ap.add_argument("--txt", default=str(OUT / "sweep_report.txt"))
    a = ap.parse_args()

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    days = [d for d in trading_days(conn, "NIFTY") if start <= d <= end]

    base = StrategyConfig(structure="iron_condor", short_delta=0.16,
                          wing_delta=0.05, dte_target=30, dte_tolerance=10,
                          profit_take_fraction=0.5, min_trades=100,
                          max_concurrent=1, symbol="NIFTY")

    say("=" * 108)
    say("PARAMETER SWEEP -- one dimension at a time")
    say("=" * 108)
    say(f"  sample {days[0]} .. {days[-1]}  ({len(days)} sessions)")
    say(f"  base   {base.describe()}")
    say("")
    say("  Read the trade-off, not the maximum. A best cell picked from a grid")
    say("  this size is a selection artefact, and on a strategy whose result is")
    say("  decided by two trades in sixty it is close to meaningless.")
    say("")
    say("  'R/risk /yr' is the annualised return per rupee-year of MAXIMUM LOSS")
    say("  deployed -- the capital actually at risk, and the only figure here")
    say("  that can honestly be set beside the risk-free rate (5.64% over this")
    say("  sample). 'RoM /yr' is the same thing against MARGIN, which is a")
    say("  LEVERAGED number: the exchange collateralises a median of only ~26%")
    say("  of a structure's maximum loss, so it flatters every cell by ~4x.")

    dims = [d.strip() for d in a.dims.split(",")]
    results: dict = {"base": base.describe(),
                     "sample": [str(days[0]), str(days[-1])]}

    if "structure" in dims:
        results["structure"] = sweep(
            conn, base, days, "STRUCTURE -- and the unhedged twins",
            [("iron_condor", {"structure": "iron_condor"}),
             ("bull_put_spread", {"structure": "bull_put_spread"}),
             ("bear_call_spread", {"structure": "bear_call_spread"}),
             ("naked_strangle *", {"structure": "naked_strangle"}),
             ("naked_put *", {"structure": "naked_put"})],
            "* UNHEDGED, shown as the comparator the wing must beat. Not traded.")

    if "dte" in dims:
        results["dte"] = sweep(
            conn, base, days, "DAYS TO EXPIRY at entry",
            [(f"DTE {n}", {"dte_target": n,
                           "dte_tolerance": max(3, n // 3)})
             for n in (7, 14, 21, 30, 45, 60)],
            "Theta grows into expiry; so does gamma, which is what turns an "
            "adverse move into an unmanageable loss.")

    if "short_delta" in dims:
        results["short_delta"] = sweep(
            conn, base, days, "SHORT-STRIKE DELTA",
            [(f"short {d:.2f}d", {"short_delta": d})
             for d in (0.08, 0.10, 0.16, 0.20, 0.30, 0.40)],
            "Further out = higher win rate and less premium. The win rate is "
            "not the point; what happens when it is wrong is.")

    if "wing" in dims:
        results["wing_delta"] = sweep(
            conn, base, days, "WING DELTA -- how far out the protection sits",
            [(f"wing {d:.2f}d", {"wing_delta": d, "wing_width_points": None})
             for d in (0.02, 0.03, 0.05, 0.08, 0.10)],
            "Cheaper wing = more credit and more maximum loss. Return on "
            "MARGIN is the test, not credit captured.")
        results["wing_points"] = sweep(
            conn, base, days, "WING WIDTH -- fixed point distance instead",
            [(f"wing +{w:.0f}pt", {"wing_delta": None, "wing_width_points": w})
             for w in (200, 300, 500, 800, 1200)],
            "A fixed width does not adapt to volatility, which is the same "
            "objection that rules out percentage-of-spot strike selection.")

    if "management" in dims:
        results["management"] = sweep(
            conn, base, days, "MANAGEMENT RULE",
            [("hold to expiry", {"profit_take_fraction": None,
                                 "hold_to_expiry": True,
                                 "stop_loss_credit_multiple": None,
                                 "exit_dte": None}),
             ("profit take 25%", {"profit_take_fraction": 0.25}),
             ("profit take 50%", {"profit_take_fraction": 0.50}),
             ("profit take 75%", {"profit_take_fraction": 0.75}),
             ("exit at 10 DTE", {"profit_take_fraction": None,
                                 "exit_dte": 10}),
             ("exit at 5 DTE", {"profit_take_fraction": None,
                                "exit_dte": 5}),
             ("PT50 + stop 2x", {"profit_take_fraction": 0.50,
                                 "stop_loss_credit_multiple": 2.0}),
             ("PT50 + stop 1x", {"profit_take_fraction": 0.50,
                                 "stop_loss_credit_multiple": 1.0})],
            "The published claim is that closing at 50% of maximum profit "
            "beats holding. It is a claim, not a result -- this tests it.")

    if "slippage" in dims:
        say("")
        say("=" * 108)
        say("SENSITIVITY: SLIPPAGE -- the assumption with no data behind it")
        say("=" * 108)
        say("  The bhavcopy carries no bid-ask. The wing is 2-3 orders of")
        say("  magnitude thinner than the leg it protects (NIFTY 2025-08-01: the")
        say("  near-money put traded 40,466 times, the 5-delta wing 101), so this")
        say("  is the assumption most able to flip the answer.")
        say(HEADER)
        slip_out = {}
        for label, sl in [
            ("none (unreal)", SlippageModel(rel=0.0, ticks=0.0)),
            ("0.5% / 1 tick", SlippageModel(rel=0.005, ticks=1)),
            ("1% / 1 tick", SlippageModel(rel=0.01, ticks=1)),
            ("2% / 2 ticks", SlippageModel(rel=0.02, ticks=2)),
            ("4% / 4 ticks", SlippageModel(rel=0.04, ticks=4)),
        ]:
            bt = Backtest(conn, base, slippage=sl)
            s = trade_stats(bt.run(days))
            say(line(label, {"stats": s}))
            slip_out[label] = {"n": s.n, "total_pnl": s.total_pnl,
                               "rom_total": s.total_return_on_margin,
                               "worst": s.worst_trade}
        results["slippage"] = slip_out

    Path(a.json).write_text(json.dumps(results, default=str, indent=1))
    Path(a.txt).write_text("\n".join(_lines) + "\n")
    print(f"\nwrote {a.json}\nwrote {a.txt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
