"""Does an index regime filter help?

The last open item in FINAL_LOGIC.md. The premise is easy to state: the
strategy lost money in 2018, 2019, 2022, 2025 and 2026, and those are the
stretches when the market itself was going nowhere. Standing aside when the
index is below its own 200-day average should remove them.

The premise is also exactly the kind of thing that is true in-sample by
construction, so the tests here are built to make that visible:

  THE INVERSE IS RUN TOO. "Trade only when the index is BELOW its 200-day"
  should be markedly worse if the filter is real. If both halves beat the
  unfiltered baseline, the baseline was unlucky and neither half means
  anything.

  TIME IN MARKET IS REPORTED. A filter that is on 95% of the time cannot be
  responsible for a large change, and one that is on 50% of the time has
  halved the exposure -- so comparing CAGR alone would flatter it.

  YEAR BY YEAR IS REPORTED, because the whole claim is about which years it
  removes. A filter that lifts CAGR while leaving the bad years intact has
  done something else.

Regime is computed on the NIFTY index from the application's own daily_bars,
causally: the average at date d uses closes up to d.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
from patlib.bars import load_sqlite
from patlib.indicators import sma
from vcp_backtest import simulate, summarise
from vcp_final import DB, build_trades

OUT = Path(__file__).resolve().parent.parent / "out"


def regimes(index_symbol="NIFTY", segment="IDX_I"):
    b = load_sqlite(DB, index_symbol, segment)
    c = b.close
    s200, s50 = sma(c, 200), sma(c, 50)
    d = [str(x) for x in b.date]
    rising = np.zeros(len(c), bool)
    for i in range(len(c)):
        j = max(0, i - 21)
        rising[i] = s200[i] > s200[j]
    out = {
        "above 200-day": {d[i]: bool(c[i] > s200[i]) for i in range(len(c))},
        "200-day rising": {d[i]: bool(rising[i]) for i in range(len(c))},
        "above AND rising": {d[i]: bool(c[i] > s200[i] and rising[i]) for i in range(len(c))},
        "50-day > 200-day": {d[i]: bool(s50[i] > s200[i]) for i in range(len(c))},
        "BELOW 200-day (inverse)": {d[i]: bool(c[i] <= s200[i]) for i in range(len(c))},
    }
    return out, d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=1_000_000)
    ap.add_argument("--per-trade", type=float, default=100_000)
    ap.add_argument("--cost", type=float, default=0.003)
    a = ap.parse_args()

    rows = json.loads((OUT / "vcp_entry_bases.json").read_text())
    trades = build_trades(rows)["early + EMA50"]
    regs, idx_dates = regimes()

    def run(reg, flatten=False):
        ser, led, sk = simulate(trades, a.capital, a.per_trade, a.cost,
                                regime_on=reg, flatten=flatten)
        if not led:
            return None
        s, months, yearly = summarise(ser, led, a.capital, sk, len(trades))
        return s, yearly

    L = ["Index regime filter on the NIFTY, applied to `early entry + EMA50 trail`",
         f"Rs {a.capital:,.0f} capital, Rs {a.per_trade:,.0f} per trade, "
         f"{a.cost:.1%} round-trip cost", ""]
    base, base_yr = run(None)
    L.append(f"{'filter':<30}{'in mkt':>8}{'trades':>8}{'CAGR':>8}{'maxDD':>8}"
             f"{'MAR':>7}{'final':>10}{'win%':>7}")
    L.append(f"{'none (baseline)':<30}{'100%':>8}{base['trades']:>8}"
             f"{base['cagr']:>8.1f}{base['maxdd']:>8.1f}{base['mar']:>7.2f}"
             f"{base['final']/1e5:>9.1f}L{base['win']:>7.1f}")
    rows_out = {"none": base_yr}
    for name, reg in regs.items():
        on = np.mean([reg.get(d, True) for d in idx_dates])
        for flat, tag in ((False, ""), (True, " + flatten")):
            r = run(reg, flat)
            if r is None:
                continue
            s, yr = r
            L.append(f"{name + tag:<30}{on:>8.0%}{s['trades']:>8}{s['cagr']:>8.1f}"
                     f"{s['maxdd']:>8.1f}{s['mar']:>7.2f}{s['final']/1e5:>9.1f}L"
                     f"{s['win']:>7.1f}")
            rows_out[name + tag] = yr
    L.append("")
    L.append("in mkt = share of sessions the filter allows new entries")

    # year by year for the headline candidates
    L.append("")
    L.append("year by year (% return on the book)")
    keys = ["none", "above 200-day", "above 200-day + flatten",
            "above AND rising", "BELOW 200-day (inverse)"]
    keys = [k for k in keys if k in rows_out]
    years = sorted({y["y"] for k in keys for y in rows_out[k]})
    L.append(f"{'year':<8}" + "".join(f"{k[:17]:>19}" for k in keys))
    for y in years:
        cells = ""
        for k in keys:
            m = [r for r in rows_out[k] if r["y"] == y]
            cells += f"{(m[0]['ret'] if m else 0.0):>19.1f}"
        L.append(f"{y:<8}{cells}")
    L.append("")
    for k in keys:
        neg = sum(1 for r in rows_out[k] if r["ret"] < 0)
        L.append(f"   {k:<30}{neg} negative years of {len(rows_out[k])}")

    txt = "\n".join(L)
    print(txt)
    (OUT / "vcp_regime_report.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
