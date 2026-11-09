"""The board, the four-point check and the picks. Every array is causal at bar k.

THE RULE (inherited from the screener's features.py): a value at [s, d, k] may
use bars 0..k of day d and any earlier day, never bar k+1. The order engine
reads a gate at bar j-1 to decide whether an order is live in bar j.

Arrays are [S, D, B] unless noted. "Long" and "short" are the two sides of
the blog's board.
"""
from __future__ import annotations

from functools import cached_property

import numpy as np
import pandas as pd
from numba import njit

from islib.features import Features

from . import EXP

BOARD_SIZE = 20          # strongest names shown per side
ALIGN_MIN = 3            # four-point check: at least 3 of 4
EVENT_FALL = -0.05       # day move at or below this, at any bar so far -> event risk
PICKS = 3
PICKS_PER_SECTOR = 2


def load_sectors(symbols: list[str]) -> tuple[np.ndarray, list[str]]:
    """symbol -> sector index. A symbol missing from data/sectors.csv is an
    error, not a silent 'Other': an unmapped name would never agree with its
    sector and quietly lose a point on every check."""
    m = pd.read_csv(EXP / "data" / "sectors.csv").set_index("symbol")["sector"]
    names = sorted(m.unique())
    idx = {n: i for i, n in enumerate(names)}
    return np.array([idx[m[s]] for s in symbols], dtype=np.int64), names


@njit(cache=True)
def _picks(order, valid, event, sector, n_board, n_picks, per_sector):
    """order: [S, D, B] names sorted strongest first (argsort); valid: on that
    side of the board. Walks the board in rank order, skips event risk, caps
    each sector, stops at n_picks."""
    S, D, B = order.shape
    out = np.zeros((S, D, B), dtype=np.bool_)
    n_sec = sector.max() + 1
    for d in range(D):
        for k in range(B):
            used = np.zeros(n_sec, dtype=np.int64)
            got = 0
            for r in range(n_board):
                s = order[r, d, k]
                if not valid[s, d, k]:
                    break
                if event[s, d, k]:
                    continue
                g = sector[s]
                if used[g] >= per_sector:
                    continue
                used[g] += 1
                out[s, d, k] = True
                got += 1
                if got >= n_picks:
                    break
    return out


class Board:
    def __init__(self, F: Features, sector: np.ndarray):
        self.F = F
        self.p = F.p
        self.sector = sector

    @cached_property
    def universe(self) -> np.ndarray:
        """[S, D] every F&O name with a kept day, a previous close, and no
        corporate-action gap flag."""
        return (~np.isnan(self.p.turnover_cr) & ~self.p.gap_flag
                & ~np.isnan(self.F.prev_close))

    @cached_property
    def day_move(self) -> np.ndarray:
        """Move vs the previous close, at the close of bar k (last trade)."""
        with np.errstate(all="ignore"):
            m = self.F.close_ff / self.F.prev_close[..., None] - 1
        m[~self.universe] = np.nan
        return m.astype(np.float32)

    @cached_property
    def score(self) -> tuple[np.ndarray, np.ndarray]:
        """(r, score): return since the close of bar 0 (09:20, when the blog's
        board restarts), and r x efficiency, efficiency = net move / path."""
        c = self.F.close_ff
        c0 = c[:, :, :1]
        step = np.abs(np.diff(c, axis=2, prepend=c0))
        step = np.nan_to_num(step)
        path = np.cumsum(step, axis=2)
        with np.errstate(all="ignore"):
            r = c / c0 - 1
            er = np.where(path > 0, np.abs(c - c0) / path, 0.0)
        r[~self.universe] = np.nan
        return r.astype(np.float32), (r * er).astype(np.float32)

    @cached_property
    def event(self) -> np.ndarray:
        """The day move has been <= -5% at some bar so far."""
        m = np.where(np.isnan(self.day_move), np.inf, self.day_move)
        return np.minimum.accumulate(m, axis=2) <= EVENT_FALL

    def _side_rank(self, side: int):
        r, score = self.score
        with np.errstate(invalid="ignore"):
            ok = (side * r > 0) & np.isfinite(score)
        key = np.where(ok, side * score, -np.inf)
        order = np.argsort(-key, axis=0, kind="stable")
        rank = np.empty_like(order, dtype=np.int16)
        np.put_along_axis(rank, order, np.arange(key.shape[0], dtype=np.int16)[:, None, None], axis=0)
        top = ok & (rank < BOARD_SIZE)
        return order, ok, top

    @cached_property
    def board_long(self):
        return self._side_rank(+1)

    @cached_property
    def board_short(self):
        return self._side_rank(-1)

    @cached_property
    def breadth(self) -> tuple[np.ndarray, np.ndarray]:
        """[D, B] bullish and bearish counts across the universe."""
        m = self.day_move
        with np.errstate(invalid="ignore"):
            return (m > 0).sum(axis=0), (m < 0).sum(axis=0)

    @cached_property
    def sector_other(self) -> np.ndarray:
        """Equal-weight mean day move of the OTHER universe names in the same
        sector. NaN when the sector has no other name moving."""
        m = self.day_move
        out = np.full_like(m, np.nan)
        for g in np.unique(self.sector):
            idx = np.flatnonzero(self.sector == g)
            sub = m[idx]
            tot = np.nansum(sub, axis=0)
            cnt = (~np.isnan(sub)).sum(axis=0)
            n_other = cnt[None] - (~np.isnan(sub))
            with np.errstate(all="ignore"):
                out[idx] = np.where(n_other > 0, (tot[None] - np.nan_to_num(sub)) / n_other, np.nan)
        return out

    def align(self, side: int) -> np.ndarray:
        """Four-point count, int8 0..4: top 20 + sector + breadth + VWAP."""
        top = (self.board_long if side > 0 else self.board_short)[2]
        up, dn = self.breadth
        with np.errstate(invalid="ignore"):
            sec = side * self.sector_other > 0
            br = np.broadcast_to((side * (up - dn) > 0)[None], top.shape)
            vw = side * (self.F.close_ff - self.F.vwap) > 0
        return (top.astype(np.int8) + sec + br + vw).astype(np.int8)

    def picks(self, side: int) -> np.ndarray:
        order, ok, _ = self.board_long if side > 0 else self.board_short
        return _picks(order, ok, self.event, self.sector, BOARD_SIZE, PICKS, PICKS_PER_SECTOR)

    def gate(self, side: int, mode: str = "align3") -> np.ndarray:
        """Whether an order on `side` may be live in the NEXT bar.

        align3  top 20, no event risk, >= 3 of the four points (the primary rule)
        align4  top 20, no event risk, all four
        top20   top 20, no event risk, the other three ignored
        none    any universe name without event risk (the check ignored)
        picks   one of the top-3 picks, >= 3 of the four points
        """
        top = (self.board_long if side > 0 else self.board_short)[2]
        ev = self.event
        if mode == "none":
            return self.universe[..., None] & ~ev
        if mode == "top20":
            return top & ~ev
        a = self.align(side)
        if mode == "align3":
            return top & ~ev & (a >= ALIGN_MIN)
        if mode == "align4":
            return top & ~ev & (a == 4)
        if mode == "picks":
            return self.picks(side) & (a >= ALIGN_MIN)
        raise ValueError(mode)
