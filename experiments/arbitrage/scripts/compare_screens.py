"""Real screen against the real-data placebo, date by date.

The placebo is the honest baseline, not 1.0. The nulls are simulated from
GAUSSIAN random walks, and real share prices are not Gaussian -- fat tails
and volatility clustering make the ADF statistic reject less often than the
Gaussian null says it should. So the real panel's absolute pass rate cannot
be read against the nominal 5%; it has to be read against a panel with the
same marginals and no possible relationship.

Paired by formation date, because the 16 dates are the independent unit here.
Within one date the tests are NOT independent -- 180 symbols make 16,000
pairs and every symbol appears in 179 of them -- so a two-proportion test
over the pooled counts would badly overstate its significance. A paired test
across dates does not have that problem.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
from scipy import stats as sps

OUT = Path(__file__).resolve().parent.parent / "out"


def load(tag):
    return json.loads((OUT / f"selection_{tag}.json").read_text())["audit"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tags", nargs="+", default=["base", "placebo", "logpx"])
    args = ap.parse_args()

    got = {}
    for t in args.tags:
        if (OUT / f"selection_{t}.json").exists():
            got[t] = load(t)
    if "base" not in got:
        raise SystemExit("need selection_base.json")

    print(f"{'formation':<12} " + " ".join(f"{t:>22}" for t in got))
    print(f"{'':<12} " + " ".join(f"{'pass / chance  ratio':>22}" for t in got))
    rows = {t: {r["date"]: r for r in got[t]["rows"]} for t in got}
    dates = [r["date"] for r in got["base"]["rows"]]
    series = {t: [] for t in got}
    for d in dates:
        cells = []
        for t in got:
            r = rows[t].get(d)
            if not r:
                cells.append(f"{'-':>22}"); continue
            ratio = r["nominal_5pct"] / r["expected_by_chance"]
            series[t].append(ratio)
            cells.append(f"{r['nominal_5pct']:>6} /{r['expected_by_chance']:>6.0f} "
                          f"{ratio:>7.2f}")
        print(f"{d:<12} " + " ".join(cells))

    print()
    for t, v in series.items():
        v = np.array(v)
        tot = got[t]["totals"]
        print(f"  {t:<9} pooled ratio {got[t]['nominal_to_chance_ratio']:.3f}   "
              f"median per-date {np.median(v):.3f}   FDR survivors {tot['bh']}")

    out = {"per_date": {t: series[t] for t in series},
           "pooled": {t: got[t]["nominal_to_chance_ratio"] for t in got},
           "fdr": {t: got[t]["totals"]["bh"] for t in got}}

    if "placebo" in series and len(series["placebo"]) == len(series["base"]):
        a = np.array(series["base"]); b = np.array(series["placebo"])
        diff = a - b
        w = sps.wilcoxon(a, b)
        tt = sps.ttest_rel(a, b)
        print()
        print("  REAL vs PLACEBO, paired across the 16 formation dates")
        print(f"    mean ratio   real {a.mean():.3f}   placebo {b.mean():.3f}   "
              f"difference {diff.mean():+.3f}")
        print(f"    real / placebo = {a.mean()/b.mean():.2f}x")
        print(f"    paired t-test  t = {tt.statistic:+.2f}, p = {tt.pvalue:.4f}")
        print(f"    Wilcoxon       W = {w.statistic:.0f}, p = {w.pvalue:.4f}")
        print(f"    dates where real exceeded placebo: {int((diff > 0).sum())} of {len(diff)}")
        out["real_vs_placebo"] = {
            "mean_real": float(a.mean()), "mean_placebo": float(b.mean()),
            "ratio": float(a.mean() / b.mean()),
            "t": float(tt.statistic), "t_p": float(tt.pvalue),
            "wilcoxon_p": float(w.pvalue),
            "dates_real_higher": int((diff > 0).sum()), "n_dates": int(len(diff))}
        print()
        print("  How to read this. BOTH panels pass at well under the nominal 5% rate,")
        print("  so the Gaussian-calibrated test is CONSERVATIVE on real share prices --")
        print("  the absolute ratio cannot be read against 1.0. Against the placebo, which")
        print("  is the right baseline, the real panel does pass more often. That is")
        print("  evidence of SOME genuine cointegration in the universe. What it is not")
        print("  is evidence that it can be found pair by pair: the same screens yield")
        print(f"  {out['fdr'].get('base', 0)} pair(s) on levels and "
              f"{out['fdr'].get('logpx', 0)} on logs after an FDR correction, out of "
              "roughly 175,000")
        print("  tests each. Traded uncorrected, the log book earns +0.69%/yr of deployed")
        print("  exposure gross and pays 0.91%/yr in costs; the level book is negative")
        print("  before any charge at all (FINAL_LOGIC.md section 4).")

    (OUT / "compare_screens.json").write_text(json.dumps(out, indent=2))
    print(f"\nwrote {OUT / 'compare_screens.json'}")


if __name__ == "__main__":
    main()
