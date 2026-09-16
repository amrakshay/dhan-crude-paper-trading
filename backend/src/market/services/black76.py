"""Black-76 pricing for options on futures.

Used ONLY by the synthetic feed, to generate a chain that is internally
consistent (puts and calls that respect parity, greeks that match the prices).

Real greeks and IV come from Dhan's option chain endpoint, never from here --
see src/market/services/greeks_poller.py. Black-76 rather than Black-Scholes
because MCX OPTFUT contracts are options ON the futures contract, so there is
no spot/carry term: the future is the underlying.
"""
import math
from typing import Dict

SQRT_2PI = math.sqrt(2.0 * math.pi)


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / SQRT_2PI


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _d1_d2(future: float, strike: float, years: float, vol: float) -> tuple:
    variance = vol * math.sqrt(years)
    d1 = (math.log(future / strike) + 0.5 * vol * vol * years) / variance
    return d1, d1 - variance


def price(
    future: float,
    strike: float,
    years: float,
    vol: float,
    rate: float = 0.065,
    option_type: str = "CE",
) -> float:
    """Black-76 option price. Falls back to intrinsic at expiry or zero vol."""
    if years <= 0 or vol <= 0 or future <= 0 or strike <= 0:
        intrinsic = (future - strike) if option_type == "CE" else (strike - future)
        return max(intrinsic, 0.0)

    discount = math.exp(-rate * years)
    d1, d2 = _d1_d2(future, strike, years, vol)
    if option_type == "CE":
        return discount * (future * _norm_cdf(d1) - strike * _norm_cdf(d2))
    return discount * (strike * _norm_cdf(-d2) - future * _norm_cdf(-d1))


def greeks(
    future: float,
    strike: float,
    years: float,
    vol: float,
    rate: float = 0.065,
    option_type: str = "CE",
) -> Dict[str, float]:
    """Delta, gamma, theta (per day) and vega (per 1 vol point)."""
    if years <= 0 or vol <= 0 or future <= 0 or strike <= 0:
        in_the_money = (future > strike) if option_type == "CE" else (future < strike)
        return {
            "delta": (1.0 if option_type == "CE" else -1.0) if in_the_money else 0.0,
            "gamma": 0.0,
            "theta": 0.0,
            "vega": 0.0,
        }

    discount = math.exp(-rate * years)
    d1, d2 = _d1_d2(future, strike, years, vol)
    pdf = _norm_pdf(d1)
    root_years = math.sqrt(years)

    if option_type == "CE":
        delta = discount * _norm_cdf(d1)
        theta = (
            -future * discount * pdf * vol / (2 * root_years)
            - rate * strike * discount * _norm_cdf(d2)
            + rate * future * discount * _norm_cdf(d1)
        )
    else:
        delta = -discount * _norm_cdf(-d1)
        theta = (
            -future * discount * pdf * vol / (2 * root_years)
            + rate * strike * discount * _norm_cdf(-d2)
            - rate * future * discount * _norm_cdf(-d1)
        )

    return {
        "delta": delta,
        "gamma": discount * pdf / (future * vol * root_years),
        "theta": theta / 365.0,        # per calendar day
        "vega": future * discount * pdf * root_years / 100.0,  # per 1 vol point
    }


def implied_volatility(
    option_price: float,
    future: float,
    strike: float,
    years: float,
    rate: float = 0.065,
    option_type: str = "CE",
    tolerance: float = 1e-6,
    max_iterations: int = 100,
) -> float:
    """Implied vol by bisection.

    Bisection rather than Newton-Raphson: it cannot diverge on deep-OTM strikes
    where vega collapses, and 100 iterations of it is still trivial next to a
    network round trip.
    """
    if years <= 0 or option_price <= 0:
        return 0.0

    low, high = 1e-4, 5.0
    if price(future, strike, years, high, rate, option_type) < option_price:
        return high

    for _ in range(max_iterations):
        mid = 0.5 * (low + high)
        theoretical = price(future, strike, years, mid, rate, option_type)
        if abs(theoretical - option_price) < tolerance:
            return mid
        if theoretical < option_price:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)
