"""Cointegration, mean reversion, and the hedge ratio.

Everything a pair needs, written here rather than imported, for one reason
that turns out to matter: **the critical values are simulated, not looked
up.** Published ADF and Engle-Granger tables are asymptotic and assume a
fixed lag order. The procedure actually run here selects its lag by AIC on a
finite sample of a specific length, and the null distribution of *that*
statistic is not the one in the table. Simulating it means the p-value
describes the test that was run.

It is also the honest way round given what this experiment is for. The
handoff's warning is that two independent random walks look cointegrated
often enough to be alarming; a test whose size is *measured* on independent
random walks answers that directly, and a test whose size is *assumed* from
a table does not.

`scripts/validate_stats.py` checks the simulated critical values against the
published asymptotic ones and against statsmodels where it is installed.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"


# ---------------------------------------------------------------------------
# Least squares
# ---------------------------------------------------------------------------

def ols(y: np.ndarray, X: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (beta, residuals, standard errors). X must include its own
    constant column if one is wanted."""
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    n, k = X.shape
    dof = max(n - k, 1)
    s2 = float(resid @ resid) / dof
    try:
        cov = s2 * np.linalg.inv(X.T @ X)
        se = np.sqrt(np.clip(np.diag(cov), 0, None))
    except np.linalg.LinAlgError:                      # pragma: no cover
        se = np.full(k, np.nan)
    return beta, resid, se


# ---------------------------------------------------------------------------
# Augmented Dickey-Fuller
# ---------------------------------------------------------------------------

def _fit_adf(x: np.ndarray, lag: int, trend: str, rows: int | None = None):
    """One ADF regression at a fixed lag. `rows` trims the sample from the
    FRONT so several lag orders can be compared on identical observations."""
    n = len(x)
    dx = np.diff(x)
    avail = n - lag - 1
    if rows is None:
        rows = avail
    if rows < 12 or rows > avail:
        return None
    start = n - 1 - rows                     # index into dx of the first row used
    y = dx[start:]
    cols = [x[start:n - 1]]
    for j in range(1, lag + 1):
        cols.append(dx[start - j:n - 1 - j])
    if trend == "c":
        cols.append(np.ones(rows))
    X = np.column_stack(cols)
    beta, resid, se = ols(y, X)
    ssr = float(resid @ resid)
    if ssr <= 0 or not np.isfinite(se[0]) or se[0] <= 0:
        return None
    aic = rows * np.log(ssr / rows) + 2 * X.shape[1]
    return aic, float(beta[0] / se[0])


def _adf_stat(x: np.ndarray, maxlag: int, trend: str) -> tuple[float, int]:
    """The t-statistic on the level coefficient, with the lag chosen by AIC.

    `trend` is "n" (no deterministic term) or "c" (a constant). "c" is used
    for a raw series; "n" is used for an Engle-Granger residual, which is
    mean-zero by construction because the first-stage regression had an
    intercept. Fitting a second intercept to it is a real and common error --
    it costs power and it is not what the critical values are for.

    **AIC is compared on a FIXED sample.** Every lag order is fitted to the
    same `n - maxlag - 1` observations, because an information criterion
    computed on different numbers of rows is not comparable between models --
    a longer lag drops a row and is charged for it twice. Written the obvious
    way (refit each lag on its own maximal sample) this agreed with
    statsmodels only to a correlation of 0.987 and diverged by as much as
    0.72 in the statistic, which at the 5% critical value is the difference
    between a pair and no pair. The chosen lag is then refitted on its own
    full sample, which is the standard convention.
    """
    x = np.asarray(x, dtype=float)
    n = len(x)
    maxlag = max(0, min(maxlag, n - 14))
    best_aic, best_lag = np.inf, 0
    fixed_rows = n - maxlag - 1
    for lag in range(maxlag + 1):
        fit = _fit_adf(x, lag, trend, rows=fixed_rows)
        if fit is None:
            continue
        if fit[0] < best_aic:
            best_aic, best_lag = fit[0], lag
    final = _fit_adf(x, best_lag, trend)
    if final is None:
        return np.nan, 0
    return final[1], best_lag


