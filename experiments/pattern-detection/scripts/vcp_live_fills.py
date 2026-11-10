"""Does the adopted VCP book survive the fills a live run can actually get?

`vcp_backtest.py` buys at the CLOSE of the bar the base is first visible and
exits the 50 EMA trail at the CLOSE that broke it. A live run decides on
finished daily bars that arrive after the close (the server's nightly runs at
08:35 the next morning), so both of those become the NEXT SESSION'S OPEN.

This replays the same signals, with the same gates (3T, stop >= 0.5 ATR14 from
the decision-time close), three ways:

  A  close fills, flat 0.3% round trip    -- must reproduce FINAL_LOGIC.md
  B  next-open fills, flat 0.3%           -- the fill-timing change alone
  C  next-open fills, the app's own NSE delivery rate card, plus slippage

C is what the server would record. Rates are the ones in
backend/conf/charges/nse-equity-delivery.yaml (version 2026-09-18), copied
below because this venv has no YAML parser; `--check-rates` prints one round
trip so it can be compared with the backend engine's figure.

Live rules that differ from the backtest and are modelled here:
  * a signal whose next open is at or below its stop is not bought
    (the base failed overnight; there is nothing left to be long of);
  * the stop is armed from the fill bar onwards;
  * the trail and the 250-session cap are decided on a close and sold at the
    next open.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
from patlib.bars import load_sqlite
from patlib.indicators import atr

from vcp_backtest import simulate as reference_simulate, summarise
from vcp_final import DB, OUT, build_trades, ema

# --- the rate card (nse-equity-delivery.yaml, 2026-09-18) ---------------------
STT = 0.001                 # each side
EXCH = 0.0000306990         # each side
IPFT = 0.000000001          # each side
SEBI = 0.000001             # each side
STAMP = 0.00015             # buy side only
DP = 12.50                  # per sell order
GST = 0.18                  # on exch + ipft + sebi + dp (+ brokerage, which is 0)


def _half_up(turnover: float, rate: float, places: int = 0) -> float:
    """turnover x rate rounded half-up in DECIMAL, as the engine does; a float
    product sits a hair under the .5 and rounds the wrong way."""
    from decimal import Decimal, ROUND_HALF_UP
    x = Decimal(str(round(turnover, 2))) * Decimal(str(rate))
    return float(x.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def leg_charges(side: str, turnover: float) -> float:
    stt = _half_up(turnover, STT)                    # rupee_half_up
    exch = turnover * EXCH
    ipft = turnover * IPFT
    sebi = round(turnover * SEBI, 2)
    stamp = turnover * STAMP if side == "BUY" else 0.0   # the engine does not round it
    dp = DP if side == "SELL" else 0.0
    gst = (exch + ipft + sebi + dp) * GST
    return stt + exch + ipft + sebi + stamp + dp + gst


MAX_HOLD = 250


def gated_rows(rows, min_t=3, min_stop_atr=0.5):
    """The adopted gates, judged at DECISION time on the signal bar's close."""
    def tcount(r):
        # Each base's OWN footprint. Keying a lookup on (symbol, seen_idx) let a
        # second base seen the same day overwrite the first's count -- 19
        # stock-days in the step-3 file (found 2026-11-09 against the server).
        tok = str(r.get("variant", "")).split()
        return int(tok[-1][:-1]) if tok and tok[-1].endswith("T") and tok[-1][:-1].isdigit() else 0
    out = [r for r in rows if tcount(r) >= min_t and r.get("immediate_idx") is not None]
    by = defaultdict(list)
    for r in out:
        by[r["symbol"]].append(r)
    keep = []
    for sym, g in by.items():
        b = load_sqlite(DB, sym)
        a14 = atr(b, 14)
        for r in g:
            i, e, stop = r["immediate_idx"], float(r["immediate_entry"]), float(r["stop"])
            av = float(a14[i])
            if e > stop and av > 0 and (e - stop) / av >= min_stop_atr:
                keep.append(r)
    return keep


