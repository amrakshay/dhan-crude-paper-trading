"""The dense panel: every symbol on one calendar, in 5-minute slots.

Arrays are [S, D, B] float32 -- symbol x trading day x bar -- with NaN where
the vendor has no bar (a window with no trade, or a day the symbol was not
kept). Bar b is the window starting 09:15 + 5b minutes, so:

    bar 0   09:15-09:20   the opening auction print lives here
    bar 2   09:25-09:30   its CLOSE is the first moment anything may be decided
    bar 3   09:30-09:35   the first bar an order can fill in
    bar 71  15:10-15:15   its CLOSE is the forced exit (closing auction, 15:15)
    bar 74  15:25-15:30   exists before Aug 2026; never traded here

Built once from data/clean/ by `scripts/build_panel.py` and cached as .npz.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

EXP = Path(__file__).resolve().parent.parent
CLEAN = EXP / "data" / "clean"
CACHE = EXP / "data" / "panel.npz"

N_BARS = 75
FIRST_DECISION_BAR = 2     # decide on the close of the 09:25 bar, i.e. at 09:30
LAST_EXIT_BAR = 71         # flat on the close of the 15:10 bar, i.e. by 15:15
INDEX = "NIFTY50"
# Development / holdout split, fixed in README.md before any result was seen.
HOLDOUT_START = date(2023, 1, 1)

FIELDS = ("open", "high", "low", "close", "volume")


@dataclass
class Panel:
    symbols: list[str]
    dates: list[date]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    index_close: np.ndarray            # [D, B], Nifty 50
    index_open: np.ndarray             # [D, B]
    turnover_cr: np.ndarray            # [S, D], per-day turnover (NaN = day not kept)
    gap_flag: np.ndarray               # [S, D] bool
    _sym: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        self._sym = {s: i for i, s in enumerate(self.symbols)}

    def sym(self, name: str) -> int:
        return self._sym[name]

    @property
    def shape(self):
        return self.close.shape

    def day_mask(self, holdout: bool = False) -> np.ndarray:
        """Which days a run may look at. The holdout is opt-in and logged."""
        d = np.array(self.dates)
        return d >= HOLDOUT_START if holdout else d < HOLDOUT_START


def build(clean_dir: Path = CLEAN) -> Panel:
    days = pd.read_parquet(clean_dir / "days.parquet")
    calendar = sorted(days.loc[(days["symbol"] == INDEX) & days["valid"], "date"])
    d_idx = {d: i for i, d in enumerate(calendar)}
    files = sorted(p for p in (clean_dir / "5m").glob("*.parquet") if p.stem != INDEX)
    symbols = [p.stem for p in files]
    S, D = len(symbols), len(calendar)
    arrays = {f: np.full((S, D, N_BARS), np.nan, dtype=np.float32) for f in FIELDS}
    for s, p in enumerate(files):
        b = pd.read_parquet(p)
        di = b["date"].map(d_idx)
        ok = di.notna() & (b["bar"] >= 0) & (b["bar"] < N_BARS)
        di = di[ok].astype(int).to_numpy()
        bi = b.loc[ok, "bar"].astype(int).to_numpy()
        for f in FIELDS:
            arrays[f][s, di, bi] = b.loc[ok, f].to_numpy(dtype=np.float32)

    ib = pd.read_parquet(clean_dir / "5m" / f"{INDEX}.parquet")
    index_close = np.full((D, N_BARS), np.nan, dtype=np.float32)
    index_open = np.full((D, N_BARS), np.nan, dtype=np.float32)
    di = ib["date"].map(d_idx)
    ok = di.notna()
    index_close[di[ok].astype(int), ib.loc[ok, "bar"].astype(int)] = ib.loc[ok, "close"]
    index_open[di[ok].astype(int), ib.loc[ok, "bar"].astype(int)] = ib.loc[ok, "open"]

    turnover = np.full((S, D), np.nan, dtype=np.float32)
    gap_flag = np.zeros((S, D), dtype=bool)
    kept = days[days["valid"] & days["symbol"].isin(symbols) & days["date"].isin(d_idx)]
    si = kept["symbol"].map({s: i for i, s in enumerate(symbols)}).to_numpy()
    dj = kept["date"].map(d_idx).to_numpy()
    turnover[si, dj] = kept["turnover_cr"].to_numpy()
    gap_flag[si, dj] = kept["gap_flag"].fillna(False).to_numpy(dtype=bool)
    return Panel(symbols, list(calendar), **arrays, index_close=index_close,
                 index_open=index_open, turnover_cr=turnover, gap_flag=gap_flag)


def save(panel: Panel, path: Path = CACHE) -> None:
    np.savez(path, symbols=np.array(panel.symbols), dates=np.array([d.isoformat() for d in panel.dates]),
             **{f: getattr(panel, f) for f in FIELDS}, index_close=panel.index_close,
             index_open=panel.index_open, turnover_cr=panel.turnover_cr, gap_flag=panel.gap_flag)


def load(path: Path = CACHE) -> Panel:
    z = np.load(path, allow_pickle=False)
    return Panel(list(z["symbols"]), [date.fromisoformat(x) for x in z["dates"]],
                 **{f: z[f] for f in FIELDS}, index_close=z["index_close"],
                 index_open=z["index_open"], turnover_cr=z["turnover_cr"], gap_flag=z["gap_flag"])
