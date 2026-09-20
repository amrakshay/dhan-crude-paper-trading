"""Cash-accounted backtest of the decided VCP rule set, and its dashboard.

Sizing is the one the brief specified and it is NOT the risk-based sizing the
earlier studies used: a FIXED notional per position, so the number of
positions you can hold is whatever the cash supports. Start with 10 lakh and
1 lakh a trade and you hold ten; compound to 15 lakh and you hold fifteen;
draw down to 8 lakh and you hold eight. Slot count is an output, not a
setting, which is why the dashboard plots it.

That choice materially changes the result versus risk-based sizing. Risking
1% of equity on a 2.5% stop asks for a 39% position; a flat 1 lakh on a
10 lakh book is 10%, so each trade carries about a quarter of the risk and
roughly a quarter of the return. Lower CAGR, much lower drawdown. Both are
defensible; they are simply different books.

Equity is marked to market DAILY on open positions, so the drawdown is the
one you would actually have lived through rather than the one visible only
at exits.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
from patlib.bars import load_sqlite
from vcp_final import DB, build_trades

OUT = Path(__file__).resolve().parent.parent / "out"


def simulate(trades, capital=1_000_000.0, per_trade=100_000.0, cost=0.003,
             compound=False, slots=10, regime_on=None, flatten=False):
    """`regime_on`: {date -> bool}. Entries are refused on days it is False.
    `flatten`: also close every open position at that day's close."""
    by_entry = defaultdict(list)
    for t in trades:
        by_entry[t["entry_date"]].append(t)

    syms = sorted({t["symbol"] for t in trades})
    px, dates_of = {}, {}
    for s in syms:
        b = load_sqlite(DB, s)
        px[s] = {str(d): float(c) for d, c in zip(b.date, b.close)}
        dates_of[s] = [str(d) for d in b.date]
    all_dates = sorted({d for s in syms for d in dates_of[s]})
    first = min(t["entry_date"] for t in trades)
    all_dates = [d for d in all_dates if d >= first]

    def mtm_open(pos, prices, day):
        return sum(q["qty"] * prices[q["symbol"]].get(day, q["entry"]) for q in pos)

    cash = capital
    open_pos, ledger, series = [], [], []
    peak = capital
    skipped = 0

    for d in all_dates:
        # --- exits first: cash comes back before anything is bought
        still = []
        for p in open_pos:
            if p["exit_date"] <= d:
                proceeds = p["qty"] * p["exit"]
                fee = (p["invested"] + proceeds) * (cost / 2.0)
                net = proceeds - p["invested"] - fee
                cash += proceeds - fee
                ledger.append(dict(
                    symbol=p["symbol"], entry_date=p["entry_date"],
                    exit_date=p["exit_date"], days=p["bars"], reason=p["reason"],
                    net=round(net, 2), entry=round(p["entry"], 2),
                    exit=round(p["exit"], 2), qty=p["qty"],
                    invested=int(round(p["invested"])),
                    ret_pct=round(100.0 * net / p["invested"], 2)))
            else:
                still.append(p)
        open_pos = still

        # --- regime: flatten first, so freed cash is not then redeployed
        if regime_on is not None and flatten and not regime_on.get(d, True):
            keep = []
            for p in open_pos:
                mark = px[p["symbol"]].get(d)
                if mark is None:
                    keep.append(p); continue
                proceeds = p["qty"] * mark
                fee = (p["invested"] + proceeds) * (cost / 2.0)
                cash += proceeds - fee
                ledger.append(dict(
                    symbol=p["symbol"], entry_date=p["entry_date"], exit_date=d,
                    days=p["bars"], reason="regime",
                    net=round(proceeds - p["invested"] - fee, 2),
                    entry=round(p["entry"], 2), exit=round(mark, 2), qty=p["qty"],
                    invested=int(round(p["invested"])),
                    ret_pct=round(100.0 * (proceeds - p["invested"] - fee) / p["invested"], 2)))
            open_pos = keep

        # --- entries, in date order (VCP_RANKING.md: nothing beats it)
        blocked = regime_on is not None and not regime_on.get(d, True)
        for t in by_entry.get(d, []):
            if blocked:
                skipped += 1
                continue
            # Fixed notional keeps the bet the same size for ever, so as the
            # book compounds the position shrinks from 10% of it to under 2%
            # and the strategy quietly de-levers. `compound` instead keeps the
            # bet at equity/slots, which holds the slot count near 10.
            size = ((cash + mtm_open(open_pos, px, d)) / slots) if compound else per_trade
            if cash < size:
                skipped += 1
                continue
            qty = int(size // t["entry"])
            if qty <= 0:
                skipped += 1
                continue
            invested = qty * t["entry"]
            cash -= invested
            open_pos.append(dict(**t, qty=qty, invested=invested))

        mtm = sum(p["qty"] * px[p["symbol"]].get(d, p["entry"]) for p in open_pos)
        eq = cash + mtm
        peak = max(peak, eq)
        series.append(dict(d=d, e=round(eq), dd=round(-100.0 * (peak - eq) / peak, 2),
                           n=len(open_pos), inv=round(mtm)))

    for p in open_pos:                       # liquidate at the end
        proceeds = p["qty"] * p["exit"]
        fee = (p["invested"] + proceeds) * (cost / 2.0)
        cash += proceeds - fee
        ledger.append(dict(
            symbol=p["symbol"], entry_date=p["entry_date"], exit_date=p["exit_date"],
            days=p["bars"], reason=p["reason"],
            net=round(proceeds - p["invested"] - fee, 2),
            entry=round(p["entry"], 2), exit=round(p["exit"], 2), qty=p["qty"],
            invested=int(round(p["invested"])),
            ret_pct=round(100.0 * (proceeds - p["invested"] - fee) / p["invested"], 2)))
    ledger.sort(key=lambda r: r["entry_date"])
    return series, ledger, skipped


def summarise(series, ledger, capital, skipped, n_signals):
    eq = np.array([s["e"] for s in series], float)
    ds = [s["d"] for s in series]
    final = float(eq[-1])
    yrs = (np.datetime64(ds[-1]) - np.datetime64(ds[0])).astype(int) / 365.25
    cagr = (final / capital) ** (1 / yrs) - 1
    maxdd = min(s["dd"] for s in series)
    nets = np.array([r["net"] for r in ledger], float)
    wins, losses = nets[nets > 0], nets[nets <= 0]
    gp, gl = wins.sum(), -losses.sum()

    # monthly and yearly, from the marked equity curve
    mrets, last_by_month = {}, {}
    for s in series:
        last_by_month[s["d"][:7]] = s["e"]
    keys = sorted(last_by_month)
    prev = capital
    for k in keys:
        mrets[k] = round(100.0 * (last_by_month[k] / prev - 1.0), 2)
        prev = last_by_month[k]
    months = {}
    for k, v in mrets.items():
        y, m = k.split("-")
        months.setdefault(y, [None] * 12)[int(m) - 1] = v

    last_by_year, yearly = {}, []
    for s in series:
        last_by_year[s["d"][:4]] = s["e"]
    prev = capital
    for y in sorted(last_by_year):
        tr = [r for r in ledger if r["entry_date"][:4] == y]
        yearly.append(dict(y=int(y), ret=round(100.0 * (last_by_year[y] / prev - 1.0), 1),
                           n=len(tr),
                           win=round(100.0 * np.mean([r["net"] > 0 for r in tr]), 1) if tr else 0.0,
                           pnl=int(round(sum(r["net"] for r in tr)))))
        prev = last_by_year[y]

    mv = [v for v in mrets.values()]
    npos = np.array([s["n"] for s in series], float)
    return dict(
        start=int(capital), final=int(round(final)),
        cagr=round(100 * cagr, 1), maxdd=round(maxdd, 1),
        mar=round(100 * cagr / abs(maxdd), 2) if maxdd else 0.0,
        trades=len(ledger), win=round(100.0 * float((nets > 0).mean()), 1),
        avg_hold=round(float(np.mean([r["days"] for r in ledger])), 1),
        pf=round(float(gp / gl), 2) if gl > 0 else 0.0,
        avg_win=int(round(wins.mean())) if len(wins) else 0,
        avg_loss=int(round(losses.mean())) if len(losses) else 0,
        best_mo=round(max(mv), 1), worst_mo=round(min(mv), 1),
        period=f"{ledger[0]['entry_date']} → {ledger[-1]['exit_date']}",
        total_pnl=int(round(final - capital)),
        signals=n_signals, skipped=skipped,
        max_pos=int(npos.max()), avg_pos=round(float(npos.mean()), 1),
        exposure=round(100.0 * float(np.mean([s["inv"] / max(s["e"], 1)
                                              for s in series])), 1),
        per_trade=int(np.median([r["invested"] for r in ledger])),
    ), months, yearly


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=1_000_000)
    ap.add_argument("--per-trade", type=float, default=100_000)
    ap.add_argument("--cost", type=float, default=0.003)
    ap.add_argument("--rule", default="early + EMA50")
    ap.add_argument("--compound", action="store_true",
                    help="size each bet at equity/slots instead of a fixed amount")
    ap.add_argument("--slots", type=int, default=10)
    ap.add_argument("--out", default="vcp_dashboard.html")
    ap.add_argument("--min-contractions", type=int, default=3,
                    help="skip bases with fewer than this many contractions "
                         "(the T count in the footprint); 0 disables")
    ap.add_argument("--min-stop-atr", type=float, default=0.5,
                    help="skip signals whose stop sits closer than this many "
                         "ATR14 from entry; 0 disables")
    a = ap.parse_args()

    rows = json.loads((OUT / "vcp_entry_bases.json").read_text())
    trades = build_trades(rows)[a.rule]
    if a.min_contractions > 1:
        # The T count is the last token of Minervini's own footprint
        # ("10W 12/5 3T"), which the detector already stores on the base.
        tc = {}
        for r in rows:
            tok = str(r.get("variant", "")).split()
            if tok and tok[-1].endswith("T") and tok[-1][:-1].isdigit():
                tc[(r["symbol"], r["seen_idx"])] = int(tok[-1][:-1])
        before = len(trades)
        trades = [t for t in trades
                  if tc.get((t["symbol"], t["entry_idx"]), 0) >= a.min_contractions]
        print(f"contraction filter (>= {a.min_contractions}T): "
              f"{len(trades)} of {before} signals kept")
    if a.min_stop_atr > 0:
        # A stop inside a fraction of the stock's own daily range is not a
        # stop, it is a coin flip: signals under 0.4 ATR won 8% of the time
        # and produced a tenth of the profit while occupying a third of the
        # slots. Dropping them is the one filter that improved return, drawdown
        # and win rate at once -- see VCP_LOSSES.md.
        from patlib.indicators import atr as _atr
        from collections import defaultdict as _dd
        grp = _dd(list)
        for t in trades:
            grp[t["symbol"]].append(t)
        keep = []
        for sym, g in grp.items():
            b = load_sqlite(DB, sym)
            a14 = _atr(b, 14)
            for t in g:
                av = float(a14[t["entry_idx"]])
                if av > 0 and (t["entry"] - t["stop"]) / av >= a.min_stop_atr:
                    keep.append(t)
        print(f"stop-distance filter (>= {a.min_stop_atr} ATR): "
              f"{len(keep)} of {len(trades)} signals kept")
        trades = keep
    print(f"{len(trades)} signals; simulating "
          f"Rs {a.capital:,.0f} capital, Rs {a.per_trade:,.0f} per trade, "
          f"{a.cost:.1%} round-trip cost")
    series, ledger, skipped = simulate(trades, a.capital, a.per_trade, a.cost,
                                       a.compound, a.slots)
    summary, months, yearly = summarise(series, ledger, a.capital, skipped, len(trades))

    thin = [s for i, s in enumerate(series) if i % 5 == 0 or i == len(series) - 1]
    data = dict(summary=summary, series=thin, months=months, yearly=yearly,
                trades=ledger)
    html = TEMPLATE.replace("__DATA__", json.dumps(data, separators=(",", ":")))
    bits = [f"Fixed &#8377;{a.per_trade:,.0f} a position from a "
            f"&#8377;{a.capital:,.0f} book, so the number of open trades "
            f"floats with the cash."]
    if a.min_contractions > 1:
        bits.append(f"Only bases with {a.min_contractions} or more contractions.")
    if a.min_stop_atr > 0:
        bits.append(f"Signals whose stop sits closer than {a.min_stop_atr:g}&nbsp;ATR "
                    "are skipped.")
    # Built from the flags rather than written out, because the two hand-edited
    # versions of this sentence both went stale without anything failing.
    html = html.replace("__SUBTITLE__", " ".join(bits))
    html = html.replace("__GENERATED__", date.today().isoformat())
    out = OUT / a.out
    out.write_text(html)
    for k in ("start", "final", "cagr", "maxdd", "mar", "trades", "win", "pf",
              "avg_hold", "max_pos", "avg_pos", "skipped"):
        print(f"   {k:<10}{summary[k]}")
    print(f"\nwrote {out}  ({out.stat().st_size / 1024:.0f} KB)")


