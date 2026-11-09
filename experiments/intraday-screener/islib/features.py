"""Causal features on the panel.

THE RULE: a value at [s, d, k] may use bars 0..k of day d and any bar of an
earlier day -- never bar k+1 or later. `tests/test_features.py` enforces it by
corrupting every bar after k and asserting nothing at k moves. A decision on
the close of bar k fills at the OPEN of bar k+1 (see engine.py), so features
at k are exactly what a live screener could have known.
"""
from __future__ import annotations

from functools import cached_property

import numpy as np
import pandas as pd

from .panel import Panel

OPENING_RANGE_BARS = 3      # 09:15-09:30: bars 0, 1, 2
TOD_LOOKBACK_DAYS = 20      # "normal volume for this time of day"
ATR_BARS = 14
LIQUIDITY_LOOKBACK_DAYS = 20


def ffill_bars(x: np.ndarray) -> np.ndarray:
    """Forward-fill NaN along the last axis (bars), within each day only."""
    mask = np.isnan(x)
    idx = np.where(~mask, np.arange(x.shape[-1]), 0)
    np.maximum.accumulate(idx, axis=-1, out=idx)
    out = np.take_along_axis(x, idx, axis=-1)
    # Leading NaNs (nothing yet today) stay NaN.
    lead = np.minimum.accumulate(mask, axis=-1)
    out[lead] = np.nan
    return out


def rolling_prior_mean(x: np.ndarray, window: int, min_periods: int) -> np.ndarray:
    """Mean of x over the PREVIOUS `window` days (axis 1), excluding today.

    x is [S, D, ...]; days where x is NaN do not count towards min_periods.
    """
    S, D = x.shape[:2]
    flat = np.moveaxis(x, 1, 0).reshape(D, -1)
    out = (pd.DataFrame(flat).rolling(window, min_periods=min_periods).mean()
           .shift(1).to_numpy(dtype=np.float32))
    return np.moveaxis(out.reshape((D, S) + x.shape[2:]), 0, 1)


