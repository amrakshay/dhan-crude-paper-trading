"""Black-76 pricing, implied volatility and greeks.

WHERE THIS IS AND IS NOT USED, because it decides whether the study is honest:

  USED     to back implied volatility OUT of a real traded price, so strikes can
           be chosen by delta; to reprice legs under SPAN's shifted scenarios,
           which is a margin question, not a P&L one; and inside the synthetic
           ground-truth harness, where the right answer is known by construction.

  NOT USED to produce any P&L number.  Every rupee of profit and loss in this
           study comes from a real traded or settled price in the NSE bhavcopy.

That separation is what keeps the variance risk premium *measured* rather than
assumed.  The handoff's longest warning was about synthesising option prices
with an IV assumption; because real prices were obtainable, none of it applies.

Black-76 on a FORWARD, not Black-Scholes on spot.  The forward is recovered from
put-call parity on the day's own option chain (see `forward_from_parity`), so
dividends, the repo rate and any futures basis are absorbed into an observed
number instead of being assumed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

SQRT_2PI = math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / SQRT_2PI


def _d1d2(f: float, k: float, t: float, sigma: float) -> tuple[float, float]:
    v = sigma * math.sqrt(t)
    d1 = (math.log(f / k) + 0.5 * sigma * sigma * t) / v
    return d1, d1 - v


def price(f: float, k: float, t: float, sigma: float, is_call: bool,
          r: float = 0.0) -> float:
    """Black-76 price of a European option on a forward `f`.

    `t` in years, `sigma` annualised.  `r` discounts to present value; pass 0.0
    for an undiscounted (forward) price.
    """
    if t <= 0 or sigma <= 0 or f <= 0 or k <= 0:
        intrinsic = max(f - k, 0.0) if is_call else max(k - f, 0.0)
        return intrinsic * math.exp(-r * max(t, 0.0))
    d1, d2 = _d1d2(f, k, t, sigma)
    df = math.exp(-r * t)
    if is_call:
        return df * (f * _norm_cdf(d1) - k * _norm_cdf(d2))
    return df * (k * _norm_cdf(-d2) - f * _norm_cdf(-d1))


def implied_vol(target: float, f: float, k: float, t: float, is_call: bool,
                r: float = 0.0, lo: float = 1e-4, hi: float = 8.0,
                tol: float = 1e-8, max_iter: int = 200,
                min_vega: float = 1.0) -> float | None:
    """Back sigma out of a traded price.  None when no sigma can produce it.

    Returns None rather than a boundary value in three cases, all of which
    occur in real bhavcopy data and all of which would otherwise manufacture a
    volatility surface where the market published none:

      * the price is below intrinsic, or above the forward -- common on
        untraded strikes carrying a stale close;
      * no sigma in [lo, hi] reproduces it;
      * **vega at the solution is below `min_vega`**, which is the
        deep-in-the-money and far-expired case.  An option 1500 points in the
        money with a week to run is worth its intrinsic at any volatility, so
        its "implied volatility" is whatever bracket the solver happened to
        land in.  Returning that number would put noise into every delta
        computed from it, and delta is how strikes get chosen.

    `min_vega` is in price units per 1.00 of sigma.  The default of 1.0 is
    permissive.  Callers working from REAL quotes should pass something scaled
    to the tick: for IV to be identifiable to half a volatility point off a
    price quoted in 0.05 steps, vega must exceed roughly 0.05 / 0.005 = 10.
    `chain.py` passes that explicitly rather than relying on this default.
    """
    if t <= 0 or f <= 0 or k <= 0 or target <= 0:
        return None
    df = math.exp(-r * t)
    intrinsic = (max(f - k, 0.0) if is_call else max(k - f, 0.0)) * df
    if target < intrinsic - 1e-9:
        return None
    upper = (f if is_call else k) * df
    if target > upper + 1e-9:
        return None

    f_lo = price(f, k, t, lo, is_call, r) - target
    f_hi = price(f, k, t, hi, is_call, r) - target
    if f_lo > 0 or f_hi < 0:
        return None

    # Bisect on SIGMA, not on price.  Stopping when the price residual is
    # small converges the wrong quantity: on a low-vega option a price accurate
    # to 1e-7 still leaves the volatility uncertain by basis points, because
    # vol error = price error / vega.
    a, b = lo, hi
    for _ in range(max_iter):
        if (b - a) < tol:
            break
        m = 0.5 * (a + b)
        if price(f, k, t, m, is_call, r) - target < 0:
            a = m
        else:
            b = m
    sol = 0.5 * (a + b)
    # vega is per 1.00 of sigma; below min_vega the price carries no volatility
    # information and the solution is an artefact of the bracket.
    if greeks(f, k, t, sol, is_call, r).vega < min_vega:
        return None
    return sol


@dataclass(frozen=True)
class Greeks:
    delta: float
    gamma: float
    vega: float      # per 1.00 of sigma (i.e. per 100 vol points)
    theta: float     # per year


def greeks(f: float, k: float, t: float, sigma: float, is_call: bool,
           r: float = 0.0) -> Greeks:
    if t <= 0 or sigma <= 0 or f <= 0 or k <= 0:
        itm = (f > k) if is_call else (f < k)
        return Greeks(delta=(1.0 if is_call else -1.0) if itm else 0.0,
                      gamma=0.0, vega=0.0, theta=0.0)
    d1, d2 = _d1d2(f, k, t, sigma)
    df = math.exp(-r * t)
    sqrt_t = math.sqrt(t)
    delta = df * (_norm_cdf(d1) if is_call else _norm_cdf(d1) - 1.0)
    gamma = df * _norm_pdf(d1) / (f * sigma * sqrt_t)
    vega = df * f * _norm_pdf(d1) * sqrt_t
    theta = -df * f * _norm_pdf(d1) * sigma / (2.0 * sqrt_t)
    return Greeks(delta=delta, gamma=gamma, vega=vega, theta=theta)


def forward_from_parity(chain: dict[float, tuple[float | None, float | None]],
                        t: float, r: float = 0.0,
                        min_strikes: int = 3) -> float | None:
    """Recover the forward from put-call parity:  F = K + (C - P) * e^(rT).

    `chain` maps strike -> (call price, put price).  The strike whose |C - P| is
    smallest is nearest the money and has the tightest parity, so it is used.

    This is what lets the study price options without assuming a dividend yield
    or a repo rate: whatever the market's carry actually was that day is already
    inside C - P.  Returns None when too few strikes have BOTH sides quoted.
    """
    usable = [(k, c, p) for k, (c, p) in chain.items()
              if c is not None and p is not None and c > 0 and p > 0]
    if len(usable) < min_strikes:
        return None
    k, c, p = min(usable, key=lambda x: abs(x[1] - x[2]))
    return k + (c - p) * math.exp(r * t)


__all__ = ["price", "implied_vol", "greeks", "Greeks", "forward_from_parity"]
