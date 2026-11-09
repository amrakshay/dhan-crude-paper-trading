"""Step 0b + REGIME.md: is it a bear market, and when were the three rules on?

Descriptive only. Computes no strategy return: the intraday 09:30 -> 15:10
NIFTY short (the key control) is NOT computed here, so the regime rules are
fixed before anything about the strategies is known.

    .venv/bin/python scripts/breadth_regime.py      -> out/regime.json, out/regime_daily.csv
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bearlib import OUT  # noqa: E402
from bearlib import daily as D  # noqa: E402
from bearlib import regime as R  # noqa: E402

START = pd.Timestamp("2017-04-03")      # first session of the 5-min panel
DEV_END = pd.Timestamp("2022-12-31")
HOLDOUT_START = pd.Timestamp("2023-01-01")


def r3(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), 4)


def current_state(nifty, fb, nb, nxb, midsel) -> dict:
    c = nifty["close"]
    st = R.index_state(c)
    last = c.index[-1]
    hi_date = c.loc["2025-06-01":].idxmax()
    monthly = c.resample("ME").last().pct_change()
    m26 = {d.strftime("%b"): round(100 * v, 1) for d, v in monthly.loc["2026"].items()}
    def b_at(b):
        d = b.dropna(subset=["below50"]).index[-1]
        row = b.loc[d]
        return {"as_of": d.date().isoformat(), "below_sma50": r3(row.below50),
                "below_sma200": r3(row.below200), "median_dd_from_52w_high": r3(row.median_dd),
                "names": int(row.n50)}
    ms = midsel.dropna()
    return {
        "nifty": {
            "as_of": last.date().isoformat(), "close": round(float(c.iloc[-1]), 2),
            "high": round(float(c.loc[hi_date]), 2), "high_date": hi_date.date().isoformat(),
            "drawdown": r3(c.iloc[-1] / c.loc[hi_date] - 1.0),
            "sma50": round(float(st["sma50"].iloc[-1]), 1),
            "sma200": round(float(st["sma200"].iloc[-1]), 1),
            "sma200_falling": bool(st["sma200_falling"].iloc[-1]),
            "monthly_2026_pct": m26,
        },
        "fno_210": b_at(fb),
        "nifty500": b_at(nb),
        "nifty500_ex_fno": b_at(nxb),
        "nifty_midcap_select": {
            "as_of": ms.index[-1].date().isoformat(),
            "drawdown_from_252_high": r3(ms.iloc[-1] / ms.iloc[-252:].max() - 1.0),
        },
    }


def lag_table(on_trade: pd.Series, nifty_close: pd.Series, dds: list[dict]) -> list[dict]:
    """For each ex-post NIFTY decline >= 10%: when did the rule (as TRADED, i.e.
    known the evening before) first switch on after the peak, how much of the
    fall had already happened, and after the trough how far had the index
    rallied before the rule switched off."""
    rows = []
    for d in dds:
        pk, tr = d["peak"], d["trough"]
        if tr < START:
            continue
        win = on_trade.loc[pk:]
        first_on = win[win].index.min() if win.any() else None
        rec = d["recovered"] or nifty_close.index[-1]
        after = on_trade.loc[tr:rec]
        # Off = the first session after the trough that the rule is off and stays
        # off for at least 10 sessions (a one-day flicker is not "off").
        off = None
        vals = after.to_numpy()
        for k in range(len(vals)):
            if not vals[k] and not vals[k:k + 10].any():
                off = after.index[k]
                break
        on_after_trough = int(after.sum())
        row = {
            "peak": pk.date().isoformat(), "trough": tr.date().isoformat(),
            "decline": r3(d["decline"]),
            "first_on": first_on.date().isoformat() if first_on is not None else None,
        }
        if first_on is not None and first_on <= tr:
            fell = nifty_close.loc[first_on] / nifty_close.loc[pk] - 1.0
            row["share_of_fall_missed_before_on"] = r3(fell / d["decline"])
        else:
            row["share_of_fall_missed_before_on"] = 1.0 if first_on is None or first_on > tr else None
        row["off_after_trough"] = off.date().isoformat() if off is not None else None
        if off is not None:
            # The close of the last on-session before `off`; 0 if never on after the trough.
            last_on = nifty_close.loc[tr:off].iloc[-2] if off > tr else nifty_close.loc[tr]
            row["rally_from_trough_before_off"] = r3(last_on / nifty_close.loc[tr] - 1.0)
        row["on_sessions_after_trough"] = on_after_trough
        rows.append(row)
    return rows


def big_up_days(on_trade: pd.Series, nifty: pd.DataFrame) -> dict:
    cc = nifty["close"].pct_change()
    oc = nifty["close"] / nifty["open"] - 1.0
    on = on_trade.reindex(cc.index).fillna(False)
    out = {}
    for name, r in (("close_to_close", cc), ("open_to_close", oc)):
        out[name] = {
            "on_days": int(on.sum()),
            "up_2pct_on": int(((r >= 0.02) & on).sum()),
            "up_3pct_on": int(((r >= 0.03) & on).sum()),
            "up_2pct_all": int((r >= 0.02).sum()),
            "up_3pct_all": int((r >= 0.03).sum()),
            "down_2pct_on": int(((r <= -0.02) & on).sum()),
            "down_3pct_on": int(((r <= -0.03) & on).sum()),
        }
    return out


def main():
    nifty = D.nifty_daily()
    fno = D.fno_daily()
    fw = D.wide(fno, "close_adj")
    n5, n_spikes = D.nifty500_daily()
    fb = R.breadth(fw)
    nb = R.breadth(n5)
    ex = [s for s in n5.columns if s not in set(fw.columns)]
    nxb = R.breadth(n5[ex])
    midsel = D.index_close("Nifty Midcap Select")

    at_close = R.rules(nifty, fb)
    traded = R.for_trading(at_close).loc[START:fw.index.max()]
    nifty_w = nifty.loc[:fw.index.max()]
    dds = R.drawdowns(nifty["close"], 0.10)

    per_rule = {}
    for rule in traded.columns:
        on = traded[rule]
        per_rule[rule] = {
            "whole_sample": R.flips(on),
            "episodes": R.episodes(on),
            "episodes_dev": R.episodes(on.loc[:DEV_END]),
            "episodes_holdout": R.episodes(on.loc[HOLDOUT_START:]),
            "on_days_dev": int(on.loc[:DEV_END].sum()),
            "on_days_holdout": int(on.loc[HOLDOUT_START:].sum()),
            "lag_vs_nifty_10pct_declines": lag_table(on, nifty_w["close"], dds),
            "big_index_days": big_up_days(on, nifty_w),
            "on_at_last_close": bool(at_close[rule].loc[:fw.index.max()].iloc[-1]),
        }
    # Agreement between rules.
    agree = {}
    cols = list(traded.columns)
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            both = (traded[a] & traded[b]).sum()
            either = (traded[a] | traded[b]).sum()
            agree[f"{a}~{b}"] = r3(both / either) if either else None
    # F&O median-drawdown episodes: when were stocks (not the index) in a bear?
    fno_bear = (fb["median_dd"] <= -0.20).loc[START:]
    n500_bear = (nb["median_dd"] <= -0.20).loc[START:]

    result = {
        "generated_from": {
            "nifty": "daily_bars IDX_I NIFTY (read-only)",
            "fno": "intraday-screener/data/clean/days.parquet, adjusted closes",
            "nifty500": f"daily_bars NSE_EQ, today's 500 constituents; {n_spikes} isolated "
                        "one-day excursions >15% removed",
            "window": [START.date().isoformat(), fw.index.max().date().isoformat()],
        },
        "current": current_state(nifty, fb, nb, nxb, midsel),
        "rules": per_rule,
        "jaccard_overlap": agree,
        "nifty_declines_10pct_ex_post": [
            {"peak": d["peak"].date().isoformat(), "trough": d["trough"].date().isoformat(),
             "recovered": d["recovered"].date().isoformat() if d["recovered"] is not None else None,
             "decline": r3(d["decline"])} for d in dds],
        "stock_bear_episodes_median_dd_le_20pct": {
            "fno_210": R.episodes(fno_bear, bridge=20, min_on=10),
            "nifty500": R.episodes(n500_bear, bridge=20, min_on=10),
        },
    }
    OUT.mkdir(exist_ok=True)
    (OUT / "regime.json").write_text(json.dumps(result, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    daily = pd.concat([
        nifty["close"].rename("nifty"), R.index_state(nifty["close"])[["sma50", "sma200", "dd252"]],
        fb.add_prefix("fno_"), nb.add_prefix("n500_"), nxb.add_prefix("n500x_"),
        at_close.add_prefix("rule_at_close_"), traded.add_prefix("rule_traded_"),
    ], axis=1).loc[START:]
    daily.to_csv(OUT / "regime_daily.csv", float_format="%.5f")
    print(json.dumps(result, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)))


if __name__ == "__main__":
    main()