def live_trades(rows):
    """Next-open entry; stop armed from the fill bar; trail/cap sold next open."""
    by = defaultdict(list)
    for r in rows:
        by[r["symbol"]].append(r)
    out, gapped = [], 0
    for sym, g in by.items():
        b = load_sqlite(DB, sym)
        ma = ema(b.close, 50)
        n = len(b)
        for r in g:
            i, stop, bo = r["immediate_idx"], float(r["stop"]), r.get("breakout_idx")
            f = i + 1
            if f >= n:
                continue
            entry = float(b.open[f])
            if entry <= stop:
                gapped += 1
                continue
            last = min(n - 1, f + MAX_HOLD)
            ex_i, ex, why = last, float(b.close[last]), "cap"
            for j in range(f, last + 1):
                if float(b.low[j]) <= stop:
                    op = float(b.open[j])
                    ex_i, ex, why = j, (op if op < stop else stop), "stop"
                    break
                if bo is not None and j > bo and float(b.close[j]) < ma[j]:
                    k = min(j + 1, n - 1)
                    ex_i, ex, why = k, float(b.open[k]) if k > j else float(b.close[j]), "trail"
                    break
            else:
                if last + 1 < n:                       # cap decided on a close
                    ex_i, ex = last + 1, float(b.open[last + 1])
            out.append(dict(
                symbol=sym, entry_idx=f, exit_idx=ex_i,
                entry_date=str(b.date[f]), exit_date=str(b.date[ex_i]),
                entry=entry, exit=ex, stop=stop,
                R=(ex - entry) / (entry - stop), ret=ex / entry - 1.0,
                risk_pct=(entry - stop) / entry, bars=ex_i - f, reason=why,
                year=str(b.date[f])[:4]))
    return out, gapped