TEMPLATE = r"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NSE VCP — Backtest Ledger</title>
<style>
:root{
  --bg:#F6F8F6; --surface:#FFFFFF; --ink:#182220; --muted:#5A6660; --border:#E3E8E4;
  --accent:#0E7A5F; --gain:#1B8A5A; --loss:#C4483E; --chip:#EDF2EE; --grid:#EAEFEA;
  --gain-soft:rgba(27,138,90,.12); --loss-soft:rgba(196,72,62,.12);
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --bg:#0F1412; --surface:#161C19; --ink:#E7ECE9; --muted:#93A099; --border:#263029;
  --accent:#34B98B; --gain:#279E66; --loss:#E85C50; --chip:#1E2622; --grid:#1F2823;
  --gain-soft:rgba(39,158,102,.18); --loss-soft:rgba(232,92,80,.18);
}}
:root[data-theme="dark"]{
  --bg:#0F1412; --surface:#161C19; --ink:#E7ECE9; --muted:#93A099; --border:#263029;
  --accent:#34B98B; --gain:#279E66; --loss:#E85C50; --chip:#1E2622; --grid:#1F2823;
  --gain-soft:rgba(39,158,102,.18); --loss-soft:rgba(232,92,80,.18);
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.5 "Avenir Next","Segoe UI",-apple-system,BlinkMacSystemFont,sans-serif;}
.wrap{max-width:1180px;margin:0 auto;padding:28px 16px 80px}
.mono{font-family:ui-monospace,"SF Mono","Cascadia Code",Menlo,Consolas,monospace;
  font-variant-numeric:tabular-nums}
