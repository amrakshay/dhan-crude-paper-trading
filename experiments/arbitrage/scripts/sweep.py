"""Parameter sweeps -- one axis at a time, reporting the trade-off.

Not a search for the best cell. The best cell of a grid run on one sample is
the luckiest cell, and quoting it is the same error as quoting the strongest
pair out of 200,000 tests. What a sweep is for is the SHAPE: whether the
result is a plateau or a spike, and which direction each knob pushes return
and drawdown.

Every cell reuses the cached screens, so nothing here re-selects pairs --
except `--axis formation`, which must, and which is therefore slow.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
from arblib import backtest as bt
from arblib import bootstrap, charges, data, pairs
from backtest_pairs import build_plan, load_screens
from select_pairs import run as run_selection

OUT = Path(__file__).resolve().parent.parent / "out"

BASE = dict(formation=756, trading=126, min_turnover=5e7, fno_only=True,
            log_prices=False,
            sims=200000, q=0.05, correction="none", top_k=20, per_symbol=1,
            z_in=2.0, z_out=0.5, z_stop=3.5, hold_hl=4.0, max_half_life=30.0,
            capital=1_000_000.0, gross_per_pair=200_000.0, max_open=10,
            card="nse-equity-futures", collateral_yield=1.0)

AXES = {
    "z_in": [1.0, 1.5, 2.0, 2.5, 3.0],
    "z_out": [-0.5, 0.0, 0.5, 1.0],
    "z_stop": [2.5, 3.0, 3.5, 4.5, 99.0],
    "hold_hl": [1.0, 2.0, 4.0, 8.0, 99.0],
    "top_k": [5, 10, 20, 40, 80],
    "min_turnover": [1e7, 5e7, 2e8, 1e9],
    "correction": ["none", "bh", "bonferroni"],
    "card": ["nse-equity-futures", "nse-equity-intraday", "nse-equity-delivery"],
    "collateral_yield": [0.0, 0.5, 1.0],
    "gross_per_pair": [50_000.0, 100_000.0, 200_000.0, 500_000.0],
    "max_half_life": [10.0, 20.0, 30.0, 60.0],
    "formation": [504, 756, 1008],
    "log_prices": [False, True],
}


def one(cfg, dates, close, opn, vol, workers=10):
    if cfg["formation"] != BASE["formation"] or cfg["min_turnover"] != BASE["min_turnover"]:
        run_selection(cfg["formation"], cfg["trading"], cfg["min_turnover"],
                      cfg["fno_only"], cfg["sims"], workers, 1200, None,
                      cfg["log_prices"])
    screens = load_screens(cfg["formation"], cfg["trading"], cfg["min_turnover"],
                           cfg["fno_only"], cfg["sims"], len(dates), cfg["log_prices"])
    plan, kept = build_plan(screens, cfg["q"], cfg["top_k"], cfg["per_symbol"],
                            cfg["correction"], cfg["log_prices"])
    rules = bt.TradeRules(z_in=cfg["z_in"], z_out=cfg["z_out"], z_stop=cfg["z_stop"],
                          hold_cap_half_lives=cfg["hold_hl"],
                          max_half_life=cfg["max_half_life"])
    book = bt.BookRules(capital=cfg["capital"], gross_per_pair=cfg["gross_per_pair"],
                        max_open=cfg["max_open"], card=cfg["card"],
                        collateral_yield=cfg["collateral_yield"])
    res = bt.simulate(plan, dates, close, opn, rules, book)
    s = bt.summarise(res, book)
    eq = np.asarray(res.equity, float)
    if len(eq) > 10:
        rf = np.asarray(charges.risk_free_growth(res.dates))
        ci = bootstrap.mean_ci(np.diff(eq) / eq[:-1] - np.diff(rf) / rf[:-1],
                               n_boot=800, mean_block=max(5.0, s["mean_bars_held"]))
        s["excess_ci_lo"] = ci["lo"] * 252
        s["excess_ci_hi"] = ci["hi"] * 252
        s["excess_excludes_zero"] = ci["excludes_zero"]
    s["pairs_selected"] = int(sum(kept))
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--axis", nargs="+", default=["z_in", "z_out", "z_stop",
                                                  "hold_hl", "top_k", "correction",
                                                  "card", "collateral_yield",
                                                  "max_half_life"])
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--log-base", action="store_true",
                    help="sweep around the LOG-price specification instead")
    ap.add_argument("--tag", default="sweep")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    dates, close, opn, vol = data.panel_ohlc(min_bars=1200)
    if args.log_base:
        BASE["log_prices"] = True

    results = {}
    for axis in args.axis:
        if axis not in AXES:
            raise SystemExit(f"unknown axis {axis}; known: {sorted(AXES)}")
        print(f"\n=== {axis} ===")
        print(f"  {'value':>22} {'pairs':>6} {'trades':>7} {'win':>7} {'CAGR':>8} "
              f"{'excess':>8} {'maxDD':>8} {'Sharpe':>7} {'costs/gross':>12} {'CI>0':>5}")
        rows = []
        for v in AXES[axis]:
            cfg = dict(BASE); cfg[axis] = v
            s = one(cfg, dates, close, opn, vol, args.workers)
            rows.append({"value": v, **s})
            cg = (f"{s['cost_share_of_gross']:.1%}"
                  if s.get("cost_share_of_gross") is not None else "-")
            print(f"  {str(v):>22} {s['pairs_selected']:>6} {s['n_trades']:>7} "
                  f"{s['win_rate']:>7.1%} {s['cagr']:>8.2%} {s['excess_cagr']:>+8.2%} "
                  f"{s['max_drawdown']:>8.2%} {s['sharpe_vs_rf']:>7.2f} {cg:>12} "
                  f"{'yes' if s.get('excess_excludes_zero') else 'no':>5}")
        results[axis] = rows
    (OUT / f"{args.tag}.json").write_text(json.dumps(results, indent=2, default=float))
    print(f"\nwrote {OUT / f'{args.tag}.json'}")


if __name__ == "__main__":
    main()
