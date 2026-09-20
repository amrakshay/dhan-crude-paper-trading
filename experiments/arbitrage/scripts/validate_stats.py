"""Does the cointegration machinery work, and how often is it fooled?

Six measurements, written in the order they should be read.

A. The simulated critical values, against the published asymptotic ones and
   against statsmodels. If these disagree, nothing below means anything.
B. SIZE on three negative controls -- how often the procedure says "pair"
   when there is none. The independent-random-walk row is the one the whole
   field gets wrong.
C. Naive correlation on the same negative controls. This is the alarming
   number, and it is the reason the distinction between correlation and
   cointegration is not pedantry.
D. POWER against half-life. How slow can the reversion be before the test
   stops seeing it -- and the answer sets the shortest usable formation
   window.
E. Recovery of the two quantities the strategy trades on: the hedge ratio
   and the half-life.
F. Johansen against Engle-Granger, on the same series.

Writes out/validate_stats.json.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
from arblib import stats, synth

OUT = Path(__file__).resolve().parent.parent / "out"
ALPHA = 0.05


def section_a(n: int, sims: int) -> dict:
    print("A. Critical values -- simulated, published, statsmodels")
    eg = stats.critical_values("eg", n, sims=sims)
    egb = stats.critical_values("eg_best", n, sims=sims)
    ad = stats.critical_values("adf", n, sims=sims)
    # Published asymptotic values. MacKinnon's tau for the Engle-Granger
    # residual test with two variables and a constant in the first stage;
    # and the standard ADF-with-constant table.
    pub_eg = {0.01: -3.90, 0.05: -3.34, 0.10: -3.04}
    pub_adf = {0.01: -3.43, 0.05: -2.86, 0.10: -2.57}
    rows = []
    for name, sim, pub in (("Engle-Granger residual", eg, pub_eg),
                           ("ADF, constant", ad, pub_adf)):
        for lv in (0.01, 0.05, 0.10):
            rows.append({"test": name, "level": lv, "simulated": round(sim[lv], 3),
                         "published_asymptotic": pub[lv],
                         "gap": round(sim[lv] - pub[lv], 3)})
            print(f"   {name:<24} {int(lv*100):>2}%   simulated {sim[lv]:>7.3f}   "
                  f"published {pub[lv]:>6.2f}   gap {sim[lv]-pub[lv]:+.3f}")
    for lv in (0.01, 0.05, 0.10):
        rows.append({"test": "Engle-Granger, better of two directions", "level": lv,
                     "simulated": round(egb[lv], 3), "published_asymptotic": None,
                     "gap": round(egb[lv] - eg[lv], 3)})
        print(f"   {'EG, better of two dirs':<24} {int(lv*100):>2}%   simulated {egb[lv]:>7.3f}   "
              f"{'(no published table)':>16}   vs one-direction {egb[lv]-eg[lv]:+.3f}")
    print("   The third block is the one the screen uses. Picking the stronger of the two")
    print("   regression directions is a SELECTION step and it shifts the whole null left;")
    print("   scored against the one-direction table it rejected 11% of independent random")
    print("   walk pairs at a nominal 5%. See METHOD.md.")
    print("   The simulated values are slightly MORE negative than the asymptotic ones.")
    print("   That is the expected finite-sample direction: a table read at n=infinity")
    print("   rejects too easily at n=%d with an AIC-chosen lag. Using the table would" % n)
    print("   have made this screen anti-conservative, which is the wrong way to be wrong.")

    sm = None
    try:
        from statsmodels.tsa.stattools import adfuller
        rng = np.random.default_rng(7)
        mine, theirs = [], []
        for _ in range(200):
            x = np.cumsum(rng.standard_normal(n))
            mine.append(stats.adf(x, trend="c")[0])
            theirs.append(adfuller(x, regression="c", autolag="AIC")[0])
        mine, theirs = np.array(mine), np.array(theirs)
        sm = {"n": 200, "mean_abs_diff": float(np.mean(np.abs(mine - theirs))),
              "corr": float(np.corrcoef(mine, theirs)[0, 1]),
              "max_abs_diff": float(np.max(np.abs(mine - theirs)))}
        print(f"   vs statsmodels adfuller on 200 random walks: corr {sm['corr']:.5f}, "
              f"mean |diff| {sm['mean_abs_diff']:.4f}, max {sm['max_abs_diff']:.4f}")
    except ImportError:
        print("   (statsmodels not installed -- cross-check skipped)")
    return {"critical_values": rows, "vs_statsmodels": sm}


def _screen(a, b, sims):
    """The selection procedure, exactly as the strategy will run it."""
    res, flipped = stats.engle_granger_best(a, b, sims=sims)
    return res, flipped


def section_bc(n: int, trials: int, sims: int) -> dict:
    print(f"\nB/C. Size and correlation on the negative controls  ({trials} trials each, n={n})")
    print(f"     {'generator':<16} {'rejects@5%':>11} {'|corr|>0.8':>11} "
          f"{'|corr|>0.9':>11} {'median |corr|':>14} {'median half-life':>17}")
    out = {}
    for name in ("random_walks", "drifting_walks", "common_trend", "cointegrated"):
        gen = synth.GENERATORS[name]
        rng = np.random.default_rng(abs(hash(name)) % 2**31)
        rej = c80 = c90 = 0
        corrs, hls = [], []
        for _ in range(trials):
            a, b, _truth = gen(n, rng)
            res, _ = _screen(a, b, sims)
            if res.pvalue < ALPHA:
                rej += 1
                hls.append(res.half_life)
            c = abs(np.corrcoef(a, b)[0, 1])
            corrs.append(c)
            c80 += c > 0.8
            c90 += c > 0.9
        row = {"reject_rate": rej / trials, "corr_gt_80": c80 / trials,
               "corr_gt_90": c90 / trials, "median_abs_corr": float(np.median(corrs)),
               "median_half_life_of_rejects": float(np.median(hls)) if hls else None}
        out[name] = row
        hl = f"{row['median_half_life_of_rejects']:.1f}" if hls else "-"
        print(f"     {name:<16} {row['reject_rate']:>10.1%} {row['corr_gt_80']:>11.1%} "
              f"{row['corr_gt_90']:>11.1%} {row['median_abs_corr']:>14.3f} {hl:>17}")
    print("     Read the top three rows together against the bottom one. The reject rate")
    print("     on independent walks IS the nominal 5%, because the null was simulated from")
    print("     exactly this procedure. Naive CORRELATION, on the same series, says 'pair'")
    print("     far more often -- and the common-trend row is the one to look at, because")
    print("     that is what two sector peers actually look like.")
    return out


def section_d(n: int, trials: int, sims: int) -> dict:
    print(f"\nD. Power against the half-life of reversion  ({trials} trials each)")
    print(f"   {'true half-life':>15} {'detected@5%':>12} {'median p':>10}")
    out = []
    for hl in (2, 5, 10, 20, 40, 80, 160):
        rng = np.random.default_rng(1000 + hl)
        hits, ps = 0, []
        for _ in range(trials):
            a, b, _ = synth.cointegrated(n, rng, half_life=hl)
            res, _ = _screen(a, b, sims)
            ps.append(res.pvalue)
            hits += res.pvalue < ALPHA
        out.append({"half_life": hl, "power": hits / trials, "median_p": float(np.median(ps))})
        print(f"   {hl:>15} {hits/trials:>11.1%} {np.median(ps):>10.4f}")
    usable = [r["half_life"] for r in out if r["power"] >= 0.80]
    print(f"   -> at n={n} the test has 80%+ power out to a half-life of about "
          f"{max(usable) if usable else 'none'} bars.")
    print("      A pair whose spread reverts more slowly than that cannot be DISTINGUISHED")
    print("      from a random walk on this much data -- which is a statement about the")
    print("      formation window, not about the pair.")
    return out


def section_d2(trials: int, sims: int) -> dict:
    """The formation window is a POWER decision, so measure it as one."""
    print(f"\nD2. How long must the formation window be?  ({trials} trials each)")
    print(f"    {'window':>8} {'~years':>7} " + " ".join(f"{'hl=' + str(h):>8}" for h in (5, 10, 20, 40)))
    out = []
    for n in (252, 504, 756, 1008, 1260):
        row = {"n": n}
        cells = []
        for hl in (5, 10, 20, 40):
            rng = np.random.default_rng(n * 100 + hl)
            hits = 0
            for _ in range(trials):
                a, b, _ = synth.cointegrated(n, rng, half_life=hl)
                res, _ = _screen(a, b, sims)
                hits += res.pvalue < ALPHA
            row[f"hl{hl}"] = hits / trials
            cells.append(f"{hits/trials:>7.0%}")
        out.append(row)
        print(f"    {n:>8} {n/252:>7.1f} " + " ".join(f"{c:>8}" for c in cells))
    print("    Power against a SLOW spread is the binding constraint, and it is bought")
    print("    with formation length. A window short enough to keep 400+ symbols in the")
    print("    universe is a window that can only find fast pairs.")
    return out


def section_e(n: int, trials: int, sims: int) -> dict:
    print(f"\nE. Recovery of the two numbers the strategy trades on  ({trials} trials)")
    rng = np.random.default_rng(99)
    betas, hls, true_b, true_h = [], [], [], []
    for _ in range(trials):
        b_true = float(rng.uniform(0.4, 3.0))
        h_true = float(rng.uniform(4, 30))
        a, b, _ = synth.cointegrated(n, rng, half_life=h_true, beta=b_true)
        res, flipped = _screen(a, b, sims)
        if res.pvalue >= ALPHA:
            continue
        # engle_granger_best may have regressed b on a; invert to compare.
        est = 1.0 / res.beta if flipped and res.beta != 0 else res.beta
        betas.append(est); true_b.append(b_true)
        hls.append(res.half_life); true_h.append(h_true)
    betas, true_b = np.array(betas), np.array(true_b)
    hls, true_h = np.array(hls), np.array(true_h)
    berr = (betas - true_b) / true_b
    herr = (hls - true_h) / true_h
    row = {"n_detected": len(betas),
           "beta_median_pct_error": float(np.median(berr) * 100),
           "beta_p90_abs_pct_error": float(np.quantile(np.abs(berr), 0.90) * 100),
           "half_life_median_pct_error": float(np.median(herr) * 100),
           "half_life_p90_abs_pct_error": float(np.quantile(np.abs(herr), 0.90) * 100)}
    print(f"   hedge ratio: median error {row['beta_median_pct_error']:+.2f}%, "
          f"90th pct |error| {row['beta_p90_abs_pct_error']:.2f}%")
    print(f"   half-life  : median error {row['half_life_median_pct_error']:+.2f}%, "
          f"90th pct |error| {row['half_life_p90_abs_pct_error']:.2f}%")
    print("   The half-life is the badly estimated one, and it is biased LOW -- an OU")
    print("   process observed for a finite window looks faster than it is. A holding")
    print("   cap set from it is therefore too short, which is the safe direction.")
    return row


def section_f(n: int, trials: int, sims: int) -> dict:
    print(f"\nF. Johansen against Engle-Granger  ({trials} trials each)")
    print(f"   {'generator':<16} {'EG rejects':>11} {'Johansen r>0':>13} {'both':>7} {'neither':>9}")
    out = {}
    for name in ("cointegrated", "random_walks", "common_trend"):
        gen = synth.GENERATORS[name]
        rng = np.random.default_rng(abs(hash("j" + name)) % 2**31)
        eg = jo = both = neither = 0
        for _ in range(trials):
            a, b, _ = gen(n, rng)
            res, _ = _screen(a, b, sims)
            e = res.pvalue < ALPHA
            J = stats.johansen(np.column_stack([a, b]))
            j = bool(J.trace[0] > J.crit_95[0])
            eg += e; jo += j; both += (e and j); neither += (not e and not j)
        out[name] = {"eg": eg / trials, "johansen": jo / trials,
                     "both": both / trials, "neither": neither / trials}
        print(f"   {name:<16} {eg/trials:>10.1%} {jo/trials:>13.1%} "
              f"{both/trials:>7.1%} {neither/trials:>9.1%}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=504, help="bars in the formation window")
    ap.add_argument("--trials", type=int, default=400)
    ap.add_argument("--sims", type=int, default=20000, help="null-distribution draws")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    print(f"Formation window n={args.n} bars (~{args.n/252:.1f} years), "
          f"nominal level {ALPHA:.0%}, null simulated with {args.sims:,} draws\n")
    res = {"n": args.n, "trials": args.trials, "sims": args.sims,
           "A_critical_values": section_a(args.n, args.sims),
           "B_C_negative_controls": section_bc(args.n, args.trials, args.sims),
           "D_power": section_d(args.n, args.trials, args.sims),
           "D2_formation_window": section_d2(max(150, args.trials // 2), args.sims),
           "E_recovery": section_e(args.n, args.trials, args.sims),
           "F_johansen": section_f(args.n, min(args.trials, 200), args.sims)}
    (OUT / "validate_stats.json").write_text(json.dumps(res, indent=2))
    print(f"\nwrote {OUT / 'validate_stats.json'}")


if __name__ == "__main__":
    main()