header h1{font-size:26px;font-weight:650;margin:0 0 2px;letter-spacing:-.01em;text-wrap:balance}
header .sub{color:var(--muted);font-size:14px;margin:0}
.eyebrow{text-transform:uppercase;letter-spacing:.09em;font-size:11px;font-weight:600;color:var(--accent);margin:0 0 6px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:22px 0 26px}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px 14px}
.kpi .l{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);font-weight:600}
.kpi .v{font-size:21px;font-weight:650;margin-top:2px}
.kpi .s{font-size:12px;color:var(--muted)}
.pos{color:var(--gain)} .neg{color:var(--loss)}
section{margin:26px 0}
h2{font-size:16px;font-weight:650;margin:0 0 4px}
.note{color:var(--muted);font-size:13px;margin:0 0 12px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px}
.charts{display:grid;grid-template-columns:1fr;gap:14px}
@media(min-width:900px){.charts{grid-template-columns:3fr 2fr}}
svg{display:block;width:100%;height:auto}
svg text{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:11px;fill:var(--muted)}
.tooltip{position:fixed;pointer-events:none;background:var(--surface);border:1px solid var(--border);
  border-radius:6px;padding:8px 10px;font-size:12.5px;box-shadow:0 4px 14px rgba(0,0,0,.14);
  z-index:10;display:none;min-width:150px}
