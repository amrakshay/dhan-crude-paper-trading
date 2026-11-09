"""STEP 5 -- why did some setups run, and how should the losers be cut?

CHANNEL DATA ONLY (his call, 2026-10-04): 169 setups over two months. Every
"best" below is EXPLORATORY -- picked on the same small sample it is scored on,
in one market. Nothing here has been validated out of sample.

Part 1, why the winners won. Outcome = net return from the next session's open
to the close 10 sessions later (a fixed horizon, so an early listing is not
favoured just for having had longer). Each feature is known at the list date
(nothing after it) and is scored by Spearman correlation with that outcome,
with a 95% interval that resamples LIST DATES. With ~20 features, roughly one
will clear zero by chance alone.

Part 2, follow-through. Do the first 1-5 sessions after entry say anything
about the next 20? If they do, an early exit for laggards is justified; if
not, cutting early only adds cost.

Part 3, exits. Every counted setup bought at the next open; only the exit rule
varies. Per-trade results, then a Rs 10L book at Rs 1.5L a position (at most
6 open) that recycles freed cash into the next new setup -- the capital
rotation he asked about. Open positions are valued at the last close.

Run from this directory, after analyse.py:
    .venv/bin/python scripts/winners_and_exits.py
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from analyse import (BOOT, BUY_COST, POSITION, RNG, SELL_COST, SLIPPAGE,
                     counted_setups, load_bars, net_return)

EXP = Path(__file__).resolve().parent.parent
OUT = EXP / "out"
BOOK = 1_000_000.0
MAX_POSITIONS = int(BOOK // POSITION)      # 6


# ----------------------------------------------------------------- features
def features_at(b: pd.DataFrame, t: int) -> dict:
    """Everything here uses bars [0..t] only."""
    v = b.iloc[: t + 1]
    c = v.close
    hi10, lo10 = v.high.iloc[-10:].max(), v.low.iloc[-10:].min()
    hi252 = v.high.iloc[-252:].max()
    ema20 = c.ewm(span=20, adjust=False).mean()
    atr = v.atr14
    rng5 = v.high.iloc[-5:].max() / v.low.iloc[-5:].min() - 1
    rng20 = v.high.iloc[-20:].max() / v.low.iloc[-20:].min() - 1
    return {
        "f_base_depth_10d": hi10 / lo10 - 1,
        "f_tightness_5v20": rng5 / rng20 if rng20 > 0 else np.nan,
        "f_atr_ratio_10v50": (v.high - v.low).iloc[-10:].mean() / (v.high - v.low).iloc[-50:].mean(),
        "f_vol_dry_5v50": v.volume.iloc[-5:].mean() / v.volume.iloc[-50:].mean(),
        "f_listday_vol_v50": v.volume.iloc[-1] / v.volume.iloc[-50:].mean(),
        "f_listday_ret": c.iloc[-1] / c.iloc[-2] - 1,
        "f_close_in_10d_range": (c.iloc[-1] - lo10) / (hi10 - lo10) if hi10 > lo10 else np.nan,
        "f_to_pivot": hi10 / c.iloc[-1] - 1,
        "f_ext_over_ema50": c.iloc[-1] / v.ema50.iloc[-1] - 1,
        "f_ext_over_ema20": c.iloc[-1] / ema20.iloc[-1] - 1,
        "f_ret_126d": c.iloc[-1] / c.iloc[-127] - 1 if len(v) > 127 else np.nan,
        "f_from_252d_high": c.iloc[-1] / hi252 - 1,
        "f_days_since_252d_high": int(len(v.high.iloc[-252:]) - 1 - np.argmax(v.high.iloc[-252:].values)),
        "f_atr_pct": atr.iloc[-1] / c.iloc[-1],
    }


def follow_through(b: pd.DataFrame, t: int) -> dict:
    """Behaviour in the first sessions AFTER entry at t+1's open."""
    out = {}
    if t + 1 >= len(b):
        return out
    e = b.open.iloc[t + 1]
    hi10 = b.high.iloc[t - 9: t + 1].max()
    for n in (1, 3, 5):
        j = t + n
        if j < len(b):
            out[f"ft_ret_{n}d"] = b.close.iloc[j] / e - 1
            out[f"ft_broke_pivot_{n}d"] = float(b.high.iloc[t + 1: j + 1].max() > hi10)
    for h in (10, 20):
        if t + h < len(b):
            out[f"out_ret_{h}d"] = net_return(e, b.close.iloc[t + h])[1]
    # what happens AFTER the early window -- the part an exit decision can change.
    # Correlating day-3 return with the full 20-day return would be partly
    # mechanical, since the 20 days contain the first 3.
    for n in (1, 3, 5):
        if t + 20 < len(b):
            out[f"rest_after_{n}d"] = b.close.iloc[t + 20] / b.close.iloc[t + n] - 1
    return out


