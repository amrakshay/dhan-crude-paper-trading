"""Synthetic markets with a KNOWN answer, for ground truth and negative controls.

The point is not to simulate anything realistic.  It is to build a market where
the right answer is known in closed form, write it into the SAME SQLite schema
the real bhavcopy cache uses, and check that the unmodified engine recovers it.
If the engine cannot find a zero where a zero is known to be, nothing it says
about real data is worth reading.

THE ONE IDENTITY EVERYTHING RESTS ON
------------------------------------
With zero drift and zero rates, an option sold at implied volatility `iv` and
held to expiry against an underlying that realises volatility `rv` has

    E[P&L of the short]  =  BlackPrice(iv) - BlackPrice(rv)

So:
    iv > rv   selling is paid the variance risk premium   -> positive
    iv = rv   selling is a fair bet                       -> ZERO
    iv < rv   selling is the wrong side                   -> negative

That third case is the negative control the handoff asks for, and it is the one
a backtest with a subtle sign error or a lookahead will fail: a broken engine
tends to make money in every regime.

PATH GENERATORS
---------------
  gbm        constant volatility, the base case
  student_t  fat tails, same volatility -- more disasters, same average day
  jump       Merton jump-diffusion -- the gap through the wing
  crash      a deterministic COVID-shaped drop
  vol_expand volatility ramping up through the sample
  trend      a strong upward drift with frequent gaps -- the case that breaches
             the short CALL side over and over
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from . import blackscholes as bs
from .bhavcopy import SCHEMA

TRADING_DAYS = 252

# Columns named explicitly rather than positionally: `turnover` was added to
# the real schema later and a positional VALUES list silently breaks when the
# column count changes.
INSERT_BHAV = ("INSERT OR REPLACE INTO fo_bhav "
               "(trade_date,symbol,kind,expiry,strike,opt_type,open,high,low,"
               " close,settle,underlying,oi,volume,num_trades,lot_size,source) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------

def gbm(n: int, sigma_ann: float, s0: float, rng: np.random.Generator,
        mu: float = 0.0) -> np.ndarray:
    dt = 1.0 / TRADING_DAYS
    z = rng.standard_normal(n)
    logret = (mu - 0.5 * sigma_ann ** 2) * dt + sigma_ann * math.sqrt(dt) * z
    return s0 * np.exp(np.cumsum(np.concatenate([[0.0], logret])))


def student_t(n: int, sigma_ann: float, s0: float, rng: np.random.Generator,
              df: int = 4) -> np.ndarray:
    """Same annualised volatility, far fatter tails."""
    dt = 1.0 / TRADING_DAYS
    raw = rng.standard_t(df, n)
    raw = raw / math.sqrt(df / (df - 2.0))          # unit variance
    logret = -0.5 * sigma_ann ** 2 * dt + sigma_ann * math.sqrt(dt) * raw
    return s0 * np.exp(np.cumsum(np.concatenate([[0.0], logret])))


def jump(n: int, sigma_ann: float, s0: float, rng: np.random.Generator,
         jump_per_year: float = 4.0, jump_mean: float = -0.03,
         jump_sd: float = 0.04) -> np.ndarray:
    """Merton jump-diffusion: the gap that goes through a bought wing."""
    dt = 1.0 / TRADING_DAYS
    z = rng.standard_normal(n)
    diff = (-0.5 * sigma_ann ** 2) * dt + sigma_ann * math.sqrt(dt) * z
    n_j = rng.poisson(jump_per_year * dt, n)
    jumps = np.array([
        rng.normal(jump_mean * k, jump_sd * math.sqrt(k)) if k else 0.0
        for k in n_j])
    return s0 * np.exp(np.cumsum(np.concatenate([[0.0], diff + jumps])))


def crash(n: int, sigma_ann: float, s0: float, rng: np.random.Generator,
          at: float = 0.5, depth: float = 0.35, days: int = 20) -> np.ndarray:
    """A deterministic COVID-shaped drawdown dropped into an ordinary path."""
    p = gbm(n, sigma_ann, s0, rng)
    start = int(len(p) * at)
    end = min(start + days, len(p))
    per_day = math.log(1.0 - depth) / max(end - start, 1)
    shock = np.zeros(len(p))
    shock[start:end] = per_day
    return p * np.exp(np.cumsum(shock))


def vol_expand(n: int, sigma_ann: float, s0: float, rng: np.random.Generator,
               end_multiple: float = 3.0) -> np.ndarray:
    """Volatility ramps up -- the regime in which short premium should lose."""
    dt = 1.0 / TRADING_DAYS
    ramp = np.linspace(1.0, end_multiple, n)
    z = rng.standard_normal(n)
    sig = sigma_ann * ramp
    logret = -0.5 * sig ** 2 * dt + sig * math.sqrt(dt) * z
    return s0 * np.exp(np.cumsum(np.concatenate([[0.0], logret])))


def trend(n: int, sigma_ann: float, s0: float, rng: np.random.Generator,
          drift: float = 0.18, jump_per_year: float = 6.0,
          jump_mean: float = 0.02, jump_sd: float = 0.03) -> np.ndarray:
    """A strong TRENDING, gap-heavy path -- upward drift with frequent gaps.

    The handoff named this as its own negative control, and it is not the same
    test as `jump`.  A delta-neutral seller is not hurt by volatility alone but
    by DIRECTION that keeps going: the short call side of a condor is breached
    again and again, and each new position is sold into a market that then runs
    through it too.  Gaps UPWARD are the case a crash generator never produces.
    """
    dt = 1.0 / TRADING_DAYS
    z = rng.standard_normal(n)
    diff = (drift - 0.5 * sigma_ann ** 2) * dt + sigma_ann * math.sqrt(dt) * z
    n_j = rng.poisson(jump_per_year * dt, n)
    jumps = np.array([
        rng.normal(jump_mean * k, jump_sd * math.sqrt(k)) if k else 0.0
        for k in n_j])
    return s0 * np.exp(np.cumsum(np.concatenate([[0.0], diff + jumps])))


GENERATORS = {"gbm": gbm, "student_t": student_t, "jump": jump,
              "crash": crash, "vol_expand": vol_expand, "trend": trend}


# --------------------------------------------------------------------------
# a synthetic market written into the real schema
# --------------------------------------------------------------------------

@dataclass
class SynthSpec:
    generator: str = "gbm"
    sigma_real: float = 0.15          # what the path ACTUALLY realises
    vrp: float = 0.0                  # iv = sigma_real * (1 + vrp)
    skew_slope: float = 0.0           # iv += slope * -log(K/F); >0 = put skew
    s0: float = 24000.0
    n_days: int = 252
    lot_size: int = 75
    strike_step: float = 200.0
    # The grid is FIXED for the whole market, spanning the path's realised
    # range with a wide margin -- NOT re-centred daily.  A daily re-centred
    # grid silently delists the wing a live position is holding, so a trade
    # opened at one forward can become impossible to close after a move.
    # Real NSE chains never delist a strike, so this matches reality and the
    # earlier per-day window did not.
    grid_low_multiple: float = 0.70
    grid_high_multiple: float = 1.30
    max_strikes: int = 400            # the step widens rather than the grid
                                      # growing without bound on a long path
    band: float | None = None         # None = write the WHOLE grid every day,
                                      # which is what NSE effectively does.
                                      # A band is cheaper but can delist the
                                      # far wing of a live position after a
                                      # large move -- a crash drops the forward
                                      # 35%, and the CALL wing bought before it
                                      # then sits above any band centred on the
                                      # new forward.  That truncates precisely
                                      # the tail the crash regime exists to
                                      # produce, so the default is the full grid.
    expiry_every: int = 7             # trading days between expiries
    expiry_horizon_days: int = 35     # only write expiries this far ahead
    start: date = date(2020, 1, 1)
    seed: int = 0

    def iv_for(self, strike: float, forward: float) -> float:
        base = self.sigma_real * (1.0 + self.vrp)
        if self.skew_slope:
            base += self.skew_slope * -math.log(strike / forward)
        return max(base, 0.01)


# -- vectorised Black-76, for building a market fast ------------------------
# The scalar path in blackscholes.py stays the reference; these two are checked
# against it in scripts/run_negative_controls.py level 0.

def _norm_cdf_vec(x: np.ndarray) -> np.ndarray:
    from scipy.special import erfc
    return 0.5 * erfc(-x / np.sqrt(2.0))


def _black76_vec(f: float, k: np.ndarray, t: float, sigma: np.ndarray,
                 is_call: bool) -> np.ndarray:
    k = np.asarray(k, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    if t <= 0:
        return np.maximum(f - k, 0.0) if is_call else np.maximum(k - f, 0.0)
    v = sigma * math.sqrt(t)
    d1 = (np.log(f / k) + 0.5 * sigma * sigma * t) / v
    d2 = d1 - v
    if is_call:
        return f * _norm_cdf_vec(d1) - k * _norm_cdf_vec(d2)
    return k * _norm_cdf_vec(-d2) - f * _norm_cdf_vec(-d1)


def _iv_vec(spec: "SynthSpec", k: np.ndarray, f: float) -> np.ndarray:
    base = spec.sigma_real * (1.0 + spec.vrp)
    out = np.full(len(k), base, dtype=float)
    if spec.skew_slope:
        out = out + spec.skew_slope * -np.log(np.asarray(k, dtype=float) / f)
    return np.maximum(out, 0.01)


def build_market(spec: SynthSpec, db_path: str) -> dict:
    """Write a complete synthetic option market and return its ground truth."""
    rng = np.random.default_rng(spec.seed)
    gen = GENERATORS[spec.generator]
    path = gen(spec.n_days, spec.sigma_real, spec.s0, rng)

    days = [spec.start + timedelta(days=i) for i in range(len(path))]
    # expiries every `expiry_every` sessions, on a session
    expiry_idx = list(range(spec.expiry_every, len(days), spec.expiry_every))
    expiries = [days[i] for i in expiry_idx]

    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.execute("DELETE FROM fo_bhav")
    conn.execute("DELETE FROM fo_volt")
    batch: list[tuple] = []

    volt = []
    # ONE fixed strike grid for the whole market -- see SynthSpec.  The STEP
    # widens if the path wandered far enough to need more than `max_strikes`;
    # a grid that grows without bound turns an 8-year path into 14 million rows
    # and exhausts memory before it proves anything.
    low = float(path.min()) * spec.grid_low_multiple
    high = float(path.max()) * spec.grid_high_multiple
    step = max(spec.strike_step,
               math.ceil((high - low) / spec.max_strikes / spec.strike_step)
               * spec.strike_step)
    ks = np.arange(math.floor(low / step), math.ceil(high / step) + 1) * step
    ks = ks[ks > 0]

    # realised daily vol of the path, used for the margin model's sigma
    logret = np.diff(np.log(path))
    for i, (d, s) in enumerate(zip(days, path, strict=True)):
        lo = max(0, i - 60)
        dv = float(np.std(logret[lo:i])) if i > 5 else spec.sigma_real / math.sqrt(TRADING_DAYS)
        volt.append((d.isoformat(), "SYNTH", dv))
        f = float(s)   # zero drift, zero rates -> forward == spot
        # Grid POINTS are global and never move; only the WINDOW written each
        # day follows the forward.  That is what real chains do -- NSE adds
        # strikes as the index moves and never removes one -- so a wing held by
        # a live position cannot vanish mid-trade the way a re-centred grid
        # would make it.  A move larger than `band` within one trade's life is
        # caught by Backtest.data_faults rather than silently ignored.
        if spec.band is None:
            ks_d = ks
        else:
            sel = (ks >= f * (1 - spec.band)) & (ks <= f * (1 + spec.band))
            ks_d = ks[sel]
            if len(ks_d) == 0:
                continue
        ivs = _iv_vec(spec, ks_d, f)
        for e in expiries:
            if e < d or (e - d).days > spec.expiry_horizon_days:
                continue
            t = max((e - d).days, 0) / 365.0
            for is_call in (True, False):
                px = np.round(_black76_vec(f, ks_d, t, ivs, is_call), 2)
                ot = "CE" if is_call else "PE"
                de, ee = d.isoformat(), e.isoformat()
                batch.extend(
                    (de, "SYNTH", "IDO", ee, float(k), ot,
                     float(v), float(v), float(v), float(v), float(v), f,
                     1_000_000, 1_000_000, 100_000, spec.lot_size, "synth")
                    for k, v in zip(ks_d, px, strict=True))
        if len(batch) > 400_000:
            conn.executemany(INSERT_BHAV, batch)
            batch.clear()
    if batch:
        conn.executemany(INSERT_BHAV, batch)
    conn.executemany("INSERT OR REPLACE INTO fo_volt VALUES (?,?,?)", volt)
    conn.commit()

    realised = float(np.std(logret) * math.sqrt(TRADING_DAYS))
    return {"path": path, "days": days, "expiries": expiries,
            "realised_vol": realised, "spec": spec, "db": db_path}


# --------------------------------------------------------------------------
# closed-form ground truth
# --------------------------------------------------------------------------

def expected_short_pnl(iv: float, rv: float, f: float, k: float, t: float,
                       is_call: bool, qty: int) -> float:
    """E[P&L] of a short option held to expiry, zero drift and zero rates.

    Equal to (price at the volatility SOLD) minus (price at the volatility the
    underlying actually REALISES), times the quantity.  This is the number the
    Monte Carlo must reproduce.
    """
    return qty * (bs.price(f, k, t, iv, is_call) - bs.price(f, k, t, rv, is_call))


__all__ = ["SynthSpec", "build_market", "expected_short_pnl", "GENERATORS",
           "gbm", "student_t", "jump", "crash", "vol_expand", "trend",
           "TRADING_DAYS"]
