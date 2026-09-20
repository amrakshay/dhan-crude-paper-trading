"""Pair selection: the universe, the screen, and the multiple-comparison count.

The rule that governs this file is **causality**. A pair is selected on a
formation window and traded on a disjoint later window, and nothing computed
on the trading window may reach back into the selection. Selecting on the
full history is the single most common error in published pairs research and
it is not a subtle one: it guarantees that every pair reverted, because that
is what it was chosen for.

The second rule is that **the screen's size is reported, not assumed**.
Screening the F&O-eligible universe is tens of thousands of hypothesis tests.
At a nominal 5% that is thousands of spurious pairs before any real one, and
no amount of care downstream recovers from selecting on them.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import numpy as np

from . import data, stats

#: Draws for the per-leg I(1) pre-test's null. See `screen`.
I1_SIMS = 20000


# ---------------------------------------------------------------------------
# The universe, as of a formation date
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UniverseSpec:
    formation_bars: int = 756          # ~3 years
    min_price: float = 30.0            # rupees, median over the window
    min_turnover: float = 5e7          # rupees/day, MEDIAN over the window
    fno_only: bool = True              # the overnight-short constraint
    max_missing: float = 0.02          # share of the window a symbol may miss
    log_prices: bool = False
    # Cointegrate LOG prices rather than levels. Both specifications are used
    # in the literature and they are DIFFERENT STRATEGIES, not two views of
    # one. A level spread `Pb - beta*Pa` is held as a fixed SHARE ratio and
    # needs no rebalancing; a log spread `ln Pb - beta*ln Pa` is a fixed
    # VALUE ratio and drifts out of hedge as soon as the prices move, so
    # holding it means rebalancing, which means paying costs again. Levels
    # are the default here because they are what a daily-bar strategy can
    # actually hold; logs are screened as an alternative and reported.


def eligible(dates, close, vol, end_idx: int, spec: UniverseSpec,
             fno: set[str] | None = None) -> list[str]:
    """Symbols tradeable as at bar `end_idx`, using bars 0..end_idx only.

    `end_idx` is the LAST bar of the formation window and is inclusive. The
    trading window starts at `end_idx + 1`, so nothing here has seen it.
    """
    a = end_idx + 1 - spec.formation_bars
    if a < 0:
        return []
    fno = fno if fno is not None else (data.fno_eligible() if spec.fno_only else None)
    out = []
    for sym, px in close.items():
        if spec.fno_only and fno is not None and sym not in fno:
            continue
        w = px[a:end_idx + 1]
        miss = np.isnan(w)
        if miss.mean() > spec.max_missing or np.isnan(w[-1]) or np.isnan(w[0]):
            continue
        good = w[~miss]
        if np.median(good) < spec.min_price:
            continue
        v = vol[sym][a:end_idx + 1]
        turn = good * v[~miss]
        if np.median(turn) < spec.min_turnover:
            continue
        # A symbol whose formation window contains an unadjusted corporate
        # break carries a step change the regression will read as a level
        # shift. Those dates are enumerated in data.EXTREME_MOVES.
        if _spans_break(sym, dates[a], dates[end_idx]):
            continue
        out.append(sym)
    return sorted(out)


def _spans_break(sym: str, d0, d1) -> bool:
    for ds in data.EXTREME_MOVES.get(sym, ()):
        d = np.datetime64(ds)
        if d0 <= d <= d1:
            return True
    return False


# ---------------------------------------------------------------------------
# The screen
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    a: str                 # the leg regressed ON (the independent leg)
    b: str                 # the dependent leg: b = alpha + beta*a + spread
    beta: float
    alpha: float
    stat: float
    pvalue: float
    half_life: float
    mu: float              # spread mean over the formation window
    sigma: float           # spread s.d. over the formation window
    n: int
    log_prices: bool = False

    def spread(self, pb: np.ndarray, pa: np.ndarray) -> np.ndarray:
        if self.log_prices:
            return np.log(pb) - self.beta * np.log(pa) - self.alpha
        return pb - self.beta * pa - self.alpha

    def zscore(self, pb, pa) -> np.ndarray:
        """FROZEN standardisation: mu and sigma come from formation and never
        move. Recomputing them on a trailing trading window would let a spread
        that has drifted away redefine its own mean until it looks normal --
        which hides exactly the structural break the stop exists to catch."""
        return (self.spread(pb, pa) - self.mu) / self.sigma


def screen(close, end_idx: int, syms: list[str], spec: UniverseSpec,
           sims: int = 20000, max_half_life: float | None = None,
           progress=None, i1_alpha: float = 0.10) -> tuple[list[Candidate], dict]:
    """Every pair of `syms`, tested on the formation window.

    Returns `(candidates, audit)`. `audit` carries the number of tests, which
    is the number the p-values have to be corrected for.

    **Each leg must first fail to reject a unit root of its own.**
    Cointegration is a property of two I(1) series: it says a linear
    combination of two individually non-stationary series is stationary. If a
    leg is ALREADY stationary over the window -- a share that traded sideways
    for three years -- then the residual of any regression on it is stationary
    too, and the Engle-Granger test fires on a pair that has no long-run
    relationship at all, only two flat lines.

    Omitting this pre-test is a standard and expensive mistake, and it was
    caught here by the real-data placebo (`METHOD.md`): a panel of scrambled
    real prices, in which no genuine relationship can exist, produced MORE
    FDR-surviving "cointegrated" pairs than the real panel did. The excess was
    sideways windows pairing with each other.

    `i1_alpha` is the level at which a leg is rejected as already stationary.
    It is deliberately loose (0.10 rather than 0.05): here a false rejection
    only costs a candidate, while a false acceptance manufactures one.

    The pre-test uses its OWN, smaller null (`I1_SIMS`) rather than the
    pair screen's. The pair p-values need resolution down to 1e-5 because
    they are corrected across tens of thousands of tests; a single threshold
    at 10% needs nothing of the sort, and building a 200,000-draw ADF null
    inside every worker turned a three-minute screen into an hour of
    regenerating a distribution that was never read below its tenth
    percentile.
    """
    a0 = end_idx + 1 - spec.formation_bars
    cols = {s: close[s][a0:end_idx + 1] for s in syms}
    if spec.log_prices:
        cols = {s: np.log(v) for s, v in cols.items()}
    # Fill the small number of permitted gaps by carrying the last price --
    # inside the FORMATION window only, where a stale price biases the hedge
    # ratio slightly and cannot manufacture a trade. The trading window is
    # never filled; a missing bar there means no fill, which is the truth.
    for s, v in cols.items():
        cols[s] = _ffill(v)

    # The I(1) pre-condition, once per symbol rather than once per pair.
    i1, dropped = [], []
    for s in syms:
        stat, _ = stats.adf(cols[s], trend="c")
        if stats.pvalue(stat, "adf", len(cols[s]), I1_SIMS) < i1_alpha:
            dropped.append(s)          # already stationary -- not an I(1) leg
        else:
            i1.append(s)
    syms = i1

    out, tested = [], 0
    pairs = list(combinations(syms, 2))
    for i, (x, y) in enumerate(pairs):
        tested += 1
        res, flipped = stats.engle_granger_best(cols[x], cols[y], sims=sims)
        # `engle_granger_best(u, v)` regresses u ON v and reports flipped=False;
        # flipped=True means it took the other direction, v on u. The DEPENDENT
        # leg is the one the spread is quoted in, so it is x when not flipped.
        # Written the other way round -- which it was -- every spread is built
        # from the wrong regression: beta and alpha belong to one ordering and
        # the prices to the other. The p-values are unaffected (the statistic is
        # the same number either way), so the multiple-comparison audit stayed
        # correct while every trade was wrong. It was caught by the synthetic
        # backtest: the engine found the planted pairs and then LOST money on
        # them, and a spread whose z-score had mean -3.2 and s.d. 1.3 over its
        # own formation window is not a standardisation error, it is a different
        # series. See METHOD.md.
        dep, ind = (x, y) if not flipped else (y, x)
        if max_half_life is not None and not (0 < res.half_life <= max_half_life):
            continue
        out.append(Candidate(
            a=ind, b=dep, beta=res.beta, alpha=res.alpha, stat=res.stat,
            pvalue=res.pvalue, half_life=res.half_life,
            mu=float(np.mean(res.resid)), sigma=float(np.std(res.resid, ddof=1)),
            n=res.n, log_prices=spec.log_prices))
        if progress and i % progress == 0:
            print(f"      {i:>6}/{len(pairs)} pairs", flush=True)
    return out, {"symbols": len(syms), "tests": tested,
                 "dropped_stationary": len(dropped),
                 "dropped_names": sorted(dropped)[:40],
                 "p_resolution": 1.0 / (sims + 1)}


def _ffill(v: np.ndarray) -> np.ndarray:
    v = v.copy()
    idx = np.where(~np.isnan(v), np.arange(len(v)), 0)
    np.maximum.accumulate(idx, out=idx)
    return v[idx]


# ---------------------------------------------------------------------------
# Multiple comparisons
# ---------------------------------------------------------------------------

def benjamini_hochberg(pvals: np.ndarray, q: float = 0.05) -> np.ndarray:
    """Boolean mask of discoveries controlling the false discovery rate at q.

    FDR rather than Bonferroni, for a reason worth stating. Bonferroni
    controls the chance of even ONE false pair and, over 20,000 tests, admits
    almost nothing -- and its threshold, alpha/N, falls BELOW the resolution
    of a 20,000-draw simulated null, so the number it would produce is an
    artefact of the simulation rather than a measurement. FDR answers the
    question a portfolio actually asks: of the pairs I trade, what share are
    noise?
    """
    p = np.asarray(pvals, float)
    n = len(p)
    if n == 0:
        return np.zeros(0, bool)
    order = np.argsort(p)
    ranked = p[order]
    thresh = q * (np.arange(1, n + 1) / n)
    passed = ranked <= thresh
    k = np.max(np.where(passed)[0]) + 1 if passed.any() else 0
    mask = np.zeros(n, bool)
    if k:
        mask[order[:k]] = True
    return mask


def bonferroni(pvals: np.ndarray, alpha: float = 0.05) -> np.ndarray:
    p = np.asarray(pvals, float)
    return p <= alpha / max(1, len(p))


def disjoint_by_symbol(cands: list[Candidate], limit_per_symbol: int = 1
                       ) -> list[Candidate]:
    """Keep the strongest pairs while capping how often any symbol appears.

    Without this the screen returns the same two or three names paired with
    everything, and a book of twenty "diversified" pairs is one bet. Applied
    after the correction, greedily, strongest first.
    """
    seen: dict[str, int] = {}
    out = []
    for c in sorted(cands, key=lambda c: c.pvalue):
        if seen.get(c.a, 0) >= limit_per_symbol or seen.get(c.b, 0) >= limit_per_symbol:
            continue
        seen[c.a] = seen.get(c.a, 0) + 1
        seen[c.b] = seen.get(c.b, 0) + 1
        out.append(c)
    return out
