"""Order-block setups, detected on finished 5-minute bars, causally.

Everything is written for the LONG side. The short side is the same code run
in "long space": prices multiplied by -1, so a high becomes minus the low, a
red candle becomes a green one, a swing high a swing low, and the previous
day's high the previous day's low. `sign` selects the space; nothing below
knows which side it is on. Converting back is `price = sign * value`.

A setup marked at bar k uses bars 0..k of the day and the previous days'
highs only (tests/test_setups.py corrupts every later bar and asserts no
setup marked at or before k moves).

Rules (README.md, fixed before any result):
  structural levels  opening-range high (bars 0-2, usable from bar 3) and every
                     confirmed swing high (beats the 2 bars before, not beaten
                     by the 2 after; usable from the bar after confirmation).
                     A level is used up when a bar closes above it.
  break of structure close above an unused level, at bar k >= 3.
  impulse            bar k's true range >= 1.5 x ATR5(k-1), or a fair-value
                     gap (low of bar m > high of bar m-2) between block and k.
  order block        last bearish bar (close < open) in the 10 bars before k.
  Z, S1              block high; block low - 0.1 x ATR5(k). Needs close_k > Z.
  liquidity          above Z, not yet traded through today: today's high so
                     far, today's unswept confirmed swing highs, previous day's
                     high, prior-5-session high. R1, R2 = the two nearest.
"""
from __future__ import annotations

import numpy as np
from numba import njit

OR_BARS = 3
FIRST_BOS_BAR = 3
LAST_MARK_BAR = 56        # an order must be live by bar 57 (the 13:55 decision)
IMPULSE_TR_ATR = 1.5
OB_LOOKBACK = 10
STOP_BUFFER_ATR = 0.1

# Columns of the setup array.
COLS = ("s", "d", "k", "ob", "Z", "S1", "R1", "R2", "obl", "impulse")
NC = len(COLS)


@njit(cache=True)
def _day(o, h, l, c, atr, pdh, p5h, s, d, out, row, write):
    """One name-day in long space. Appends setups to `out` from `row` on;
    returns the next free row. With write=False only counts."""
    B = o.shape[0]
    lev = np.empty(B + 1)          # structural levels
    lev_from = np.empty(B + 1, dtype=np.int64)
    lev_used = np.zeros(B + 1, dtype=np.bool_)
    nlev = 0
    sw = np.empty(B)               # confirmed swing highs (for liquidity)
    sw_bar = np.empty(B, dtype=np.int64)
    nsw = 0
    day_high = -np.inf
    cand = np.empty(B + 4)
    for k in range(min(B, LAST_MARK_BAR + 1)):
        if np.isnan(c[k]):
            continue
        if h[k] > day_high:
            day_high = h[k]
        # --- break of structure, against levels usable before bar k ----------
        if k >= FIRST_BOS_BAR:
            broke = False
            for i in range(nlev):
                if not lev_used[i] and lev_from[i] < k and c[k] > lev[i]:
                    lev_used[i] = True
                    broke = True
            if broke:
                # the order block: last bearish bar before k
                ob = -1
                for j in range(k - 1, max(-1, k - 1 - OB_LOOKBACK), -1):
                    if not np.isnan(c[j]) and c[j] < o[j]:
                        ob = j
                        break
                if ob >= 0 and not np.isnan(atr[k]):
                    # impulse: a wide breaking bar, or a fair-value gap in the leg
                    kind = 0
                    pc = np.nan
                    for j in range(k - 1, -1, -1):
                        if not np.isnan(c[j]):
                            pc = c[j]
                            break
                    tr = h[k] - l[k]
                    if not np.isnan(pc):
                        tr = max(tr, abs(h[k] - pc), abs(l[k] - pc))
                    if k >= 1 and not np.isnan(atr[k - 1]) and tr >= IMPULSE_TR_ATR * atr[k - 1]:
                        kind = 1
                    else:
                        for m in range(ob + 2, k + 1):
                            if (not np.isnan(l[m]) and not np.isnan(h[m - 2])
                                    and l[m] > h[m - 2]):
                                kind = 2
                                break
                    Z = h[ob]
                    S1 = l[ob] - STOP_BUFFER_ATR * atr[k]
                    if kind > 0 and c[k] > Z and Z > S1:
                        # liquidity above Z, not yet traded through
                        nc = 0
                        cand[nc] = day_high
                        nc += 1
                        for i in range(nsw):
                            if sw[i] > Z:
                                swept = False
                                for j in range(sw_bar[i] + 1, k + 1):
                                    if not np.isnan(h[j]) and h[j] > sw[i]:
                                        swept = True
                                        break
                                if not swept:
                                    cand[nc] = sw[i]
                                    nc += 1
                        if not np.isnan(pdh) and pdh > Z and day_high <= pdh:
                            cand[nc] = pdh
                            nc += 1
                        if not np.isnan(p5h) and p5h > Z and day_high <= p5h:
                            cand[nc] = p5h
                            nc += 1
                        lv = np.sort(cand[:nc])
                        R1 = np.nan
                        R2 = np.nan
                        for x in lv:
                            if x <= Z:
                                continue
                            if np.isnan(R1):
                                R1 = x
                            elif x - R1 > 1e-6 * abs(R1):     # distinct from R1
                                R2 = x
                                break
                        if write:
                            out[row, 0] = s
                            out[row, 1] = d
                            out[row, 2] = k
                            out[row, 3] = ob
                            out[row, 4] = Z
                            out[row, 5] = S1
                            out[row, 6] = R1
                            out[row, 7] = R2
                            out[row, 8] = l[ob]
                            out[row, 9] = kind
                        row += 1
        # --- levels that become usable from the NEXT bar -----------------------
        if k == OR_BARS - 1:
            orh = -np.inf
            for j in range(OR_BARS):
                if not np.isnan(h[j]) and h[j] > orh:
                    orh = h[j]
            if orh > -np.inf:
                lev[nlev] = orh
                lev_from[nlev] = k
                nlev += 1
        if k >= 4:
            i = k - 2
            ok = True
            for j in range(i - 2, i + 3):
                if np.isnan(h[j]):
                    ok = False
            if ok and h[i] > h[i - 1] and h[i] > h[i - 2] and h[i] >= h[i + 1] and h[i] >= h[i + 2]:
                lev[nlev] = h[i]
                lev_from[nlev] = k
                nlev += 1
                sw[nsw] = h[i]
                sw_bar[nsw] = i
                nsw += 1
    return row