.tooltip .t-date{color:var(--muted);font-size:11px}
.hm{overflow-x:auto}
.hm table{border-collapse:separate;border-spacing:2px;width:100%}
.hm th{font-size:11px;color:var(--muted);font-weight:600;padding:2px 4px;text-align:center}
.hm th.yr{text-align:right;padding-right:8px}
.hm td{border-radius:3px;text-align:center;padding:5px 2px;font-size:11.5px;min-width:44px}
.filters{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 12px}
.filters select,.filters input{background:var(--surface);color:var(--ink);border:1px solid var(--border);
  border-radius:6px;padding:7px 9px;font-size:13.5px;font-family:inherit}
.filters input{width:150px}
.filters select:focus-visible,.filters input:focus-visible,th.sortable:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.fsummary{font-size:13px;color:var(--muted);margin-left:auto}
.tblwrap{overflow-x:auto;border:1px solid var(--border);border-radius:10px;background:var(--surface);max-height:640px}
table.trades{border-collapse:collapse;width:100%;min-width:920px;font-size:13.5px}
.trades th{position:sticky;top:0;background:var(--surface);text-align:right;padding:10px 10px;
  font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
  border-bottom:1px solid var(--border);white-space:nowrap;z-index:2}
.trades th.txt{text-align:left}
.trades th.sortable{cursor:pointer;user-select:none}
.trades th.sortable:hover{color:var(--ink)}
.trades td{padding:8px 10px;border-bottom:1px solid var(--grid);text-align:right;white-space:nowrap}
.trades td.txt{text-align:left}
.trades tr:hover td{background:var(--chip)}
.sym{font-weight:600}
.chip{display:inline-block;background:var(--chip);border-radius:999px;padding:1px 9px;font-size:12px;color:var(--muted)}
.rsn-stop{color:var(--loss)} .rsn-trail{color:var(--accent)} .rsn-cap{color:var(--muted)}
.retbar{display:inline-block;height:8px;border-radius:2px;vertical-align:middle;margin-left:6px}
.caveat{background:var(--surface);border:1px solid var(--border);border-left:3px solid var(--loss);
  border-radius:8px;padding:14px 16px;font-size:13.5px}
.caveat ul{margin:8px 0 0;padding-left:20px} .caveat li{margin:4px 0;color:var(--muted)}
footer{color:var(--muted);font-size:12.5px;margin-top:34px;border-top:1px solid var(--border);padding-top:14px}
footer p{margin:4px 0}
.themebtn{position:absolute;top:24px;right:20px;background:var(--surface);color:var(--muted);
  border:1px solid var(--border);border-radius:6px;padding:6px 11px;font:inherit;font-size:12.5px;cursor:pointer}
</style>
<div class="wrap">
<button class="themebtn" id="themebtn" type="button">theme</button>
<header>
  <p class="eyebrow">Experiment · pattern-detection</p>
  <h1>NSE VCP — backtest ledger</h1>
  <p class="sub">Volatility Contraction Pattern, entered at the forming bar, trailed on the 50 EMA after breakout.
     __SUBTITLE__</p>
