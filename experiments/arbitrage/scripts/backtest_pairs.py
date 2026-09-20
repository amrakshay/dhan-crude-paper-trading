"""The portfolio simulation, its baselines, and its significance test.

Reads the cached walk-forward screens, builds a book, and measures it against
the two comparators that matter:

* **the risk-free rate**, because a market-neutral book has no market
  exposure to be paid for taking. Beating the index is not the question;
  beating cash is.
* **a date-matched random-pair baseline** -- identical universe, identical
  dates, identical sizing and costs, with the cointegration screen REMOVED
  and pairs drawn at random. This is the control that isolates what the
  screen contributes. If the random book does as well, the statistics are
  decoration.

Significance is a stationary block bootstrap over daily excess returns, with
the mean block length set from the strategy's own holding period.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
from arblib import backtest as bt
from arblib import bootstrap, charges, data, pairs, stats
from select_pairs import cache_path, plan_windows

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"


def load_screens(formation, trading, min_turnover, fno_only, sims, n_dates,
                 log_prices=False):
    windows = plan_windows(n_dates, formation, trading)
    out = []
    for end_idx, a, b in windows:
        p = cache_path(end_idx, formation, min_turnover, fno_only, sims, log_prices)
        if not p.exists():
            raise SystemExit(f"missing screen cache {p.name} -- run select_pairs.py first")
        out.append((json.loads(p.read_text()), a, b))
    return out


def to_candidates(raw, log_prices=False) -> list[pairs.Candidate]:
    return [pairs.Candidate(
        a=c["a"], b=c["b"], beta=c["beta"], alpha=c["alpha"], stat=c["stat"],
        pvalue=c["pvalue"], half_life=(c["half_life"] if c["half_life"] is not None
                                       else float("inf")),
        mu=c["mu"], sigma=c["sigma"], n=c["n"], log_prices=log_prices)
        for c in raw["candidates"]]


def build_plan(screens, q, top_k, per_symbol, correction="bh", log_prices=False):
    """`(trade_start, trade_end, [Candidate...])` per window, after correction."""
    plan, kept = [], []
    for raw, a, b in screens:
        cands = to_candidates(raw, log_prices)
        if not cands:
            plan.append((a, b, [])); kept.append(0); continue
        p = np.array([c.pvalue for c in cands])
        if correction == "bh":
            mask = pairs.benjamini_hochberg(p, q=q)
        elif correction == "bonferroni":
            mask = pairs.bonferroni(p, alpha=q)
        elif correction == "none":
            mask = p < q
        else:
            raise ValueError(correction)
        sel = [c for c, m in zip(cands, mask) if m]
        sel = pairs.disjoint_by_symbol(sel, limit_per_symbol=per_symbol)[:top_k]
        plan.append((a, b, sel)); kept.append(len(sel))
    return plan, kept


def random_plan(screens, dates, close, vol, formation, min_turnover, fno_only,
                top_k, per_symbol, seed=4242):
    """The date-matched control: same universe and dates, random pairs.

    The hedge ratio, mu and sigma still come from a formation-window
    regression -- a random pair still needs a spread to trade -- but nothing
    is required of its p-value. Everything the screen does is removed and
    nothing else is.
    """
    rng = np.random.default_rng(seed)
    spec = pairs.UniverseSpec(formation_bars=formation, min_turnover=min_turnover,
                              fno_only=fno_only)
    plan = []
    for raw, a, b in screens:
        end_idx = raw["end_idx"]
        syms = pairs.eligible(dates, close, vol, end_idx, spec)
        a0 = end_idx + 1 - formation
        cols = {s: pairs._ffill(close[s][a0:end_idx + 1]) for s in syms}
        want, sel, seen, tries = top_k, [], {}, 0
        while len(sel) < want and tries < want * 40 and len(syms) > 2:
            tries += 1
            x, y = rng.choice(len(syms), 2, replace=False)
            sx, sy = syms[x], syms[y]
            if seen.get(sx, 0) >= per_symbol or seen.get(sy, 0) >= per_symbol:
                continue
            res, flipped = stats.engle_granger_best(cols[sx], cols[sy], sims=20000)
            dep, ind = (sx, sy) if not flipped else (sy, sx)   # see pairs.screen
            seen[sx] = seen.get(sx, 0) + 1
            seen[sy] = seen.get(sy, 0) + 1
            sel.append(pairs.Candidate(
                a=ind, b=dep, beta=res.beta, alpha=res.alpha, stat=res.stat,
                pvalue=res.pvalue, half_life=res.half_life,
                mu=float(np.mean(res.resid)), sigma=float(np.std(res.resid, ddof=1)),
                n=res.n))
        plan.append((a, b, sel))
    return plan


def report(name, summary):
    s = summary
    print(f"\n--- {name} ---")
    print(f"  {s['start']} .. {s['end']}  ({s['years']}y)")
    print(f"  CAGR {s['cagr']:>8.2%}     risk-free {s['risk_free_cagr']:>7.2%}"
          f"     EXCESS {s['excess_cagr']:>+7.2%}")
    print(f"  vol  {s['vol_annual']:>8.2%}     max DD   {s['max_drawdown']:>8.2%}"
          f"     Sharpe vs rf {s['sharpe_vs_rf']:>5.2f}   MAR {s['mar']:>5.2f}")
    print(f"  trades {s['n_trades']:>6}  win {s['win_rate']:>6.1%}  "
          f"mean hold {s['mean_bars_held']:>4.1f} bars  mean open {s['mean_open']:.1f}")
    print(f"  gross P&L Rs {s['total_gross_pnl']:>12,.0f}   costs Rs {s['total_costs']:>11,.0f}"
          f"   net Rs {s['total_net_pnl']:>12,.0f}")
    if s["cost_share_of_gross"] is not None:
        if s.get("gross_pnl_negative"):
            print(f"  GROSS P&L WAS NEGATIVE -- the spreads lost money before any charge. "
                  f"Costs added a further {s['cost_share_of_gross']:.1%} of that loss again.")
        else:
            print(f"  costs are {s['cost_share_of_gross']:.1%} of gross P&L")
    print(f"  exposure: mean gross Rs {s['mean_gross_exposure']:>11,.0f}, "
          f"mean |net| Rs {s['mean_abs_net_exposure']:>10,.0f} "
          f"({s['mean_net_over_gross']:.1%} of gross)")
    print(f"  AT MATCHED EXPOSURE: net {s['net_per_exposure_year']:>+7.2%}/yr of gross "
          f"exposure carried (gross of costs {s['gross_per_exposure_year']:>+7.2%}/yr); "
          f"deployment {s['deployment']:.2f}x capital")
    print(f"  per trade: mean R {s['mean_R']:>+.3%}  median R {s['median_R']:>+.3%}  "
          f"worst 1% R {s['worst_1pct_R']:>+.2%}")
    print(f"  worst 1% of trades: threshold Rs {s['worst_1pct_threshold']:>10,.0f}, "
          f"mean of that tail Rs {s['worst_1pct_mean']:>10,.0f}")
    print(f"  exits {s['exit_reasons']}   skipped {s['skipped']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--formation", type=int, default=756)
    ap.add_argument("--trading", type=int, default=126)
    ap.add_argument("--min-turnover", type=float, default=5e7)
    ap.add_argument("--all-symbols", action="store_true")
    ap.add_argument("--sims", type=int, default=200000)
    ap.add_argument("--min-bars", type=int, default=1200)
    ap.add_argument("--q", type=float, default=0.05)
    ap.add_argument("--correction", default="bh", choices=["bh", "bonferroni", "none"])
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--per-symbol", type=int, default=1)
    ap.add_argument("--z-in", type=float, default=2.0)
    ap.add_argument("--z-out", type=float, default=0.5)
    ap.add_argument("--z-stop", type=float, default=3.5)
    ap.add_argument("--hold-hl", type=float, default=4.0)
    ap.add_argument("--max-half-life", type=float, default=30.0)
    ap.add_argument("--capital", type=float, default=1_000_000.0)
    ap.add_argument("--gross-per-pair", type=float, default=200_000.0)
    ap.add_argument("--max-open", type=int, default=10)
    ap.add_argument("--card", default="nse-equity-futures")
    ap.add_argument("--collateral-yield", type=float, default=1.0)
    ap.add_argument("--log-prices", action="store_true")
    ap.add_argument("--no-baseline", action="store_true")
    ap.add_argument("--tag", default="base")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    dates, close, opn, vol = data.panel_ohlc(min_bars=args.min_bars)
    screens = load_screens(args.formation, args.trading, args.min_turnover,
                           not args.all_symbols, args.sims, len(dates),
                           args.log_prices)
    plan, kept = build_plan(screens, args.q, args.top_k, args.per_symbol,
                            args.correction, args.log_prices)
    print(f"pairs selected per window ({args.correction}, q={args.q}): {kept}  "
          f"total {sum(kept)}")

    rules = bt.TradeRules(z_in=args.z_in, z_out=args.z_out, z_stop=args.z_stop,
                          hold_cap_half_lives=args.hold_hl,
                          max_half_life=args.max_half_life)
    book = bt.BookRules(capital=args.capital, gross_per_pair=args.gross_per_pair,
                        max_open=args.max_open, card=args.card,
                        collateral_yield=args.collateral_yield)

    res = bt.simulate(plan, dates, close, opn, rules, book)
    summary = bt.summarise(res, book)
    report("cointegration-screened book", summary)

    payload = {"args": vars(args), "pairs_per_window": kept, "summary": summary,
               "equity": res.equity, "dates": res.dates,
               "gross_exposure": res.gross_exposure, "net_exposure": res.net_exposure,
               "margin_used": res.margin_used, "open_count": res.open_count,
               "trades": [t.__dict__ for t in res.trades]}

    # ---- significance ----------------------------------------------------
    eq = np.asarray(res.equity, float)
    if len(eq) > 10:
        rets = np.diff(eq) / eq[:-1]
        rf = np.asarray(charges.risk_free_growth(res.dates))
        rf_d = np.diff(rf) / rf[:-1]
        blk = max(5.0, summary["mean_bars_held"])
        ci = bootstrap.mean_ci(rets - rf_d, n_boot=2000, mean_block=blk)
        tb = bootstrap.trade_bootstrap(np.array([t.net_pnl for t in res.trades]))
        payload["bootstrap_daily_excess"] = ci
        payload["bootstrap_trades"] = tb
        print(f"\n  block bootstrap on DAILY EXCESS return "
              f"(mean block {blk:.0f} bars, 2,000 resamples)")
        print(f"    mean {ci['mean']*252:+.2%}/yr   95% CI "
              f"[{ci['lo']*252:+.2%}, {ci['hi']*252:+.2%}]   "
              f"{'EXCLUDES zero' if ci['excludes_zero'] else 'straddles zero'}")
        print(f"  bootstrap on TRADE net P&L (5,000 resamples)")
        print(f"    mean Rs {tb['mean']:+,.0f}   95% CI [Rs {tb['lo']:+,.0f}, "
              f"Rs {tb['hi']:+,.0f}]   "
              f"{'EXCLUDES zero' if tb['excludes_zero'] else 'straddles zero'}")

    # ---- date-matched random-pair baseline -------------------------------
    if not args.no_baseline:
        print("\nbuilding the date-matched random-pair baseline ...")
        rplan = random_plan(screens, dates, close, vol, args.formation,
                            args.min_turnover, not args.all_symbols,
                            args.top_k, args.per_symbol)
        rres = bt.simulate(rplan, dates, close, opn, rules, book)
        rsum = bt.summarise(rres, book)
        report("date-matched RANDOM-PAIR baseline (screen removed)", rsum)
        # The baseline must be bootstrapped too, or an 83-trade book's point
        # estimate gets compared with a 1,004-trade book's as though the two
        # were equally well determined. They are not.
        rtb = bootstrap.trade_bootstrap(np.array([t.net_pnl for t in rres.trades]))
        print(f"    bootstrap on baseline TRADE net P&L: mean Rs {rtb['mean']:+,.0f}  "
              f"95% CI [Rs {rtb['lo']:+,.0f}, Rs {rtb['hi']:+,.0f}]  "
              f"{'EXCLUDES zero' if rtb['excludes_zero'] else 'straddles zero'}")
        payload["baseline_random"] = {"summary": rsum, "equity": rres.equity,
                                      "dates": rres.dates, "bootstrap_trades": rtb}
        d = summary["excess_cagr"] - rsum["excess_cagr"]
        de = summary["net_per_exposure_year"] - rsum["net_per_exposure_year"]
        print(f"\n  screen contributes {d:+.2%} of excess CAGR, and {de:+.2%}/yr of "
              f"return on exposure, over random pairs")
        print(f"  (the second is the fair comparison: the screened book deployed "
              f"{summary['deployment']:.2f}x capital against the baseline's "
              f"{rsum['deployment']:.2f}x, so CAGR is not comparing like with like)")

    (OUT / f"backtest_{args.tag}.json").write_text(json.dumps(payload, default=float))
    print(f"\nwrote {OUT / f'backtest_{args.tag}.json'}")


if __name__ == "__main__":
    main()