@njit(cache=True)
def _all(O, H, L, C, ATR, PDH, P5H, valid, sign, out, write):
    S, D, B = O.shape
    row = 0
    o = np.empty(B)
    h = np.empty(B)
    l = np.empty(B)
    c = np.empty(B)
    a = np.empty(B)
    for s in range(S):
        for d in range(D):
            if not valid[s, d]:
                continue
            for b in range(B):
                a[b] = ATR[s, d, b]
                if sign > 0:
                    o[b] = O[s, d, b]
                    h[b] = H[s, d, b]
                    l[b] = L[s, d, b]
                    c[b] = C[s, d, b]
                else:
                    o[b] = -O[s, d, b]
                    h[b] = -L[s, d, b]
                    l[b] = -H[s, d, b]
                    c[b] = -C[s, d, b]
            row = _day(o, h, l, c, a, PDH[s, d], P5H[s, d], s, d, out, row, write)
    return row


def prior_levels(p, sign: int) -> tuple[np.ndarray, np.ndarray]:
    """[S, D] previous day's high and prior-5-session high, in long space
    (for the short side: minus the previous low and minus the 5-day low)."""
    with np.errstate(all="ignore"):
        if sign > 0:
            dh = np.nanmax(p.high, axis=2)
        else:
            dh = -np.nanmin(p.low, axis=2)
    dh = dh.astype(np.float64)
    pdh = np.full_like(dh, np.nan)
    pdh[:, 1:] = dh[:, :-1]
    p5 = np.full_like(dh, np.nan)
    for lag in range(1, 6):
        sh = np.full_like(dh, np.nan)
        sh[:, lag:] = dh[:, :-lag]
        p5 = np.fmax(p5, sh)
    return pdh, p5


def detect(p, atr: np.ndarray, valid: np.ndarray, sign: int) -> np.ndarray:
    """All setups on one side, as an [N, len(COLS)] float64 array in LONG
    space, sorted by (s, d, k)."""
    pdh, p5 = prior_levels(p, sign)
    args = (p.open, p.high, p.low, p.close, atr, pdh, p5, valid, sign)
    n = _all(*args, np.empty((0, NC)), False)
    out = np.empty((n, NC))
    _all(*args, out, True)
    return out