</header>

<div class="kpis" id="kpis"></div>

<section>
  <h2>Equity and drawdown</h2>
  <p class="note">Open positions are marked to market daily, so the drawdown is the one you would have lived through — not only what shows up at exits.</p>
  <div class="charts">
    <div class="card"><div id="eqChart"></div><div id="ddChart"></div></div>
    <div class="card"><div id="yrChart"></div></div>
  </div>
</section>

<section>
  <h2>Positions held</h2>
  <p class="note">Ten at the start by construction. It rises as the book compounds and falls in drawdowns — the count is an output of the cash rule, not a setting.</p>
  <div class="card"><div id="posChart"></div></div>
</section>

<section>
  <h2>Monthly returns</h2>
  <p class="note">Percent change in marked equity, month on month.</p>
  <div class="card hm"><div id="heatmap"></div></div>
</section>

<section>
  <h2>Every trade</h2>
  <div class="filters">
    <select id="fYear"></select>
    <select id="fReason">
      <option value="">all exits</option>
      <option value="stop">stopped out</option>
      <option value="trail">50 EMA trail</option>
      <option value="cap">holding cap</option>
    </select>
    <select id="fSide">
      <option value="">winners and losers</option>
      <option value="w">winners</option>
      <option value="l">losers</option>
    </select>
    <input id="fSym" type="search" placeholder="symbol" autocomplete="off">
    <span class="fsummary" id="fsum"></span>
  </div>
  <div class="tblwrap">
    <table class="trades" id="tradesTbl">
      <thead><tr>
        <th class="txt sortable" data-k="symbol" tabindex="0">Symbol</th>
        <th class="txt sortable" data-k="entry_date" tabindex="0">Entry</th>
        <th class="txt sortable" data-k="exit_date" tabindex="0">Exit</th>
        <th class="sortable" data-k="days" tabindex="0">Days</th>
        <th class="txt">Reason</th>
        <th class="sortable" data-k="entry" tabindex="0">In</th>
        <th class="sortable" data-k="exit" tabindex="0">Out</th>
        <th class="sortable" data-k="qty" tabindex="0">Qty</th>
        <th class="sortable" data-k="invested" tabindex="0">Invested</th>
        <th class="sortable" data-k="net" tabindex="0">Net &#8377;</th>
        <th class="sortable" data-k="ret_pct" tabindex="0">Return</th>
      </tr></thead>
      <tbody id="tbody"></tbody>
    </table>
  </div>
</section>

<section>
  <h2>Before you believe any of this</h2>
  <div class="caveat">
    The strategy underperforms simply buying and holding the same universe (22.9% CAGR,
    but at a 46.7% drawdown) and its case is risk-adjusted, not absolute.
    <ul>
      <li><b>Four years carry everything.</b> 2017, 2020, 2021 and 2023 produce essentially
          the whole return; the other seven are mildly negative, including the two most
          recent. A 19.8% CAGR is the average of a very lumpy series, not an income.</li>
      <li><b>Selective, so a lot of the book is idle.</b> The CAGR is computed on the
          full &#8377;10L including cash that was never deployed, which is honest but
          understates what the invested money did. The deployed-capital return is much
          higher &#8212; and so is the return of simply holding an index fund with the rest.</li>
      <li><b>One trade in four wins.</b> Profit factor holds up only because the average
          winner is several times the average loser. Taking the signals selectively
          is how you end up with the losers and none of the winners.</li>
      <li><b>Survivorship bias.</b> The universe is today's Nifty 500 members held back to 2016, which flatters both the strategy and the benchmark.</li>
      <li><b>In-sample.</b> Detector thresholds came from a synthetic benchmark, but the entry policy and the 50 EMA exit were both chosen by looking at this data.</li>
      <li><b>A bull market.</b> A slow trailing stop flatters itself in one, and 2016&#8211;2026 on the NSE was one.</li>
      <li><b>No liquidity or impact model.</b> Fills assume the close, and gapped stops assume the open.</li>
      <li><b>No regime filter</b> and no ranking of same-day candidates &#8212; ranking was tested and made no reliable difference.</li>
    </ul>
  </div>
</section>

<footer>
  <p>Generated __GENERATED__ by <span class="mono">scripts/vcp_backtest.py</span> from the paper-trading app's own <span class="mono">daily_bars</span>. Read-only; nothing here places an order.</p>
  <p>Costs 0.3% round trip. Exits fill at the close that triggered them; a stop gapped through fills at the open.</p>
</footer>
</div>
<div class="tooltip" id="tip"></div>
<script>
const DATA = __DATA__;
const S = DATA.summary, tip = document.getElementById('tip');
const inr = n => '₹' + Math.round(n).toLocaleString('en-IN');
const lakh = n => '₹' + (n/100000).toFixed(2) + 'L';
const sgn = n => (n>=0?'+':'') + n.toFixed(1) + '%';

