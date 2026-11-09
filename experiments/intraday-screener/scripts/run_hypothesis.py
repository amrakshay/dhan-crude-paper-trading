"""STEP 4 -- run one pre-registered hypothesis against the pass bar.

Development period (before 2023-01-01) by default. The pass bar is README.md's,
fixed before any result was seen:

  1. net mean per trade > 0 at 5 bps slippage, day-bootstrap 95% CI clear of 0;
  2. beats EACH control (random side, delayed entry, random name) -- the
     day-bootstrap CI of (hypothesis - control) clear of 0;
  3. positive in a majority of calendar years;
  4. positive after costs on the HOLDOUT, run once (--holdout).

The holdout is guarded: it needs --holdout, every holdout run is appended to
out/holdout_log.json, and a second holdout run of the same hypothesis is
refused unless --rerun-reason explains why (and that reason is logged too).

Writes out/<hypothesis>_<period>.json and the trades as parquet.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from islib import panel as P
from islib.engine import ExecConfig, simulate
from islib.features import Features
from islib.hypotheses import CONTROLS, HYPOTHESES
from islib.stats import day_bootstrap, diff_bootstrap, summary, taken

OUT = P.EXP / "out"
SLIPPAGE_LEVELS = (0.0002, 0.0005, 0.0010)
PASS_SLIPPAGE = 0.0005
# FEASIBILITY.md / CLEANING.md: 2021-2023 intraday ranges sit inside the daily
# range on 1-2% of days, cause unverified. Reported with and without.
SUSPECT_YEARS = (2021, 2022, 2023)


def guard_holdout(name: str, reason: str | None) -> None:
    log_path = OUT / "holdout_log.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else []
    earlier = [e for e in log if e["hypothesis"] == name]
    if earlier and not reason:
        raise SystemExit(
            f"{name} has already been run on the holdout ({earlier[0]['at']}). The holdout "
            f"is run ONCE; pass --rerun-reason to override, and the reason will be logged.")
    log.append({"hypothesis": name, "at": datetime.now().isoformat(timespec="seconds"),
                "rerun_reason": reason})
    log_path.write_text(json.dumps(log, indent=1))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("hypothesis", choices=sorted(HYPOTHESES))
    ap.add_argument("--holdout", action="store_true", help="run on 2023-01 onwards (ONCE)")
    ap.add_argument("--rerun-reason", default=None)
    ap.add_argument("--n-liquid", type=int, default=100)
    ap.add_argument("--notional", type=float, default=100_000.0)
    ap.add_argument("--control-reps", type=int, default=3)
    args = ap.parse_args()

    h = HYPOTHESES[args.hypothesis]
    period = "holdout" if args.holdout else "dev"
    if args.holdout:
        guard_holdout(h.name, args.rerun_reason)

    t0 = time.time()
    p = P.load()
    F = Features(p, n_liquid=args.n_liquid)
    days = p.day_mask(holdout=args.holdout)
    sig = h.signals(F, days)
    print(f"{h.name} [{period}]: {len(sig)} signals on {int(days.sum())} days "
          f"({time.time() - t0:.0f}s)", flush=True)

    result = {"hypothesis": h.name, "period": period, "n_liquid": args.n_liquid,
              "notional": args.notional, "run_at": datetime.now().isoformat(timespec="seconds"),
              "by_slippage": {}}
    trades = None
    for slip in SLIPPAGE_LEVELS:
        cfg = ExecConfig(slippage=slip, notional=args.notional)
        t = simulate(p, sig, cfg)
        result["by_slippage"][f"{slip * 1e4:.0f}bps"] = summary(t) | {"bootstrap": day_bootstrap(t)}
        if slip == PASS_SLIPPAGE:
            trades = t
    print(f"  simulated at {len(SLIPPAGE_LEVELS)} slippage levels ({time.time() - t0:.0f}s)", flush=True)

    cfg = ExecConfig(slippage=PASS_SLIPPAGE, notional=args.notional)
    yrs = pd.to_datetime(trades["date"]).dt.year
    result["excluding_2021_2023"] = day_bootstrap(trades[~yrs.isin(SUSPECT_YEARS)])

    controls = {}
    for cname, fn in CONTROLS.items():
        reps = []
        for r in range(args.control_reps):
            c = simulate(p, fn(h, F, sig, np.random.default_rng(r)), cfg)
            reps.append(c)
        pooled = pd.concat(reps, ignore_index=True)
        controls[cname] = {
            "net_mean_bps": round(1e4 * taken(pooled)["net_ret"].mean(), 2),
            "taken_frac": round(len(taken(pooled)) / len(pooled), 3),
            "hypothesis_minus_control": diff_bootstrap(trades, pooled),
        }
        print(f"  control {cname}: {controls[cname]['net_mean_bps']} bps "
              f"({time.time() - t0:.0f}s)", flush=True)
    result["controls"] = controls

    main_ = result["by_slippage"][f"{PASS_SLIPPAGE * 1e4:.0f}bps"]
    b = main_["bootstrap"]
    checks = {
        "1_net_positive_ci_clear": b["ci95"][0] > 0,
        "2_beats_every_control": all(c["hypothesis_minus_control"]["ci95_bps"][0] > 0
                                     for c in controls.values()),
        "3_majority_of_years_positive": main_.get("years_positive", 0) > main_.get("years", 0) / 2,
    }
    if args.holdout:
        checks["4_holdout_net_positive"] = b["mean"] > 0
    result["pass_bar"] = checks | {"PASS": all(checks.values())}

    OUT.mkdir(exist_ok=True)
    stem = f"{h.name}_{period}_{int(args.notional // 1000)}k"
    (OUT / f"{stem}.json").write_text(json.dumps(result, indent=1, default=str))
    trades.to_parquet(OUT / f"{stem}_trades.parquet", index=False)

    print(f"\n{h.name} [{period}] at {PASS_SLIPPAGE * 1e4:.0f} bps slippage, Rs {args.notional:,.0f}/trade:")
    print(f"  {main_['taken']} trades, {main_['trades_per_day']}/day; gross {main_['gross_mean_bps']} bps, "
          f"costs {main_['cost_mean_bps']} bps, NET {main_['net_mean_bps']} bps "
          f"(CI {[round(1e4 * x, 1) for x in b['ci95']]}), win {main_['win_rate_net']:.1%}")
    for k, v in result["by_slippage"].items():
        print(f"  slippage {k}: net {v['net_mean_bps']} bps")
    print(f"  by year: {main_['by_year_net_bps']}")
    print(f"  excluding 2021-23: {1e4 * result['excluding_2021_2023']['mean']:.2f} bps")
    for k, v in controls.items():
        print(f"  vs {k}: {v['hypothesis_minus_control']}")
    print(f"  PASS BAR: {result['pass_bar']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