def adf(x: np.ndarray, maxlag: int | None = None, trend: str = "c") -> tuple[float, int]:
    if maxlag is None:
        # Schwert's rule, the usual default.
        maxlag = int(np.ceil(12 * (len(x) / 100.0) ** 0.25))
        maxlag = min(maxlag, max(1, len(x) // 10))
    return _adf_stat(x, maxlag, trend)


# ---------------------------------------------------------------------------
# The simulated null
# ---------------------------------------------------------------------------

def _null_key(kind: str, n: int, sims: int) -> Path:
    h = hashlib.sha1(f"{kind}|{n}|{sims}".encode()).hexdigest()[:12]
    return CACHE / f"null_{kind}_{n}_{sims}_{h}.json"


@lru_cache(maxsize=32)
def null_distribution(kind: str, n: int, sims: int = 20000, seed: int = 20260920
                      ) -> np.ndarray:
    """The distribution of the test statistic under the null, by simulation.

    Three kinds:

    * "adf"     -- one random walk; the null is a unit root.
    * "eg"      -- two independent random walks, one regressed on the other
                   in a FIXED direction. The textbook Engle-Granger null.
    * "eg_best" -- the same, but taking the BETTER of the two directions,
                   which is what `engle_granger_best` does and therefore what
                   the screen actually runs.

    The third exists because of a measured bug. Scoring pairs with
    `engle_granger_best` and reading the p-value off the "eg" null rejected
    **11% of independent random walk pairs at a nominal 5%** -- taking the
    minimum of two correlated statistics is a selection step, and it roughly
    doubles the size. A screen over 125,000 candidates calibrated that way
    produces twice the spurious pairs its p-values claim. The fix is not a
    correction factor; it is to simulate the null of the procedure that is
    run.

    Cached to disk: 20,000 Engle-Granger simulations at n=756 take about a
    minute and are needed by every screen.
    """
    path = _null_key(kind, n, sims)
    if path.exists():
        return np.asarray(json.loads(path.read_text()), dtype=float)

    rng = np.random.default_rng(seed)
    stats = np.empty(sims)
    for i in range(sims):
        if kind == "adf":
            x = np.cumsum(rng.standard_normal(n))
            stats[i], _ = adf(x, trend="c")
        elif kind in ("eg", "eg_best"):
            a = np.cumsum(rng.standard_normal(n))
            b = np.cumsum(rng.standard_normal(n))
            X = np.column_stack([b, np.ones(n)])
            _, resid, _ = ols(a, X)
            s1, _ = adf(resid, trend="n")
            if kind == "eg":
                stats[i] = s1
            else:
                X2 = np.column_stack([a, np.ones(n)])
                _, resid2, _ = ols(b, X2)
                s2, _ = adf(resid2, trend="n")
                stats[i] = min(s1, s2)
        else:
            raise ValueError(kind)
    stats = stats[np.isfinite(stats)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stats.tolist()))
    return stats


def pvalue(stat: float, kind: str, n: int, sims: int = 20000) -> float:
    """Left-tail p-value against the simulated null. Both tests reject low."""
    if not np.isfinite(stat):
        return 1.0
    null = null_distribution(kind, n, sims)
    return float((np.sum(null <= stat) + 1) / (len(null) + 1))


def critical_values(kind: str, n: int, levels=(0.01, 0.05, 0.10),
                    sims: int = 20000) -> dict[float, float]:
    null = null_distribution(kind, n, sims)
    return {lv: float(np.quantile(null, lv)) for lv in levels}


# ---------------------------------------------------------------------------
# Engle-Granger
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Cointegration:
    beta: float           # hedge ratio: units of x per unit of y
    alpha: float          # intercept
    stat: float           # ADF t-statistic on the residual
    pvalue: float
    resid: np.ndarray
    half_life: float
    n: int

    @property
    def spread(self) -> np.ndarray:
        return self.resid


def engle_granger(y: np.ndarray, x: np.ndarray, sims: int = 20000,
                  null: str = "eg") -> Cointegration:
    """Regress y on x, then test the residual for a unit root.

    Direction matters and is NOT symmetric -- regressing y on x and x on y
    give different statistics on a finite sample. `engle_granger_best` picks
    the stronger direction, which is what the literature does and what the
    multiple-comparison count in `PAIRS_SELECTION.md` has to account for.
    """
    y, x = np.asarray(y, float), np.asarray(x, float)
    n = len(y)
    X = np.column_stack([x, np.ones(n)])
    beta, resid, _ = ols(y, X)
    stat, _ = adf(resid, trend="n")
    return Cointegration(
        beta=float(beta[0]), alpha=float(beta[1]), stat=float(stat),
        pvalue=pvalue(stat, null, n, sims), resid=resid,
        half_life=half_life(resid), n=n,
    )


def engle_granger_best(a: np.ndarray, b: np.ndarray, sims: int = 20000):
    """The stronger of the two directions, and which one it was.

    Returns `(result, flipped)`. `flipped` True means b was regressed on a,
    so the spread is `b - beta*a - alpha` and the position is long b / short
    beta units of a.
    """
    fwd = engle_granger(a, b, sims, null="eg_best")
    rev = engle_granger(b, a, sims, null="eg_best")
    return (fwd, False) if fwd.stat <= rev.stat else (rev, True)


# ---------------------------------------------------------------------------
# Mean reversion speed
# ---------------------------------------------------------------------------

def half_life(spread: np.ndarray) -> float:
    """Ornstein-Uhlenbeck half-life, in bars.

    Discretely: ds_t = lambda*(s_{t-1} - mu) + e_t, and the half-life is
    -ln(2)/ln(1+lambda). Returns inf when the fitted process does not revert
    (lambda >= 0), which is a legitimate answer and must not be clipped to a
    large number -- a pair whose spread does not revert should be REFUSED,
    not held for a long time.
    """
    s = np.asarray(spread, float)
    if len(s) < 10:
        return np.inf
    ds = np.diff(s)
    X = np.column_stack([s[:-1], np.ones(len(ds))])
    beta, _, _ = ols(ds, X)
    lam = float(beta[0])
    if lam >= 0 or (1.0 + lam) <= 0:
        return np.inf
    return float(-np.log(2.0) / np.log(1.0 + lam))


def hurst(x: np.ndarray, max_lag: int = 60) -> float:
    """Hurst exponent by the variance-of-differences method. <0.5 reverts."""
    x = np.asarray(x, float)
    lags = np.arange(2, min(max_lag, len(x) // 2))
    tau = np.array([np.std(x[l:] - x[:-l]) for l in lags])
    ok = tau > 0
    if ok.sum() < 4:
        return np.nan
    coeffs = np.polyfit(np.log(lags[ok]), np.log(tau[ok]), 1)
    return float(coeffs[0])


# ---------------------------------------------------------------------------
# Johansen
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Johansen:
    trace: np.ndarray            # trace statistic for r = 0, 1, ...
    eigenvalues: np.ndarray
    eigenvectors: np.ndarray     # columns, in the same order
    crit_95: np.ndarray          # Osterwald-Lenum, constant in the CE space


#: Osterwald-Lenum (1992) trace-statistic critical values at 95%, model with
#: an unrestricted constant, for n-r = 1..4. Only n=2 is used here (a pair),
#: so only the first two entries bind.
#: source (secondary): Osterwald-Lenum, "A Note with Quantiles of the
#:   Asymptotic Distribution of the ML Cointegration Rank Test Statistics",
#:   Oxford Bulletin of Economics and Statistics 54 (1992), Table 1*.
#: confidence: UNCONFIRMED against the original table, which was not
#:   obtained. The 2-variable values (15.41, 3.76) are the ones quoted
#:   identically in every reference checked.
#:
#: THESE ARE ASYMPTOTIC AND THEY ARE OVER-SIZED HERE. Measured on 200
#: independent random-walk pairs at n=504, the trace test rejects r=0 in
#: **14%** of cases at a nominal 5% (`scripts/validate_stats.py` section F).
#: That is the same finite-sample direction the ADF table showed, but much
#: larger, and it is why Johansen is used here ONLY as a secondary opinion on
#: a pair that Engle-Granger has already selected -- never as a selector of
#: its own. Simulating a Johansen null for the exact procedure would fix it;
#: it was not done, because nothing in the strategy depends on the trace
#: test's own p-value.
TRACE_95 = np.array([15.41, 3.76, 29.68, 47.21])


def johansen(Y: np.ndarray, k_ar_diff: int = 1) -> Johansen:
    """Reduced-rank regression, constant in the cointegrating space.

    Included because the handoff asks for the distinction to be made, and
    because for TWO series it is a genuine cross-check on Engle-Granger
    rather than a restatement: Johansen is symmetric in the two legs, where
    Engle-Granger has to pick a direction and gives a different answer
    depending which. Where they disagree on a pair, the pair is marginal.
    """
    Y = np.asarray(Y, float)
    T, n = Y.shape
    dY = np.diff(Y, axis=0)
    lagY = Y[k_ar_diff:-1] if k_ar_diff else Y[:-1]
    Z = dY[k_ar_diff:]
    lags = [dY[k_ar_diff - j:-j if j else None] for j in range(1, k_ar_diff + 1)]
    W = np.column_stack(lags + [np.ones(len(Z))]) if lags else np.ones((len(Z), 1))

    def resid_on(M):
        beta, *_ = np.linalg.lstsq(W, M, rcond=None)
        return M - W @ beta

    R0, R1 = resid_on(Z), resid_on(lagY)
    T0 = len(R0)
    S00 = R0.T @ R0 / T0
    S11 = R1.T @ R1 / T0
    S01 = R0.T @ R1 / T0

    S11inv_half = np.linalg.inv(np.linalg.cholesky(S11))
    M = S11inv_half @ S01.T @ np.linalg.inv(S00) @ S01 @ S11inv_half.T
    vals, vecs = np.linalg.eigh((M + M.T) / 2)
    order = np.argsort(vals)[::-1]
    vals, vecs = np.clip(vals[order], 0, 0.999999), vecs[:, order]
    beta = S11inv_half.T @ vecs

    trace = np.array([-T0 * np.sum(np.log(1.0 - vals[r:])) for r in range(n)])
    return Johansen(trace=trace, eigenvalues=vals, eigenvectors=beta,
                    crit_95=TRACE_95[:n])
