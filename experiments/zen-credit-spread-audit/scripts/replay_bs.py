"""Replay the REBUILT rules on 5-min NIFTY bars, pricing options with the
calibrated Black-76 model (scripts/bs_calibrate.py -> out/bs_calibration.json).

This is the independent test the rule-fit could not be: the rules decide every
entry and exit on their own, with no access to Dhan's recorded trades. The
recorded trades are used only AFTERWARDS, to compare.

Rules (README, "Rebuilt entry and exit rules"):
  entry   flat; a bar closing between 10:15 and 14:00; no trade opened today yet.
          Sell CE if that bar fell, PE if it rose.  Short = nearest 50 to spot,
          long = 400 further OTM, nearest weekly expiry (0-DTE included).
  reverse once: a trade opened today and stopped today -> open the opposite
          side at once (still before 14:00).
  stop    spread value >= credit + 40   (checked at the bar's adverse extreme;
          a gap through it fills at the gapped value)
  target  spread value <= 10            (favourable extreme; checked after the stop)
  time    next trading day at the bar closing 15:00 (14:55 from Jul 2026, which
          Dhan stamps 14:53); an expiry-day entry exits at that time the same day.

Pricing limits, measured by the calibration and NOT corrected here:
  * ~3.8 pts/trade optimistic on average, mostly because a stop is priced ~9.5
    pts too cheap (intraday IV rise is invisible to a previous-day VIX);
  * the model needs the previous day's VIX, which ends 2026-09-17, so the replay
    ends 2026-09-18.

Writes out/replay_trades[<tag>].csv. Flags: --target (0 = none), --min-body. Run with ../intraday-screener/.venv/bin/python.
"""
import argparse
import json
import math
import pathlib
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXP = ROOT.parent
sys.path.insert(0, str(ROOT / "scripts"))
from bs_calibrate import CARRY, model_price, year_frac  # noqa: E402

START, END = pd.Timestamp("2025-07-09"), pd.Timestamp("2026-09-18 23:59")
LOT_65_FROM = pd.Timestamp("2025-12-31")
EXIT_1455_FROM = pd.Timestamp("2026-07-01")
STOP_PTS, TARGET_VALUE, WIDTH = 40.0, 10.0, 400


