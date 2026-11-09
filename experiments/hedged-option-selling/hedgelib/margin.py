"""SPAN-shaped margin for an index option structure.

The single most important thing this module does is REPRICE BOTH LEGS under
every scenario.  That is what makes the bought wing's margin benefit a measured
quantity rather than an assumption -- and the margin benefit is the entire
economic reason a retail seller buys the wing at all.

Driven by NSE's own published daily volatility (FOVOLT), so the requirement
expands in a crisis exactly when the strategy is losing.  A flat percentage-of-
notional model gets that backwards and flatters the strategy precisely where it
is most dangerous.

Marked APPROXIMATION.  See conf/margin/nse-index-options-margin.yaml for what
it is not, and why no historical backtest can do better.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from . import blackscholes as bs

DEFAULT_MODEL = (Path(__file__).resolve().parents[1]
                 / "conf/margin/nse-index-options-margin.yaml")


@dataclass(frozen=True)
class PosLeg:
    """One leg of a live structure, as the margin model needs to see it.

    qty is SIGNED in units: negative for short.  `iv` is the leg's own implied
    volatility, backed out of its real traded price -- so the smile is carried
    into the scenarios rather than flattened.
    """
    strike: float
    is_call: bool
    qty: int
    iv: float
    price: float            # current per-unit mark, from real data

    @property
    def is_short(self) -> bool:
        return self.qty < 0


@dataclass
class MarginResult:
    scan_risk: float
    elm: float
    net_premium: float
    total: float
    worst_scenario: int
    worst_scenario_desc: str
    price_scan_range: float
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"scan_risk": self.scan_risk, "elm": self.elm,
                "net_premium": self.net_premium, "total": self.total,
                "worst_scenario": self.worst_scenario,
                "worst_scenario_desc": self.worst_scenario_desc,
                "price_scan_range": self.price_scan_range, **self.detail}


class MarginModel:
    def __init__(self, path: str | Path = DEFAULT_MODEL):
        self.path = Path(path)
        with open(self.path) as f:
            self.cfg = yaml.safe_load(f)
        self.version = self.cfg["version"]
        self._scen = self._build_scenarios()

    # -- config helpers ----------------------------------------------------

    def _dated(self, node: list, on: date) -> float:
        chosen = None
        for e in node:
            f = e["from"]
            f = f if isinstance(f, date) else date.fromisoformat(str(f))
            if f <= on:
                chosen = e["value"]
        return 0.0 if chosen is None else float(chosen)

    def _build_scenarios(self) -> list[tuple[float, float, float, str]]:
        """(price_fraction_of_psr, vol_direction, weight, description)."""
        s = self.cfg["scenarios"]
        out: list[tuple[float, float, float, str]] = []
        for pf in (0.0, 1 / 3, -1 / 3, 2 / 3, -2 / 3, 1.0, -1.0):
            for vd in (1.0, -1.0):
                out.append((pf, vd, 1.0,
                            f"F{pf:+.2f}xPSR, vol{'+' if vd > 0 else '-'}"))
        m, w = float(s["extreme_move_multiple"]), float(s["extreme_move_weight"])
        out.append((m, 0.0, w, f"F+{m:g}xPSR extreme (weight {w})"))
        out.append((-m, 0.0, w, f"F-{m:g}xPSR extreme (weight {w})"))
        return out

    # -- the model ---------------------------------------------------------

    def price_scan_range(self, forward: float, sigma_daily: float) -> float:
        c = self.cfg["price_scan_range"]
        mult = float(c["index"]["sigma_multiple"])
        mpor = float(c["mpor_days"]) ** 0.5
        floor = float(c["index"]["minimum_fraction_of_underlying"])
        return max(mult * sigma_daily * mpor, floor) * forward

    def compute(self, legs: list[PosLeg], forward: float, sigma_daily: float,
                t_years: float, on: date, expiry: date) -> MarginResult:
        """Initial margin = SPAN scan risk + ELM (+ net premium payable).

        Values are total rupees for the structure as given.
        """
        psr = self.price_scan_range(forward, sigma_daily)
        vsr = float(self.cfg["volatility_scan_range"]["relative"])

        def value_at(f: float, vol_mult: float) -> float:
            v = 0.0
            for leg in legs:
                iv = max(leg.iv * vol_mult, 1e-4)
                v += leg.qty * bs.price(f, leg.strike, t_years, iv, leg.is_call)
            return v

        base = value_at(forward, 1.0)
        worst_loss, worst_i, worst_desc = 0.0, -1, "no scenario loses"
        for i, (pf, vd, w, desc) in enumerate(self._scen):
            f = forward + pf * psr
            vm = 1.0 + vd * vsr
            loss = (base - value_at(f, vm)) * w
            if loss > worst_loss:
                worst_loss, worst_i, worst_desc = loss, i, desc
        scan_risk = max(worst_loss, 0.0)

        elm = self._elm(legs, forward, t_years, on, expiry)

        net_prem = 0.0
        inc_from = self.cfg["net_option_premium"]["include_from"]
        inc_from = (inc_from if isinstance(inc_from, date)
                    else date.fromisoformat(str(inc_from)))
        if on >= inc_from:
            # sum(qty x price) is what the structure COSTS to open: a bought leg
            # has positive qty and is paid for, a sold leg has negative qty and
            # is received.  So it is positive for a net DEBIT and negative for a
            # net credit, and only a debit adds to the upfront requirement.
            #
            # The sign here was inverted at first, which added the CREDIT to the
            # margin of every credit structure -- inflating the calibration case
            # by exactly the premium received.
            paid = sum(leg.qty * leg.price for leg in legs)
            net_prem = max(paid, 0.0)

        return MarginResult(
            scan_risk=scan_risk, elm=elm, net_premium=net_prem,
            total=scan_risk + elm + net_prem,
            worst_scenario=worst_i, worst_scenario_desc=worst_desc,
            price_scan_range=psr,
            detail={"base_value": base, "sigma_daily": sigma_daily,
                    "vol_scan_range": vsr})

    def _elm(self, legs: list[PosLeg], forward: float, t_years: float,
             on: date, expiry: date) -> float:
        """ELM on short option legs.

        The load-bearing choice here is `net_long_against_short`, because the
        FAQ's "ELM shall be netted at client and prop levels" does not say
        whether a long option at a DIFFERENT strike offsets a short one.

        Both readings are implemented.  The netting reading is the default,
        because the gross reading produces a margin for a 500-point NIFTY
        credit spread that is roughly twice the structure's own maximum loss
        and about 60% of the naked requirement -- while brokers report hedged
        spreads at Rs 15,000-30,000 per lot and at "roughly a quarter to a
        third of the margin a naked short needs".  The netting reading lands
        inside both.  See MARGIN.md: this is a CALIBRATION against reported
        figures, not a reading of the rule text, and the gross alternative is
        carried through the sweep so the choice is never hidden.
        """
        c = self.cfg["extreme_loss_margin"]
        addon = self._dated(c["expiry_day_addon"], on)
        deep = float(c["deep_otm_delta_below"])
        long_dated_days = float(c["long_dated_days_above"])
        netting = bool(c.get("net_long_against_short", False))

        def rate_for(leg: PosLeg) -> float:
            g = bs.greeks(forward, leg.strike, t_years, leg.iv, leg.is_call)
            if t_years * 365.0 > long_dated_days:
                r = float(c["short_index_option_long_dated"])
            elif abs(g.delta) < deep:
                r = float(c["short_index_option_deep_otm"])
            else:
                r = float(c["short_index_option"])
            return r + (addon if on == expiry else 0.0)

        basis_of = (lambda leg: forward) if c["notional_basis"] == "forward" \
            else (lambda leg: leg.strike)

        shorts = [l for l in legs if l.is_short]
        if not shorts:
            return 0.0
        if not netting:
            return sum(rate_for(l) * basis_of(l) * abs(l.qty) for l in shorts)

        # Netted: longs in the same option TYPE offset shorts unit for unit,
        # cheapest-to-offset first (the short legs with the largest charge are
        # relieved first, which is what a portfolio-risk system does).
        total = 0.0
        for is_call in (True, False):
            s = sorted((l for l in shorts if l.is_call == is_call),
                       key=lambda l: -rate_for(l) * basis_of(l))
            long_units = sum(l.qty for l in legs
                             if not l.is_short and l.is_call == is_call)
            for leg in s:
                short_units = abs(leg.qty)
                offset = min(long_units, short_units)
                long_units -= offset
                exposed = short_units - offset
                if exposed > 0:
                    total += rate_for(leg) * basis_of(leg) * exposed
        return total


__all__ = ["MarginModel", "PosLeg", "MarginResult"]