class Features:
    def __init__(self, panel: Panel, n_liquid: int = 100):
        self.p = panel
        self.n_liquid = n_liquid

    # --- price -------------------------------------------------------------
    @cached_property
    def close_ff(self) -> np.ndarray:
        """Last traded price as of the close of bar k."""
        return ffill_bars(self.p.close)

    @cached_property
    def day_open(self) -> np.ndarray:
        """[S, D] the first bar's open. Bar 0 is always present on a kept day
        unless the name did not trade in it (cleaning allows a first bar up to
        09:25); then the first available open."""
        o = self.p.open
        first = np.argmax(~np.isnan(o), axis=2)
        return np.take_along_axis(o, first[..., None], axis=2)[..., 0]

    @cached_property
    def prev_close(self) -> np.ndarray:
        """[S, D] the previous trading day's last close; NaN if that day was not kept."""
        last = self.close_ff[:, :, -1]
        out = np.full_like(last, np.nan)
        out[:, 1:] = last[:, :-1]
        return out

    @cached_property
    def gap(self) -> np.ndarray:
        """[S, D] overnight gap, known at 09:15."""
        return self.day_open / self.prev_close - 1

    @cached_property
    def ret_since_open(self) -> np.ndarray:
        return self.close_ff / self.day_open[..., None] - 1

    @cached_property
    def index_ret_since_open(self) -> np.ndarray:
        """[D, B] Nifty 50 return from its 09:15 open to the close of bar k."""
        c = ffill_bars(self.p.index_close)
        return c / self.p.index_open[:, :1] - 1

    @cached_property
    def opening_range(self) -> tuple[np.ndarray, np.ndarray]:
        """[S, D] high and low of 09:15-09:30. Known from the close of bar 2."""
        k = OPENING_RANGE_BARS
        with np.errstate(all="ignore"):
            return (np.nanmax(self.p.high[:, :, :k], axis=2),
                    np.nanmin(self.p.low[:, :, :k], axis=2))

    @cached_property
    def vwap(self) -> np.ndarray:
        tp = (self.p.high + self.p.low + self.p.close) / 3
        v = np.nan_to_num(self.p.volume)
        pv = np.nancumsum(np.nan_to_num(tp) * v, axis=2)
        cv = np.cumsum(v, axis=2)
        with np.errstate(all="ignore"):
            return np.where(cv > 0, pv / cv, np.nan).astype(np.float32)

    @cached_property
    def atr5(self) -> np.ndarray:
        """Mean true range of the last 14 bars that EXIST, across day boundaries.

        The first bar of a day uses high-low only, so an overnight gap is not
        counted as intraday range. Units: price.
        """
        h, l, c = self.p.high, self.p.low, self.p.close
        pc = np.full_like(c, np.nan)
        pc[:, :, 1:] = ffill_bars(c)[:, :, :-1]
        with np.errstate(all="ignore"):
            tr = np.fmax(h - l, np.fmax(np.abs(h - pc), np.abs(l - pc)))
        tr = np.where(np.isnan(h), np.nan, tr)
        S, D, B = tr.shape
        flat = tr.reshape(S, D * B).T
        # Rolling over the last N *present* bars: compress out the NaNs per
        # symbol, roll, scatter back, then forward-fill onto missing slots.
        res = np.full((D * B, S), np.nan, dtype=np.float32)
        for s in range(S):
            col = flat[:, s]
            present = ~np.isnan(col)
            if present.sum() == 0:
                continue
            r = pd.Series(col[present]).rolling(ATR_BARS, min_periods=5).mean().to_numpy()
            tmp = np.full(D * B, np.nan)
            tmp[present] = r
            res[:, s] = pd.Series(tmp).ffill().to_numpy()
        return res.T.reshape(S, D, B)

    # --- continuous-series indicators (H5) ----------------------------------
    def _over_present_bars(self, fn, *fields: np.ndarray) -> np.ndarray:
        """Apply `fn` to each symbol's series of PRESENT bars, joined across
        days in time order, as a 5-minute chart shows them. `fn` takes one
        1-D array per field, returns one value per bar, and must be causal
        (only `.rolling`/`.ewm`-style operations). The result is scattered
        back and forward-filled onto missing slots within the series."""
        S, D, B = self.p.close.shape
        flats = [f.reshape(S, D * B) for f in fields]
        res = np.full((S, D * B), np.nan, dtype=np.float32)
        for s in range(S):
            present = ~np.isnan(flats[0][s])
            if present.sum() == 0:
                continue
            r = fn(*(f[s][present].astype(np.float64) for f in flats))
            tmp = np.full(D * B, np.nan)
            tmp[present] = r
            res[s] = pd.Series(tmp).ffill().to_numpy()
        return res.reshape(S, D, B)

    def ema(self, span: int) -> np.ndarray:
        return self._ema_cache.setdefault(span, self._over_present_bars(
            lambda c: pd.Series(c).ewm(span=span, adjust=False).mean().to_numpy(),
            self.p.close))

    @cached_property
    def _ema_cache(self) -> dict:
        return {}

    @cached_property
    def adx14(self) -> np.ndarray:
        """Wilder's ADX(14) on 5-minute bars, carried across days. True range
        includes the overnight gap here, unlike `atr5`, because that is how a
        chart's ADX is computed and H5 names the chart indicator."""
        n = 14

        def adx(h, l, c):
            ph, pl, pc = np.roll(h, 1), np.roll(l, 1), np.roll(c, 1)
            up, dn = h - ph, pl - l
            pdm = np.where((up > dn) & (up > 0), up, 0.0)
            ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
            tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
            pdm[0] = ndm[0] = 0.0
            tr[0] = h[0] - l[0]
            rma = lambda x: pd.Series(x).ewm(alpha=1 / n, adjust=False).mean().to_numpy()
            atr = rma(tr)
            with np.errstate(all="ignore"):
                pdi = 100 * rma(pdm) / atr
                ndi = 100 * rma(ndm) / atr
                dx = 100 * np.abs(pdi - ndi) / (pdi + ndi)
            out = rma(np.nan_to_num(dx)).copy()
            out[: 2 * n] = np.nan       # not warmed up
            return out

        return self._over_present_bars(adx, self.p.high, self.p.low, self.p.close)

    @cached_property
    def prior_bar_vol_mean20(self) -> np.ndarray:
        """Mean volume of the 20 present 5-minute bars BEFORE this one,
        carried across days. Excludes the bar itself."""
        return self._over_present_bars(
            lambda v: pd.Series(v).rolling(20, min_periods=20).mean().shift(1).to_numpy(),
            self.p.volume)

    @cached_property
    def prev_day_high(self) -> np.ndarray:
        """[S, D] the previous trading day's high over all its bars; NaN if
        that day was not kept."""
        with np.errstate(all="ignore"):
            hi = np.nanmax(self.p.high, axis=2)
        out = np.full_like(hi, np.nan)
        out[:, 1:] = hi[:, :-1]
        return out

    @cached_property
    def first_bar_high(self) -> np.ndarray:
        """[S, D] high of bar 0 (09:15-09:20). Known from bar 0's close."""
        return self.p.high[:, :, 0]

    # --- volume ------------------------------------------------------------
    @cached_property
    def cumvol(self) -> np.ndarray:
        return np.cumsum(np.nan_to_num(self.p.volume), axis=2).astype(np.float32)

    @cached_property
    def relvol(self) -> np.ndarray:
        """Cumulative volume to bar k, over its average at bar k on the
        previous 20 trading days. 1.0 = a normal day so far."""
        cv = self.cumvol.copy()
        cv[np.isnan(self.p.turnover_cr)] = np.nan   # days not kept do not define "normal"
        avg = rolling_prior_mean(cv, TOD_LOOKBACK_DAYS, min_periods=10)
        with np.errstate(all="ignore"):
            return np.where(avg > 0, self.cumvol / avg, np.nan).astype(np.float32)

    # --- universe ----------------------------------------------------------
    @cached_property
    def liquid(self) -> np.ndarray:
        """[S, D] bool: in the top `n_liquid` by median turnover over the
        previous 20 trading days (at least 15 of them kept). Point in time --
        a name is judged by what it traded then, not by what it became."""
        t = pd.DataFrame(self.p.turnover_cr.T)
        med = t.rolling(LIQUIDITY_LOOKBACK_DAYS, min_periods=15).median().shift(1)
        rank = med.rank(axis=1, ascending=False, method="first")
        ok = (rank <= self.n_liquid).to_numpy().T
        # "Kept today" is decided by the cleaner on the WHOLE day (>= 90% of its
        # bars), which a live screener could not know at 10:00. It removes
        # ~0.6% of symbol-days, mostly the Sept 2021 vendor gap -- a known,
        # small lookahead, listed in README.md's known biases.
        return ok & ~np.isnan(self.p.turnover_cr)
