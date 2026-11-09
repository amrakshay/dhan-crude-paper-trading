"""Calibrate a Black-76 pricing model against Dhan's own recorded leg prices.

The replay (scripts/replay_bs.py) has to price NIFTY options intraday with no
option data at all -- only 5-min NIFTY bars and the previous day's India VIX.
Before trusting it, measure how wrong that is on the 456 legs Dhan recorded.

Model:  atm   = VIX_prev/100 x atm_mult[dte bucket]
        z     = ln(K / F) / (atm x sqrt(T))          moneyness in standard deviations
        sigma = atm x (1 + slope[CE|PE] x z + curve[CE|PE] x z^2)
Fitted by least squares on the leg prices of ENTRIES opened before 2026-04-01,
then scored on everything else -- entries after that date and every EXIT -- so
the score is not the fit grading itself.  A first version used raw ln(K/F) for
the skew; it overpriced the 400-point wing by ~14 points at 4-6 DTE, because the
same strike is fewer standard deviations away when there is more time left.

Writes out/bs_calibration.json. Run with ../intraday-screener/.venv/bin/python.
"""
import json
import math
import pathlib
import sqlite3
import sys

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXP = ROOT.parent
sys.path.insert(0, str(EXP / "hedged-option-selling"))
from hedgelib.blackscholes import implied_vol, price  # noqa: E402

CARRY = 0.05            # forward = spot x e^(carry x T): repo ~6.5% less dividend yield
SPLIT = "2026-04-01"
BUCKETS = [(0, 0), (1, 1), (2, 3), (4, 6)]


def year_frac(t0, expiry_day):
    exp = pd.Timestamp(expiry_day) + pd.Timedelta(hours=15, minutes=30)
    return max((exp - t0).total_seconds(), 60) / (365 * 24 * 3600)


def bucket(dte):
    for lo, hi in BUCKETS:
        if lo <= dte <= hi:
            return f"{lo}-{hi}"
    return "4-6"


def load_context():
    b = pd.read_parquet(EXP / "intraday-screener" / "data" / "clean" / "5m" / "NIFTY50.parquet")
    b["ts"] = pd.to_datetime(b["ts"])
    b = b[b["ts"] >= "2025-06-01"].reset_index(drop=True)
    con = sqlite3.connect(f"file:{EXP / 'hedged-option-selling/data/cache/options.db'}?mode=ro", uri=True)
    v = pd.read_sql("select trade_date, close from index_close where index_name='India VIX'", con)
    v = v.assign(d=pd.to_datetime(v["trade_date"])).set_index("d")["close"].sort_index()
    return b, v


def spot_at(b, t):
    """NIFTY at time t, interpolated open->close inside the 5-min bar holding t.

    Not "the last bar that had closed": at a 09:16 exit that is the PREVIOUS
    day's 15:25 bar, which prices a gap-open exit as if there were no gap."""
    i = np.searchsorted(b["ts"].values, np.datetime64(t), side="right") - 1
    if i < 0:
        return np.nan
    frac = min(max((t - b["ts"].iloc[i]).total_seconds() / 300, 0), 1)
    return float(b["open"].iloc[i] + (b["close"].iloc[i] - b["open"].iloc[i]) * frac)


def vix_before(v, t):
    s = v[v.index < t.normalize()]
    return float(s.iloc[-1]) if len(s) else np.nan


def observations(b, v):
    s = pd.read_csv(ROOT / "data" / "signals.csv")
    legs = pd.read_csv(ROOT / "data" / "legs.csv")
    bars_end = b["ts"].max()
    rows = []
    for _, sg in s.iterrows():
        o = pd.Timestamp(sg["opened_ist"][:19])
        c = pd.Timestamp(sg["closed_ist"][:19])
        settled = c.normalize() > pd.Timestamp(sg["expiry"])
        for when, t, col in (("entry", o, "entry"), ("exit", c, "exit")):
            if when == "exit" and settled:
                continue
            if t > bars_end:
                continue
            spot, vix = spot_at(b, t), vix_before(v, t)
            if not (spot == spot and vix == vix):
                continue
            T = year_frac(t, sg["expiry"])
            F = spot * math.exp(CARRY * T)
            dte = (pd.Timestamp(sg["expiry"]) - t.normalize()).days
            for _, lg in legs[legs["signal_id"] == sg["signal_id"]].iterrows():
                is_call = lg["option_type"] == "CE"
                px = float(lg[col])
                rows.append({
                    "signal_id": sg["signal_id"], "when": when, "t": t, "side": lg["side"],
                    "cp": lg["option_type"], "K": lg["strike"], "F": F, "T": T, "dte": dte,
                    "bucket": bucket(dte), "vix": vix, "px": px,
                    "iv": implied_vol(px, F, lg["strike"], T, is_call, min_vega=5.0),
                    "m": math.log(lg["strike"] / F),
                })
    return pd.DataFrame(rows)


