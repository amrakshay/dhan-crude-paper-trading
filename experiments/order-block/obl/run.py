"""One configuration end to end: setups -> orders -> trades -> priced -> stats.

Shared by scripts/run_ob.py (the pass bar) and scripts/diagnostics.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

import numpy as np
import pandas as pd

from islib import panel as P
from islib.features import Features
from islib.stats import day_bootstrap, diff_bootstrap

from . import controls as CT
from . import setups as SU
from . import sim as SM
from .board import Board, load_sectors

SLIPPAGES = (0.0002, 0.0005, 0.0010)
PASS_SLIP = 0.0005
SUSPECT_YEARS = (2021, 2022, 2023)      # screener CLEANING.md: narrow 2021-23 bar ranges


@dataclass(frozen=True)
class Config:
    gate: str = "align3"           # align3 | align4 | top20 | none | picks
    target: str = "R2"             # R2 | R1 | time
    rr_min: float | None = 1.5
    first_only: bool = True
    chase: bool = False
    gate_at_mark: bool = False
    fill_bar_stop: bool = True     # False = sensitivity bound only (sim.simulate)
    account: float = SM.ACCOUNT

    def label(self) -> str:
        return (f"gate={self.gate} target={self.target} rr_min={self.rr_min} "
                f"first_only={self.first_only} chase={self.chase} gate_at_mark={self.gate_at_mark}"
                + ("" if self.fill_bar_stop else " fill_bar_stop=False")
                + (f" account={self.account:,.0f}" if self.account != SM.ACCOUNT else ""))


class World:
    """Everything that does not depend on a Config, built once."""

    def __init__(self, holdout: bool = False):
        self.holdout = holdout
        self.p = P.load()
        self.F = Features(self.p)
        self.sector, self.sector_names = load_sectors(self.p.symbols)
        self.board = Board(self.F, self.sector)
        self.D = len(self.p.dates)
        self.days = self.p.day_mask(holdout=holdout)
        self._gate_cache: dict = {}

    @cached_property
    def valid(self):
        return self.board.universe & self.days[None, :]

    @cached_property
    def setups(self):
        atr = self.F.atr5
        return {sg: SU.detect(self.p, atr, self.valid, sg) for sg in (1, -1)}

    def gates(self, mode: str):
        if mode not in self._gate_cache:
            self._gate_cache[mode] = (self.board.gate(1, mode), self.board.gate(-1, mode))
        return self._gate_cache[mode]

    @cached_property
    def picks(self):
        return {sg: self.board.picks(sg) for sg in (1, -1)}

    def orders(self, cfg: Config) -> pd.DataFrame:
        return pd.concat([SM.orders_from_setups(self.setups[sg], sg, self.D, cfg.target, cfg.rr_min)
                          for sg in (1, -1)], ignore_index=True)

    def trades(self, cfg: Config, orders: pd.DataFrame | None = None, slip: float = PASS_SLIP):
        o = self.orders(cfg) if orders is None else orders
        t = SM.simulate(self.p, self.gates(cfg.gate), o, slip=slip, first_only=cfg.first_only,
                        chase=cfg.chase, gate_at_mark=cfg.gate_at_mark, fill_bar_stop=cfg.fill_bar_stop)
        t = SM.resolve_sides(t, self.D)
        t = SM.price_trades(t, self.p, account=cfg.account)
        return self.tag(t)

    def tag(self, t: pd.DataFrame) -> pd.DataFrame:
        """Attributes known at the decision before the fill, for slicing."""
        s, sg, d = t["s_exec"].to_numpy(), t["s_gate"].to_numpy(), t["d"].to_numpy()
        j = np.maximum(t["fill_bar"].to_numpy() - 1, 0)
        long_ = t["gside"].to_numpy() > 0
        t["liquid100"] = self.F.liquid[s, d]
        t["is_pick"] = np.where(long_, self.picks[1][sg, d, j], self.picks[-1][sg, d, j])
        al, as_ = self.board.align(1), self.board.align(-1)
        t["align"] = np.where(long_, al[sg, d, j], as_[sg, d, j])
        t["sector"] = [self.sector_names[i] for i in self.sector[s]]
        return t


def with_net(t: pd.DataFrame, col="net_R") -> pd.DataFrame:
    """islib.stats reads `net_ret`; point it at the metric being judged."""
    return t.assign(net_ret=t[col])


def summarise(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"trades": 0}
    yr = pd.to_datetime(t["date"]).dt.year
    by_year = t.groupby(yr)["net_R"].mean()
    return {
        "trades": len(t),
        "trades_per_day": round(len(t) / t["d"].nunique(), 2),
        "days_with_trades": int(t["d"].nunique()),
        "gross_R": round(t["gross_R"].mean(), 4),
        "charges_R": round(t["charges_R"].mean(), 4),
        "net_R": round(t["net_R"].mean(), 4),
        "net_R_ci95": [round(x, 4) for x in day_bootstrap(with_net(t))["ci95"]],
        "win_rate": round(float((t["net_R"] > 0).mean()), 4),
        "net_bps_100k": round(t["net_bps_100k"].mean(), 2),
        "net_bps_500k": round(t["net_bps_500k"].mean(), 2),
        "exit_mix": t["reason"].value_counts(normalize=True).round(3).to_dict(),
        "by_side_net_R": t.groupby("side")["net_R"].mean().round(4).to_dict(),
        "by_year_net_R": by_year.round(4).to_dict(),
        "by_year_trades": t.groupby(yr).size().to_dict(),
        "years_positive": int((by_year > 0).sum()),
        "years": int(by_year.size),
        "median_stop_pct": round(100 * t["risk_frac_px"].median(), 3),
        "median_rr": round(t["rr"].median(), 2),
        "notional_capped_frac": round(float((t["notional"] >= 5 * SM.ACCOUNT * 0.999).mean()), 3),
    }


def controls(w: World, cfg: Config, real: pd.DataFrame, reps: int = 5) -> dict:
    o = w.orders(cfg)
    out = {}
    for name in CT.CONTROLS:
        pooled = pd.concat([w.trades(cfg, CT.apply(name, o, w.F.close_ff, w.valid, seed=r))
                            for r in range(reps)], ignore_index=True)
        d = diff_bootstrap(with_net(real), with_net(pooled))
        out[name] = {
            "net_R": round(pooled["net_R"].mean(), 4),
            "gross_R": round(pooled["gross_R"].mean(), 4),
            "trades_per_rep": round(len(pooled) / reps, 1),
            # diff_bootstrap reports x1e4 ("bps"); here the unit is R
            "strategy_minus_control_R": round(d["diff_bps"] / 1e4, 4),
            "ci95_R": [round(x / 1e4, 4) for x in d["ci95_bps"]],
        }
    return out