/* ---------- KPIs ---------- */
const kpis = [
  ['Final equity', lakh(S.final), inr(S.total_pnl)+' net', S.total_pnl>=0?'pos':'neg'],
  ['CAGR', S.cagr.toFixed(1)+'%', 'from '+lakh(S.start), S.cagr>=0?'pos':'neg'],
  ['Max drawdown', S.maxdd.toFixed(1)+'%', 'marked daily', 'neg'],
  ['MAR', S.mar.toFixed(2), 'CAGR / max DD', ''],
  ['Trades', S.trades.toLocaleString('en-IN'), S.skipped.toLocaleString('en-IN')+' skipped, no cash', ''],
  ['Win rate', S.win.toFixed(1)+'%', 'profit factor '+S.pf.toFixed(2), S.win>=50?'pos':''],
  ['Avg win / loss', inr(S.avg_win)+' / '+inr(S.avg_loss), 'per trade', ''],
  ['Avg hold', S.avg_hold.toFixed(0)+' days', 'best mo '+sgn(S.best_mo)+', worst '+sgn(S.worst_mo), ''],
  ['Positions', S.avg_pos.toFixed(1)+' avg', 'peak '+S.max_pos, ''],
  ['Capital deployed', S.exposure.toFixed(0)+'%', 'average; the rest sat in cash', ''],
];
document.getElementById('kpis').innerHTML = kpis.map(([l,v,s,c]) =>
  `<div class="kpi"><div class="l">${l}</div><div class="v mono ${c}">${v}</div><div class="s">${s}</div></div>`).join('');

/* ---------- shared chart helpers ---------- */
const W=720, PAD={l:52,r:12,t:14,b:22};
function svg(h, inner, vb){ return `<svg viewBox="0 0 ${W} ${h}" role="img" preserveAspectRatio="xMidYMid meet">${inner}</svg>`; }
function xOf(i,n,w){ return PAD.l + (w-PAD.l-PAD.r) * (n<2?0:i/(n-1)); }

/* ---------- equity ---------- */
(function(){
  const s=DATA.series, H=230, n=s.length;
  const lo=Math.min(...s.map(p=>p.e)), hi=Math.max(...s.map(p=>p.e));
  const yOf=v=>PAD.t+(H-PAD.t-PAD.b)*(1-(v-lo)/Math.max(hi-lo,1));
  let g='', ticks=4;
  for(let k=0;k<=ticks;k++){ const v=lo+(hi-lo)*k/ticks, y=yOf(v);
    g+=`<line x1="${PAD.l}" x2="${W-PAD.r}" y1="${y}" y2="${y}" stroke="var(--grid)"/>`
      +`<text x="${PAD.l-8}" y="${y+4}" text-anchor="end">${(v/100000).toFixed(0)}L</text>`; }
  const pts=s.map((p,i)=>`${xOf(i,n,W).toFixed(1)},${yOf(p.e).toFixed(1)}`).join(' ');
  const area=`${PAD.l},${H-PAD.b} ${pts} ${W-PAD.r},${H-PAD.b}`;
  let xt='';
  for(let k=0;k<6;k++){ const i=Math.round((n-1)*k/5);
    xt+=`<text x="${xOf(i,n,W)}" y="${H-6}" text-anchor="middle">${s[i].d.slice(0,7)}</text>`; }
  document.getElementById('eqChart').innerHTML = svg(H,
    g+`<polygon points="${area}" fill="var(--gain-soft)"/>`
     +`<polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="1.8"/>`
     +`<line x1="${PAD.l}" x2="${W-PAD.r}" y1="${yOf(S.start)}" y2="${yOf(S.start)}" stroke="var(--muted)" stroke-dasharray="3 3" opacity=".5"/>`
     +xt);
  const el=document.getElementById('eqChart').firstChild;
  el.addEventListener('mousemove', ev=>{
    const r=el.getBoundingClientRect(), fx=(ev.clientX-r.left)/r.width*W;
    let i=Math.round((fx-PAD.l)/(W-PAD.l-PAD.r)*(n-1)); i=Math.max(0,Math.min(n-1,i));
    const p=s[i];
    tip.innerHTML=`<div class="t-date">${p.d}</div><div class="mono">${lakh(p.e)}</div>`
      +`<div class="mono ${p.dd<-0.5?'neg':''}">dd ${p.dd.toFixed(1)}%</div>`
      +`<div class="mono">${p.n} open</div>`;
    tip.style.display='block'; tip.style.left=(ev.clientX+14)+'px'; tip.style.top=(ev.clientY-10)+'px';
  });
  el.addEventListener('mouseleave', ()=>tip.style.display='none');
})();

