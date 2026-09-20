"""Can the simulator find an edge that is definitely there?

A backtest that reports no edge is only informative if it would have reported
one. This builds a synthetic panel whose pairs are cointegrated BY
CONSTRUCTION with a known half-life, runs the identical pipeline over it, and
checks three things:

1. the screen finds the planted pairs and not the decoys;
2. the book makes money on them, gross AND net of the real rate card;
3. the same book run on a panel of pure random walks makes nothing.

If (1) and (2) fail the engine is broken. If (3) succeeds the engine is
lying. Only when all three land is a null result on real data worth
reporting.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
from arblib import backtest as bt
from arblib import pairs, stats, synth

OUT = Path(__file__).resolve().parent.parent / "out"


def make_panel(n_days: int, n_planted: int, n_decoy: int, half_life: float, seed: int):
    """`(dates, close, open, planted)` -- planted pairs plus unrelated decoys."""
    rng = np.random.default_rng(seed)
    close, planted = {}, []
    for k in range(n_planted):
        a, b, _ = synth.cointegrated(n_days, rng, half_life=half_life,
                                     beta=float(rng.uniform(0.6, 1.8)),
                                     sigma_x=6.0, sigma_e=14.0, level=800.0)
        close[f"P{k}A"] = np.clip(b, 50, None)
        close[f"P{k}B"] = np.clip(a, 50, None)
        planted.append((f"P{k}A", f"P{k}B"))
    for k in range(n_decoy):
        close[f"D{k}"] = np.clip(800 + np.cumsum(rng.standard_normal(n_days) * 6.0
                                                 + 0.10), 50, None)
    dates = np.arange(np.datetime64("2014-01-01"), np.datetime64("2014-01-01")
                      + n_days).astype("datetime64[D]")
    # Fills happen at the next OPEN. Give the synthetic panel a realistic
    # overnight gap rather than open==close, which would make every fill free.
    opn = {s: v * (1.0 + rng.standard_normal(n_days) * 0.004) for s, v in close.items()}
    return dates, close, opn, planted


def run(dates, close, opn, formation, trading, sims, top_k, rules, book,
        label, planted=None):
    syms = sorted(close)
    n = len(dates)
    plan, found_true, found_false, kept = [], 0, 0, []
    end = formation - 1
    truth = {frozenset(p) for p in (planted or [])}
    while end + trading < n:
        cands, audit = pairs.screen(
            close, end, syms,
            pairs.UniverseSpec(formation_bars=formation, fno_only=False,
                               min_turnover=0.0, min_price=0.0),
            sims=sims)
        p = np.array([c.pvalue for c in cands])
        mask = pairs.benjamini_hochberg(p, q=0.05)
        sel = pairs.disjoint_by_symbol([c for c, m in zip(cands, mask) if m])[:top_k]
        for c in sel:
            if frozenset((c.a, c.b)) in truth:
                found_true += 1
            else:
                found_false += 1
        kept.append(len(sel))
        plan.append((end + 1, end + trading, sel))
        end += trading
    res = bt.simulate(plan, dates, close, opn, rules, book)
    s = bt.summarise(res, book)
    print(f"\n  [{label}]  selected per window {kept}")
    if planted is not None:
        print(f"    of {sum(kept)} selections, {found_true} were planted pairs "
              f"and {found_false} were not")
    print(f"    trades {s['n_trades']:>4}  win {s['win_rate']:>6.1%}  "
          f"gross Rs {s['total_gross_pnl']:>+11,.0f}  costs Rs {s['total_costs']:>10,.0f}  "
          f"net Rs {s['total_net_pnl']:>+11,.0f}")
    print(f"    CAGR {s['cagr']:>+7.2%}  maxDD {s['max_drawdown']:>7.2%}  "
          f"mean open {s['mean_open']:.1f}  exits {s['exit_reasons']}")
    return {"summary": s, "kept": kept, "found_true": found_true,
            "found_false": found_false}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1600)
    ap.add_argument("--formation", type=int, default=756)
    ap.add_argument("--trading", type=int, default=126)
    ap.add_argument("--planted", type=int, default=12)
    ap.add_argument("--decoys", type=int, default=30)
    ap.add_argument("--half-life", type=float, default=12.0)
    ap.add_argument("--sims", type=int, default=20000)
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    rules = bt.TradeRules()
    book = bt.BookRules(capital=1_000_000.0, gross_per_pair=200_000.0, max_open=10)

    print("1. A panel with 12 PLANTED cointegrated pairs (half-life "
          f"{args.half_life:.0f} bars) and 30 unrelated decoys")
    d, c, o, planted = make_panel(args.days, args.planted, args.decoys,
                                  args.half_life, seed=1)
    good = run(d, c, o, args.formation, args.trading, args.sims, 20, rules, book,
               "planted", planted)

    print("\n2. The SAME pipeline on a panel of pure random walks -- nothing planted")
    d2, c2, o2, _ = make_panel(args.days, 0, args.planted * 2 + args.decoys,
                               args.half_life, seed=2)
    null = run(d2, c2, o2, args.formation, args.trading, args.sims, 20, rules, book,
               "random walks", [])

    ok_find = good["found_true"] > 0 and good["found_true"] > good["found_false"]
    ok_earn = good["summary"]["total_net_pnl"] > 0
    ok_null = abs(null["summary"]["total_net_pnl"]) < abs(good["summary"]["total_net_pnl"])
    print("\n  VERDICT")
    print(f"    finds the planted pairs            {'PASS' if ok_find else 'FAIL'}")
    print(f"    makes money on them, net of costs  {'PASS' if ok_earn else 'FAIL'}")
    print(f"    makes less on random walks         {'PASS' if ok_null else 'FAIL'}")
    print("    -> a null result on real data is only meaningful if all three pass.")

    (OUT / "validate_backtest.json").write_text(json.dumps(
        {"planted": good, "random": null,
         "verdict": {"finds": ok_find, "earns": ok_earn, "null_quiet": ok_null}},
        indent=2, default=float))
    print(f"\nwrote {OUT / 'validate_backtest.json'}")


if __name__ == "__main__":
    main()
