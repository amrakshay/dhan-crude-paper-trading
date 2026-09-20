"""Walk-forward pair selection, and the multiple-comparison accounting.

At each formation date the whole eligible universe is screened -- every
unordered pair -- and the result is cached to `data/cache/screen_*.json`.
Nothing about the trading window is visible to any of it.

The output that matters as much as the pairs is the AUDIT: how many
hypotheses were tested, how many the nominal 5% would pass, how many survive
a false-discovery-rate correction, and what share of the survivors the
correction implies are still noise.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
from arblib import data, pairs, stats

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
OUT = ROOT / "out"


def plan_windows(n_dates: int, formation: int, trading: int) -> list[tuple[int, int, int]]:
    """`(formation_end, trade_start, trade_end)`, disjoint and in order."""
    out = []
    end = formation - 1
    while end + trading < n_dates:
        out.append((end, end + 1, end + trading))
        end += trading
    return out


def _screen_one(args) -> dict:
    # Belt and braces: the spawned child runs _bootstrap on import, but pin
    # again here in case numpy was already initialised. See _bootstrap.py.
    import os
    for v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
              "VECLIB_MAXIMUM_THREADS"):
        os.environ[v] = "1"
    (end_idx, formation, min_turnover, fno_only, sims, min_bars, log_prices,
     placebo_seed) = args
    dates, close, vol = data.panel(min_bars=min_bars)
    spec = pairs.UniverseSpec(formation_bars=formation, min_turnover=min_turnover,
                              fno_only=fno_only, log_prices=log_prices)
    syms = pairs.eligible(dates, close, vol, end_idx, spec)
    if placebo_seed is not None:
        close = _scramble(close, syms, end_idx, formation, placebo_seed)
    cands, audit = pairs.screen(close, end_idx, syms, spec, sims=sims)
    return {
        "end_idx": end_idx, "end_date": str(dates[end_idx]), "audit": audit,
        "candidates": [
            {"a": c.a, "b": c.b, "beta": c.beta, "alpha": c.alpha, "stat": c.stat,
             "pvalue": c.pvalue, "half_life": (c.half_life if np.isfinite(c.half_life)
                                               else None),
             "mu": c.mu, "sigma": c.sigma, "n": c.n}
            for c in cands],
    }


def _scramble(close, syms, end_idx, formation, seed):
    """THE REAL-DATA PLACEBO.

    Replace each symbol's formation window with a window of ITS OWN history
    from a different, randomly chosen date. Every series keeps the statistical
    properties that matter -- fat tails, volatility clustering, the actual
    price level and drift of that name -- and no two series can be genuinely
    related any more, because they are no longer contemporaneous.

    This is the control the synthetic negative controls cannot provide. Those
    test the procedure against GAUSSIAN random walks; real equity prices are
    not Gaussian, and a test calibrated on Gaussian walks could be mis-sized
    on real ones in either direction. Whatever pass rate this placebo
    produces is the rate the real screen has to BEAT to have found anything.
    """
    rng = np.random.default_rng(seed)
    a0 = end_idx + 1 - formation
    out = dict(close)
    for s in syms:
        full = close[s]
        ok = np.where(np.isfinite(full))[0]
        if len(ok) < formation + 20:
            continue
        lo, hi = ok[0], ok[-1] - formation
        if hi <= lo:
            continue
        start = int(rng.integers(lo, hi))
        w = full[start:start + formation]
        if not np.isfinite(w).all():
            continue
        v = full.copy()
        v[a0:end_idx + 1] = w
        out[s] = v
    return out


def cache_path(end_idx, formation, min_turnover, fno_only, sims,
               log_prices=False, placebo_seed=None) -> Path:
    suffix = ("_log" if log_prices else "") + (
        f"_placebo{placebo_seed}" if placebo_seed is not None else "")
    # v2: the dependent/independent labels were swapped and neither leg was
    # tested for its own unit root. Both are fixed; the version marker makes
    # a stale cache impossible to pick up silently.
    return CACHE / (f"screen_v2_e{end_idx}_f{formation}_t{int(min_turnover)}"
                    f"_{'fno' if fno_only else 'all'}_s{sims}{suffix}.json")


def run(formation: int, trading: int, min_turnover: float, fno_only: bool,
        sims: int, workers: int, min_bars: int, limit: int | None,
        log_prices: bool = False, placebo_seed: int | None = None) -> list[dict]:
    dates, close, vol = data.panel(min_bars=min_bars)
    windows = plan_windows(len(dates), formation, trading)
    if limit:
        windows = windows[:limit]
    print(f"{len(windows)} formation dates, {formation}-bar formation "
          f"({formation/252:.1f}y), {trading}-bar trading window "
          f"({trading/252:.1f}y)")
    print(f"  trading spans {dates[windows[0][1]]} .. {dates[windows[-1][2]]}")

    todo, done = [], {}
    for end_idx, _, _ in windows:
        p = cache_path(end_idx, formation, min_turnover, fno_only, sims,
                       log_prices, placebo_seed)
        if p.exists():
            done[end_idx] = json.loads(p.read_text())
        else:
            todo.append((end_idx, formation, min_turnover, fno_only, sims,
                         min_bars, log_prices, placebo_seed))
    print(f"  {len(done)} cached, {len(todo)} to screen")

    if todo:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for r in ex.map(_screen_one, todo):
                done[r["end_idx"]] = r
                cache_path(r["end_idx"], formation, min_turnover, fno_only, sims,
                           log_prices, placebo_seed).write_text(json.dumps(r))
                print(f"    screened {r['end_date']}: {r['audit']['symbols']} symbols, "
                      f"{r['audit']['tests']:,} tests", flush=True)
    return [done[e] for e, _, _ in windows]


def audit_table(screens: list[dict], q: float) -> dict:
    print(f"\nMultiple comparisons  (Benjamini-Hochberg, q={q:.0%})")
    print(f"  {'formation':<12} {'syms':>5} {'tests':>8} {'p<5%':>7} "
          f"{'expected':>9} {'BH pass':>8} {'est. FDR':>9} {'at floor':>9}")
    rows, tot = [], {"tests": 0, "nominal": 0, "bh": 0}
    for s in screens:
        p = np.array([c["pvalue"] for c in s["candidates"]])
        n = s["audit"]["tests"]
        floor = s["audit"]["p_resolution"]
        nominal = int((p < 0.05).sum())
        bh = pairs.benjamini_hochberg(p, q=q)
        n_bh = int(bh.sum())
        at_floor = int((p[bh] <= floor * 1.0001).sum()) if n_bh else 0
        row = {"date": s["end_date"], "symbols": s["audit"]["symbols"], "tests": n,
               "nominal_5pct": nominal, "expected_by_chance": round(0.05 * n, 1),
               "bh_pass": n_bh, "expected_false_among_bh": round(q * n_bh, 1),
               "at_p_floor": at_floor}
        rows.append(row)
        tot["tests"] += n; tot["nominal"] += nominal; tot["bh"] += n_bh
        row["dropped_stationary"] = s["audit"].get("dropped_stationary", 0)
        print(f"  {s['end_date']:<12} {row['symbols']:>5} {n:>8,} {nominal:>7} "
              f"{row['expected_by_chance']:>9.0f} {n_bh:>8} "
              f"{row['expected_false_among_bh']:>9.1f} {at_floor:>9}")
    print(f"  {'TOTAL':<12} {'':>5} {tot['tests']:>8,} {tot['nominal']:>7} "
          f"{0.05*tot['tests']:>9.0f} {tot['bh']:>8}")
    print()
    print(f"  Read the two middle columns first. Across every formation date the screen")
    print(f"  ran {tot['tests']:,} hypothesis tests; at a nominal 5% about "
          f"{0.05*tot['tests']:,.0f} pairs")
    print(f"  pass BY CHANCE ALONE, against {tot['nominal']:,} that actually did. If that")
    ratio = tot['nominal'] / (0.05 * tot['tests']) if tot['tests'] else 0
    print(f"  ratio ({ratio:.2f}x) is near 1, the nominal screen has found nothing a coin")
    print(f"  could not have found, and selecting the 'best' pairs from it is selecting")
    print(f"  the luckiest noise. {tot['bh']:,} survive the FDR correction.")
    return {"rows": rows, "totals": tot, "nominal_to_chance_ratio": ratio}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--formation", type=int, default=756)
    ap.add_argument("--trading", type=int, default=126)
    ap.add_argument("--min-turnover", type=float, default=5e7)
    ap.add_argument("--all-symbols", action="store_true",
                    help="drop the F&O-eligible restriction (NOT executable -- "
                         "see FEASIBILITY.md section 3)")
    ap.add_argument("--sims", type=int, default=200000)
    ap.add_argument("--workers", type=int, default=9)
    ap.add_argument("--min-bars", type=int, default=1200)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--q", type=float, default=0.05)
    ap.add_argument("--log-prices", action="store_true")
    ap.add_argument("--placebo-seed", type=int, default=None,
                    help="replace each formation window with the SAME symbol's "
                         "prices from a different date -- the real-data placebo")
    ap.add_argument("--tag", default="base")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    screens = run(args.formation, args.trading, args.min_turnover,
                  not args.all_symbols, args.sims, args.workers,
                  args.min_bars, args.limit, args.log_prices, args.placebo_seed)
    audit = audit_table(screens, args.q)
    (OUT / f"selection_{args.tag}.json").write_text(json.dumps(
        {"args": vars(args), "audit": audit}, indent=2))
    print(f"\nwrote {OUT / f'selection_{args.tag}.json'}")


if __name__ == "__main__":
    main()
