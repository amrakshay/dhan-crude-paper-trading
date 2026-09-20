"""Walk-forward portfolio simulation for a market-neutral pairs book.

Cash-accounted, both legs, costs from the rate card, margin on the futures
legs, and marked to market every day -- so the drawdown reported is the one
that would have been lived through, not the one visible at exits only.

Three decisions in here are worth reading before the numbers are believed.

**Fills are at the next open.** A signal read off the close of day t is
executed at the open of day t+1. Filling at the signal bar's own close is a
one-bar lookahead and for a mean-reversion strategy it is the worst kind:
the signal fires exactly at the extreme, so the fill would always be at the
best price of the move.

**The standardisation is frozen at formation.** mu and sigma come from the
formation window and never move. A trailing re-estimate lets a spread that is
drifting apart redefine its own mean until it looks normal again, which hides
the structural break the stop exists to catch.

**A negative hedge ratio is refused.** beta < 0 means the fitted relation puts
both legs on the SAME side, which is not a hedge -- it is a leveraged
directional bet with a market-neutral label. Those pairs are dropped, and how
many is reported.

**Two spread specifications, two sizing rules.** A LEVEL spread
`P_b - beta*P_a` is a fixed SHARE ratio: `q_a = beta * q_b`, and it stays
hedged as prices move without anything being done to it. A LOG spread
`ln P_b - beta*ln P_a` is a fixed VALUE ratio: the position holds `V_b` in b
and `beta*V_b` in a, so the share counts are `q_b = V_b/P_b` and
`q_a = beta*V_b/P_a`. That ratio drifts out of hedge as soon as the prices
move, and holding it properly means rebalancing and paying to do so. It is
NOT rebalanced here -- the drift over a twelve-bar hold is small and
rebalancing costs are not modelled -- and that is an optimism in favour of
the log variant, stated rather than hidden.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import numpy as np

from . import charges as ch

LONG_SPREAD, SHORT_SPREAD = 1, -1


@dataclass
class TradeRules:
    z_in: float = 2.0
    z_out: float = 0.5
    z_stop: float = 3.5
    hold_cap_half_lives: float = 4.0     # bars = this x the estimated half-life
    hold_cap_max: int = 60               # absolute ceiling, bars
    max_half_life: float = 30.0          # a pair slower than this is not traded
    min_beta: float = 0.05               # refuse a degenerate hedge ratio


@dataclass
class BookRules:
    capital: float = 1_000_000.0
    gross_per_pair: float = 200_000.0    # long leg + short leg, at entry
    max_open: int = 10
    card: str = "nse-equity-futures"
    margin_fraction: float | None = None  # None -> read it from the card
    collateral_yield: float = 1.0
    # Share of the policy rate earned on cash and on posted margin. The
    # default is 1.0 -- FULLY earning -- and that is the realistic setting for
    # an Indian futures book: exchange margin can be met by pledging liquid
    # fund or treasury-bill units, and cash not posted sits in the same place.
    #
    # It also makes the headline number mean something. This book is deployed
    # about a fifth of the time, so with idle cash earning zero the reported
    # CAGR is mostly a statement about how long the strategy sat out, and the
    # "excess over risk-free" becomes the cost of holding cash rather than a
    # measure of the trades. At 1.0 the excess IS the trading contribution.
    # The pessimistic 0.0 setting is swept in PAIRS_SWEEP.md.
    compound: bool = False


@dataclass
class Trade:
    pair: str
    a: str
    b: str
    beta: float
    direction: int
    entry_date: str
    exit_date: str | None = None
    entry_z: float = 0.0
    exit_z: float = 0.0
    qa: float = 0.0
    qb: float = 0.0
    pa_in: float = 0.0
    pb_in: float = 0.0
    pa_out: float = 0.0
    pb_out: float = 0.0
    gross: float = 0.0
    cost: float = 0.0
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    bars: int = 0
    reason: str = ""
    half_life: float = 0.0
    pvalue: float = 1.0
    # The frozen formation constants, carried on the trade so a chart can
    # reproduce the EXACT z-score the strategy acted on. Re-standardising a
    # spread over the displayed window instead gives a line the trade markers
    # do not sit on, which looks like a plotting wobble and is actually a
    # different series.
    alpha: float = 0.0
    mu: float = 0.0
    sigma: float = 1.0
    log_prices: bool = False

    @property
    def ret_on_gross(self) -> float:
        return self.net_pnl / self.gross if self.gross else 0.0


@dataclass
class Result:
    equity: list[float] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    gross_exposure: list[float] = field(default_factory=list)
    net_exposure: list[float] = field(default_factory=list)
    margin_used: list[float] = field(default_factory=list)
    open_count: list[int] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    skipped: dict = field(default_factory=dict)


def _leg_cost(card: str, side: str, turnover: float, on: str) -> float:
    return float(ch.charge(card, ch.Leg(side, Decimal(str(round(turnover, 2))),
                                        date.fromisoformat(str(on)[:10]))).total)


def simulate(plan, dates, close, opn, rules: TradeRules, book: BookRules) -> Result:
    """`plan` is a list of `(start_idx, end_idx, [Candidate, ...])` windows.

    Windows must be disjoint and in order. Every position is closed at its
    window's last bar: a pair's selection expires with its window, and
    carrying it under a formation that has not been re-tested is how a
    backtest holds a broken spread to a happy ending.
    """
    margin_frac = (book.margin_fraction if book.margin_fraction is not None
                   else float(ch.margin_fraction(book.card)))
    equity = book.capital
    res = Result()
    skipped = {"negative_beta": 0, "slow": 0, "tiny_beta": 0, "no_price": 0,
               "no_slot": 0, "no_cash": 0}
    open_pos: list[dict] = []

    for start, end, cands in plan:
        usable = []
        for c in cands:
            if c.beta <= 0:
                skipped["negative_beta"] += 1
                continue
            if abs(c.beta) < rules.min_beta:
                skipped["tiny_beta"] += 1
                continue
            if not (0 < c.half_life <= rules.max_half_life):
                skipped["slow"] += 1
                continue
            usable.append(c)

        for i in range(start, end + 1):
            day = str(dates[i])
            last_bar = (i == end)

            # ---- mark to market, then act ------------------------------
            for p in open_pos:
                pa, pb = close[p["a"]][i], close[p["b"]][i]
                if np.isfinite(pa) and np.isfinite(pb):
                    p["pa_last"], p["pb_last"] = pa, pb
                p["bars"] += 1

            # ---- exits ------------------------------------------------
            still = []
            for p in open_pos:
                c = p["cand"]
                pa, pb = p["pa_last"], p["pb_last"]
                z = float(c.zscore(np.array([pb]), np.array([pa]))[0])
                reason = ""
                if last_bar:
                    reason = "window"
                elif p["direction"] == LONG_SPREAD and z >= -rules.z_out:
                    reason = "target"
                elif p["direction"] == SHORT_SPREAD and z <= rules.z_out:
                    reason = "target"
                elif abs(z) >= rules.z_stop and np.sign(z) == np.sign(p["entry_z"]):
                    reason = "stop"
                elif p["bars"] >= p["cap"]:
                    reason = "cap"
                if not reason:
                    still.append(p)
                    continue
                # Fill at the NEXT open; on the window's last bar there is no
                # next open inside the window, so the close is used and the
                # trade is flagged -- it is the only fill in the book that is
                # not at an open.
                j = min(i + 1, len(dates) - 1)
                oa, ob = opn[p["a"]][j], opn[p["b"]][j]
                if last_bar or not (np.isfinite(oa) and np.isfinite(ob)):
                    oa, ob = pa, pb
                equity += _close(p, oa, ob, day, z, reason, book, res)
            open_pos = still

            # ---- entries ----------------------------------------------
            # NOTE: this block must NOT `continue` out of the day. Skipping
            # ahead when the book is full also skipped the recording block
            # below, so the equity and exposure series lost a point on exactly
            # the days the book was fullest -- and the drawdown was then
            # computed from a series with its most-invested days missing. The
            # symptom was an equity series 1,868 long where the date axis was
            # 2,016, noticed only because a baseline with a different fill rate
            # came out a different length.
            can_enter = not last_bar and len(open_pos) < book.max_open
            held = {p["cand"].a for p in open_pos} | {p["cand"].b for p in open_pos}
            for c in (usable if can_enter else ()):
                if len(open_pos) >= book.max_open:
                    break
                if c.a in held or c.b in held:
                    continue
                pa, pb = close[c.a][i], close[c.b][i]
                if not (np.isfinite(pa) and np.isfinite(pb)):
                    continue
                z = float(c.zscore(np.array([pb]), np.array([pa]))[0])
                if abs(z) < rules.z_in or abs(z) >= rules.z_stop:
                    continue
                j = i + 1
                if j > end:
                    continue
                oa, ob = opn[c.a][j], opn[c.b][j]
                if not (np.isfinite(oa) and np.isfinite(ob)):
                    skipped["no_price"] += 1
                    continue
                gross = book.gross_per_pair * (equity / book.capital if book.compound else 1.0)
                if c.log_prices:
                    vb = gross / (1.0 + c.beta)     # value-weighted, see the docstring
                    qb, qa = vb / ob, c.beta * vb / oa
                else:
                    qb = gross / (ob + c.beta * oa)  # share-weighted
                    qa = c.beta * qb
                need = margin_frac * gross
                in_use = sum(p["margin"] for p in open_pos)
                if in_use + need > equity:
                    skipped["no_cash"] += 1
                    continue
                direction = LONG_SPREAD if z < 0 else SHORT_SPREAD
                # LONG the spread = long b, short a. SHORT = the reverse.
                side_b = "BUY" if direction == LONG_SPREAD else "SELL"
                side_a = "SELL" if direction == LONG_SPREAD else "BUY"
                cost = (_leg_cost(book.card, side_b, qb * ob, str(dates[j]))
                        + _leg_cost(book.card, side_a, qa * oa, str(dates[j])))
                equity -= cost
                open_pos.append({
                    "cand": c, "a": c.a, "b": c.b, "direction": direction,
                    "qa": qa, "qb": qb, "pa_in": oa, "pb_in": ob,
                    "pa_last": pa, "pb_last": pb, "entry_date": str(dates[j]),
                    "entry_z": z, "bars": 0, "cost": cost, "margin": need,
                    "gross": qb * ob + qa * oa,
                    "cap": int(min(rules.hold_cap_max,
                                   max(3, round(rules.hold_cap_half_lives * c.half_life)))),
                })
                held |= {c.a, c.b}

            # ---- record ------------------------------------------------
            unreal, gross_x, net_x = 0.0, 0.0, 0.0
            for p in open_pos:
                d = p["direction"]
                unreal += (d * p["qb"] * (p["pb_last"] - p["pb_in"])
                           - d * p["qa"] * (p["pa_last"] - p["pa_in"]))
                lb = d * p["qb"] * p["pb_last"]
                la = -d * p["qa"] * p["pa_last"]
                gross_x += abs(lb) + abs(la)
                net_x += lb + la
            if book.collateral_yield and i > start:
                days = (np.datetime64(dates[i]) - np.datetime64(dates[i - 1])).astype(int)
                r = ch.risk_free_annual(date.fromisoformat(str(dates[i])[:10])) / 100.0
                equity += equity * book.collateral_yield * r * days / 365.0
            res.dates.append(day)
            res.equity.append(equity + unreal)
            res.gross_exposure.append(gross_x)
            res.net_exposure.append(net_x)
            res.margin_used.append(sum(p["margin"] for p in open_pos))
            res.open_count.append(len(open_pos))

    res.skipped = skipped
    return res


def _close(p, oa, ob, day, z, reason, book: BookRules, res: Result) -> float:
    d = p["direction"]
    side_b = "SELL" if d == LONG_SPREAD else "BUY"
    side_a = "BUY" if d == LONG_SPREAD else "SELL"
    cost_out = (_leg_cost(book.card, side_b, p["qb"] * ob, day)
                + _leg_cost(book.card, side_a, p["qa"] * oa, day))
    gross_pnl = (d * p["qb"] * (ob - p["pb_in"]) - d * p["qa"] * (oa - p["pa_in"]))
    total_cost = p["cost"] + cost_out
    c = p["cand"]
    res.trades.append(Trade(
        pair=f"{c.b}/{c.a}", a=c.a, b=c.b, beta=c.beta, direction=d,
        entry_date=p["entry_date"], exit_date=day, entry_z=p["entry_z"], exit_z=z,
        qa=p["qa"], qb=p["qb"], pa_in=p["pa_in"], pb_in=p["pb_in"],
        pa_out=oa, pb_out=ob, gross=p["gross"], cost=total_cost,
        gross_pnl=gross_pnl, net_pnl=gross_pnl - total_cost, bars=p["bars"],
        reason=reason, half_life=c.half_life, pvalue=c.pvalue,
        alpha=c.alpha, mu=c.mu, sigma=c.sigma, log_prices=c.log_prices))
    return gross_pnl - cost_out


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def summarise(res: Result, book: BookRules) -> dict:
    eq = np.asarray(res.equity, float)
    if len(eq) < 2:
        return {"error": "no equity series"}
    ds = [date.fromisoformat(d) for d in res.dates]
    years = (ds[-1] - ds[0]).days / 365.25
    total = eq[-1] / eq[0] - 1
    cagr = (eq[-1] / eq[0]) ** (1 / years) - 1 if years > 0 else 0.0

    rets = np.diff(eq) / eq[:-1]
    vol = float(np.std(rets, ddof=1) * np.sqrt(252))
    peak = np.maximum.accumulate(eq)
    dd = eq / peak - 1
    maxdd = float(dd.min())

    rf = ch.risk_free_growth(res.dates)
    rf_total = rf[-1] - 1
    rf_cagr = rf[-1] ** (1 / years) - 1 if years > 0 else 0.0
    rf_daily = np.diff(np.asarray(rf)) / np.asarray(rf)[:-1]
    excess = rets - rf_daily
    sharpe = float(np.mean(excess) / np.std(excess, ddof=1) * np.sqrt(252)) if np.std(excess) else 0.0

    nets = np.array([t.net_pnl for t in res.trades]) if res.trades else np.zeros(0)
    wins = float((nets > 0).mean()) if len(nets) else 0.0
    worst1 = float(np.quantile(nets, 0.01)) if len(nets) else 0.0
    worst1_mean = float(nets[nets <= np.quantile(nets, 0.01)].mean()) if len(nets) > 100 else (
        float(nets.min()) if len(nets) else 0.0)

    gx = np.asarray(res.gross_exposure, float)
    nx = np.asarray(res.net_exposure, float)

    # RETURN ON EXPOSURE, which is how two books that deployed different
    # amounts of capital have to be compared. CAGR on a fixed starting
    # capital rewards a book for sitting out: with cash earning the policy
    # rate, a strategy that never trades reports the risk-free rate and a
    # perfectly respectable Sharpe. Dividing the trading P&L by the average
    # gross exposure actually carried, per year, removes that -- it is the
    # rate the DEPLOYED rupees earned. The handoff's rule, and it reverses
    # the apparent ranking of two books here.
    exposure_years = float(gx.mean()) * years
    rs = np.array([t.net_pnl / t.gross for t in res.trades if t.gross]) if res.trades else np.zeros(0)
    return {
        "start": res.dates[0], "end": res.dates[-1], "years": round(years, 2),
        "final_equity": float(eq[-1]), "total_return": float(total), "cagr": float(cagr),
        "vol_annual": vol, "max_drawdown": maxdd,
        "mar": float(cagr / abs(maxdd)) if maxdd else float("inf"),
        "risk_free_total": float(rf_total), "risk_free_cagr": float(rf_cagr),
        "excess_cagr": float(cagr - rf_cagr), "sharpe_vs_rf": sharpe,
        "n_trades": len(res.trades), "win_rate": wins,
        "avg_net": float(nets.mean()) if len(nets) else 0.0,
        "median_net": float(np.median(nets)) if len(nets) else 0.0,
        "worst_1pct_threshold": worst1, "worst_1pct_mean": worst1_mean,
        "total_costs": float(sum(t.cost for t in res.trades)),
        "total_gross_pnl": float(sum(t.gross_pnl for t in res.trades)),
        "total_net_pnl": float(sum(t.net_pnl for t in res.trades)),
        # Ratio against the ABSOLUTE gross, with a flag -- because when gross
        # P&L is negative "costs were 49% of gross P&L" reads as though there
        # was something for them to eat. There was not: the spreads lost money
        # before a single charge was applied, and that is a different and much
        # more damning statement than an expensive strategy.
        "cost_share_of_gross": (float(sum(t.cost for t in res.trades)
                                      / abs(sum(t.gross_pnl for t in res.trades)))
                                if sum(t.gross_pnl for t in res.trades) else None),
        "gross_pnl_negative": bool(sum(t.gross_pnl for t in res.trades) < 0),
        "mean_gross_exposure": float(gx.mean()), "max_gross_exposure": float(gx.max()),
        "mean_abs_net_exposure": float(np.abs(nx).mean()),
        "max_abs_net_exposure": float(np.abs(nx).max()),
        "mean_net_over_gross": float(np.mean(np.abs(nx[gx > 0]) / gx[gx > 0])) if (gx > 0).any() else 0.0,
        "net_per_exposure_year": (float(sum(t.net_pnl for t in res.trades)
                                        / exposure_years) if exposure_years else 0.0),
        "gross_per_exposure_year": (float(sum(t.gross_pnl for t in res.trades)
                                          / exposure_years) if exposure_years else 0.0),
        "mean_R": float(rs.mean()) if len(rs) else 0.0,
        "median_R": float(np.median(rs)) if len(rs) else 0.0,
        "worst_1pct_R": float(np.quantile(rs, 0.01)) if len(rs) else 0.0,
        "deployment": (float(gx.mean() / book.capital) if book.capital else 0.0),
        "mean_open": float(np.mean(res.open_count)),
        "mean_bars_held": float(np.mean([t.bars for t in res.trades])) if res.trades else 0.0,
        "exit_reasons": {r: sum(1 for t in res.trades if t.reason == r)
                         for r in ("target", "stop", "cap", "window")},
        "skipped": res.skipped,
    }