/* ---------- drawdown ---------- */
(function(){
  const s=DATA.series, H=92, n=s.length;
  const lo=Math.min(...s.map(p=>p.dd));
  const yOf=v=>PAD.t+(H-PAD.t-PAD.b)*(v/Math.min(lo,-1));
  const pts=s.map((p,i)=>`${xOf(i,n,W).toFixed(1)},${yOf(p.dd).toFixed(1)}`).join(' ');
  document.getElementById('ddChart').innerHTML = svg(H,
    `<text x="${PAD.l-8}" y="${PAD.t+4}" text-anchor="end">0%</text>`
   +`<text x="${PAD.l-8}" y="${H-PAD.b}" text-anchor="end">${lo.toFixed(0)}%</text>`
   +`<polygon points="${PAD.l},${PAD.t} ${pts} ${W-PAD.r},${PAD.t}" fill="var(--loss-soft)"/>`
   +`<polyline points="${pts}" fill="none" stroke="var(--loss)" stroke-width="1.2"/>`);
})();

/* ---------- positions held ---------- */
(function(){
  const s=DATA.series, H=150, n=s.length;
  const hi=Math.max(...s.map(p=>p.n));
  const yOf=v=>PAD.t+(H-PAD.t-PAD.b)*(1-v/Math.max(hi,1));
  let g='';
  for(let k=0;k<=4;k++){ const v=hi*k/4, y=yOf(v);
    g+=`<line x1="${PAD.l}" x2="${W-PAD.r}" y1="${y}" y2="${y}" stroke="var(--grid)"/>`
      +`<text x="${PAD.l-8}" y="${y+4}" text-anchor="end">${v.toFixed(0)}</text>`; }
  const pts=s.map((p,i)=>`${xOf(i,n,W).toFixed(1)},${yOf(p.n).toFixed(1)}`).join(' ');
  let xt='';
  for(let k=0;k<6;k++){ const i=Math.round((n-1)*k/5);
    xt+=`<text x="${xOf(i,n,W)}" y="${H-6}" text-anchor="middle">${s[i].d.slice(0,7)}</text>`; }
  document.getElementById('posChart').innerHTML = svg(H,
    g+`<line x1="${PAD.l}" x2="${W-PAD.r}" y1="${yOf(10)}" y2="${yOf(10)}" stroke="var(--muted)" stroke-dasharray="3 3" opacity=".6"/>`
     +`<polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="1.4"/>`+xt);
})();

/* ---------- yearly ---------- */
(function(){
  const y=DATA.yearly, H=340, n=y.length, w=W;
  const vals=y.map(r=>r.ret), lo=Math.min(0,...vals), hi=Math.max(0,...vals);
  const L=70, top=16, bot=24, bw=(w-L-16)/n*0.62;
  const yOf=v=>top+(H-top-bot)*(1-(v-lo)/Math.max(hi-lo,1));
  let out=`<text x="${L-8}" y="${top+4}" text-anchor="end">${hi.toFixed(0)}%</text>`
         +`<text x="${L-8}" y="${H-bot}" text-anchor="end">${lo.toFixed(0)}%</text>`
         +`<line x1="${L}" x2="${w-16}" y1="${yOf(0)}" y2="${yOf(0)}" stroke="var(--border)"/>`;
  y.forEach((r,i)=>{
    const cx=L+(w-L-16)*(i+0.5)/n, y0=yOf(0), y1=yOf(r.ret);
    out+=`<rect x="${cx-bw/2}" y="${Math.min(y0,y1)}" width="${bw}" height="${Math.abs(y1-y0)||1}" `
       +`rx="2" fill="${r.ret>=0?'var(--gain)':'var(--loss)'}" opacity=".85"><title>${r.y}: ${r.ret}% · ${r.n} trades · ${r.win}% win · ${inr(r.pnl)}</title></rect>`
       +`<text x="${cx}" y="${H-8}" text-anchor="middle">${String(r.y).slice(2)}</text>`
       +`<text x="${cx}" y="${(r.ret>=0?y1-5:y1+13)}" text-anchor="middle" fill="var(--ink)">${r.ret.toFixed(0)}</text>`;
  });
  document.getElementById('yrChart').innerHTML=
    `<div style="font-size:12px;color:var(--muted);margin-bottom:6px">Return by year (%)</div>`+svg(H,out);
})();

