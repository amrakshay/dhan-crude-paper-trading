"""Measure how well each rebuilt rule reproduces Dhan's recorded trades.

Reads data/signals.csv, data/features.csv (scripts/features.py) and the 5-min
NIFTY bars. Run with ../intraday-screener/.venv/bin/python. Prints one line per
rule: what it says and how many trades it matches.

This scores each rule AGAINST the recorded trades. It is not a replay: a replay
needs intraday option prices, which are not on disk and which Dhan's chart API
no longer serves to this account (DH-902, Free plan).
"""
import pathlib

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
BARS = ROOT.parent / "intraday-screener" / "data" / "clean" / "5m" / "NIFTY50.parquet"
BARS_END = pd.Timestamp("2026-09-25 15:30")


def pct(x):
    return f"{np.mean(x) * 100:5.1f}%  ({int(np.sum(x))}/{len(x)})"


def main():
    s = pd.read_csv(ROOT / "data" / "features.csv")
    s["o"] = pd.to_datetime(s["opened_ist"].str[:19])
    s["c"] = pd.to_datetime(s["closed_ist"].str[:19])
    s["exp"] = pd.to_datetime(s["expiry"])
    s = s.sort_values("o").reset_index(drop=True)
    s["pts"] = s["credit"] - s["debit_at_exit"]
    s["t_exit"] = s["c"].dt.strftime("%H:%M")
    s["settled"] = s["c"].dt.normalize() > s["exp"]

    b = pd.read_parquet(BARS)
    b["ts"] = pd.to_datetime(b["ts"])
    b = b[b["ts"] >= "2025-06-01"].reset_index(drop=True)
    idx, op, cl = b["ts"].values, b["open"].values, b["close"].values
    cov = s[s["spot"].notna()].copy()
    last = [np.searchsorted(idx, np.datetime64(t - pd.Timedelta(minutes=5)), side="right") - 1
            for t in cov["o"]]
    cov["bar_down"] = cl[last] < op[last]
    cov["bar_body"] = np.abs(cl[last] / op[last] - 1) * 100
    is_ce = cov["option_type"] == "CE"

    print(f"trades: {len(s)}; with NIFTY 5-min context: {len(cov)} (bars end {BARS_END:%Y-%m-%d})\n")
    print("ENTRY")
    print("  direction = sell CE if last closed 5-min bar fell, else PE ",
          pct((cov["bar_down"] == is_ce).values))
    big = cov["bar_body"] >= 0.03
    print("    ... when that bar's body >= 0.03%                        ",
          pct((cov["bar_down"] == is_ce)[big].values))
    atm = (cov["spot"] / 50).round() * 50
    print("  short strike = nearest 50 to spot                          ", pct((cov["short_strike"] == atm).values))
    print("  short strike within 50 pts of spot                         ",
          pct(((cov["short_strike"] - cov["spot"]).abs() <= 50).values))
    print("  long strike = short +/- 400 (away from money)              ",
          pct((s["width"] == 400).values))
    print("  entry between 10:15 and 10:21                              ",
          pct((s["o"].dt.strftime("%H:%M") <= "10:21").values))

    print("\nEXIT")
    timed = s["t_exit"].isin(["15:00", "14:53"])
    before = s["c"] < "2026-07-01"
    print("  time exit 15:00 before Jul 2026, 14:53 from Jul 2026       ",
          pct(np.where(before[timed], s["t_exit"][timed] == "15:00", s["t_exit"][timed] == "14:53")))
    nxt = s[timed & (s["c"].dt.normalize() > s["o"].dt.normalize())]
    same = s[timed & (s["c"].dt.normalize() == s["o"].dt.normalize())]
    print("  time exits on the NEXT trading day                         ", f"{len(nxt)} trades;",
          f"{len(same)} same-day, of which {int((same['dte_at_entry'] == 0).sum())} entered on expiry day")
    stops = s[(~timed) & (~s["settled"]) & (s["pts"] < 0) & (s["t_exit"] > "09:20")]
    print(f"  intraday stop: spread value >= credit + 40 pts             "
          f"{len(stops)} stops, none better than {stops['pts'].max():.1f}, median {stops['pts'].median():.1f}")
    tgt = s[(~timed) & (~s["settled"]) & (s["pts"] > 0)]
    near10 = tgt["debit_at_exit"].between(6, 12)
    print("  target: buy back when spread value <= ~10 pts              ", pct(near10.values),
          "of early profitable exits left 6-12 pts")
    held = (s["c"].dt.normalize() - s["o"].dt.normalize()).dt.days
    print("  never held past the next trading day (calendar days <= 4)  ", pct((held <= 4).values))

    print("\nRE-ENTRY")
    s["d"] = s["o"].dt.normalize()
    s["nth"] = s.groupby("d").cumcount() + 1
    print("  at most 2 trades per day                                   ",
          pct((s.groupby("d").size() <= 2).values))
    two = s.groupby("d").filter(lambda g: len(g) == 2).groupby("d")["option_type"].agg(lambda x: x.iloc[0] != x.iloc[1])
    print("  second trade of a day is the opposite side                 ", pct(two.values))


if __name__ == "__main__":
    main()
