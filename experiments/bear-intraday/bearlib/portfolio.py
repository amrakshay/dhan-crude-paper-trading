"""The pre-registered portfolio (METHOD.md), monthly calendar and monthly bar.

Capital is not compounded: every figure is % of the starting ₹5 L, so a month
in 2018 and a month in 2026 are measured on the same ruler.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .costs import RateCard

CAPITAL = 500_000.0
MAX_POS = 5
POS_CAP = 500_000.0           # 5x capital / 5 positions
DAY_LIMIT = -0.015
MONTH_LIMIT = -0.03
MONTHLY_BAR = {"share_positive": 0.70, "worst_month": -0.04, "max_losing_streak": 2}


def run_stocks(trades: pd.DataFrame, risk: float = 0.005, card: RateCard | None = None,
               limits: bool = True) -> pd.DataFrame:
    """Take trades in time order under the risk rules. Returns the taken ones
    with rupee P&L."""
    card = card or RateCard.load()
    t = trades[trades["net_ret"].notna()].copy()
    t["date"] = pd.to_datetime(t["date"])
    t = t.sort_values(["date", "entry_bar", "s"])
    out = []
    month_pnl: dict = {}
    for day, g in t.groupby("date", sort=True):
        mkey = (day.year, day.month)
        open_until: list[tuple[int, float]] = []     # (exit_bar, pnl) of taken trades
        for row in g.itertuples(index=False):
            e = int(row.entry_bar)
            done = [p for x, p in open_until if x < e]
            day_real = sum(done)
            live = sum(1 for x, _ in open_until if x >= e)
            if live >= MAX_POS:
                continue
            if limits and day_real <= DAY_LIMIT * CAPITAL:
                continue
            if limits and month_pnl.get(mkey, 0.0) + day_real <= MONTH_LIMIT * CAPITAL:
                continue
            notional = min(risk * CAPITAL / max(row.risk_frac, 1e-6), POS_CAP)
            # Rupee P&L on this notional; charges at the actual legs.
            entry, exit_ = row.entry_px, row.exit_px
            qty = notional / entry
            buy, sell = (qty * entry, qty * exit_) if row.side > 0 else (qty * exit_, qty * entry)
            cost = float(card.round_trip(buy, sell))
            pnl = row.side * qty * (exit_ - entry) - cost
            open_until.append((int(row.exit_bar), pnl))
            out.append({**row._asdict(), "notional": notional, "cost_rs": cost, "pnl_rs": pnl})
        month_pnl[mkey] = month_pnl.get(mkey, 0.0) + sum(p for _, p in open_until)
    return pd.DataFrame(out)


def run_index(itrades: pd.DataFrame, gross_cap: float = 5 * CAPITAL, one_lot: bool = False) -> pd.DataFrame:
    """Index short sized to floor(gross_cap / lot notional) lots (>= 1)."""
    t = itrades[itrades["net_ret"].notna()].copy()
    lot_notional = t["notional"] / t["lots"]
    n = np.ones(len(t)) if one_lot else np.maximum(1, np.floor(gross_cap / lot_notional))
    scale = n / t["lots"]
    t["pnl_rs"] = t["pnl_rs"] * scale   # charges are near-linear in size at these notionals
    t["notional"] = lot_notional * n
    t["date"] = pd.to_datetime(t["date"])
    return t


def monthly(book: pd.DataFrame, months: pd.PeriodIndex) -> pd.Series:
    """% of capital per calendar month over `months`; months with no trades = 0."""
    if book.empty:
        return pd.Series(0.0, index=months)
    m = book.groupby(book["date"].dt.to_period("M"))["pnl_rs"].sum() / CAPITAL
    return m.reindex(months, fill_value=0.0)


def monthly_bar(m: pd.Series, traded: pd.Series) -> dict:
    """The operator's bar (README decision 4). A month with no trades is flat,
    not a win, and is EXCLUDED from the share and the streak; the streak counts
    consecutive losing TRADED months (a flat month neither extends nor breaks
    it -- the stricter reading)."""
    tm = m[traded]
    share = float((tm > 0).mean()) if len(tm) else 0.0
    worst = float(m.min()) if len(m) else 0.0
    streak = best = 0
    for v in tm:
        streak = streak + 1 if v < 0 else 0
        best = max(best, streak)
    ok = (share >= MONTHLY_BAR["share_positive"] and worst >= MONTHLY_BAR["worst_month"]
          and best <= MONTHLY_BAR["max_losing_streak"])
    return {"traded_months": int(len(tm)), "share_positive": round(share, 3),
            "worst_month": round(worst, 4), "longest_losing_streak": int(best), "passes": bool(ok)}


def book_stats(book: pd.DataFrame, months: pd.PeriodIndex, sessions: int) -> dict:
    m = monthly(book, months)
    traded = pd.Series(False, index=months)
    if not book.empty:
        traded.loc[book["date"].dt.to_period("M").unique()] = True
    daily_pnl = book.groupby("date")["pnl_rs"].sum() if not book.empty else pd.Series(dtype=float)
    cum = daily_pnl.sort_index().cumsum() / CAPITAL
    dd = float((cum - cum.cummax().clip(lower=0)).min()) if len(cum) else 0.0
    years = len(months) / 12
    return {
        "trades": int(len(book)),
        "total_pct": round(float(book["pnl_rs"].sum() / CAPITAL) if len(book) else 0.0, 4),
        "annual_pct_simple": round(float(book["pnl_rs"].sum() / CAPITAL / years) if len(book) else 0.0, 4),
        "max_drawdown": round(dd, 4),
        "worst_day": round(float(daily_pnl.min() / CAPITAL) if len(daily_pnl) else 0.0, 4),
        "exposure_days_share": round(len(daily_pnl) / sessions, 3),
        "monthly_bar": monthly_bar(m, traded),
        "calendar": {str(k): round(float(v), 4) for k, v in m.items()},
    }