def load():
    b = pd.read_parquet(EXP / "intraday-screener" / "data" / "clean" / "5m" / "NIFTY50.parquet")
    b["ts"] = pd.to_datetime(b["ts"])
    b = b[(b["ts"] >= START) & (b["ts"] <= END)].reset_index(drop=True)
    b["d"] = b["ts"].dt.normalize()
    con = sqlite3.connect(f"file:{EXP / 'hedged-option-selling/data/cache/options.db'}?mode=ro", uri=True)
    vix = pd.read_sql("select trade_date, close from index_close where index_name='India VIX'", con)
    vix = vix.assign(d=pd.to_datetime(vix["trade_date"])).set_index("d")["close"].sort_index()
    exps = pd.read_sql("select distinct expiry from fo_bhav where symbol='NIFTY' and kind='IDO' "
                       "and trade_date>='2025-06-01'", con)
    expiries = set(pd.to_datetime(exps["expiry"]))
    expiries |= set(pd.to_datetime(pd.read_csv(ROOT / "data" / "signals.csv")["expiry"]))
    p = json.loads((ROOT / "out" / "bs_calibration.json").read_text())["params"]
    return b, vix, sorted(expiries), p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=float, default=TARGET_VALUE,
                    help="buy back when spread value <= this; 0 disables the target")
    ap.add_argument("--min-body", type=float, default=0.0,
                    help="skip an entry bar whose body is below this %% of price")
    ap.add_argument("--tag", default="", help="suffix for the output file")
    args = ap.parse_args()
    b, vix, expiries, p = load()
    days = list(b["d"].unique())
    nxt_day = {d: days[i + 1] if i + 1 < len(days) else None for i, d in enumerate(days)}
    vix_prev = {d: float(vix[vix.index < d].iloc[-1]) for d in days}

    def value(pos, spot, t):
        """Spread value (short minus long) at spot, time t."""
        T = year_frac(t, pos["expiry"])
        F = spot * math.exp(CARRY * T)
        dte = (pos["expiry"] - t.normalize()).days
        v = vix_prev[t.normalize()]
        s = model_price(p, F, pos["ks"], T, v, pos["cp"], dte)
        l = model_price(p, F, pos["kl"], T, v, pos["cp"], dte)
        return s - l, s, l

    def open_pos(r, t_end, cp, nth):
        d = r.d
        expiry = next(e for e in expiries if e >= d)
        ks = round(r.close / 50) * 50
        kl = ks + WIDTH if cp == "CE" else ks - WIDTH
        late = t_end >= EXIT_1455_FROM
        pos = {"opened": t_end, "cp": cp, "ks": ks, "kl": kl, "expiry": expiry, "nth": nth,
               "lot_size": 65 if d >= LOT_65_FROM else 75, "spot_in": round(r.close, 2),
               "exit_day": d if expiry == d else nxt_day[d],
               "exit_hhmm": "14:55" if late else "15:00"}
        credit, s_in, l_in = value(pos, r.close, t_end)
        pos.update(credit=round(credit, 2), s_in=round(s_in, 2), l_in=round(l_in, 2))
        return pos

    trades, pos = [], None
    opened_today = {}          # day -> list of trades opened that day
    for r in b.itertuples():
        t_open, t_end = r.ts, r.ts + pd.Timedelta(minutes=5)
        hhmm = t_end.strftime("%H:%M")
        if pos is not None:
            ce = pos["cp"] == "CE"
            adverse = r.high if ce else r.low
            favour = r.low if ce else r.high
            v_open = value(pos, r.open, t_open)[0]
            v_adv = value(pos, adverse, t_end)[0]
            v_fav = value(pos, favour, t_end)[0]
            exit_at = None
            if max(v_open, v_adv) >= pos["credit"] + STOP_PTS:
                fill = max(pos["credit"] + STOP_PTS, v_open)
                spot_x = r.open if v_open >= pos["credit"] + STOP_PTS else adverse
                exit_at, reason = (fill, spot_x), "stop"
            elif args.target > 0 and min(v_open, v_fav) <= args.target:
                fill = min(args.target, v_open)
                spot_x = r.open if v_open <= args.target else favour
                exit_at, reason = (fill, spot_x), "target"
            elif r.d == pos["exit_day"] and hhmm == pos["exit_hhmm"]:
                exit_at, reason = (value(pos, r.close, t_end)[0], r.close), "time"
            if exit_at:
                fill, spot_x = exit_at
                _, s_px, l_px = value(pos, spot_x, t_end)
                # scale the two legs so they sum to the fill (the stop/target fill is a spread level)
                adj = fill - (s_px - l_px)
                trades.append({**{k: pos[k] for k in ("opened", "cp", "ks", "kl", "expiry", "lot_size",
                                                       "credit", "s_in", "l_in", "spot_in", "nth")},
                               "closed": t_end, "reason": reason, "debit": round(fill, 2),
                               "s_out": round(max(s_px + adj, 0.05), 2), "l_out": round(max(l_px, 0.0), 2),
                               "spot_out": round(spot_x, 2), "pts": round(pos["credit"] - fill, 2)})
                last = trades[-1]
                pos = None
                # stop-and-reverse, once, for a trade opened and stopped today
                if (reason == "stop" and last["opened"].normalize() == r.d and last["nth"] == 1
                        and "10:15" <= hhmm < "14:00"):
                    pos = open_pos(r, t_end, "PE" if last["cp"] == "CE" else "CE", 2)
                continue
        if (pos is None and "10:15" <= hhmm < "14:00" and not opened_today.get(r.d)
                and abs(r.close / r.open - 1) * 100 >= args.min_body):
            pos = open_pos(r, t_end, "CE" if r.close < r.open else "PE", 1)
        if pos is not None and pos["opened"] == t_end:
            opened_today.setdefault(r.d, []).append(pos)

    out = pd.DataFrame(trades)
    out.to_csv(ROOT / "out" / f"replay_trades{args.tag}.csv", index=False)
    print(f"replayed {len(out)} trades {out['opened'].min():%Y-%m-%d} -> {out['opened'].max():%Y-%m-%d}")
    print(out.groupby("reason")["pts"].agg(["count", "mean", "sum"]).round(1))
    print("total pts", round(out["pts"].sum(), 1), " win rate", round((out["pts"] > 0).mean() * 100, 1))


if __name__ == "__main__":
    main()