/* ---------- monthly heatmap ---------- */
(function(){
  const M=DATA.months, ys=Object.keys(M).sort();
  const all=ys.flatMap(y=>M[y].filter(v=>v!==null));
  const cap=Math.max(...all.map(Math.abs))||1;
  const MN=['J','F','M','A','M','J','J','A','S','O','N','D'];
  let h='<table><thead><tr><th class="yr"></th>'+MN.map(m=>`<th>${m}</th>`).join('')+'<th>Year</th></tr></thead><tbody>';
  ys.forEach(y=>{
    const row=M[y]; const tot=row.filter(v=>v!==null).reduce((a,b)=>a*(1+b/100),1);
    h+=`<tr><th class="yr mono">${y}</th>`;
    row.forEach(v=>{
      if(v===null){ h+=`<td style="background:var(--chip);opacity:.35"></td>`; return; }
      const a=Math.min(Math.abs(v)/cap,1)*0.85+0.08;
      const c=v>=0?`rgba(27,138,90,${a})`:`rgba(196,72,62,${a})`;
      h+=`<td class="mono" style="background:${c}">${v.toFixed(1)}</td>`;
    });
    h+=`<td class="mono" style="font-weight:650">${((tot-1)*100).toFixed(1)}</td></tr>`;
  });
  document.getElementById('heatmap').innerHTML=h+'</tbody></table>';
})();

/* ---------- trades ---------- */
(function(){
  const T=DATA.trades;
  const years=[...new Set(T.map(t=>t.entry_date.slice(0,4)))].sort();
  document.getElementById('fYear').innerHTML='<option value="">all years</option>'+years.map(y=>`<option>${y}</option>`).join('');
  let sortK='entry_date', sortDir=1, view=T;
  const maxAbs=Math.max(...T.map(t=>Math.abs(t.ret_pct)));
  const tb=document.getElementById('tbody');

  function apply(){
    const y=document.getElementById('fYear').value, r=document.getElementById('fReason').value,
          sd=document.getElementById('fSide').value, q=document.getElementById('fSym').value.trim().toUpperCase();
    view=T.filter(t=> (!y||t.entry_date.startsWith(y)) && (!r||t.reason===r)
      && (!sd|| (sd==='w'? t.net>0 : t.net<=0)) && (!q||t.symbol.includes(q)));
    view=[...view].sort((a,b)=>{const x=a[sortK],z=b[sortK];return (x>z?1:x<z?-1:0)*sortDir;});
    const net=view.reduce((s,t)=>s+t.net,0), win=view.filter(t=>t.net>0).length;
    document.getElementById('fsum').textContent =
      `${view.length} trades · ${view.length?(100*win/view.length).toFixed(0):0}% win · net ${inr(net)}`;
    tb.innerHTML=view.slice(0,600).map(t=>{
      const w=Math.max(2,Math.abs(t.ret_pct)/maxAbs*46);
      return `<tr><td class="txt sym">${t.symbol}</td><td class="txt mono">${t.entry_date}</td>`
        +`<td class="txt mono">${t.exit_date}</td><td class="mono">${t.days}</td>`
        +`<td class="txt"><span class="chip rsn-${t.reason}">${t.reason}</span></td>`
        +`<td class="mono">${t.entry.toFixed(2)}</td><td class="mono">${t.exit.toFixed(2)}</td>`
        +`<td class="mono">${t.qty}</td><td class="mono">${inr(t.invested)}</td>`
        +`<td class="mono ${t.net>=0?'pos':'neg'}">${inr(t.net)}</td>`
        +`<td class="mono ${t.ret_pct>=0?'pos':'neg'}">${t.ret_pct.toFixed(1)}%`
        +`<span class="retbar" style="width:${w}px;background:${t.ret_pct>=0?'var(--gain)':'var(--loss)'}"></span></td></tr>`;
    }).join('') + (view.length>600?`<tr><td colspan="11" class="txt" style="color:var(--muted)">showing the first 600 of ${view.length} — narrow the filters to see the rest</td></tr>`:'');
  }
  ['fYear','fReason','fSide','fSym'].forEach(id=>
    document.getElementById(id).addEventListener('input',apply));
  document.querySelectorAll('th.sortable').forEach(th=>{
    const go=()=>{ const k=th.dataset.k; sortDir = (k===sortK)? -sortDir : (k==='symbol'||k.endsWith('date')?1:-1); sortK=k; apply(); };
    th.addEventListener('click',go);
    th.addEventListener('keydown',e=>{ if(e.key==='Enter'||e.key===' '){e.preventDefault();go();} });
  });
  apply();
})();

/* ---------- theme ---------- */
document.getElementById('themebtn').addEventListener('click',()=>{
  const cur=document.documentElement.getAttribute('data-theme');
  const next = cur==='dark' ? 'light' : cur==='light' ? 'dark'
    : (matchMedia('(prefers-color-scheme: dark)').matches?'light':'dark');
  document.documentElement.setAttribute('data-theme',next);
  try{ localStorage.setItem('vcp-theme',next); }catch(e){}
});
try{ const t=localStorage.getItem('vcp-theme'); if(t) document.documentElement.setAttribute('data-theme',t); }catch(e){}
</script>
"""

if __name__ == "__main__":
    main()
