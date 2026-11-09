"""Development-period runs (2017-04-03 -> 2022-12-30). There is no holdout
switch in this script on purpose; the holdout has its own, logged script.

    .venv/bin/python scripts/run_dev.py index_short          # the key control as a strategy
    .venv/bin/python scripts/run_dev.py B1_breakdown [--regime A|B|C]
    .venv/bin/python scripts/run_dev.py all

Writes out/dev_<name>[_<regime>].json and appends to out/variant_log.json.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import argparse
import json
from datetime import date, datetime

import numpy as np
import pandas as pd

from islib import panel as PN

from bearlib import OUT
from bearlib.engine import index_bars
from bearlib.hyp import HYPOTHESES
from bearlib.run import Context, evaluate, evaluate_index_short
from bearlib.universe import Universe

DEV_END = date(2022, 12, 31)


def regime_days(p, U, rule: str) -> np.ndarray:
    if rule == "B":
        return U.regime_b
    ref = pd.read_csv(OUT / "regime_daily.csv", index_col=0, parse_dates=True)
    col = {"A": "rule_traded_A_sma200", "C": "rule_traded_C_drawdown"}[rule]
    s = ref[col].astype(bool).reindex(pd.to_datetime(p.dates)).fillna(False)
    return s.to_numpy()


def episodes(rule: str) -> list[dict]:
    r = json.loads((OUT / "regime.json").read_text())
    key = {"A": "A_sma200", "B": "B_breadth", "C": "C_drawdown"}[rule]
    return r["rules"][key]["episodes_dev"]


def log_variant(name: str, rule: str, result: dict):
    path = OUT / "variant_log.json"
    log = json.loads(path.read_text()) if path.exists() else []
    log.append({"run": name, "regime": rule, "period": "dev",
                "at": datetime.now().isoformat(timespec="seconds"),
                "net_5bps": result.get("net_5bps") or result.get("net_1bps")})
    path.write_text(json.dumps(log, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what")
    ap.add_argument("--regime", default="B", choices=["A", "B", "C"])
    ap.add_argument("--no-controls", action="store_true")
    a = ap.parse_args()
    p = PN.load()
    U = Universe(p)
    ctx = Context(p, U, index_bars(p))
    dates = np.array(p.dates)
    period = dates <= DEV_END
    on = regime_days(p, U, a.regime) & period
    eps = episodes(a.regime)
    todo = (["index_short"] + list(HYPOTHESES)) if a.what == "all" else [a.what]
    for name in todo:
        if name == "index_short":
            res = evaluate_index_short(ctx, on, period, eps)
        else:
            res = evaluate(ctx, name, on, period, eps, controls=not a.no_controls)
        res["regime"] = a.regime
        res["on_days"] = int(on.sum())
        suffix = "" if a.regime == "B" else f"_{a.regime}"
        (OUT / f"dev_{name}{suffix}.json").write_text(json.dumps(res, indent=1, default=str))
        log_variant(name, a.regime, res)
        print(json.dumps({k: v for k, v in res.items() if k not in ("portfolio", "episodes")},
                         indent=1, default=str))
        if "portfolio" in res:
            for k, v in res["portfolio"].items():
                print(k, {x: v[x] for x in v if x not in ("calendar",)})


if __name__ == "__main__":
    main()
