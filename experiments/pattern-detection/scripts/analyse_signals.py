"""Read a scan's signals and ask whether anything in them is real.

Three questions, in order of how much they matter:

  1. Is the excess return distinguishable from zero? A mean is not a finding.
  2. Does the SCORE predict the outcome? If a detector's own confidence has
     no relationship to what happens next, the score is decoration and every
     threshold chosen using it is arbitrary.
  3. Does the pattern beat its own control group -- the same signals with
     the confirming evidence (volume, trend) absent?

On the t-statistics: forward windows OVERLAP, both within a symbol and
across symbols on the same day, so the observations are not independent and
the naive t is too generous. A block bootstrap by CALENDAR MONTH is reported
alongside, which resamples whole months and so keeps same-day clustering
intact. Where the two disagree, believe the bootstrap.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np

OUT = Path(__file__).resolve().parent.parent / "out"


def boot_ci(x: np.ndarray, blocks: list[str], n: int = 2000, seed: int = 7):
    """Block bootstrap over calendar months."""
    x = np.asarray(x, float)
    ok = np.isfinite(x)
    x, blocks = x[ok], list(np.asarray(blocks)[ok])
    if len(x) < 20:
        return float("nan"), float("nan")
    idx = defaultdict(list)
    for i, b in enumerate(blocks):
        idx[b].append(i)
    keys = list(idx)
    rng = np.random.default_rng(seed)
    means = np.empty(n)
    for k in range(n):
        pick = rng.choice(len(keys), size=len(keys), replace=True)
        sel = np.concatenate([idx[keys[j]] for j in pick])
        means[k] = x[sel].mean()
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="real_strict_D")
    ap.add_argument("--horizon", type=int, default=20)
    ap.add_argument("--long-only", action="store_true",
                    help="score every signal as a long, ignoring direction")
    a = ap.parse_args()
    sigs = json.loads((OUT / f"{a.tag}_signals.json").read_text())
    h = a.horizon
    key = f"dexc{h}" if not a.long_only else f"exc{h}"
    L = [f"{a.tag}: {len(sigs)} signals, {h}-bar excess return over the "
         f"date-matched average of every symbol"
         + ("  [scored long-only]" if a.long_only else "  [signed to the "
            "direction the pattern implies]"), ""]

    L.append(f"{'group':<30}{'n':>6}{'mean':>9}{'median':>9}{'t':>7}"
             f"{'boot 95% CI':>22}{'win%':>7}")

    def row(label, g):
        x = np.array([s[key] for s in g], float)
        ok = np.isfinite(x)
        x = x[ok]
        if len(x) < 5:
            return
        months = [s["signal_date"][:7] for s, k in zip(g, ok) if k]
        t = x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if x.std() > 0 else 0
        lo, hi = boot_ci(x, months)
        L.append(f"{label:<30}{len(x):>6}{x.mean():>9.2%}{np.median(x):>9.2%}"
                 f"{t:>7.2f}{f'[{lo:+.2%}, {hi:+.2%}]':>22}{(x > 0).mean():>7.0%}")

    by_pat = defaultdict(list)
    for s in sigs:
        by_pat[s["pattern"]].append(s)
        if s["pattern"] == "triangle":
            by_pat[f"triangle:{s['variant']}"].append(s)
    for pat in sorted(by_pat):
        g = by_pat[pat]
        row(pat, g)
        # does the score predict?
        sc = np.array([s["score"] for s in g])
        if len(set(np.round(sc, 3))) > 3:
            cut = np.percentile(sc, [50])
            row(f"   score <= {cut[0]:.2f}", [s for s in g if s["score"] <= cut[0]])
            row(f"   score >  {cut[0]:.2f}", [s for s in g if s["score"] > cut[0]])
            x = np.array([s[key] for s in g], float)
            m = np.isfinite(x)
            if m.sum() > 20:
                r = np.corrcoef(sc[m], x[m])[0, 1]
                L.append(f"   {'score/outcome correlation':<27}{r:>+9.3f}")
        L.append("")

    txt = "\n".join(L)
    print(txt)
    (OUT / f"{a.tag}_analysis_h{h}{'_long' if a.long_only else ''}.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
