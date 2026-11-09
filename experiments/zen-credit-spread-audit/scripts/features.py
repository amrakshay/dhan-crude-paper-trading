"""Attach market context to every signal, as it stood at the moment of entry.

Inputs, all already on disk:
  ../intraday-screener/data/clean/5m/NIFTY50.parquet   5-min NIFTY 50, to 2026-09-25
  ../hedged-option-selling/data/cache/options.db       daily India VIX + NIFTY option
                                                       bhavcopy (OI), to 2026-09-17
Dhan's chart API now refuses this account (DH-902, Free plan), so nothing later.

Only information available BEFORE the entry is used: the last fully closed 5-min
bar, and the previous session's daily figures.

Run with ../intraday-screener/.venv/bin/python (needs pandas + pyarrow).
Writes data/features.csv.
"""
import pathlib
import sqlite3

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXP = ROOT.parent
BARS = EXP / "intraday-screener" / "data" / "clean" / "5m" / "NIFTY50.parquet"
DB = EXP / "hedged-option-selling" / "data" / "cache" / "options.db"


def main():
    sig = pd.read_csv(ROOT / "data" / "signals.csv")
    sig["opened"] = pd.to_datetime(sig["opened_ist"].str[:19])
    bars = pd.read_parquet(BARS)
    bars["ts"] = pd.to_datetime(bars["ts"])
    bars = bars[bars["ts"] >= "2025-01-01"].sort_values("ts").reset_index(drop=True)
    bars["date"] = bars["ts"].dt.normalize()

    daily = bars.groupby("date").agg(open=("open", "first"), high=("high", "max"),
                                     low=("low", "min"), close=("close", "last"))
    daily["ret"] = daily["close"].pct_change()
    for n in (3, 5, 10, 20):
        daily[f"ret{n}"] = daily["close"].pct_change(n)
    daily["sma20"] = daily["close"].rolling(20).mean()
    daily["rng"] = (daily["high"] - daily["low"]) / daily["close"]

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    vix = pd.read_sql("select trade_date, close as vix from index_close "
                      "where index_name='India VIX' and trade_date>='2025-01-01'", con)
    vix["date"] = pd.to_datetime(vix["trade_date"])
    vix = vix.set_index("date")["vix"]
    oi = pd.read_sql("""select trade_date, expiry, opt_type, sum(oi) oi from fo_bhav
        where symbol='NIFTY' and kind='IDO' and trade_date>='2025-06-01'
        group by 1,2,3""", con)
    oi["trade_date"] = pd.to_datetime(oi["trade_date"])
    oi["expiry"] = pd.to_datetime(oi["expiry"])
    near = oi[oi["expiry"] >= oi["trade_date"]]
    near = near[near["expiry"] == near.groupby("trade_date")["expiry"].transform("min")]
    pcr = near.pivot_table(index="trade_date", columns="opt_type", values="oi", aggfunc="sum")
    pcr = (pcr["PE"] / pcr["CE"]).rename("pcr_near")

    rows = []
    for _, s in sig.iterrows():
        t = s["opened"]
        d = t.normalize()
        # last bar that had CLOSED by the entry time
        done = bars[(bars["date"] == d) & (bars["ts"] + pd.Timedelta(minutes=5) <= t)]
        prev_days = daily.index[daily.index < d]
        if done.empty or len(prev_days) < 21:
            rows.append({"signal_id": s["signal_id"]})
            continue
        pd_ = prev_days[-1]
        prev = daily.loc[pd_]
        spot = done["close"].iloc[-1]
        day_open = done["open"].iloc[0]
        sh, lg = s["short_strike"], s["long_strike"]
        is_ce = s["option_type"] == "CE"
        dist = (sh - spot) if is_ce else (spot - sh)   # >0 = short strike out of the money
        v = vix.loc[:pd_].iloc[-1] if (vix.index <= pd_).any() else np.nan
        dte = max(int(s["dte_at_entry"]), 0)
        sigma_pts = spot * v / 100 * np.sqrt((dte + 1) / 365) if v == v else np.nan
        rows.append({
            "signal_id": s["signal_id"],
            "spot": round(spot, 2),
            "gap_pct": round((day_open / prev["close"] - 1) * 100, 3),
            "open_to_entry_pct": round((spot / day_open - 1) * 100, 3),
            "prevclose_to_entry_pct": round((spot / prev["close"] - 1) * 100, 3),
            "entry_vs_day_hi_pct": round((spot / done["high"].max() - 1) * 100, 3),
            "entry_vs_day_lo_pct": round((spot / done["low"].min() - 1) * 100, 3),
            "prev_ret_pct": round(prev["ret"] * 100, 3),
            "prev_close_in_range": round((prev["close"] - prev["low"]) / (prev["high"] - prev["low"]), 3),
            **{f"ret{n}_pct": round(prev[f"ret{n}"] * 100, 3) for n in (3, 5, 10, 20)},
            "vs_sma20_pct": round((prev["close"] / prev["sma20"] - 1) * 100, 3),
            "prev_range_pct": round(prev["rng"] * 100, 3),
            "vix_prev": v,
            "pcr_near_prev": round(pcr.loc[:pd_].iloc[-1], 3) if (pcr.index <= pd_).any() and pd_ - pcr.loc[:pd_].index[-1] < pd.Timedelta(days=4) else np.nan,
            "short_otm_pts": round(dist, 2),
            "short_otm_pct": round(dist / spot * 100, 3),
            "short_otm_sigma": round(dist / sigma_pts, 3) if sigma_pts == sigma_pts else np.nan,
            "short_strike_mod": int(sh) % 100,
            "weekday": t.day_name()[:3],
            "entry_time": t.strftime("%H:%M"),
        })
    f = pd.DataFrame(rows)
    out = sig.drop(columns=["opened"]).merge(f, on="signal_id", how="left")
    out.to_csv(ROOT / "data" / "features.csv", index=False)
    print(len(out), "rows;", out["spot"].notna().sum(), "with intraday context;",
          out["pcr_near_prev"].notna().sum(), "with PCR")


if __name__ == "__main__":
    main()
