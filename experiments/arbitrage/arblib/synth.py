"""Synthetic series with a known answer, and the negative controls.

The point of this file is the SECOND half. Generating cointegrated pairs and
checking the detector finds them measures power, which is the easy and
flattering half. Generating pairs that are *not* cointegrated and counting
how often the procedure says they are is what the whole field gets wrong,
and it is the only way to know what a screen over 125,000 candidates is
really producing.

Four generators:

* `cointegrated`   -- a random walk, plus a second leg tied to it by an OU
                      error with a KNOWN half-life. The answer is yes.
* `random_walks`   -- two independent random walks. The answer is no, and
                      this is the spurious-regression null.
* `drifting_walks` -- two independent random walks WITH drift. Still no, and
                      much worse: correlation of levels goes near 1 by
                      construction because both series mostly go up.
* `common_trend_but_not_cointegrated` -- two series sharing a slow common
                      factor plus independent random walks. This is the one
                      that looks most like a real pair of sector peers and is
                      the hardest negative control of the three.
"""
from __future__ import annotations

import numpy as np


def _ou(n: int, half_life: float, sigma: float, rng) -> np.ndarray:
    """An AR(1) with the requested half-life, started at its stationary draw."""
    phi = 0.5 ** (1.0 / half_life)
    e = np.empty(n)
    e[0] = rng.standard_normal() * sigma / np.sqrt(max(1e-9, 1 - phi ** 2))
    for i in range(1, n):
        e[i] = phi * e[i - 1] + rng.standard_normal() * sigma
    return e


def cointegrated(n: int, rng, half_life: float = 10.0, beta: float = 1.5,
                 sigma_x: float = 1.0, sigma_e: float = 1.0, level: float = 500.0):
    """`(a, b, truth)` where a = beta*b + alpha + OU(half_life)."""
    b = level + np.cumsum(rng.standard_normal(n) * sigma_x)
    a = beta * b + 0.0 + _ou(n, half_life, sigma_e, rng)
    return a, b, {"cointegrated": True, "beta": beta, "half_life": half_life}


def random_walks(n: int, rng, sigma: float = 1.0, level: float = 500.0):
    """Two independent random walks. THE negative control."""
    a = level + np.cumsum(rng.standard_normal(n) * sigma)
    b = level + np.cumsum(rng.standard_normal(n) * sigma)
    return a, b, {"cointegrated": False, "kind": "independent"}


def drifting_walks(n: int, rng, sigma: float = 1.0, drift: float = 0.06,
                   level: float = 500.0):
    """Independent walks that both drift upward -- an equity market's default
    state. Correlation of levels is near 1 and means nothing."""
    a = level + np.cumsum(rng.standard_normal(n) * sigma + drift)
    b = level + np.cumsum(rng.standard_normal(n) * sigma + drift)
    return a, b, {"cointegrated": False, "kind": "drifting"}


def common_trend_but_not_cointegrated(n: int, rng, sigma_f: float = 0.8,
                                      sigma_i: float = 0.6, level: float = 500.0):
    """A shared factor PLUS independent idiosyncratic random walks.

    Two sector peers driven by the same sector move. They are highly
    correlated, they look like a pair on a chart, and no linear combination
    of them is stationary -- because each carries its own unit root that the
    other cannot cancel. The hardest negative control, and the most realistic.
    """
    f = np.cumsum(rng.standard_normal(n) * sigma_f)
    a = level + f + np.cumsum(rng.standard_normal(n) * sigma_i)
    b = level + f + np.cumsum(rng.standard_normal(n) * sigma_i)
    return a, b, {"cointegrated": False, "kind": "common_trend"}


GENERATORS = {
    "cointegrated": cointegrated,
    "random_walks": random_walks,
    "drifting_walks": drifting_walks,
    "common_trend": common_trend_but_not_cointegrated,
}