def spearman_ci(x: pd.Series, y: pd.Series, dates: pd.Series) -> tuple[float, float, float, int]:
    df = pd.DataFrame({"x": x.values, "y": y.values, "d": dates.values}).dropna()
    if len(df) < 15:
        return (np.nan, np.nan, np.nan, len(df))
    rho = df.x.rank().corr(df.y.rank())
    groups = [g for _, g in df.groupby("d")]
    k = len(groups)
    boots = []
    for _ in range(BOOT // 5):
        s = pd.concat([groups[i] for i in RNG.integers(0, k, k)])
        boots.append(s.x.rank().corr(s.y.rank()))
    lo, hi = np.nanpercentile(boots, [2.5, 97.5])
    return float(rho), float(lo), float(hi), len(df)


# -------------------------------------------------------------------- exits
@dataclass(frozen=True)
class Rule:
    name: str
    stop: str = "none"          # none | pct | struct | atr
    stop_k: float = 0.0
    trail: str = "none"         # none | ema20 | ema50 | chand | donch10
    trail_k: float = 0.0
    time_n: int = 0             # 0 = off; else exit at close of session n ...
    time_min: float = 0.0       # ... unless up at least this much by then
    max_hold: int = 0           # 0 = off


RULES = [
    Rule("hold to end (baseline)"),
    Rule("hold 10 sessions", max_hold=10),
    Rule("hold 20 sessions", max_hold=20),
    Rule("stop -5%", stop="pct", stop_k=0.05),
    Rule("stop -8%", stop="pct", stop_k=0.08),
    Rule("10d-low stop + EMA50 (FINAL_LOGIC)", stop="struct", trail="ema50"),
    Rule("10d-low stop + EMA20", stop="struct", trail="ema20"),
    Rule("2xATR stop + EMA20", stop="atr", stop_k=2.0, trail="ema20"),
    Rule("chandelier 3xATR", trail="chand", trail_k=3.0),
    Rule("10-day-low trail", trail="donch10"),
    Rule("5-day time stop (<0%) + EMA20", time_n=5, time_min=0.0, trail="ema20"),
    Rule("5-day time stop (<+3%) + EMA20", time_n=5, time_min=0.03, trail="ema20"),
    Rule("stop -8% + 5-day time stop + EMA20", stop="pct", stop_k=0.08, time_n=5,
         time_min=0.0, trail="ema20"),
    # POST HOC -- added after seeing that day-5 behaviour carries information and
    # that every trail cut winners. Chosen on this sample; treat as a hypothesis.
    Rule("[post hoc] 5-day time stop (<0%), else hold 20", time_n=5, time_min=0.0, max_hold=20),
    Rule("[post hoc] 5-day time stop (<0%), else hold 10", time_n=5, time_min=0.0, max_hold=10),
]


def run_exit(b: pd.DataFrame, t: int, r: Rule) -> dict | None:
    if t + 1 >= len(b):
        return None
    ei, last = t + 1, len(b) - 1
    e = b.open.iloc[ei]
    ema20 = b.close.ewm(span=20, adjust=False).mean()
    stop = {"none": -np.inf,
            "pct": e * (1 - r.stop_k),
            "struct": b.low.iloc[t - 9: t + 1].min(),
            "atr": e - r.stop_k * b.atr14.iloc[t]}[r.stop]
    hi_close = e
    exit_i, exit_px, why = last, b.close.iloc[last], "open"
    for i in range(ei, last + 1):
        bar = b.iloc[i]
        # trailing levels use information up to the PREVIOUS close
        trail_stop = -np.inf
        if r.trail == "chand" and i > ei:
            trail_stop = hi_close - r.trail_k * b.atr14.iloc[i - 1]
        if r.trail == "donch10" and i > ei:
            trail_stop = b.low.iloc[i - 10: i].min()
        level = max(stop, trail_stop)
        if bar.low <= level:
            exit_i, why = i, "stop" if level == stop else "trail"
            exit_px = min(bar.open, level)     # a gap through the level fills at the open
            break
        n = i - ei + 1
        if r.trail == "ema20" and i > ei and bar.close < ema20.iloc[i]:
            exit_i, exit_px, why = i, bar.close, "ema20"
            break
        if r.trail == "ema50" and i > ei and bar.close < bar.ema50:
            exit_i, exit_px, why = i, bar.close, "ema50"
            break
        if r.time_n and n == r.time_n and bar.close / e - 1 < r.time_min:
            exit_i, exit_px, why = i, bar.close, "time"
            break
        if r.max_hold and n == r.max_hold:
            exit_i, exit_px, why = i, bar.close, "max_hold"
            break
        hi_close = max(hi_close, bar.close)
    g, net = net_return(e, exit_px)
    return {"entry_date": b.index[ei], "exit_date": b.index[exit_i], "entry": e,
            "exit": exit_px, "why": why, "held": exit_i - ei + 1, "net": net, "gross": g}


def portfolio(trades: pd.DataFrame, calendar: pd.DatetimeIndex, bars: dict,
              shuffle: np.random.Generator | None = None) -> dict:
    """Rs 10L, Rs 1.5L a slot, freed cash reused. Same-day candidates are taken
    in the channel's rank order (or a random order when `shuffle` is given);
    a setup with no free slot is skipped."""
    cash, open_pos, taken, skipped = BOOK, [], 0, 0
    equity = []
    t_by_day = {d: (g.sample(frac=1, random_state=int(shuffle.integers(1 << 31)))
                    if shuffle is not None else g.sort_values("rank"))
                for d, g in trades.groupby("entry_date")}
    for d in calendar:
        # exits first (cash from an exit at d's close funds d+1's opens)
        still = []
        for p in open_pos:
            if p["exit_date"] <= d and p["why"] != "open":
                cash += p["qty"] * p["exit"] * (1 - SLIPPAGE) - p["sell_cost"]
            else:
                still.append(p)
        open_pos = still
        for _, tr in t_by_day.get(d, pd.DataFrame()).iterrows():
            if len(open_pos) >= MAX_POSITIONS or cash < POSITION:
                skipped += 1
                continue
            fill = tr.entry * (1 + SLIPPAGE)
            qty = POSITION / fill
            cash -= POSITION + BUY_COST(POSITION)
            open_pos.append({**tr.to_dict(), "qty": qty,
                             "sell_cost": SELL_COST(qty * tr.exit * (1 - SLIPPAGE))})
            taken += 1
        mtm = sum(p["qty"] * bars[p["key"]].close.asof(d) for p in open_pos)
        equity.append((d, cash + mtm, len(open_pos)))
    eq = pd.DataFrame(equity, columns=["date", "equity", "positions"]).set_index("date")
    dd = (eq.equity / eq.equity.cummax() - 1).min()
    return {"final_equity": float(eq.equity.iloc[-1]), "return_pct": float(eq.equity.iloc[-1] / BOOK - 1),
            "max_drawdown_pct": float(dd), "trades_taken": taken, "setups_skipped": skipped,
            "avg_positions": float(eq.positions.mean()),
            "deployed_pct": float(eq.positions.mean() * POSITION / BOOK)}


def main() -> None:
    s = pd.read_csv(EXP / "data" / "setups_mainboard.csv", parse_dates=["list_date"])
    keys = s[["exchange", "dhan_symbol"]].drop_duplicates().itertuples(index=False)
    bars = {(ex, sym): load_bars(ex, sym) for ex, sym in keys}
    calendar = pd.DatetimeIndex(sorted(set().union(*[b.index for b in bars.values()])))
    s = counted_setups(s, calendar[calendar >= s.list_date.min() - pd.Timedelta(days=60)])
    c = s[s.counted].copy()

    # ---- part 1 + 2
    rows = []
    for r in c.itertuples(index=False):
        b = bars[(r.exchange, r.dhan_symbol)]
        t = b.index.get_loc(r.list_date)
        rows.append({"list_date": r.list_date, "symbol": r.symbol, "rank": r.rank,
                     "sector": r.sector, "s_adr_pct": r.adr_pct,
                     "s_pct_from_52wh": r.pct_from_52wh, "s_value20d_cr": r.value20d_cr,
                     "s_rs63_pct": r.rs63_pct, "s_inside_day": float(r.inside_day),
                     "s_bse": float(r.exchange == "BSE"), "s_be_series": float(r.symbol.endswith("-BE")),
                     **features_at(b, t), **follow_through(b, t)})
    f = pd.DataFrame(rows)
    f.to_csv(OUT / "why_winners.csv", index=False)

    feat_cols = ["rank"] + [k for k in f if k.startswith(("s_", "f_"))]
    corr = []
    for k in feat_cols:
        rho, lo, hi, n = spearman_ci(f[k], f.out_ret_10d, f.list_date)
        corr.append({"feature": k, "rho": rho, "lo": lo, "hi": hi, "n": n,
                     "clears_zero": bool(lo > 0 or hi < 0)})
    corr = pd.DataFrame(corr).sort_values("rho", key=lambda x: -x.abs())
    ft = []
    for k, rest in [("ft_ret_1d", "rest_after_1d"), ("ft_ret_3d", "rest_after_3d"),
                    ("ft_ret_5d", "rest_after_5d"), ("ft_broke_pivot_3d", "rest_after_3d"),
                    ("ft_broke_pivot_5d", "rest_after_5d")]:
        rho, lo, hi, n = spearman_ci(f[k], f[rest], f.list_date)
        ft.append({"early_signal": k, "vs": rest, "rho": rho, "lo": lo, "hi": hi, "n": n})
    ft = pd.DataFrame(ft)
    # how much of the outcome is just the DAY it was listed (the market)?
    o = f.dropna(subset=["out_ret_10d"])
    day_mean = o.groupby("list_date").out_ret_10d.transform("mean")
    share_by_day = float(1 - ((o.out_ret_10d - day_mean) ** 2).sum()
                         / ((o.out_ret_10d - o.out_ret_10d.mean()) ** 2).sum())
    # winners vs the rest, for the features that cleared zero, in plain units
    o = o.assign(group=np.where(o.out_ret_10d > 0.05, "won >5%",
                       np.where(o.out_ret_10d < -0.05, "lost >5%", "flat")))
    profile = o.groupby("group")[feat_cols + ["ft_ret_3d"]].median().T
    early = f.dropna(subset=["ft_ret_5d", "rest_after_5d"]).copy()
    early["first5"] = np.where(early.ft_ret_5d > 0, "up after 5 days", "down after 5 days")
    early_tab = early.groupby("first5").rest_after_5d.agg(["count", "mean", "median",
                                                          lambda x: (x > 0).mean()])
    early_tab.columns = ["count", "mean_day5_to_20", "median_day5_to_20", "share_rising"]

    # ---- part 3
    per_rule, books = [], {}
    for rule in RULES:
        tr = []
        for r in c.itertuples(index=False):
            key = (r.exchange, r.dhan_symbol)
            b = bars[key]
            res = run_exit(b, b.index.get_loc(r.list_date), rule)
            if res:
                tr.append({**res, "symbol": r.symbol, "rank": r.rank, "key": key,
                           "list_date": r.list_date})
        tr = pd.DataFrame(tr)
        closed = tr[tr.why != "open"]
        per_rule.append({
            "rule": rule.name, "trades": len(tr), "still_open": int((tr.why == "open").sum()),
            "mean_net": tr.net.mean(), "median_net": tr.net.median(),
            "win_rate": (tr.net > 0).mean(), "avg_held": tr.held.mean(),
            "net_per_100_capital_days": 100 * tr.net.sum() / tr.held.sum(),
            "worst": tr.net.min(), "p90": tr.net.quantile(0.9),
            "closed_mean": closed.net.mean() if len(closed) else np.nan,
        })
        cal = calendar[calendar >= c.list_date.min()]
        book = portfolio(tr, cal, bars)
        # The book takes only a fraction of the setups, so WHICH ones it got is
        # luck. Re-run with random same-day order and a random start in the
        # first 10 sessions; report the spread, not just the one path.
        rng = np.random.default_rng(7)
        runs = [portfolio(tr, cal[int(rng.integers(0, 10)):], bars, shuffle=rng)["return_pct"]
                for _ in range(200)]
        book.update(runs_p10=float(np.percentile(runs, 10)), runs_median=float(np.median(runs)),
                    runs_p90=float(np.percentile(runs, 90)))
        books[rule.name] = book
        (OUT / "exits").mkdir(exist_ok=True)
        slug = "".join(ch if ch.isalnum() else "_" for ch in rule.name).strip("_").lower()
        tr.drop(columns=["key"]).to_csv(OUT / "exits" / f"{slug}.csv", index=False)
    pr = pd.DataFrame(per_rule)
    bk = pd.DataFrame(books).T

    pd.set_option("display.width", 200)
    print("== part 1: feature vs 10-session net return (Spearman, CI by list date)")
    print(corr.round(3).to_string(index=False))
    print(f"\nshare of 10-session outcome explained by the LIST DATE alone: {share_by_day:.1%}")
    print("\nmedians by outcome group (10-session):")
    print(profile.round(3).to_string())
    print("\n== part 2: early behaviour vs 20-session net return")
    print(ft.round(3).to_string(index=False))
    print(early_tab.round(4).to_string())
    print("\n== part 3: exit rules, per trade")
    print(pr.round(4).to_string(index=False))
    print("\n== part 3: Rs 10L book, Rs 1.5L slots")
    print(bk.round(4).to_string())
    (OUT / "winners_exits_summary.json").write_text(json.dumps({
        "features": corr.to_dict("records"), "share_explained_by_list_date": share_by_day,
        "follow_through": ft.to_dict("records"),
        "first3_split": early_tab.reset_index().to_dict("records"),
        "exit_rules": pr.to_dict("records"), "book": bk.reset_index().to_dict("records"),
    }, indent=1, default=str))


if __name__ == "__main__":
    main()
