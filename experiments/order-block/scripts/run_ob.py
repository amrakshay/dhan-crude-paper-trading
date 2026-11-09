"""The primary test: the blog's full playbook against the pass bar.

  1. net R per trade > 0 at 5 bps slippage, day-bootstrap 95% CI clear of 0
  2. beats each control (random side / depth / name), CI of the difference clear of 0
  3. positive in a majority of calendar years
  4. holdout (2023-01 on), run ONCE, still positive -- needs --holdout, is logged,
     and is not to be run without Akshay's go-ahead (README.md)

Writes out/<label>_<period>.json and the trades as parquet.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime

import _bootstrap  # noqa: F401
import pandas as pd

from obl import EXP
from obl.run import PASS_SLIP, SLIPPAGES, SUSPECT_YEARS, Config, World, controls, summarise, with_net
from islib.stats import day_bootstrap

OUT = EXP / "out"


def guard_holdout(label: str, reason: str | None) -> None:
    path = OUT / "holdout_log.json"
    log = json.loads(path.read_text()) if path.exists() else []
    if any(e["config"] == label for e in log) and not reason:
        raise SystemExit(f"{label} has already been run on the holdout. It is run ONCE; "
                         f"--rerun-reason overrides and is logged.")
    log.append({"config": label, "at": datetime.now().isoformat(timespec="seconds"),
                "rerun_reason": reason})
    path.write_text(json.dumps(log, indent=1))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--holdout", action="store_true")
    ap.add_argument("--rerun-reason")
    ap.add_argument("--control-reps", type=int, default=5)
    args = ap.parse_args()

    cfg = Config()
    period = "holdout" if args.holdout else "dev"
    if args.holdout:
        guard_holdout(cfg.label(), args.rerun_reason)
    t0 = time.time()
    w = World(holdout=args.holdout)
    res = {"config": cfg.label(), "period": period, "run_at": datetime.now().isoformat(timespec="seconds"),
           "by_slippage": {}}
    main_t = None
    for slip in SLIPPAGES:
        t = w.trades(cfg, slip=slip)
        res["by_slippage"][f"{slip * 1e4:.0f}bps"] = summarise(t)
        if slip == PASS_SLIP:
            main_t = t
    print(f"simulated ({time.time() - t0:.0f}s)", flush=True)
    yrs = pd.to_datetime(main_t["date"]).dt.year
    res["excluding_2021_2023"] = round(day_bootstrap(with_net(main_t[~yrs.isin(SUSPECT_YEARS)]))["mean"], 4)
    res["liquid_top100"] = summarise(main_t[main_t["liquid100"]])
    res["outside_top100"] = summarise(main_t[~main_t["liquid100"]])
    res["picks_only"] = summarise(main_t[main_t["is_pick"]])
    res["align_4_of_4"] = summarise(main_t[main_t["align"] == 4])
    res["align_3_of_4"] = summarise(main_t[main_t["align"] == 3])
    res["controls"] = controls(w, cfg, main_t, reps=args.control_reps)
    print(f"controls ({time.time() - t0:.0f}s)", flush=True)

    m = res["by_slippage"][f"{PASS_SLIP * 1e4:.0f}bps"]
    checks = {
        "1_net_positive_ci_clear": m["net_R_ci95"][0] > 0,
        "2_beats_every_control": all(c["ci95_R"][0] > 0 for c in res["controls"].values()),
        "3_majority_of_years_positive": m["years_positive"] > m["years"] / 2,
    }
    if args.holdout:
        checks["4_holdout_net_positive"] = m["net_R"] > 0
    res["pass_bar"] = checks | {"PASS": all(checks.values())}

    OUT.mkdir(exist_ok=True)
    stem = f"OB1_playbook_{period}"
    (OUT / f"{stem}.json").write_text(json.dumps(res, indent=1, default=str))
    main_t.to_parquet(OUT / f"{stem}_trades.parquet", index=False)
    print(json.dumps({k: res[k] for k in ("by_slippage", "controls", "pass_bar")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
