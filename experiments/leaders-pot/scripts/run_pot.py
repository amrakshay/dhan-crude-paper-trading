"""Run the pre-registered leaders-pot configurations and evaluate the pass bar.

Runs (every one is counted in out/run_ledger.json):
  baseline V0 (copied engine, pot off)          full + truncated at 2026-02-26
  pot f=0.25 (primary), 0.15, 0.35 (sensitivity) full + truncated
  diagnostic: none here (see scripts/diag_*.py)

Usage (from experiments/leaders-pot):
  PYTHONDONTWRITEBYTECODE=1 /Users/akshay/Workarea/local/pullback/backend/.venv/bin/python scripts/run_pot.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from lpot.data import load  # noqa: E402
from lpot.engine import Cfg, run, stats  # noqa: E402

OUT = HERE / "out"; OUT.mkdir(exist_ok=True)
TRUNC = "2026-02-26"          # last session before the 2026 gate-off window
LONG = 30                     # spec 11: windows longer than 30 sessions
V0_FINAL = 7_093_012


def episodes(nifty: pd.DataFrame, end: str | None):
    n = nifty.copy().sort_values("date").reset_index(drop=True)
    n["sma"] = n.close.rolling(200).mean(); n["on"] = n.close > n.sma
    if end: n = n[n.date <= end].reset_index(drop=True)
    eps, cur = [], None
    for i in range(260, len(n)):
        if not n.on.iloc[i]:
            cur = [i, i] if cur is None else [cur[0], i]
        elif cur is not None:
            eps.append(cur); cur = None
    if cur is not None: eps.append(cur)
    rows = []
    for k, (a, b) in enumerate(eps, start=1):
        is_open = b == len(n) - 1
        base_i = a - 1                             # last gate-ON close before
        end_i = b if is_open else b + 2            # close of the day the liquidation fills
        end_i = min(end_i, len(n) - 1)
        rows.append(dict(episode=k, start=n.date.iloc[a], last_off=n.date.iloc[b], sessions=b - a + 1,
                         open=is_open, base_date=n.date.iloc[base_i], end_date=n.date.iloc[end_i],
                         nifty_move=100 * (n.close.iloc[b] / n.close.iloc[base_i] - 1),
                         nifty_trough=100 * (n.close.iloc[a:b + 1].min() / n.close.iloc[base_i] - 1)))
    return pd.DataFrame(rows)


def eq_series(curve):
    return pd.Series({d: v for d, v in curve})


def dd_info(curve):
    eq = eq_series(curve); eq.index = pd.to_datetime(eq.index)
    dd = eq / eq.cummax() - 1
    trough = dd.idxmin(); peak = eq[:trough].idxmax()
    return dict(dd_peak=str(peak.date()), dd_trough=str(trough.date()))


def yearly(curve):
    eq = eq_series(curve); eq.index = pd.to_datetime(eq.index)
    y = eq.resample("YE").last()
    first = eq.iloc[0]
    out = {}
    prev = first
    for d, v in y.items():
        out[d.year] = 100 * (v / prev - 1); prev = v
    return out


def evaluate(name, base, res, eps, nifty_end):
    m0 = stats(base["trades"], base["curve"]); m = stats(res["trades"], res["curve"])
    b = eq_series(base["curve"]); c = eq_series(res["curve"])
    t = res["trades"]; pt = t[t.book == "pot"].copy()
    ev = res["events"]
    act = ev[ev.kind == "activate"].set_index("episode")
    # engine episode ids are 1..k in calendar order; they must line up with the recomputed table
    assert len(act) == len(eps), "episode count mismatch"
    for k in range(len(eps)):   # activation = the open right after the episode's first OFF close
        assert eps.start.iloc[k] < act["date"].iloc[k] and (k + 1 == len(eps) or act["date"].iloc[k] <= eps.start.iloc[k + 1])
        assert act.index[k] == eps.episode.iloc[k]
    # map engine episode ids (1..k in order) onto the recomputed episode table: same ordering
    rows = []
    for _, e in eps.iterrows():
        bw = 100 * (b[e.end_date] / b[e.base_date] - 1); cw = 100 * (c[e.end_date] / c[e.base_date] - 1)
        ep = pt[pt.episode == e.episode]
        allot = float(act.loc[e.episode, "allotted"]) if e.episode in act.index else np.nan
        rows.append(dict(**e.to_dict(), base_ret=bw, comb_ret=cw, diff=cw - bw,
                         pot_trades=len(ep), pot_pnl=float(ep.net.sum()), allotted=allot,
                         pot_ret=100 * ep.net.sum() / allot if allot and allot > 0 else np.nan,
                         pot_win=float((ep.net > 0).mean() * 100) if len(ep) else np.nan))
    w = pd.DataFrame(rows)
    longw = w[w.sessions > LONG]
    pot_total = float(pt.net.sum())
    best_ep = int(w.loc[w.pot_pnl.idxmax(), "episode"]) if len(w) else None
    lowo = pot_total - float(w.pot_pnl.max()) if len(w) else np.nan
    need = 3
    c1 = bool(m["cagr"] > m0["cagr"]) and int((longw["diff"] > 0).sum()) >= need
    c2 = bool(m["dd"] >= m0["dd"] - 2.0)
    c3 = bool(m["mar"] >= m0["mar"])
    c4 = bool(pot_total > 0 and lowo > 0)
    return dict(name=name, base=m0, comb=m, windows=w, long_pos=int((longw["diff"] > 0).sum()),
                long_n=len(longw), c1=c1, c2=c2, c3=c3, c4=c4, pot_total=pot_total, lowo=lowo,
                best_ep=best_ep, pot_trades=pt, base_dd=dd_info(base["curve"]), comb_dd=dd_info(res["curve"]))


def fmt_m(m):
    return (f"final Rs {m['final']:>12,.0f} | CAGR {m['cagr']:6.2f}% | DD {m['dd']:7.2f}% | MAR {m['mar']:.3f} | "
            f"n {m['n']} | win {m['win']:.1f}% | worst mo {m['worst_mo']:.2f}%")


def main() -> None:
    t0 = time.time()
    syms, nifty = load()
    ledger = []

    def go(cfg: Cfg):
        r = run({k: v.copy() for k, v in syms.items()}, nifty, cfg)
        m = stats(r["trades"], r["curve"])
        ledger.append(dict(name=cfg.name, pot_frac=cfg.pot_frac, end=cfg.end_date,
                           final=float(m["final"]), cagr=float(m["cagr"]), dd=float(m["dd"]), mar=float(m["mar"])))
        print(f"  ran {cfg.name:22s} {fmt_m(m)}   ({time.time()-t0:.0f}s)")
        return r

    print(f"loaded {len(syms)} symbols; NIFTY -> {nifty.date.max()}")
    base_full = go(Cfg(name="V0 copied, full"))
    assert round(stats(base_full["trades"], base_full["curve"])["final"]) == V0_FINAL, "V0 regression failed"
    base_tr = go(Cfg(name="V0 copied, trunc", end_date=TRUNC))
    eps_full = episodes(nifty, None); eps_tr = episodes(nifty, TRUNC)

    results = {}
    for f in (0.25, 0.15, 0.35):
        for label, end, base, eps in (("full", None, base_full, eps_full), ("trunc", TRUNC, base_tr, eps_tr)):
            r = go(Cfg(name=f"pot{int(f*100)} {label}", pot_frac=f, end_date=end))
            results[(f, label)] = (r, evaluate(f"pot{int(f*100)} {label}", base, r, eps, end))

    # ---------------- outputs ----------------
    summary = {}
    for (f, label), (r, e) in results.items():
        key = f"pot{int(f*100)}_{label}"
        e["windows"].to_csv(OUT / f"{key}_windows.csv", index=False)
        e["pot_trades"].to_csv(OUT / f"{key}_pot_trades.csv", index=False)
        r["trades"].to_csv(OUT / f"{key}_all_trades.csv", index=False)
        r["daily"].to_csv(OUT / f"{key}_daily_equity.csv", index=False)
        pt = e["pot_trades"]
        w = e["windows"]
        no26 = pt[pt.entry_date < "2026-02-27"]
        summary[key] = dict(
            base={k: float(v) for k, v in e["base"].items()}, comb={k: float(v) for k, v in e["comb"].items()},
            base_dd=e["base_dd"], comb_dd=e["comb_dd"],
            c1=e["c1"], c2=e["c2"], c3=e["c3"], c4=e["c4"], long_pos=e["long_pos"], long_n=e["long_n"],
            pot_total=e["pot_total"], lowo=e["lowo"], best_ep=e["best_ep"],
            pot_n=len(pt), pot_win=float((pt.net > 0).mean() * 100) if len(pt) else None,
            pot_mean_ret=float(pt.ret_pct.mean()) if len(pt) else None,
            pot_median_ret=float(pt.ret_pct.median()) if len(pt) else None,
            pot_total_ex2026=float(no26.net.sum()), pot_n_ex2026=len(no26),
            pot_short_ep_pnl=float(w[w.sessions <= LONG].pot_pnl.sum()),
            pot_long_ep_pnl=float(w[w.sessions > LONG].pot_pnl.sum()),
            exit_mix=pt.reason.value_counts().to_dict(),
            yearly_base=yearly((base_full if label == "full" else base_tr)["curve"]),
            yearly_comb=yearly(r["curve"]),
        )
    json.dump(summary, open(OUT / "results.json", "w"), indent=1, default=str)
    json.dump(ledger, open(OUT / "run_ledger.json", "w"), indent=1)

    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
    for key, s in summary.items():
        print(f"\n=== {key} ===")
        print("  baseline:", fmt_m(s["base"]), s["base_dd"])
        print("  combined:", fmt_m(s["comb"]), s["comb_dd"])
        print(f"  pass: C1 {s['c1']} (long windows +: {s['long_pos']}/{s['long_n']})  C2 {s['c2']}  C3 {s['c3']}  "
              f"C4 {s['c4']} (pot Rs {s['pot_total']:+,.0f}, LOWO Rs {s['lowo']:+,.0f}, best ep {s['best_ep']})")
        print(f"  pot trades {s['pot_n']} win {s['pot_win']}% mean {s['pot_mean_ret']} median {s['pot_median_ret']} | "
              f"ex-2026 Rs {s['pot_total_ex2026']:+,.0f} on {s['pot_n_ex2026']} | short eps Rs {s['pot_short_ep_pnl']:+,.0f} "
              f"long eps Rs {s['pot_long_ep_pnl']:+,.0f} | exits {s['exit_mix']}")
    w = results[(0.25, "full")][1]["windows"]
    print("\n=== pot25 full: per-episode ===")
    print(w[["episode", "start", "last_off", "sessions", "base_date", "end_date", "nifty_move", "nifty_trough",
             "base_ret", "comb_ret", "diff", "pot_trades", "pot_pnl", "allotted", "pot_ret", "pot_win"]]
          .round(2).to_string(index=False))
    print(f"\ndone in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
