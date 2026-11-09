"""The backtest engine: entry, daily marking, management, settlement.

CAUSALITY
---------
Every decision on day t uses only data from bars up to and including t, and
fills at day t's own close.  That is what an end-of-day trader does: look at
the close, send the order.  Nothing reads a price from t+1 to decide something
on t.

Exit at EXPIRY is the one place a later price is used, and it is used correctly:
each leg is settled at its OWN `SttlmPric` from the expiry-day bhavcopy.  That
is the exchange's own settlement value for that contract -- not a modelled
intrinsic, not an assumption about where the index closed.  It is the price the
position actually settles at, so using it is not hindsight, it is the fact.

PRICES
------
  entry / managed exit   `close`, and ONLY from a contract that traded that
                         session.  A stale close on an untraded strike is a
                         price nobody was quoting.
  daily mark             `settle`, defined for every contract.
  expiry                 `settle` on the expiry-day chain.

SLIPPAGE
--------
The bhavcopy has no bid-ask.  It is modelled, swept, and never assumed to be
zero -- see `SlippageModel`.  The far-OTM wing is where the book is thinnest and
is precisely the leg the strategy must buy, so a zero-slippage backtest would
systematically understate the cost of being hedged.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from . import blackscholes as bs
from .chain import Chain, load_chain, expiries_on, settlement_value
from .charges import ChargeCard, Leg
from .margin import MarginModel, PosLeg
from .structures import Structure


# --------------------------------------------------------------------------
# slippage
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class SlippageModel:
    """Half-spread paid on every fill, as a fraction of the option's price.

    Two limbs, because an option quoted at Rs 2.00 and one quoted at Rs 200
    have completely different relative spreads:

      rel     a floor proportion of the price (wide options)
      ticks   a floor in absolute ticks (cheap options, where the 0.05 tick is
              itself a large fraction of the price)

    The cost paid is the larger of the two.  `illiquid_multiple` widens it for
    contracts with few executed trades, which is what the far wing looks like.
    """
    rel: float = 0.01
    ticks: float = 1.0
    tick_size: float = 0.05
    illiquid_trades_below: int = 500
    illiquid_multiple: float = 2.0

    def half_spread(self, price: float, num_trades: int | None) -> float:
        base = max(price * self.rel, self.ticks * self.tick_size)
        if num_trades is not None and num_trades < self.illiquid_trades_below:
            base *= self.illiquid_multiple
        return base

    def fill(self, price: float, buying: bool, num_trades: int | None) -> float:
        h = self.half_spread(price, num_trades)
        return price + h if buying else max(price - h, 0.0)


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

@dataclass
class StrategyConfig:
    structure: str = "bull_put_spread"
    short_delta: float = 0.16
    wing_delta: float | None = 0.05        # wing chosen by delta ...
    wing_width_points: float | None = None  # ... or by a fixed point width
    dte_target: int = 30
    dte_tolerance: int = 10

    # management -- each is testable and most published claims are unverified
    profit_take_fraction: float | None = 0.50   # close at 50% of max profit
    stop_loss_credit_multiple: float | None = None  # close if loss > N x credit
    exit_dte: int | None = None                 # close with N days left
    hold_to_expiry: bool = False

    # Exit on a fixed percentage of CAPITAL EMPLOYED, in either direction.
    # This is a different rule from the two above: those are relative to the
    # structure's own credit or maximum profit, this one is relative to the
    # money the position ties up, which is what a "+2% or -2% and out" target
    # actually means.
    #
    # `capital_basis` decides what "capital employed" is, and the choice
    # matters enormously: margin runs at a median of ~26% of a structure's
    # maximum loss, so the same rupee move is ~4x larger as a percentage of
    # margin than as a percentage of capital at risk.
    target_pct_of_capital: float | None = None   # e.g. 0.02 for +2%
    stop_pct_of_capital: float | None = None     # e.g. 0.02 for -2%
    capital_basis: str = "max_loss"              # max_loss | margin

    # WHICH expiry, not just how far away.  NIFTY lists weeklies alongside
    # monthlies, so a "nearest to 30 DTE" rule silently picks whichever happens
    # to be closest -- and the answer changes over the sample as weeklies were
    # introduced (Feb 2019) and became liquid.  Left as "any" that is an
    # UNDECLARED regime change inside the strategy; naming it makes the choice
    # explicit and testable.
    expiry_type: str = "any"        # any | monthly | weekly

    # DIAGONAL structures only: how many expiries further out the bought leg
    # sits, and its target delta.  `far_expiry_offset=3` means "current+3",
    # which is the structure CALENDARS.md assesses.
    far_expiry_offset: int = 3
    far_wing_delta: float = 0.05
    # The far leg is 2-3 orders of magnitude thinner than the near one
    # (CALENDARS.md §3), so it gets its OWN liquidity gate rather than
    # inheriting the near leg's.
    far_min_trades: int = 1

    # RATIO: how many long wings per short leg.  1.0 is the ordinary vertical;
    # above 1.0 makes it a BACKSPREAD, where a crash stops being the disaster
    # case.  Below 1.0 leaves a naked short and is refused at build time.
    long_ratio: float = 1.0

    # liquidity gates
    min_trades: int = 100
    min_oi: int = 0

    # book
    max_concurrent: int = 1
    lots: int = 1
    symbol: str = "NIFTY"

    # capital.  MARGIN is the binding constraint for this strategy, not cash --
    # a credit structure receives money at entry and blocks margin against it.
    # Leave `capital` None for an unconstrained trade-level study (sweeps,
    # per-trade statistics); set it for the portfolio simulation.
    capital: float | None = None
    max_margin_utilisation: float = 0.50   # fraction of equity blockable
    size_by_margin_fraction: float | None = None  # lots sized to use this much
    max_lots: int = 100

    def describe(self) -> str:
        w = (f"wing@{self.wing_delta:.2f}d" if self.wing_delta is not None
             else f"wing+{self.wing_width_points:.0f}pt")
        mgmt = []
        if self.profit_take_fraction:
            mgmt.append(f"PT{int(self.profit_take_fraction * 100)}%")
        if self.stop_loss_credit_multiple:
            mgmt.append(f"SL{self.stop_loss_credit_multiple:g}x")
        if self.exit_dte:
            mgmt.append(f"exitDTE{self.exit_dte}")
        if self.hold_to_expiry:
            mgmt.append("hold")
        return (f"{self.structure} {self.short_delta:.2f}d {w} "
                f"DTE{self.dte_target}+-{self.dte_tolerance} "
                f"[{','.join(mgmt) or 'none'}]")


# --------------------------------------------------------------------------
# trade record
# --------------------------------------------------------------------------

@dataclass
class Trade:
    entry_date: date
    expiry: date                 # the NEAR expiry -- what ends the trade
    structure: str
    legs: list[tuple[float, bool, int]]
    lot_size: int
    far_expiry: date | None = None   # set only for a diagonal
    entry_forward: float = 0.0
    entry_credit: float = 0.0    # net credit received, after slippage
    max_profit: float = 0.0
    max_loss: float = 0.0
    margin_at_entry: float = 0.0
    peak_margin: float = 0.0
    exit_date: date | None = None
    exit_reason: str = ""
    exit_cost: float = 0.0       # what it cost to close (0 if expired worthless)
    gross_pnl: float = 0.0
    charges: float = 0.0
    net_pnl: float = 0.0
    max_adverse_excursion: float = 0.0   # worst mark-to-market loss seen
    days_held: int = 0
    entry_short_iv: float | None = None
    entry_short_delta: float | None = None
    marks: list[tuple[date, float, float]] = field(default_factory=list)
    # (date, mark-to-market P&L, margin)

    @property
    def return_on_margin(self) -> float:
        return self.net_pnl / self.margin_at_entry if self.margin_at_entry else 0.0

    @property
    def loss_per_margin(self) -> float:
        return min(self.net_pnl, 0.0) / self.margin_at_entry if self.margin_at_entry else 0.0

    def as_row(self) -> dict:
        return {
            "entry_date": self.entry_date.isoformat(),
            "exit_date": self.exit_date.isoformat() if self.exit_date else None,
            "expiry": self.expiry.isoformat(),
            "structure": self.structure,
            # legs are carried so the dashboard can draw the actual payoff
            # diagram rather than a stylised one
            "legs": [[k, bool(c), q] for k, c, q in self.legs],
            "lot_size": self.lot_size,
            "far_expiry": self.far_expiry.isoformat() if self.far_expiry else None,
            "days_held": self.days_held,
            "entry_forward": self.entry_forward,
            "entry_credit": self.entry_credit,
            "max_profit": self.max_profit,
            "max_loss": self.max_loss,
            "margin_at_entry": self.margin_at_entry,
            "peak_margin": self.peak_margin,
            "exit_reason": self.exit_reason,
            "exit_cost": self.exit_cost,
            "gross_pnl": self.gross_pnl,
            "charges": self.charges,
            "net_pnl": self.net_pnl,
            "return_on_margin": self.return_on_margin,
            "max_adverse_excursion": self.max_adverse_excursion,
            "entry_short_iv": self.entry_short_iv,
            "entry_short_delta": self.entry_short_delta,
        }


# --------------------------------------------------------------------------
# engine
# --------------------------------------------------------------------------

class Backtest:
    def __init__(self, conn: sqlite3.Connection, cfg: StrategyConfig,
                 charges: ChargeCard | None = None,
                 margin: MarginModel | None = None,
                 slippage: SlippageModel | None = None,
                 sigma_lookup=None):
        self.conn = conn
        self.cfg = cfg
        self.charges = charges or ChargeCard()
        self.margin = margin or MarginModel()
        self.slip = slippage or SlippageModel()
        self._sigma = sigma_lookup or self._sigma_from_db
        self._chain_cache: dict[tuple[date, date], Chain | None] = {}
        # (observed_on, trade_entry, trade_expiry) for every session a live
        # position could not be marked because a leg was missing.
        self.data_faults: list[tuple[date, date, date]] = []
        # expiries where the settlement value could not be established at all
        self.settlement_faults: list[tuple[date, date]] = []
        # how each settlement was obtained: underlying / settle / parity
        self.settlement_sources: dict[str, int] = {}
        # sessions where NSE published no volatility file and the previous
        # session's sigma was used instead, and sessions with none at all
        self.sigma_carried_forward = 0
        self.sigma_missing = 0
        self._monthly_cache: set[date] | None = None
        # how many times a margin figure was produced for a two-expiry
        # structure, which the model cannot do defensibly
        self.multi_expiry_margin_calls = 0
        # far leg of a diagonal had no tradeable quote when the trade ended
        self.far_leg_untradeable = 0
        # daily portfolio state, only populated when cfg.capital is set
        self.equity_curve: list[tuple[date, float, float, float]] = []
        # (date, equity, margin_used, margin_utilisation)
        self.rejected_for_margin = 0

    # -- data helpers ------------------------------------------------------

    def _sigma_from_db(self, on: date, symbol: str) -> float | None:
        """NSE's published daily volatility, carried forward if a day is missing.

        FOVOLT is not published for every session -- 2021-03-31 has a bhavcopy
        and no volatility file -- and returning None there made the margin model
        silently return ZERO margin for that trade.  A zero denominator then
        produced a return-on-margin of several hundred times, which is how a
        single missing file can poison every tail statistic downstream.

        A trader on that day would still have had the previous session's risk
        parameters, so the most recent earlier value is used and the substitution
        is COUNTED rather than hidden.
        """
        r = self.conn.execute(
            "SELECT daily_vol FROM fo_volt WHERE trade_date=? AND symbol=?",
            (on.isoformat(), symbol)).fetchone()
        if r:
            return float(r[0])
        r = self.conn.execute(
            "SELECT daily_vol FROM fo_volt WHERE trade_date<? AND symbol=? "
            "ORDER BY trade_date DESC LIMIT 1",
            (on.isoformat(), symbol)).fetchone()
        if r:
            self.sigma_carried_forward += 1
            return float(r[0])
        self.sigma_missing += 1
        return None

    def chain(self, on: date, expiry: date) -> Chain | None:
        key = (on, expiry)
        if key not in self._chain_cache:
            self._chain_cache[key] = load_chain(self.conn, on, self.cfg.symbol,
                                                expiry)
        return self._chain_cache[key]

    # -- structure construction -------------------------------------------

    def build(self, ch: Chain,
              far: Chain | None = None) -> tuple[Structure, dict, dict] | None:
        """Pick strikes on `ch` (and `far` for a diagonal).

        Returns (structure, entry_prices, meta).

        entry_prices are AFTER slippage, i.e. what the fill actually is.
        Returns None when any required leg is missing or illiquid -- never a
        partial structure, because a leg that could not be bought is an
        unhedged short.
        """
        c = self.cfg
        if ch.forward is None or ch.lot_size is None or ch.t_years <= 0:
            return None
        lot = ch.lot_size
        mt, mo = c.min_trades, c.min_oi

        def pick_wing(short_k: float, is_call: bool) -> float | None:
            if c.wing_width_points is not None:
                k = short_k + (c.wing_width_points if is_call
                               else -c.wing_width_points)
                q = ch.quote(k, is_call)
                return k if q and q.tradeable(mt, mo) else None
            got = ch.strike_by_delta(c.wing_delta, is_call, mt, mo)
            if got is None:
                return None
            k = got[0]
            # the wing must be strictly further out than the short leg
            if (is_call and k <= short_k) or (not is_call and k >= short_k):
                return None
            return k

        legs_spec: list[tuple[float, bool]] = []
        meta: dict = {}

        if c.structure in ("bull_put_spread", "naked_put"):
            got = ch.strike_by_delta(c.short_delta, False, mt, mo)
            if got is None:
                return None
            sk, sd, siv = got
            meta = {"short_delta": sd, "short_iv": siv}
            if c.structure == "naked_put":
                from .structures import naked_put
                st = naked_put(sk, lot, c.lots)
                legs_spec = [(sk, False)]
            else:
                lk = pick_wing(sk, False)
                if lk is None:
                    return None
                if c.long_ratio < 1.0:
                    raise ValueError(
                        "long_ratio below 1.0 leaves a naked short below the "
                        "long strike -- undefined risk, out of scope")
                from .structures import bull_put_spread
                st = bull_put_spread(sk, lk, lot, c.lots,
                                     long_ratio=c.long_ratio)
                legs_spec = [(sk, False), (lk, False)]

        elif c.structure == "bear_call_spread":
            got = ch.strike_by_delta(c.short_delta, True, mt, mo)
            if got is None:
                return None
            sk, sd, siv = got
            meta = {"short_delta": sd, "short_iv": siv}
            lk = pick_wing(sk, True)
            if lk is None:
                return None
            if c.long_ratio < 1.0:
                raise ValueError(
                    "long_ratio below 1.0 leaves a naked short -- out of scope")
            from .structures import bear_call_spread
            st = bear_call_spread(sk, lk, lot, c.lots,
                                  long_ratio=c.long_ratio)
            legs_spec = [(sk, True), (lk, True)]

        elif c.structure in ("iron_condor", "naked_strangle"):
            gp = ch.strike_by_delta(c.short_delta, False, mt, mo)
            gc = ch.strike_by_delta(c.short_delta, True, mt, mo)
            if gp is None or gc is None:
                return None
            spk, spd, spiv = gp
            sck, scd, sciv = gc
            meta = {"short_delta": (spd + scd) / 2, "short_iv": (spiv + sciv) / 2}
            if c.structure == "naked_strangle":
                from .structures import naked_strangle
                st = naked_strangle(spk, sck, lot, c.lots)
                legs_spec = [(spk, False), (sck, True)]
            else:
                lpk = pick_wing(spk, False)
                lck = pick_wing(sck, True)
                if lpk is None or lck is None:
                    return None
                from .structures import iron_condor
                st = iron_condor(spk, lpk, sck, lck, lot, c.lots)
                legs_spec = [(spk, False), (lpk, False), (sck, True), (lck, True)]
        elif c.structure in ("put_diagonal", "call_diagonal", "double_diagonal"):
            # Sell in the NEAR chain, buy further OTM in the FAR one.
            if far is None or far.forward is None or far.t_years <= 0:
                return None
            fmt = c.far_min_trades          # the far leg's own liquidity gate

            def far_wing(short_k: float, is_call: bool) -> float | None:
                got = far.strike_by_delta(c.far_wing_delta, is_call, fmt, mo)
                if got is None:
                    return None
                k = got[0]
                if (is_call and k <= short_k) or (not is_call and k >= short_k):
                    return None
                return k

            from .structures import (call_diagonal, double_diagonal,
                                     put_diagonal)
            if c.structure == "put_diagonal":
                got = ch.strike_by_delta(c.short_delta, False, mt, mo)
                if got is None:
                    return None
                sk, sd, siv = got
                lk = far_wing(sk, False)
                if lk is None:
                    return None
                st = put_diagonal(sk, ch.expiry, lk, far.expiry, lot, c.lots)
                meta = {"short_delta": sd, "short_iv": siv}
            elif c.structure == "call_diagonal":
                got = ch.strike_by_delta(c.short_delta, True, mt, mo)
                if got is None:
                    return None
                sk, sd, siv = got
                lk = far_wing(sk, True)
                if lk is None:
                    return None
                st = call_diagonal(sk, ch.expiry, lk, far.expiry, lot, c.lots)
                meta = {"short_delta": sd, "short_iv": siv}
            else:
                gp = ch.strike_by_delta(c.short_delta, False, mt, mo)
                gc = ch.strike_by_delta(c.short_delta, True, mt, mo)
                if gp is None or gc is None:
                    return None
                spk, spd, spiv = gp
                sck, scd, sciv = gc
                lpk = far_wing(spk, False)
                lck = far_wing(sck, True)
                if lpk is None or lck is None:
                    return None
                st = double_diagonal(spk, lpk, sck, lck, ch.expiry, far.expiry,
                                     lot, c.lots)
                meta = {"short_delta": (spd + scd) / 2,
                        "short_iv": (spiv + sciv) / 2}
            meta["far_iv"] = far.iv(
                st.legs[1].strike, st.legs[1].is_call) or 0.0
            meta["far_dte"] = far.dte
        else:
            raise ValueError(f"unknown structure {c.structure}")

        prices: dict = {}
        for leg in st.legs:
            lc = ch if (leg.expiry is None or leg.expiry == ch.expiry) else far
            if lc is None:
                return None
            q = lc.quote(leg.strike, leg.is_call)
            is_far = lc is far
            gate = c.far_min_trades if is_far else mt
            if q is None or not q.tradeable(gate, mo) or q.close <= 0:
                return None
            prices[leg.key] = self.slip.fill(
                q.close, buying=(leg.qty > 0), num_trades=q.num_trades)
        return st, prices, meta

    # -- marking -----------------------------------------------------------

    @staticmethod
    def _rebuild(ch: Chain, st: Structure, lots: int) -> Structure | None:
        """The same structure at `lots` lots.  Quantities scale, strikes do not."""
        from .structures import Structure as _S, StructLeg as _L
        base = st.lot_size
        if not base:
            return None
        legs = tuple(_L(l.strike, l.is_call,
                        (1 if l.qty > 0 else -1) * base * lots, l.expiry)
                     for l in st.legs)
        return _S(st.name, legs, base)

    def _leg_chain(self, today: date, leg, default: Chain) -> Chain | None:
        """The chain a LEG lives in.

        A single-expiry structure's legs carry expiry=None and all resolve to
        `default`.  A diagonal's legs each name their own expiry, so each is
        looked up in its own chain -- which is the whole reason the price key
        carries the expiry too.
        """
        if leg.expiry is None or leg.expiry == default.expiry:
            return default
        return self.chain(today, leg.expiry)

    def _mark_prices(self, today: date, ch: Chain, st: Structure,
                     field_name: str = "settle") -> dict | None:
        out = {}
        for leg in st.legs:
            lc = self._leg_chain(today, leg, ch)
            if lc is None:
                return None
            q = lc.quote(leg.strike, leg.is_call)
            if q is None:
                return None
            # On the NEAR leg's expiry day the far leg is still live and marks
            # normally; only a leg whose OWN expiry is today settles at
            # intrinsic.  That distinction is what makes a diagonal a diagonal.
            if leg.expiry is None or today >= leg.expiry:
                if today < (leg.expiry or ch.expiry):
                    out[leg.key] = getattr(q, field_name)
                    continue
                spot, how = settlement_value(lc)
                if spot is None:
                    self.settlement_faults.append((lc.trade_date, lc.expiry))
                    return None
                self.settlement_sources[how] = \
                    self.settlement_sources.get(how, 0) + 1
                out[leg.key] = (max(spot - leg.strike, 0.0) if leg.is_call
                                else max(leg.strike - spot, 0.0))
            else:
                out[leg.key] = getattr(q, field_name)
        return out

    def _margin_for(self, today: date, ch: Chain, st: Structure, prices: dict,
                    on: date) -> float:
        """SPAN-shaped margin.

        NOT DEFENSIBLE FOR A DIAGONAL, and it says so rather than returning a
        confident number: the model implements scan risk and ELM but NOT
        inter-month spread charges, which is exactly what governs a two-expiry
        position -- and SEBI withdrew the index calendar-spread benefit on
        expiry day from 2025-02-10.  For multi-expiry structures the margin is
        computed anyway (both legs scanned at their own tenor) but every
        headline return on a diagonal is reported against MAXIMUM LOSS instead.
        See CALENDARS.md section 5.
        """
        sig = self._sigma(on, self.cfg.symbol)
        if sig is None or ch.forward is None:
            return 0.0
        pos: list[PosLeg] = []
        for leg in st.legs:
            lc = self._leg_chain(today, leg, ch)
            if lc is None or lc.forward is None:
                continue
            px = prices[leg.key]
            t = max(lc.t_years, 1e-6)
            iv = bs.implied_vol(px, lc.forward, leg.strike, t,
                                leg.is_call, min_vega=1.0)
            if iv is None:
                iv = 0.15
            pos.append(PosLeg(leg.strike, leg.is_call, leg.qty, iv, px))
        if not pos:
            return 0.0
        if st.is_multi_expiry:
            self.multi_expiry_margin_calls += 1
        return self.margin.compute(pos, ch.forward, sig, max(ch.t_years, 1e-6),
                                   on, ch.expiry).total

    # -- the run -----------------------------------------------------------

    def run(self, days: list[date]) -> list[Trade]:
        c = self.cfg
        trades: list[Trade] = []
        open_trades: list[tuple[Trade, Structure]] = []
        realised = 0.0                 # closed-trade P&L booked so far

        for today in days:
            # ---- manage what is open, BEFORE considering a new entry -----
            still: list[tuple[Trade, Structure]] = []
            for tr, st in open_trades:
                closed = self._manage(tr, st, today)
                if closed:
                    realised += tr.net_pnl
                else:
                    still.append((tr, st))
            open_trades = still

            # ---- portfolio state, marked daily ---------------------------
            open_mtm = sum(tr.marks[-1][1] for tr, _ in open_trades
                           if tr.marks and tr.marks[-1][0] == today)
            margin_used = sum(tr.marks[-1][2] for tr, _ in open_trades
                              if tr.marks and tr.marks[-1][0] == today)
            if c.capital is not None:
                equity = c.capital + realised + open_mtm
                self.equity_curve.append(
                    (today, equity, margin_used,
                     margin_used / equity if equity > 0 else float("inf")))
            else:
                equity = float("inf")

            # ---- entry ---------------------------------------------------
            if len(open_trades) >= c.max_concurrent:
                continue
            if c.capital is not None and equity <= 0:
                continue   # the book is gone; stop trading rather than pretend
            from .structures import MULTI_EXPIRY
            need_far = c.structure in MULTI_EXPIRY
            all_exps = expiries_on(self.conn, today, c.symbol) if need_far else []

            built = None
            ch = None
            far_ch = None
            for exp in self._candidate_expiries(today):
                if any(t.expiry == exp for t, _ in open_trades):
                    continue
                ch = self.chain(today, exp)
                if ch is None:
                    continue
                far_ch = None
                if need_far:
                    # the expiry `far_expiry_offset` places beyond this one
                    try:
                        i = all_exps.index(exp)
                    except ValueError:
                        continue
                    j = i + c.far_expiry_offset
                    if j >= len(all_exps):
                        continue
                    far_ch = self.chain(today, all_exps[j])
                    if far_ch is None:
                        continue
                built = self.build(ch, far_ch)
                if built is not None:
                    break
            if built is None:
                continue
            st, prices, meta = built
            credit = st.net_credit(prices)
            if credit <= 0:
                continue  # a credit structure that is not a credit is a data fault

            margin0 = self._margin_for(today, ch, st, prices, today)

            # ---- size the position to a MARGIN target --------------------
            # Everything about a spread is linear in quantity -- credit, max
            # loss, scan risk and ELM all scale together -- so the number of
            # lots that fits a margin budget is a division, not a search.
            #
            # This matters for the portfolio simulation rather than for the
            # per-trade statistics.  At one lot the book sits ~96% idle and any
            # CAGR computed on the full capital is meaningless: it measures how
            # much cash was left alone, not how the strategy performed.  Return
            # on MARGIN is the scale-invariant number and is reported either way.
            if c.size_by_margin_fraction and c.capital is not None and margin0 > 0:
                budget = equity * c.size_by_margin_fraction
                want = max(1, min(c.max_lots, int(budget // margin0)))
                if want > 1:
                    rebuilt = self._rebuild(ch, st, want)
                    if rebuilt is None:
                        continue
                    st = rebuilt
                    prices = {k: v for k, v in prices.items()}
                    credit = st.net_credit(prices)
                    margin0 = self._margin_for(today, ch, st, prices, today)

            if c.capital is not None:
                budget = equity * c.max_margin_utilisation - margin_used
                if margin0 > budget:
                    self.rejected_for_margin += 1
                    continue

            entry_charges = self.charges.charge_trade(
                [Leg("SELL" if l.qty < 0 else "BUY",
                     Decimal(str(prices[l.key])), abs(l.qty))
                 for l in st.legs], today)

            tr = Trade(entry_date=today, expiry=exp, structure=st.name,
                       legs=[(l.strike, l.is_call, l.qty) for l in st.legs],
                       lot_size=st.lot_size,
                       far_expiry=(far_ch.expiry if far_ch else None),
                       entry_forward=ch.forward,
                       entry_credit=credit, max_profit=st.max_profit(prices),
                       max_loss=st.max_loss(prices), margin_at_entry=margin0,
                       peak_margin=margin0,
                       charges=float(entry_charges.total),
                       entry_short_iv=meta.get("short_iv"),
                       entry_short_delta=meta.get("short_delta"))
            trades.append(tr)
            open_trades.append((tr, st))

        # Anything still open at the end of the sample is reported, not dropped,
        # and is marked at its LAST observed mark rather than at zero -- a zero
        # would quietly count an open loser as a scratch.  The reason string is
        # kept so that summary statistics can exclude these explicitly instead
        # of silently blending an unfinished trade into a completed-trade mean.
        for tr, st in open_trades:
            tr.exit_reason = "still_open_at_sample_end"
            tr.exit_date = days[-1] if days else tr.entry_date
            if tr.marks:
                last_day, last_mtm, _ = tr.marks[-1]
                tr.gross_pnl = last_mtm
                tr.net_pnl = last_mtm - tr.charges
                tr.days_held = (last_day - tr.entry_date).days
        return trades

    def _monthlies(self) -> set[date]:
        """Expiries that are the LAST listed expiry of their calendar month."""
        if self._monthly_cache is None:
            last: dict[tuple[int, int], date] = {}
            for (v,) in self.conn.execute(
                    "SELECT DISTINCT expiry FROM fo_bhav WHERE symbol=? "
                    "  AND kind IN ('IDO','STO') ORDER BY expiry",
                    (self.cfg.symbol,)).fetchall():
                e = date.fromisoformat(v)
                k = (e.year, e.month)
                if k not in last or e > last[k]:
                    last[k] = e
            self._monthly_cache = set(last.values())
        return self._monthly_cache

    def _candidate_expiries(self, today: date) -> list[date]:
        """Every expiry inside the DTE window, best first.

        Returns a RANKED LIST rather than a single choice.  Picking one expiry
        and skipping the day when its chain cannot be built silently biases the
        sample: in 2019-2021 the nearest-to-target expiry was often a thin
        weekly, so those sessions produced no trade at all even though a liquid
        monthly sat one day further away.
        """
        c = self.cfg
        monthlies = self._monthlies() if c.expiry_type != "any" else set()
        out = []
        for e in expiries_on(self.conn, today, c.symbol):
            dte = (e - today).days
            if abs(dte - c.dte_target) > c.dte_tolerance:
                continue
            if c.expiry_type == "monthly" and e not in monthlies:
                continue
            if c.expiry_type == "weekly" and e in monthlies:
                continue
            out.append((abs(dte - c.dte_target), e))
        return [e for _, e in sorted(out)]

    def _manage(self, tr: Trade, st: Structure, today: date) -> bool:
        """Mark, then apply the exit rules.  Returns True when closed."""
        c = self.cfg
        ch = self.chain(today, tr.expiry)
        if ch is None:
            return False

        at_expiry = today >= tr.expiry
        settle = self._mark_prices(today, ch, st, "settle")
        if settle is None:
            # A leg of a LIVE position has no row in today's chain.  On real NSE
            # data this should never happen -- strikes are not delisted -- so it
            # is counted and surfaced rather than swallowed.  Swallowing it
            # would leave a losing position permanently open and quietly drop it
            # from the completed-trade statistics.
            self.data_faults.append((today, tr.entry_date, tr.expiry))
            return False
        # mark-to-market: credit received less what it would cost to close.
        # net_credit() is already "what you receive to OPEN", so it is also
        # "what you pay to CLOSE" -- the sign is not flipped again.
        cost_now = st.net_credit(settle)
        mtm = tr.entry_credit - cost_now
        margin_now = (self._margin_for(today, ch, st, settle, today)
                      if today < tr.expiry else 0.0)
        tr.peak_margin = max(tr.peak_margin, margin_now)
        tr.max_adverse_excursion = min(tr.max_adverse_excursion, mtm)
        tr.marks.append((today, mtm, margin_now))

        # ---- expiry: each leg at its INTRINSIC against the settlement ----
        if at_expiry:
            return self._close(tr, st, ch, today, settle, "expiry", at_expiry=True)

        # ---- managed exits ----------------------------------------------
        # To close early, every leg must be TRADEABLE.  If it is not, the
        # position stays open and the exit happens on the next day it can
        # actually be closed -- a spread you cannot trade out of is a real
        # risk, not a rounding error.
        close_px = self._tradeable_prices(today, ch, st)
        if close_px is None:
            return False

        # DECIDE on the same prices the trade would FILL at.  The daily mark
        # uses `settle`, which is defined for every contract and is the right
        # thing to carry a position at -- but an exit decided on settle and
        # filled at close is deciding on one number and transacting at another.
        # Both are end-of-day quantities, so this is not a lookahead; it is
        # simply consistency, and it costs nothing to get right.
        mtm_exit = tr.entry_credit - st.net_credit(close_px)

        # capital employed, on whichever basis the config names
        capital = (tr.margin_at_entry if c.capital_basis == "margin"
                   else tr.max_loss)

        reason = None
        if c.target_pct_of_capital is not None and capital > 0:
            if mtm_exit >= c.target_pct_of_capital * capital:
                reason = f"target_{c.target_pct_of_capital * 100:g}pct"
        if reason is None and c.stop_pct_of_capital is not None and capital > 0:
            if mtm_exit <= -c.stop_pct_of_capital * capital:
                reason = f"stop_{c.stop_pct_of_capital * 100:g}pct"
        # PROFIT TARGET IS A FRACTION OF THE CREDIT, NOT OF `max_profit`.
        #
        # For a vertical, a condor or a fly the two are identical -- you cannot
        # make more than the credit -- so this changes nothing for them.
        #
        # For a BACKSPREAD they are not: its max_profit is the payoff at spot
        # approaching zero, which is astronomically large (Rs 840,000 against a
        # Rs 1,600 credit at ratio 1:2). A target set as a fraction of that is
        # unreachable, so every backspread silently ran to expiry while the
        # vertical it was being compared against took profit at 50% -- two
        # different management rules presented as one comparison.
        if reason is None and c.profit_take_fraction is not None \
                and tr.entry_credit > 0:
            if mtm_exit >= c.profit_take_fraction * tr.entry_credit:
                reason = f"profit_take_{int(c.profit_take_fraction * 100)}"
        if reason is None and c.stop_loss_credit_multiple is not None:
            if mtm_exit <= -c.stop_loss_credit_multiple * tr.entry_credit:
                reason = f"stop_{c.stop_loss_credit_multiple:g}x"
        if reason is None and c.exit_dte is not None:
            if (tr.expiry - today).days <= c.exit_dte:
                reason = f"exit_dte_{c.exit_dte}"
        if reason is None:
            return False
        return self._close(tr, st, ch, today, close_px, reason)

    def _tradeable_prices(self, today: date, ch: Chain,
                          st: Structure) -> dict | None:
        """Fill prices for closing, or None if any leg cannot be traded today.

        The far leg of a diagonal gets its OWN liquidity gate (`far_min_trades`)
        rather than the near leg's: three expiries out a 5-delta strike trades
        two orders of magnitude less (CALENDARS.md section 3), and applying the
        near leg's threshold to it would refuse every diagonal ever.
        """
        c = self.cfg
        out = {}
        for leg in st.legs:
            lc = self._leg_chain(today, leg, ch)
            if lc is None:
                return None
            q = lc.quote(leg.strike, leg.is_call)
            if q is None:
                return None
            is_far = leg.expiry is not None and leg.expiry != st.near_expiry
            gate = c.far_min_trades if is_far else c.min_trades
            if not q.tradeable(gate, c.min_oi):
                return None
            out[leg.key] = self.slip.fill(
                q.close, buying=(leg.qty < 0), num_trades=q.num_trades)
        return out

    def _close(self, tr: Trade, st: Structure, ch: Chain, today: date,
               prices: dict, reason: str, at_expiry: bool = False) -> bool:
        """Book the exit.

        THE DISTINCTION THAT MATTERS FOR A DIAGONAL: on the near leg's expiry
        day the SHORT leg settles -- not a transaction, so no brokerage, no GST
        and no STT on premium -- while the FAR leg is still live and must be
        SOLD, which is an ordinary transaction with slippage and full charges.
        Treating the whole structure as "expired" would hand the far leg's sale
        to the exchange for free, which is worth real money on a position whose
        long leg is the expensive half.
        """
        c = self.cfg
        exiting = dict(prices)

        if at_expiry:
            # Legs whose OWN expiry is today settle; anything still live is sold.
            expiring = [l for l in st.legs
                        if l.expiry is None or today >= l.expiry]
            live = [l for l in st.legs if l not in expiring]

            # the far leg is SOLD at a tradeable price where one exists
            for leg in live:
                lc = self._leg_chain(today, leg, ch)
                q = lc.quote(leg.strike, leg.is_call) if lc else None
                if q is not None and q.tradeable(c.far_min_trades, c.min_oi):
                    exiting[leg.key] = self.slip.fill(
                        q.close, buying=(leg.qty < 0), num_trades=q.num_trades)
                else:
                    # no trade available: carried at its settle mark and counted,
                    # because an untradeable long leg is a real liquidity event
                    self.far_leg_untradeable += 1

            cost = st.net_credit(exiting)
            settle_spot = self._settlement_spot(ch, st, exiting)

            # settled legs: STT on the intrinsic of a LONG leg finishing ITM
            intr, exp_legs = [], []
            for leg in expiring:
                iv = (max(settle_spot - leg.strike, 0.0) if leg.is_call
                      else max(leg.strike - settle_spot, 0.0))
                intr.append(Decimal(str(iv)))
                exp_legs.append(Leg("SELL" if leg.qty < 0 else "BUY",
                                    Decimal("0"), abs(leg.qty)))
            exit_charges = self.charges.charge_expiry(exp_legs, intr, today)

            # live legs: an ordinary sale, charged as one
            if live:
                exit_charges = exit_charges + self.charges.charge_trade(
                    [Leg("BUY" if l.qty < 0 else "SELL",
                         Decimal(str(exiting[l.key])), abs(l.qty))
                     for l in live], today)
        else:
            cost = st.net_credit(exiting)
            exit_charges = self.charges.charge_trade(
                [Leg("BUY" if l.qty < 0 else "SELL",
                     Decimal(str(exiting[l.key])), abs(l.qty))
                 for l in st.legs], today)

        tr.exit_date = today
        tr.exit_reason = reason
        tr.exit_cost = cost
        tr.gross_pnl = tr.entry_credit - cost
        tr.days_held = (today - tr.entry_date).days
        tr.charges += float(exit_charges.total)
        tr.net_pnl = tr.gross_pnl - tr.charges
        return True

    @staticmethod
    def _settlement_spot(ch: Chain, st: Structure, prices: dict) -> float:
        spot, _ = settlement_value(ch)
        return spot if spot is not None else (ch.underlying or ch.forward or 0.0)


__all__ = ["Backtest", "StrategyConfig", "Trade", "SlippageModel"]
