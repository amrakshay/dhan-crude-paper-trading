"""Pre-compute the simulated null distributions, in parallel.

The p-values in this experiment come from simulating the test statistic under
the null, so the smallest p-value the screen can report is `1/(sims+1)`. That
matters for the multiple-comparison correction: screening ~16,000 pairs,
Benjamini-Hochberg's threshold for the k-th strongest pair is `k*q/N`, which
for small k falls below the resolution of a 20,000-draw null. A correction
evaluated against a floor is an artefact of the simulation, not a
measurement.

So the nulls the backtest uses are built at 200,000 draws -- resolution
5e-6 -- once, here, across cores. Roughly ten minutes on twelve cores;
the results are cached to `data/cache/` and every later run loads them.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
from arblib import stats


def _chunk(args) -> list[float]:
    kind, n, count, seed = args
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(count):
        if kind == "adf":
            out.append(stats.adf(np.cumsum(rng.standard_normal(n)), trend="c")[0])
            continue
        a = np.cumsum(rng.standard_normal(n))
        b = np.cumsum(rng.standard_normal(n))
        _, r1, _ = stats.ols(a, np.column_stack([b, np.ones(n)]))
        s1, _ = stats.adf(r1, trend="n")
        if kind == "eg":
            out.append(s1)
        else:
            _, r2, _ = stats.ols(b, np.column_stack([a, np.ones(n)]))
            s2, _ = stats.adf(r2, trend="n")
            out.append(min(s1, s2))
    return [float(v) for v in out if np.isfinite(v)]


def build(kind: str, n: int, sims: int, workers: int, seed: int = 20260920) -> Path:
    path = stats._null_key(kind, n, sims)
    if path.exists():
        print(f"   {kind} n={n} sims={sims:,}  already cached")
        return path
    per = sims // workers
    jobs = [(kind, n, per + (sims % workers if i == 0 else 0), seed + i * 7919)
            for i in range(workers)]
    got: list[float] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for part in ex.map(_chunk, jobs):
            got.extend(part)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(got))
    q = np.quantile(got, [0.01, 0.05, 0.10])
    print(f"   {kind} n={n} sims={sims:,}  1% {q[0]:.3f}  5% {q[1]:.3f}  10% {q[2]:.3f}  "
          f"-> {path.name}")
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sims", type=int, default=200000)
    ap.add_argument("--windows", type=int, nargs="+", default=[504, 756, 1008])
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()
    print(f"Building simulated nulls at {args.sims:,} draws "
          f"(p-value resolution {1/(args.sims+1):.1e})")
    for n in args.windows:
        build("eg_best", n, args.sims, args.workers)


if __name__ == "__main__":
    main()
