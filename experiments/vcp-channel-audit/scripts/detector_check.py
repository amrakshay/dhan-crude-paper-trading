"""STEP 4 -- does OUR VCP detector agree that these are VCPs?

Runs `detect_vcp` from ../pattern-detection (unchanged, imported read-only) on
every counted setup, as of its list date -- bars up to and including that day,
nothing later. Where it reports a VCP whose last bar is the list date, its own
pivot and stop (the final contraction's low) replace the 10-day proxy and the
same breakout trade from analyse.py is run on them.

Also applies the two gates FINAL_LOGIC.md adopted: 3+ contractions (3T and
up) and a stop at least 0.5 ATR14 below the entry.

Run from this directory, after analyse.py:
    .venv/bin/python scripts/detector_check.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

EXP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EXP.parent / "pattern-detection"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyse import describe, load_bars, trade_from_levels  # noqa: E402
from patlib.bars import load_csv  # noqa: E402
from patlib.vcp import detect_vcp  # noqa: E402


def main() -> None:
    res = pd.read_csv(EXP / "out" / "setups_scored.csv", parse_dates=["list_date"])
    c = res[res.counted].copy()
    rows = []
    for r in c.itertuples(index=False):
        path = EXP / "data" / "bars" / f"{r.exchange}_{r.dhan_symbol}.csv"
        pb = load_csv(path, r.dhan_symbol)
        b = load_bars(r.exchange, r.dhan_symbol)
        t = b.index.get_loc(r.list_date)
        dets = [d for d in detect_vcp(pb, t) if d.end_idx == t]
        row = {"list_date": r.list_date, "symbol": r.symbol, "rank": r.rank,
               "det_found": bool(dets)}
        if dets:
            d = max(dets, key=lambda d: d.score)
            row.update(det_state=d.state, det_variant=d.variant, det_score=d.score,
                       det_t_count=d.metrics.get("t_count"),
                       det_pivot=d.pivot_price, det_stop=d.stop_price)
            row.update({f"D_{k}": v for k, v in
                        trade_from_levels(b, t, d.pivot_price, d.stop_price).items()})
        rows.append(row)
    out = pd.DataFrame(rows)
    out.to_csv(EXP / "out" / "detector_check.csv", index=False)

    found = out[out.det_found]
    tr = found[found.get("D_status") == "traded"] if "D_status" in found else found.iloc[0:0]
    gated = tr[(tr.det_t_count >= 3) & (tr.D_atr_stop_gate_ok == True)]  # noqa: E712
    summary = {
        "counted_setups": int(len(out)),
        "detector_sees_a_vcp": int(len(found)),
        "by_state": found.det_state.value_counts().to_dict() if len(found) else {},
        "by_variant": found.det_variant.value_counts().to_dict() if len(found) else {},
        "status_counts": found.D_status.value_counts().to_dict() if len(found) else {},
        "risk_pct_median": float((found.D_pivot / found.D_stop - 1).median()) if len(found) else None,
        "trades_net_pct": describe(tr.D_net_pct, tr.list_date) if len(tr) else None,
        "trades_net_r": describe(tr.D_net_r, tr.list_date) if len(tr) else None,
        "exit_reasons": tr.D_exit_reason.value_counts().to_dict() if len(tr) else {},
        "gated_3T_and_atr": describe(gated.D_net_pct, gated.list_date) if len(gated) else None,
    }
    (EXP / "out" / "detector_summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps(summary, indent=1, default=str))


if __name__ == "__main__":
    main()