def simulate_real(trades, capital, per_trade, slip):
    """Fixed notional, cash-limited, date order; fees from the rate card and
    `slip` (fraction) against us on both legs. Mirrors vcp_backtest.simulate."""
    by_entry = defaultdict(list)
    for t in trades:
        by_entry[t["entry_date"]].append(t)
    syms = sorted({t["symbol"] for t in trades})
    px, dates = {}, set()
    for s in syms:
        b = load_sqlite(DB, s)
        px[s] = {str(d): float(c) for d, c in zip(b.date, b.close)}
        dates.update(px[s])
    first = min(t["entry_date"] for t in trades)
    cash, open_pos, ledger, series, peak, skipped = capital, [], [], [], capital, 0
    fees_paid = 0.0
    for d in sorted(x for x in dates if x >= first):
        still = []
        for p in open_pos:
            if p["exit_date"] <= d:
                fill = p["exit"] * (1 - slip)
                proceeds = p["qty"] * fill
                fee = leg_charges("SELL", proceeds)
                fees_paid += fee
                cash += proceeds - fee
                net = proceeds - fee - p["cost_basis"]
                ledger.append(dict(symbol=p["symbol"], entry_date=p["entry_date"],
                                   exit_date=p["exit_date"], days=p["bars"],
                                   reason=p["reason"], net=round(net, 2),
                                   entry=round(p["entry"], 2), exit=round(fill, 2),
                                   qty=p["qty"], invested=int(round(p["invested"])),
                                   ret_pct=round(100.0 * net / p["invested"], 2)))
            else:
                still.append(p)
        open_pos = still
        for t in by_entry.get(d, []):
            fill = t["entry"] * (1 + slip)
            qty = int(per_trade // fill)
            invested = qty * fill
            fee = leg_charges("BUY", invested) if qty > 0 else 0.0
            if qty <= 0 or cash < invested + fee:
                skipped += 1
                continue
            cash -= invested + fee
            fees_paid += fee
            open_pos.append(dict(**t, qty=qty, invested=invested, cost_basis=invested + fee))
        mtm = sum(p["qty"] * px[p["symbol"]].get(d, p["entry"]) for p in open_pos)
        eq = cash + mtm
        peak = max(peak, eq)
        series.append(dict(d=d, e=round(eq), dd=round(-100.0 * (peak - eq) / peak, 2),
                           n=len(open_pos), inv=round(mtm)))
    for p in open_pos:
        fill = p["exit"] * (1 - slip)
        proceeds = p["qty"] * fill
        fee = leg_charges("SELL", proceeds)
        fees_paid += fee
        cash += proceeds - fee
        net = proceeds - fee - p["cost_basis"]
        ledger.append(dict(symbol=p["symbol"], entry_date=p["entry_date"],
                           exit_date=p["exit_date"], days=p["bars"], reason=p["reason"],
                           net=round(net, 2), entry=round(p["entry"], 2),
                           exit=round(fill, 2), qty=p["qty"],
                           invested=int(round(p["invested"])),
                           ret_pct=round(100.0 * net / p["invested"], 2)))
    ledger.sort(key=lambda r: r["entry_date"])
    return series, ledger, skipped, fees_paid


def line(name, summary, extra=""):
    s = summary
    return (f"{name:<44}{s['cagr']:>7.1f}%{s['maxdd']:>8.1f}%{s['win']:>7.1f}%"
            f"{s['trades']:>7}{s['skipped']:>8}  {extra}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=1_000_000)
    ap.add_argument("--per-trade", type=float, default=150_000)
    ap.add_argument("--slip", type=float, default=0.0005,
                    help="adverse slippage per leg as a fraction (0.0005 = 5 bps)")
    ap.add_argument("--check-rates", action="store_true")
    ap.add_argument("--bases", default="vcp_entry_bases.json",
                    help="signal file under out/ (vcp_step1_bases.json = scanned daily)")
    a = ap.parse_args()

    if a.check_rates:
        b, s = leg_charges("BUY", 150_000), leg_charges("SELL", 150_000)
        print(f"round trip on Rs 1,50,000: buy {b:.2f} + sell {s:.2f} = {b + s:.2f} "
              f"({100 * (b + s) / 150_000:.3f}%)")
        return

    rows = json.loads((OUT / a.bases).read_text())
    rows = gated_rows(rows)
    print(f"{len(rows)} signals after 3T + 0.5 ATR gates")

    # A: the reference book, through the reference simulator.
    ref = [t for t in build_trades(rows)["early + EMA50"]]
    sA, lA, kA = reference_simulate(ref, a.capital, a.per_trade, 0.003)
    A = summarise(sA, lA, a.capital, kA, len(ref))[0]

    live, gapped = live_trades(rows)
    sB, lB, kB = reference_simulate(live, a.capital, a.per_trade, 0.003)
    B = summarise(sB, lB, a.capital, kB, len(live))[0]

    out = []
    for slip in (0.0, a.slip, 2 * a.slip):
        sC, lC, kC, fees = simulate_real(live, a.capital, a.per_trade, slip)
        C = summarise(sC, lC, a.capital, kC, len(live))[0]
        out.append((slip, C, fees))

    hdr = f"{'variant':<44}{'CAGR':>8}{'maxDD':>9}{'win':>8}{'trades':>7}{'skipped':>8}"
    L = [f"VCP adopted book: Rs {a.capital:,.0f}, Rs {a.per_trade:,.0f} a position, "
         f"3T + 0.5 ATR, early entry + EMA50 trail", "", hdr,
         line("A close fills, 0.3% flat (reference)", A),
         line("B next-open fills, 0.3% flat", B, f"{gapped} gapped below stop, not bought")]
    for slip, C, fees in out:
        L.append(line(f"C next-open, rate card, {slip * 1e4:.0f} bps slip/leg", C,
                      f"fees Rs {fees:,.0f}"))
    reasons = defaultdict(int)
    for t in live:
        reasons[t["reason"]] += 1
    L += ["", "exit reasons (live fills, all signals): "
          + ", ".join(f"{k} {v}" for k, v in sorted(reasons.items()))]
    txt = "\n".join(L)
    print(txt)
    tag = "" if a.bases == "vcp_entry_bases.json" else "_" + Path(a.bases).stem
    (OUT / f"vcp_live_fills_report{tag}.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