KEYS = [f"{lo}-{hi}" for lo, hi in BUCKETS]


def unpack(x):
    return {"atm_mult": dict(zip(KEYS, x[:4])),
            "slope": {"CE": x[4], "PE": x[5]}, "curve": {"CE": x[6], "PE": x[7]}, "carry": CARRY}


def fit(obs):
    tr = obs[(obs["when"] == "entry") & (obs["t"] < SPLIT)]
    rows = list(tr.itertuples())

    def resid(x):
        p = unpack(x)
        return [model_price(p, r.F, r.K, r.T, r.vix, r.cp, r.dte) - r.px for r in rows]

    x0 = [1.2, 1.0, 0.9, 0.8, 0.0, 0.0, 0.0, 0.0]
    sol = least_squares(resid, x0, loss="soft_l1", f_scale=10.0,
                        bounds=([0.2] * 4 + [-2, -2, -1, -1], [4.0] * 4 + [2, 2, 1, 1]))
    p = unpack(sol.x)
    return {"atm_mult": {k: round(float(v), 4) for k, v in p["atm_mult"].items()},
            "slope": {k: round(float(v), 4) for k, v in p["slope"].items()},
            "curve": {k: round(float(v), 4) for k, v in p["curve"].items()}, "carry": CARRY}


def model_price(p, F, K, T, vix, cp, dte):
    atm = vix / 100 * p["atm_mult"][bucket(dte)]
    z = math.log(K / F) / (atm * math.sqrt(T))
    sig = atm * (1 + p["slope"][cp] * z + p["curve"][cp] * z * z)
    return price(F, K, T, min(max(sig, 0.02), 3.0), cp == "CE")


def score(obs, p):
    obs = obs.copy()
    obs["model"] = [model_price(p, r.F, r.K, r.T, r.vix, r.cp, r.dte) for r in obs.itertuples()]
    sign = np.where(obs["side"] == "SELL", 1, -1)
    obs["sv_actual"], obs["sv_model"] = obs["px"] * sign, obs["model"] * sign
    spread = obs.groupby(["signal_id", "when", "t"])[["sv_actual", "sv_model"]].sum().reset_index()
    spread["err"] = spread["sv_model"] - spread["sv_actual"]
    spread["in_sample"] = (spread["when"] == "entry") & (spread["t"] < SPLIT)
    out = {}
    for name, g in (("in-sample entries", spread[spread["in_sample"]]),
                    ("out-of-sample entries", spread[(spread["when"] == "entry") & ~spread["in_sample"]]),
                    ("exits", spread[spread["when"] == "exit"])):
        e = g["err"]
        out[name] = {"n": int(len(e)), "bias": round(float(e.mean()), 2),
                     "mae": round(float(e.abs().mean()), 2),
                     "p90_abs": round(float(e.abs().quantile(0.9)), 2)}
    # does the model see the same P&L as the record? (entry->exit change in spread value)
    ent = spread[spread["when"] == "entry"].set_index("signal_id")
    ext = spread[spread["when"] == "exit"].set_index("signal_id")
    j = ent.join(ext, lsuffix="_in", rsuffix="_out", how="inner")
    pnl_a = j["sv_actual_in"] - j["sv_actual_out"]
    pnl_m = j["sv_model_in"] - j["sv_model_out"]
    out["trade_pnl_pts"] = {"n": int(len(j)), "corr": round(float(np.corrcoef(pnl_a, pnl_m)[0, 1]), 3),
                            "mean_actual": round(float(pnl_a.mean()), 2), "mean_model": round(float(pnl_m.mean()), 2),
                            "mae": round(float((pnl_m - pnl_a).abs().mean()), 2),
                            "sign_agree": round(float((np.sign(pnl_a) == np.sign(pnl_m)).mean()), 3)}
    return out


def main():
    b, v = load_context()
    obs = observations(b, v)
    p = fit(obs)
    sc = score(obs, p)
    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "out" / "bs_calibration.json").write_text(json.dumps({"params": p, "score": sc}, indent=1))
    print("params", json.dumps(p))
    for k, x in sc.items():
        print(f"{k:24} {x}")


if __name__ == "__main__":
    main()
