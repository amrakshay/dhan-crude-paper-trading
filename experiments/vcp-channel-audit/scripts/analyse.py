"""STEP 3 -- did the channel's Mainboard VCP setups work?

Two tests, side by side (agreed 2026-10-04):

  A. BREAKOUT TRADE -- how a VCP watchlist is meant to be used. After the list
     date, wait up to TRIGGER_WINDOW sessions for price to clear the pivot.
     Buy on the breakout, stop at the final contraction's low, then trail on
     the first close below the 50 EMA (the exits in
     ../pattern-detection/FINAL_LOGIC.md). A setup whose low breaks the stop
     before it ever triggers is a FAILED setup, not a trade.

  B. NEXT-OPEN BUY -- does the list carry any edge at all? Buy every counted
     setup at the next session's open, sell at the close 5 / 10 / 20 sessions
     later.

The sheets give no pivot and no stop, so both are derived from the bars up to
and including the list date: pivot = highest high, stop = lowest low, over the
last BASE_WINDOW sessions (the final contraction). 10 is the primary window;
5 and 20 are run as sensitivity checks so the answer does not hang on one
arbitrary number.

Repeats: a listing counts only if the same symbol was not listed in the
previous COOLDOWN sessions (any listing, counted or not, resets the clock).

Costs: the repo's NSE delivery rate card (rates read from the YAML, never
typed here) on a Rs 1,50,000 position, plus SLIPPAGE each side. No benchmark,
by request.

Run from this directory:
    .venv/bin/python scripts/analyse.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

EXP = Path(__file__).resolve().parent.parent
OUT = EXP / "out"
RATE_CARD = EXP.parent.parent / "backend" / "conf" / "charges" / "nse-equity-delivery.yaml"

POSITION = 150_000.0     # Rs per trade, the size FINAL_LOGIC.md settled on
SLIPPAGE = 0.001         # 0.10% each side; these are small caps, some thin
COOLDOWN = 20            # sessions
TRIGGER_WINDOW = 10      # sessions after the list date to wait for a breakout
BASE_WINDOWS = (10, 5, 20)   # first is primary
HORIZONS = (5, 10, 20)
BOOT = 5000
RNG = np.random.default_rng(20261004)


# --------------------------------------------------------------------- costs
def load_costs():
    card = yaml.safe_load(RATE_CARD.read_text())
    stt = card["stt"]["delivery_rate"]
    exch = card["exchange_transaction_charge"]["cash_market_rate"] + \
        card["exchange_transaction_charge"]["ipft_rate"]
    sebi = card["sebi_turnover_fee"]["rate"]
    stamp = card["stamp_duty"]["delivery_rate"]
    gst = card["gst"]["rate"]
    dp = next(l["amount"] for l in card["levies"] if l["name"] == "dp_charge")
    brk = card["brokerage"]["flat_per_order"]

    def buy_side(value: float) -> float:
        return round(stt * value) + round(stamp * value) + \
            (exch + sebi) * value * (1 + gst) + brk * (1 + gst)

    def sell_side(value: float) -> float:
        return round(stt * value) + (exch + sebi) * value * (1 + gst) + (dp + brk) * (1 + gst)
    return buy_side, sell_side


BUY_COST, SELL_COST = load_costs()


def ROUND_TRIP(buy_value: float, sell_value: float) -> float:
    return BUY_COST(buy_value) + SELL_COST(sell_value)


def net_return(entry: float, exit_: float) -> tuple[float, float]:
    """(gross %, net %) on a POSITION-rupee buy. Slippage worsens both fills."""
    fill_in, fill_out = entry * (1 + SLIPPAGE), exit_ * (1 - SLIPPAGE)
    qty = POSITION / fill_in
    gross = exit_ / entry - 1
    pnl = qty * (fill_out - fill_in) - ROUND_TRIP(POSITION, qty * fill_out)
    return gross, pnl / POSITION


# ---------------------------------------------------------------------- data
def load_bars(ex: str, sym: str) -> pd.DataFrame:
    b = pd.read_csv(EXP / "data" / "bars" / f"{ex}_{sym}.csv", parse_dates=["date"])
    b = b.set_index("date").sort_index()
    b["ema50"] = b.close.ewm(span=50, adjust=False).mean()
    tr = pd.concat([b.high - b.low, (b.high - b.close.shift()).abs(),
                    (b.low - b.close.shift()).abs()], axis=1).max(axis=1)
    b["atr14"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    return b


def counted_setups(s: pd.DataFrame, calendar: pd.DatetimeIndex) -> pd.DataFrame:
    pos = {d: i for i, d in enumerate(calendar)}
    s = s.sort_values(["dhan_symbol", "list_date"]).copy()
    s["session_ix"] = s.list_date.map(pos)
    s["sessions_since_last"] = s.groupby("dhan_symbol").session_ix.diff()
    s["counted"] = s.sessions_since_last.isna() | (s.sessions_since_last >= COOLDOWN)
    return s


# ---------------------------------------------------------------- test A
def breakout_trade(b: pd.DataFrame, t: int, window: int) -> dict:
    base = b.iloc[t - window + 1: t + 1]
    return trade_from_levels(b, t, base.high.max(), base.low.min())


def trade_from_levels(b: pd.DataFrame, t: int, pivot: float, stop: float) -> dict:
    last = len(b) - 1
    res = {"pivot": pivot, "stop": stop, "risk_pct": pivot / stop - 1}
    # wait for the breakout
    entry_i = None
    for i in range(t + 1, min(t + TRIGGER_WINDOW, last) + 1):
        bar = b.iloc[i]
        if bar.high > pivot:
            if bar.open <= stop:      # gapped straight through the stop: no trade
                break
            entry_i, entry = i, max(bar.open, pivot)
            break
        if bar.low <= stop:
            res.update(status="failed_before_trigger", days_to_event=i - t)
            return res
    if entry_i is None:
        complete = t + TRIGGER_WINDOW <= last
        res.update(status="no_breakout" if complete else "pending")
        return res
    res.update(entry_date=b.index[entry_i].date().isoformat(), entry=entry,
               atr_stop_gate_ok=(entry - stop) >= 0.5 * b.atr14.iloc[entry_i - 1])
    # manage it; on the entry bar a stop touch counts (pessimistic intrabar order)
    for i in range(entry_i, last + 1):
        bar = b.iloc[i]
        if bar.low <= stop:
            exit_, why = (min(bar.open, stop) if i > entry_i else stop), "stop"
            break
        if i > entry_i and bar.close < bar.ema50:
            exit_, why = bar.close, "ema50"
            break
    else:
        exit_, why, i = b.close.iloc[last], "open", last
    gross, net = net_return(entry, exit_)
    risk = entry - stop
    res.update(status="traded", exit_date=b.index[i].date().isoformat(), exit=exit_,
               exit_reason=why, sessions_held=i - entry_i + 1,
               gross_pct=gross, net_pct=net,
               net_r=net * entry * (1 + SLIPPAGE) / risk if risk > 0 else np.nan)
    return res


# ---------------------------------------------------------------- test B
def next_open_buy(b: pd.DataFrame, t: int) -> dict:
    res = {}
    if t + 1 >= len(b):
        return res
    entry = b.open.iloc[t + 1]
    res["nob_entry"] = entry
    for h in HORIZONS:
        j = t + h
        if j < len(b):
            g, n = net_return(entry, b.close.iloc[j])
            res[f"nob_gross_{h}"], res[f"nob_net_{h}"] = g, n
            seg = b.iloc[t + 1: j + 1]
            res[f"nob_mfe_{h}"] = seg.high.max() / entry - 1
            res[f"nob_mae_{h}"] = seg.low.min() / entry - 1
    return res


# ------------------------------------------------------------- statistics
def date_cluster_ci(values: pd.Series, dates: pd.Series) -> tuple[float, float]:
    """95% CI for the mean, resampling LIST DATES (setups listed the same day
    share a market and are not independent)."""
    df = pd.DataFrame({"v": values.values, "d": dates.values}).dropna()
    if df.empty:
        return (np.nan, np.nan)
    groups = [g.v.values for _, g in df.groupby("d")]
    k = len(groups)
    means = np.empty(BOOT)
    for i in range(BOOT):
        pick = RNG.integers(0, k, k)
        allv = np.concatenate([groups[p] for p in pick])
        means[i] = allv.mean()
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def describe(values: pd.Series, dates: pd.Series) -> dict:
    v = values.dropna()
    lo, hi = date_cluster_ci(values, dates)
    return {"n": int(len(v)), "mean": float(v.mean()) if len(v) else None,
            "median": float(v.median()) if len(v) else None,
            "win_rate": float((v > 0).mean()) if len(v) else None,
            "ci95_mean": [lo, hi]}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    s = pd.read_csv(EXP / "data" / "setups_mainboard.csv", parse_dates=["list_date"])
    bars = {(ex, sym): load_bars(ex, sym)
            for ex, sym in s[["exchange", "dhan_symbol"]].drop_duplicates().itertuples(index=False)}
    calendar = pd.DatetimeIndex(sorted(set().union(*[b.index for b in bars.values()])))
    calendar = calendar[calendar >= s.list_date.min() - pd.Timedelta(days=60)]
    s = counted_setups(s, calendar)

    rows = []
    for r in s.itertuples(index=False):
        b = bars[(r.exchange, r.dhan_symbol)]
        t = b.index.get_loc(r.list_date)
        row = r._asdict()
        row["list_close_dhan"] = b.close.iloc[t]
        row["sessions_after_list"] = len(b) - 1 - t
        row.update(next_open_buy(b, t))
        for w in BASE_WINDOWS:
            row.update({f"A{w}_{k}": v for k, v in breakout_trade(b, t, w).items()})
        rows.append(row)
    res = pd.DataFrame(rows).sort_values(["list_date", "rank"])
    res.to_csv(OUT / "setups_scored.csv", index=False)

    c = res[res.counted]
    summary = {
        "inputs": {
            "sheets": int(res.list_date.nunique()),
            "first_list_date": res.list_date.min().date().isoformat(),
            "last_list_date": res.list_date.max().date().isoformat(),
            "last_bar": max(b.index.max() for b in bars.values()).date().isoformat(),
            "mainboard_rows": int(len(res)), "unique_symbols": int(res.dhan_symbol.nunique()),
            "counted_setups": int(len(c)),
            "position_rs": POSITION, "slippage_each_side": SLIPPAGE,
            "round_trip_charges_rs_at_flat_price": round(ROUND_TRIP(POSITION, POSITION), 2),
        },
        "B_next_open_buy": {f"{h}d_net": describe(c[f"nob_net_{h}"], c.list_date) for h in HORIZONS}
        | {f"{h}d_gross": describe(c[f"nob_gross_{h}"], c.list_date) for h in HORIZONS},
        "A_breakout": {},
    }
    for w in BASE_WINDOWS:
        st = c[f"A{w}_status"].value_counts().to_dict()
        tr = c[c[f"A{w}_status"] == "traded"]
        closed = tr[tr[f"A{w}_exit_reason"] != "open"]
        summary["A_breakout"][f"base_{w}d"] = {
            "status_counts": st,
            "risk_pct_median": float(c[f"A{w}_risk_pct"].median()),
            "all_trades_net_pct": describe(tr[f"A{w}_net_pct"], tr.list_date),
            "all_trades_net_r": describe(tr[f"A{w}_net_r"], tr.list_date),
            "closed_trades_net_pct": describe(closed[f"A{w}_net_pct"], closed.list_date),
            "exit_reasons": tr[f"A{w}_exit_reason"].value_counts().to_dict(),
            "total_net_rs_at_position": float((tr[f"A{w}_net_pct"] * POSITION).sum()),
            "atr_gate_fail": int((tr[f"A{w}_atr_stop_gate_ok"] == False).sum()),  # noqa: E712
        }
    # descriptive splits on the primary test, small samples -- not conclusions
    w = BASE_WINDOWS[0]
    tr = c[c[f"A{w}_status"] == "traded"].copy()
    tr["rank_band"] = pd.cut(tr["rank"], [0, 3, 6, 10], labels=["1-3", "4-6", "7-10"])
    c2 = c.copy()
    c2["rank_band"] = pd.cut(c2["rank"], [0, 3, 6, 10], labels=["1-3", "4-6", "7-10"])
    summary["splits"] = {
        "A10_net_pct_by_rank": tr.groupby("rank_band", observed=True)[f"A{w}_net_pct"]
            .agg(["count", "mean", "median"]).round(4).to_dict("index"),
        "B10_net_by_rank": c2.groupby("rank_band", observed=True)["nob_net_10"]
            .agg(["count", "mean", "median"]).round(4).to_dict("index"),
        "B10_net_by_inside_day": c2.groupby("inside_day")["nob_net_10"]
            .agg(["count", "mean", "median"]).round(4).to_dict("index"),
        "B10_net_by_list_month": c2.groupby(c2.list_date.dt.strftime("%Y-%m"))["nob_net_10"]
            .agg(["count", "mean", "median"]).round(4).to_dict("index"),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps(summary, indent=1, default=str))


if __name__ == "__main__":
    main()
