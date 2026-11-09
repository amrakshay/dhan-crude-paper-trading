"""Check the cleaned 5-minute bars against an independent source: `daily_bars`.

For every kept (symbol, day) the session aggregated from 5-minute bars should
agree with the adjusted daily bar:

  * high and low within `--tol` (the daily bar also sees the closing session
    and the auction, so tiny differences are expected; big ones are not);
  * volume: intraday sum <= daily volume, and a large fraction of it.

A residual unadjusted split shows up as a ratio near 2, 3, 5 or 10; a stray
print shows up as an intraday high or low OUTSIDE the daily range. Writes
`out/validate_clean.json` and prints the worst offenders.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

EXP = Path(__file__).resolve().parent.parent
CLEAN = EXP / "data" / "clean" / "5m"
DB = EXP.parent.parent / "backend" / "data" / "paper_trading.db"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tol", type=float, default=0.005)
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    daily = pd.read_sql_query(
        "SELECT symbol, bar_date AS date, CAST(high AS REAL) dh, CAST(low AS REAL) dl, "
        "CAST(volume AS REAL) dv FROM daily_bars WHERE exchange_segment='NSE_EQ'", conn)
    daily["date"] = pd.to_datetime(daily["date"]).dt.date

    rows = []
    for f in sorted(CLEAN.glob("*.parquet")):
        sym = f.stem
        if sym == "NIFTY50":
            continue
        b = pd.read_parquet(f)
        if b.empty:
            continue
        agg = b.groupby("date").agg(ih=("high", "max"), il=("low", "min"), iv=("volume", "sum")).reset_index()
        agg.insert(0, "symbol", sym)
        rows.append(agg)
    if not rows:
        print("Nothing cleaned yet.")
        return 2
    m = pd.concat(rows).merge(daily, on=["symbol", "date"], how="left")
    missing_daily = m["dh"].isna()
    m = m[~missing_daily].copy()
    m["high_err"] = m["ih"] / m["dh"] - 1
    m["low_err"] = m["il"] / m["dl"] - 1
    m["vol_frac"] = m["iv"] / m["dv"].replace(0, np.nan)

    # Separate the two ways a day can disagree. If THIS day's high error is
    # large while the adjacent days of the same symbol agree, the one-day
    # anomaly is in `daily_bars` (never-adjusted rows exist there: BHARTIARTL
    # 2018-04-20 reads 402 between 346 and 356); a sustained error is ours.
    m = m.sort_values(["symbol", "date"])
    e = m["high_err"].abs()
    prev_ok = m.groupby("symbol")["high_err"].shift(1).abs() < args.tol
    next_ok = m.groupby("symbol")["high_err"].shift(-1).abs() < args.tol
    m["daily_isolated"] = (e > 0.02) & prev_ok & next_ok
    isolated = m[m["daily_isolated"]]
    m = m[~m["daily_isolated"]]
    bad_hi = m["high_err"].abs() > args.tol
    bad_lo = m["low_err"].abs() > args.tol
    out = {
        "daily_bars_isolated_anomalies": int(len(isolated)),
        "daily_bars_isolated_by_symbol": isolated["symbol"].value_counts().head(15).to_dict(),
        "daily_bars_isolated_examples": isolated[["symbol", "date", "ih", "dh"]].head(10).astype(str).to_dict("records"),
        "symbol_days": int(len(m)),
        "symbol_days_without_daily_bar": int(missing_daily.sum()),
        "high_within_tol_pct": round(100 * (1 - bad_hi.mean()), 3),
        "low_within_tol_pct": round(100 * (1 - bad_lo.mean()), 3),
        "high_err_quantiles": m["high_err"].quantile([0.001, 0.01, 0.5, 0.99, 0.999]).round(5).to_dict(),
        "low_err_quantiles": m["low_err"].quantile([0.001, 0.01, 0.5, 0.99, 0.999]).round(5).to_dict(),
        "vol_frac_quantiles": m["vol_frac"].quantile([0.01, 0.1, 0.5, 0.9, 0.99]).round(3).to_dict(),
        # A residual split looks like a whole-number price ratio.
        "suspected_unadjusted": int(((m["ih"] / m["dh"]).round(1).isin([0.1, 0.2, 0.3, 0.5, 2.0, 3.0, 5.0, 10.0])).sum()),
        "bad_by_symbol": (bad_hi | bad_lo).groupby(m["symbol"]).sum().sort_values(ascending=False).head(15).to_dict(),
        "bad_by_year": (bad_hi | bad_lo).groupby(pd.to_datetime(m["date"]).dt.year).mean().round(4).to_dict(),
    }
    worst = m.loc[(m["high_err"].abs() + m["low_err"].abs()).sort_values(ascending=False).index[:12],
                  ["symbol", "date", "ih", "dh", "il", "dl", "high_err", "low_err"]]
    out["worst"] = worst.astype(str).to_dict("records")
    (EXP / "out" / "validate_clean.json").write_text(json.dumps(out, indent=1, default=str))
    for k, v in out.items():
        if k != "worst":
            print(f"{k}: {v}")
    print(worst.round(4).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
