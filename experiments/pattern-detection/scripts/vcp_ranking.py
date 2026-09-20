"""Which candidate gets the slot?

79% of signals are skipped for want of a free slot, and until now they were
taken first-come-first-served by date. Signals cluster -- 73% of them arrive
on a day alongside two or more others, up to 22 at once -- so the choice is
real and made ~1,050 times.

Ranking keys tested, all computable at the signal bar:

  score        the detector's own composite
  squeeze      range in the last contraction / the first (tighter first)
  rs           6-month return, percentile-ranked ACROSS THE UNIVERSE on that
               same date -- IBD-style relative strength, not absolute return
  dry          final-contraction volume / base average (drier first)
  to_pivot     how far price still has to travel (closer first)
  rr           reward:risk to the pivot (bigger first)
  t_count      number of contractions

THE CONTROL IS THE POINT. A ranking key that beats date order by 1% CAGR has
proved nothing unless random allocation does worse, so `random` is run over
20 seeds and reported as a distribution. Any key inside that spread is noise.
Each key is also run REVERSED: if `score` helps and `score reversed` hurts by
a similar amount, the effect is real and signed; if both beat the baseline,
the baseline was simply unlucky.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
from patlib.bars import list_sqlite_symbols, load_sqlite
from vcp_final import DB, build_trades

OUT = Path(__file__).resolve().parent.parent / "out"


def relative_strength(signal_dates, lookback=126):
    """{(symbol, date): percentile of its `lookback`-bar return that day}."""
    want = set(signal_dates)
    per_date = defaultdict(dict)
    for sym in list_sqlite_symbols(DB):
        b = load_sqlite(DB, sym)
        c = b.close
        for i in range(lookback, len(b)):
            d = str(b.date[i])
            if d in want and c[i - lookback] > 0:
                per_date[d][sym] = float(c[i] / c[i - lookback] - 1.0)
    out = {}
    for d, m in per_date.items():
        if len(m) < 20:
            continue
        syms = list(m)
        vals = np.array([m[s] for s in syms])
        order = vals.argsort().argsort() / max(len(vals) - 1, 1)
        for s, p in zip(syms, order):
            out[(s, d)] = float(p)
    return out


def portfolio(trades, key=None, reverse=False, slots=12, risk_frac=0.01,
              cost=0.003, seed=None, start=1.0):
    """Day by day: book exits, then fill free slots from that day's candidates
    in rank order. `key=None` is first-come-first-served, the baseline."""
    max_weight = 1.0 / slots
    by_entry = defaultdict(list)
    for t in trades:
        by_entry[t["entry_date"]].append(t)
    exits = defaultdict(list)
    dates = sorted({t["entry_date"] for t in trades} |
                   {t["exit_date"] for t in trades})
    rng = np.random.default_rng(seed) if seed is not None else None

    equity, peak, maxdd = start, start, 0.0
    live = []                      # (exit_date, pnl)
    taken, skipped, chosen = 0, 0, []
    for d in dates:
        due = [p for p in live if p[0] <= d]
        live = [p for p in live if p[0] > d]
        for _, pnl in due:
            equity += pnl
            peak = max(peak, equity)
            maxdd = max(maxdd, (peak - equity) / peak if peak > 0 else 0.0)
        cands = by_entry.get(d, [])
        if not cands:
            continue
        free = slots - len(live)
        if free <= 0:
            skipped += len(cands)
            continue
        if key is None:
            ordered = cands
        elif key == "random":
            ordered = list(cands)
            rng.shuffle(ordered)
        else:
            vals = [c.get(key) for c in cands]
            ordered = [c for _, c in sorted(
                zip(vals, range(len(cands))),
                key=lambda z: (z[0] is None, z[0] if z[0] is not None else 0),
                reverse=reverse)]
            ordered = [cands[i] for _, i in sorted(
                zip(vals, range(len(cands))),
                key=lambda z: (z[0] is None,
                               -(z[0] if z[0] is not None else 0) if reverse
                               else (z[0] if z[0] is not None else 0)))]
        for t in ordered[:free]:
            w = min(risk_frac / max(t["risk_pct"], 1e-6), max_weight)
            live.append((t["exit_date"], equity * w * (t["ret"] - cost)))
            taken += 1
            chosen.append((t["symbol"], t["entry_date"]))
        skipped += max(0, len(ordered) - free)
    for _, pnl in live:
        equity += pnl
    yrs = (np.datetime64(dates[-1]) - np.datetime64(dates[0])).astype(int) / 365.25
    cagr = (equity / start) ** (1 / yrs) - 1 if yrs > 0.5 and equity > 0 else float("nan")
    return dict(cagr=cagr, maxdd=maxdd, final=equity, taken=taken,
                skipped=skipped, chosen=set(chosen))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rule", default="early + EMA50")
    ap.add_argument("--slots", type=int, default=12)
    ap.add_argument("--cost", type=float, default=0.003)
    ap.add_argument("--seeds", type=int, default=20)
    a = ap.parse_args()

    rows = json.loads(Path("out/vcp_entry_bases.json").read_text())
    trades = build_trades(rows)[a.rule]

    # join the detector metrics the bases file does not carry
    pre = {}
    f = Path("out/vcp_pre_signals.json")
    if f.exists():
        for r in json.loads(f.read_text()):
            pre[(r["symbol"], r["entry_idx"])] = r
    meta = {(r["symbol"], r["seen_idx"]): r for r in rows}
    for t in trades:
        m = meta.get((t["symbol"], t["entry_idx"])) or {}
        p = pre.get((t["symbol"], t["entry_idx"])) or {}
        t["score"] = m.get("score", p.get("score"))
        t["to_pivot"] = m.get("first_gap", p.get("to_pivot"))
        t["t_count"] = p.get("t_count")
        t["squeeze"] = p.get("squeeze")
        t["dry"] = p.get("dry")
        t["rr"] = p.get("rr")
    cov = {k: sum(1 for t in trades if t.get(k) is not None) / len(trades)
           for k in ("score", "squeeze", "dry", "rr", "t_count", "to_pivot")}

    print("computing cross-sectional relative strength ...")
    rs = relative_strength({t["entry_date"] for t in trades})
    for t in trades:
        t["rs"] = rs.get((t["symbol"], t["entry_date"]))
    cov["rs"] = sum(1 for t in trades if t.get("rs") is not None) / len(trades)

    base = portfolio(trades, None, slots=a.slots, cost=a.cost)
    L = [f"rule: {a.rule}   slots {a.slots}   cost {a.cost:.1%}   "
         f"{len(trades)} signals",
         f"field coverage: " + ", ".join(f"{k} {v:.0%}" for k, v in cov.items()), ""]

    rnd = [portfolio(trades, "random", slots=a.slots, cost=a.cost, seed=s)
           for s in range(a.seeds)]
    rc = np.array([r["cagr"] for r in rnd])
    rd = np.array([r["maxdd"] for r in rnd])
    L.append(f"{'allocation':<28}{'CAGR':>8}{'maxDD':>8}{'taken':>7}"
             f"{'final x':>9}{'vs date':>9}{'overlap':>9}")
    L.append(f"{'date order (baseline)':<28}{base['cagr']:>8.1%}"
             f"{base['maxdd']:>8.1%}{base['taken']:>7}{base['final']:>9.2f}"
             f"{'--':>9}{'--':>9}")
    L.append(f"{'random (mean of %d seeds)' % a.seeds:<28}{rc.mean():>8.1%}"
             f"{rd.mean():>8.1%}{rnd[0]['taken']:>7}"
             f"{np.mean([r['final'] for r in rnd]):>9.2f}"
             f"{rc.mean() - base['cagr']:>+9.1%}"
             f"{np.mean([len(r['chosen'] & base['chosen']) / max(len(base['chosen']), 1) for r in rnd]):>9.0%}")
    L.append(f"{'   random 10th-90th pct':<28}"
             f"{np.percentile(rc, 10):>8.1%}{'..':>3}{np.percentile(rc, 90):<5.1%}")
    L.append("")

    KEYS = [("score", True, "highest score first"),
            ("score", False, "LOWEST score first (sign check)"),
            ("rs", True, "strongest 6m relative strength first"),
            ("rs", False, "weakest relative strength first (sign check)"),
            ("squeeze", False, "tightest range contraction first"),
            ("squeeze", True, "loosest first (sign check)"),
            ("dry", False, "driest final contraction first"),
            ("to_pivot", False, "closest to the pivot first"),
            ("to_pivot", True, "furthest from the pivot first"),
            ("rr", True, "best reward:risk first"),
            ("t_count", True, "most contractions first")]
    for k, rev, label in KEYS:
        if cov.get(k, 0) < 0.5:
            continue
        p = portfolio(trades, k, reverse=rev, slots=a.slots, cost=a.cost)
        ov = len(p["chosen"] & base["chosen"]) / max(len(base["chosen"]), 1)
        z = (p["cagr"] - rc.mean()) / (rc.std(ddof=1) if rc.std(ddof=1) > 0 else 1)
        L.append(f"{label:<28}{p['cagr']:>8.1%}{p['maxdd']:>8.1%}{p['taken']:>7}"
                 f"{p['final']:>9.2f}{p['cagr'] - base['cagr']:>+9.1%}{ov:>9.0%}"
                 f"   z vs random {z:+.1f}")
    L.append("")
    L.append("vs date = CAGR difference against first-come-first-served")
    L.append("overlap = share of the baseline's trades this rule also took")
    L.append("z vs random = standard deviations from the random-allocation mean;")
    L.append("              inside about +/-2 is indistinguishable from luck")
    txt = "\n".join(L)
    print(txt)
    OUT.mkdir(exist_ok=True)
    (OUT / "vcp_ranking_report.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
