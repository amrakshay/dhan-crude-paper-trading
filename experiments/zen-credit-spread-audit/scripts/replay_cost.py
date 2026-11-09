"""Cost a Black-76 replay (scripts/replay_bs.py) on Rs 1,00,000 exactly as
scripts/audit.py costs Dhan's recorded trades -- same charge card, slippage
sweep, margin blocking and sizing -- and set the two side by side over the same
window, so the only difference is where the trades came from.

Per-lot margin is not modelled: each replayed trade borrows the median of
Dhan's recorded per-lot margin in its month.

Also reports a MODEL-BIAS haircut: the calibration measured the pricing model
as ~3.8 pts/trade optimistic (out/bs_calibration.json, trade_pnl_pts). The
haircut subtracts that from every replayed trade's exit, per unit.

    ../intraday-screener/.venv/bin/python -I scripts/replay_cost.py [--tag _t0]
"""
import argparse
import datetime as dt
import json
import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import audit  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def to_trades(df, margin_by_month, haircut):
    out = []
    for i, r in df.iterrows():
        o = pd.Timestamp(r["opened"]).to_pydatetime().replace(tzinfo=IST)
        c = pd.Timestamp(r["closed"]).to_pydatetime().replace(tzinfo=IST)
        # short leg bought back `haircut` points dearer: the model's measured optimism
        legs = [{"side": "SELL", "entry": r["s_in"], "exit": r["s_out"] + haircut, "lot_size": int(r["lot_size"])},
                {"side": "BUY", "entry": r["l_in"], "exit": r["l_out"], "lot_size": int(r["lot_size"])}]
        out.append(audit.Trade(f"replay-{i}", o, c, pd.Timestamp(r["expiry"]).date(),
                               margin_by_month[o.strftime("%Y-%m")], legs, False, 1))
    return out


def recorded_window(end):
    return [t for t in audit.load() if t.opened.date() <= end]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="_t0")
    args = ap.parse_args()
    df = pd.read_csv(ROOT / "out" / f"replay_trades{args.tag}.csv")
    sig = pd.read_csv(ROOT / "data" / "signals.csv")
    margin_by_month = sig.groupby(sig["opened_ist"].str[:7])["margin"].median().to_dict()
    haircut = json.loads((ROOT / "out" / "bs_calibration.json").read_text())["score"]["trade_pnl_pts"]
    haircut = round(haircut["mean_model"] - haircut["mean_actual"], 2)
    end = pd.Timestamp(df["opened"].max()).date()

    card = audit.ChargeCard()
    runs = {
        "recorded": recorded_window(end),
        "replay": to_trades(df, margin_by_month, 0.0),
        f"replay-{haircut}pt": to_trades(df, margin_by_month, haircut),
    }
    summary = {}
    for name, trades in runs.items():
        for scen in ("none", "base", "high"):
            m = audit.metrics(audit.simulate(trades, card, scen, "fixed1"))
            summary[f"{name}/{scen}"] = m
    keys = ["trades_taken", "final_equity", "return_pct", "max_dd_pct", "win_rate_pct", "profit_factor",
            "gross", "slippage", "charges", "costs_pct_of_gross"]
    print(f"window 2025-07-09 -> {end}; fixed 1 lot on Rs 1,00,000; replay file {args.tag}\n")
    cols = list(summary)
    print(f"{'':20}" + "".join(f"{c:>24}" for c in cols))
    for k in keys:
        print(f"{k:20}" + "".join(f"{str(summary[c][k]):>24}" for c in cols))
    (ROOT / "out" / f"replay_cost{args.tag}.json").write_text(json.dumps(summary, indent=1, default=str))


if __name__ == "__main__":
    main()
