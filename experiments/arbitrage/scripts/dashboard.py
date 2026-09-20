"""Build out/arbitrage_dashboard.html from the saved backtest artefacts.

Same shape as the pattern-detection experiment's dashboard, with the charts
that apply to a market-neutral book and without the ones that do not:

* equity against the RISK-FREE comparator, not against the index -- a book
  with no market exposure is not owed an equity risk premium;
* GROSS and NET exposure on one axis, because the second is the honest
  measure of how market-neutral the book actually was;
* the spread and its z-score for any traded pair, with entries and exits
  marked, which is the only way to see whether a "pair" was ever a pair;
* the selection audit -- tests run against pairs found -- which for this
  experiment is the result.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
from arblib import charges, data

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"


def spread_series(trades, dates, close, want=6):
    """For the most-traded pairs, the z-score path with its trades marked."""
    by_pair = {}
    for t in trades:
        by_pair.setdefault(t["pair"], []).append(t)
    chosen = sorted(by_pair.items(), key=lambda kv: -len(kv[1]))[:want]
    out = []
    dstr = [str(d) for d in dates]
    for pair, ts in chosen:
        t0 = ts[0]
        a, b = t0["a"], t0["b"]
        beta = t0["beta"]
        first = min(t["entry_date"] for t in ts)
        last = max(t["exit_date"] for t in ts)
        i0 = max(0, dstr.index(first) - 60)
        i1 = min(len(dstr) - 1, dstr.index(last) + 20)
        pa, pb = close[a][i0:i1 + 1], close[b][i0:i1 + 1]
        # The strategy's own frozen standardisation, not a fresh one over the
        # displayed window -- otherwise the trade markers do not lie on the line.
        with np.errstate(invalid="ignore", divide="ignore"):
            sp = ((np.log(pb) - beta * np.log(pa)) if t0.get("log_prices")
                  else (pb - beta * pa)) - t0.get("alpha", 0.0)
        ok = np.isfinite(sp)
        z = (sp - t0.get("mu", 0.0)) / (t0.get("sigma") or 1.0)
        out.append({
            "pair": pair, "a": a, "b": b, "beta": beta,
            "dates": dstr[i0:i1 + 1],
            "z": [None if not k else round(float(v), 3) for v, k in zip(z, ok)],
            "marks": [{"d": t["entry_date"], "z": round(t["entry_z"], 2), "k": "in"}
                      for t in ts]
                     + [{"d": t["exit_date"], "z": round(t["exit_z"], 2),
                         "k": t["reason"]} for t in ts],
        })
    return out


def cost_breakdown(trades, card):
    """Where the money went, by line item, summed over every fill."""
    from decimal import Decimal
    from arblib.charges import Leg, charge
    lines = {}
    for t in trades:
        for d, side_mult in ((t["entry_date"], 1), (t["exit_date"], -1)):
            for leg_q, leg_p, base_side in (
                    (t["qb"], t["pb_in"] if side_mult == 1 else t["pb_out"],
                     "BUY" if t["direction"] > 0 else "SELL"),
                    (t["qa"], t["pa_in"] if side_mult == 1 else t["pa_out"],
                     "SELL" if t["direction"] > 0 else "BUY")):
                side = base_side if side_mult == 1 else (
                    "SELL" if base_side == "BUY" else "BUY")
                c = charge(card, Leg(side, Decimal(str(round(leg_q * leg_p, 2))),
                                     date.fromisoformat(d[:10])))
                for k, v in c.lines.items():
                    lines[k] = lines.get(k, 0.0) + float(v)
    return dict(sorted(lines.items(), key=lambda kv: -kv[1]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest", default="backtest_base.json")
    ap.add_argument("--selection", default=None,
                    help="defaults to the screen matching the backtest's "
                         "specification -- a log-price book must not be "
                         "headlined with the level-price screen's audit")
    ap.add_argument("--placebo", default="selection_placebo.json")
    ap.add_argument("--sweep", default="sweep.json")
    ap.add_argument("--out", default="arbitrage_dashboard.html")
    args = ap.parse_args()

    bt = json.loads((OUT / args.backtest).read_text())
    if args.selection is None:
        args.selection = ("selection_logpx.json" if bt["args"].get("log_prices")
                          else "selection_base.json")
    sel = json.loads((OUT / args.selection).read_text()) if (OUT / args.selection).exists() else None
    plc = json.loads((OUT / args.placebo).read_text()) if (OUT / args.placebo).exists() else None
    swp = json.loads((OUT / args.sweep).read_text()) if (OUT / args.sweep).exists() else None

    dates, close, opn, vol = data.panel_ohlc(min_bars=1200)
    rf = charges.risk_free_growth(bt["dates"])
    eq0 = bt["equity"][0]

    payload = {
        "summary": bt["summary"],
        "args": bt["args"],
        "dates": bt["dates"],
        "equity": [round(v, 2) for v in bt["equity"]],
        "riskfree": [round(eq0 * v, 2) for v in rf],
        "gross": [round(v, 0) for v in bt["gross_exposure"]],
        "net": [round(v, 0) for v in bt["net_exposure"]],
        "open": bt["open_count"],
        "trades": [{k: t[k] for k in ("pair", "a", "b", "beta", "direction",
                                      "entry_date", "exit_date", "entry_z", "exit_z",
                                      "bars", "reason", "gross", "cost", "gross_pnl",
                                      "net_pnl", "half_life", "pvalue")}
                   for t in bt["trades"]],
        "cost_note": ("brokerage is the largest single line item at this position "
                      "size -- see COSTS.md section 3"),
        "spreads": spread_series(bt["trades"], dates, close),
        "costs": cost_breakdown(bt["trades"], bt["args"]["card"]),
        "boot_daily": bt.get("bootstrap_daily_excess"),
        "boot_trade": bt.get("bootstrap_trades"),
        "baseline": (bt.get("baseline_random") or {}).get("summary"),
        "baseline_equity": (bt.get("baseline_random") or {}).get("equity"),
        "selection": sel["audit"] if sel else None,
        "placebo": plc["audit"] if plc else None,
        "sweep": swp,
    }

    html = TEMPLATE.replace("__DATA__", json.dumps(payload, separators=(",", ":"),
                                                   allow_nan=False, default=float))
    html = html.replace("__GENERATED__", date.today().isoformat())
    path = OUT / args.out
    path.write_text(html)
    print(f"wrote {path}  ({path.stat().st_size/1024:.0f} KB)")


TEMPLATE = r"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NSE Pairs — Arbitrage Backtest</title>
<style>
:root{
  --bg:#F6F8F6; --surface:#FFFFFF; --ink:#182220; --muted:#5A6660; --border:#E3E8E4;
  --accent:#0E7A5F; --gain:#1B8A5A; --loss:#C4483E; --chip:#EDF2EE; --grid:#EAEFEA;
  --warn:#B4741E;
  --gain-soft:rgba(27,138,90,.12); --loss-soft:rgba(196,72,62,.12);
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --bg:#0F1412; --surface:#161C19; --ink:#E7ECE9; --muted:#93A099; --border:#263029;
  --accent:#34B98B; --gain:#279E66; --loss:#E85C50; --chip:#1E2622; --grid:#1F2823;
  --warn:#D79A43;
  --gain-soft:rgba(39,158,102,.18); --loss-soft:rgba(232,92,80,.18);
}}
:root[data-theme="dark"]{
  --bg:#0F1412; --surface:#161C19; --ink:#E7ECE9; --muted:#93A099; --border:#263029;
  --accent:#34B98B; --gain:#279E66; --loss:#E85C50; --chip:#1E2622; --grid:#1F2823;
  --warn:#D79A43;
  --gain-soft:rgba(39,158,102,.18); --loss-soft:rgba(232,92,80,.18);
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.5 "Avenir Next","Segoe UI",-apple-system,BlinkMacSystemFont,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:28px 16px 80px;position:relative}
.mono{font-family:ui-monospace,"SF Mono","Cascadia Code",Menlo,Consolas,monospace;
  font-variant-numeric:tabular-nums}
header h1{font-size:26px;font-weight:650;margin:0 0 2px;letter-spacing:-.01em;text-wrap:balance}
header .sub{color:var(--muted);font-size:14px;margin:0;max-width:74ch}
.eyebrow{text-transform:uppercase;letter-spacing:.09em;font-size:11px;font-weight:600;
  color:var(--accent);margin:0 0 6px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:22px 0 26px}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px 14px}
.kpi .l{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);font-weight:600}
.kpi .v{font-size:21px;font-weight:650;margin-top:2px;font-variant-numeric:tabular-nums}
.kpi .s{font-size:12px;color:var(--muted)}
.pos{color:var(--gain)} .neg{color:var(--loss)} .warnc{color:var(--warn)}
section{margin:30px 0}
h2{font-size:16px;font-weight:650;margin:0 0 4px}
.note{color:var(--muted);font-size:13px;margin:0 0 12px;max-width:80ch}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px}
.charts{display:grid;grid-template-columns:1fr;gap:14px}
@media(min-width:900px){.charts.two{grid-template-columns:3fr 2fr}}
svg{display:block;width:100%;height:auto}
svg text{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:11px;fill:var(--muted)}
.tooltip{position:fixed;pointer-events:none;background:var(--surface);border:1px solid var(--border);
  border-radius:6px;padding:8px 10px;font-size:12.5px;box-shadow:0 4px 14px rgba(0,0,0,.14);
  z-index:10;display:none;min-width:160px}
.tooltip .t-date{color:var(--muted);font-size:11px}
.filters{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 12px}
.filters select,.filters input{background:var(--surface);color:var(--ink);border:1px solid var(--border);
  border-radius:6px;padding:7px 9px;font-size:13.5px;font-family:inherit}
.filters input{width:150px}
.fsummary{font-size:13px;color:var(--muted);margin-left:auto}
.tblwrap{overflow-x:auto;border:1px solid var(--border);border-radius:10px;background:var(--surface);max-height:620px}
table.t{border-collapse:collapse;width:100%;font-size:13.5px}
.t th{position:sticky;top:0;background:var(--surface);text-align:right;padding:10px;
  font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
  border-bottom:1px solid var(--border);white-space:nowrap;z-index:2}
.t th.txt{text-align:left} .t th.sortable{cursor:pointer;user-select:none}
.t th.sortable:hover{color:var(--ink)}
.t td{padding:8px 10px;border-bottom:1px solid var(--grid);text-align:right;white-space:nowrap}
.t td.txt{text-align:left}
.t tr:hover td{background:var(--chip)}
.sym{font-weight:600}
.chip{display:inline-block;background:var(--chip);border-radius:999px;padding:1px 9px;font-size:12px;color:var(--muted)}
.rsn-stop{color:var(--loss)} .rsn-target{color:var(--gain)} .rsn-cap,.rsn-window{color:var(--muted)}
.caveat{background:var(--surface);border:1px solid var(--border);border-left:3px solid var(--loss);
  border-radius:8px;padding:14px 16px;font-size:13.5px}
.caveat ul{margin:8px 0 0;padding-left:20px} .caveat li{margin:5px 0;color:var(--muted)}
.headline{background:var(--surface);border:1px solid var(--border);border-left:3px solid var(--accent);
  border-radius:8px;padding:16px 18px;font-size:14.5px;margin:18px 0}
.headline p{margin:6px 0}
footer{color:var(--muted);font-size:12.5px;margin-top:38px;border-top:1px solid var(--border);padding-top:14px}
footer p{margin:4px 0}
.themebtn{position:absolute;top:24px;right:16px;background:var(--surface);color:var(--muted);
  border:1px solid var(--border);border-radius:6px;padding:6px 11px;font:inherit;font-size:12.5px;cursor:pointer}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:var(--muted);margin-top:8px}
.legend i{display:inline-block;width:11px;height:3px;border-radius:2px;margin-right:5px;vertical-align:middle}
.sw{overflow-x:auto} .sw table{border-collapse:collapse;width:100%;font-size:13px;min-width:640px}
.sw th,.sw td{padding:6px 9px;border-bottom:1px solid var(--grid);text-align:right;white-space:nowrap}
.sw th{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em;text-align:right}
.sw td.txt,.sw th.txt{text-align:left}
.sw h3{font-size:13px;margin:16px 0 4px;color:var(--accent);font-weight:650}
</style>
<div class="wrap">
<button class="themebtn" id="themebtn" type="button">theme</button>
<header>
  <p class="eyebrow">Experiment · arbitrage</p>
  <h1>NSE pairs trading — backtest ledger</h1>
  <p class="sub" id="sub"></p>
</header>

<div class="headline" id="headline"></div>
<div class="kpis" id="kpis"></div>

<section>
  <h2>Did the screen find anything?</h2>
  <p class="note">Every formation date screens the whole eligible universe. The bar
    to clear is not zero — it is the number of pairs that pass <em>by chance</em>,
    shown as the pale bar. The placebo line is the same screen run on scrambled real
    prices, where no genuine relationship can exist.</p>
  <div class="card"><div id="selChart"></div>
    <div class="legend"><span><i style="background:var(--accent)"></i>pairs found</span>
      <span><i style="background:var(--muted);opacity:.45"></i>expected by chance</span>
      <span><i style="background:var(--warn)"></i>placebo (scrambled prices)</span></div>
  </div>
</section>

<section>
  <h2>Equity, against the risk-free rate</h2>
  <p class="note">A market-neutral book has no market exposure to be paid for taking,
    so the comparator is cash, not the index. Open positions are marked to market daily.</p>
  <div class="charts two">
    <div class="card"><div id="eqChart"></div><div id="ddChart"></div></div>
    <div class="card"><div id="yrChart"></div></div>
  </div>
</section>

<section>
  <h2>Gross and net exposure</h2>
  <p class="note">The hedge ratio equalises <em>share counts</em> in the cointegrating
    relation, not rupee values — so net exposure is never zero. The ratio of net to
    gross is the honest measure of how market-neutral the book actually was.</p>
  <div class="card"><div id="expChart"></div>
    <div class="legend"><span><i style="background:var(--accent)"></i>gross</span>
      <span><i style="background:var(--loss)"></i>net (signed)</span></div>
  </div>
</section>

<section>
  <h2>A pair's spread, and what was traded on it</h2>
  <p class="note">z-score of the spread over the traded window. Entries are hollow,
    exits are filled and coloured by reason. This is where you see whether a pair
    was ever a pair.</p>
  <div class="filters"><select id="fPair"></select><span class="fsummary" id="pairInfo"></span></div>
  <div class="card"><div id="spChart"></div></div>
</section>

<section>
  <h2>Where the money went</h2>
  <p class="note">Every line item of every fill, from the rate card. Gross P&amp;L is
    what the spreads produced; the bars are what was taken out of it.</p>
  <div class="card"><div id="costChart"></div></div>
</section>

<section id="sweepSec">
  <h2>Parameter sweeps</h2>
  <p class="note">One axis at a time. Read the shape, not the best cell — the best cell
    of a grid run on one sample is the luckiest cell.</p>
  <div class="card sw" id="sweepTbl"></div>
</section>

<section>
  <h2>Every trade</h2>
  <div class="filters">
    <select id="fYear"></select>
    <select id="fReason"><option value="">all exits</option><option value="target">target</option>
      <option value="stop">stopped</option><option value="cap">holding cap</option>
      <option value="window">window end</option></select>
    <select id="fSide"><option value="">winners and losers</option>
      <option value="w">winners</option><option value="l">losers</option></select>
    <input id="fSym" type="search" placeholder="pair" autocomplete="off">
    <span class="fsummary" id="fsum"></span>
  </div>
  <div class="tblwrap"><table class="t" id="tradesTbl">
    <thead><tr>
      <th class="txt sortable" data-k="pair" tabindex="0">Pair</th>
      <th class="txt sortable" data-k="entry_date" tabindex="0">In</th>
      <th class="txt sortable" data-k="exit_date" tabindex="0">Out</th>
      <th class="sortable" data-k="bars" tabindex="0">Bars</th>
      <th class="txt">Exit</th>
      <th class="sortable" data-k="entry_z" tabindex="0">z in</th>
      <th class="sortable" data-k="exit_z" tabindex="0">z out</th>
      <th class="sortable" data-k="half_life" tabindex="0">Half-life</th>
      <th class="sortable" data-k="pvalue" tabindex="0">p</th>
      <th class="sortable" data-k="gross" tabindex="0">Gross &#8377;</th>
      <th class="sortable" data-k="cost" tabindex="0">Cost &#8377;</th>
      <th class="sortable" data-k="net_pnl" tabindex="0">Net &#8377;</th>
      <th class="sortable" data-k="r" tabindex="0">R</th>
    </tr></thead><tbody id="tbody"></tbody></table></div>
</section>

<section>
  <h2>What would make this wrong</h2>
  <div class="caveat"><ul id="caveats"></ul></div>
</section>

<footer>
  <p>Generated __GENERATED__ by <span class="mono">experiments/arbitrage/scripts/dashboard.py</span>.
     Reproduce everything with <span class="mono">scripts/run_all.py</span>.</p>
  <p>Nothing in this experiment is imported by the paper-trading application, and the
     application's database is opened read-only. There is no broker surface here.</p>
</footer>
</div>
<div class="tooltip" id="tip"></div>
<script>
const D = __DATA__;
const $ = s => document.querySelector(s);
const fmt0 = n => (n<0?"-":"")+"₹"+Math.abs(Math.round(n)).toLocaleString("en-IN");
const pct = (n,d=2) => (n*100).toFixed(d)+"%";
const spct = (n,d=2) => (n>=0?"+":"")+(n*100).toFixed(d)+"%";
const cls = n => n>0?"pos":(n<0?"neg":"");

/* ---------- theme ---------- */
(function(){
  const b=$("#themebtn");
  b.onclick=()=>{const r=document.documentElement;
    const cur=r.getAttribute("data-theme")||(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light");
    r.setAttribute("data-theme",cur==="dark"?"light":"dark");draw();};
})();
const cssv = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

/* ---------- header ---------- */
const S = D.summary, A = D.args;
$("#sub").textContent =
  `Engle-Granger cointegration on ${A.log_prices ? "LOG prices" : "price levels"}, `
  + `${A.formation}-bar formation windows, traded over ${A.trading}-bar disjoint windows. `
  + (A.correction === "none"
      ? "No multiple-comparison correction \u2014 this is the naive book, and the "
        + "correction is the point (see below). "
      : `Multiple comparisons corrected by ${A.correction.toUpperCase()} at q=${A.q}. `)
  + `Short leg in single-stock futures, modelled from cash and charged at ${A.card}. `
  + `${S.start} to ${S.end}.`;

function kpi(l,v,s,c){return `<div class="kpi"><div class="l">${l}</div>
  <div class="v ${c||''}">${v}</div><div class="s">${s||''}</div></div>`;}
$("#kpis").innerHTML = [
  kpi("CAGR", pct(S.cagr), `risk-free ${pct(S.risk_free_cagr)}`, cls(S.cagr)),
  kpi("Excess over cash", spct(S.excess_cagr), "this is the strategy", cls(S.excess_cagr)),
  kpi("Max drawdown", pct(S.max_drawdown), `MAR ${S.mar.toFixed(2)}`, "neg"),
  kpi("Sharpe vs rf", S.sharpe_vs_rf.toFixed(2), `vol ${pct(S.vol_annual)}`, cls(S.sharpe_vs_rf)),
  kpi("Trades", S.n_trades, `win ${pct(S.win_rate,1)} · ${S.mean_bars_held.toFixed(0)} bars`),
  kpi("Costs", fmt0(S.total_costs),
      S.gross_pnl_negative ? "gross P&L was already negative"
        : (S.cost_share_of_gross!=null ? pct(S.cost_share_of_gross,1)+" of gross P&L" : ""), "neg"),
  kpi("Net P&L", fmt0(S.total_net_pnl), `gross ${fmt0(S.total_gross_pnl)}`, cls(S.total_net_pnl)),
  kpi("Net / gross exposure", pct(S.mean_net_over_gross,1), "0% would be neutral",
      S.mean_net_over_gross>0.1?"warnc":""),
].join("");

/* ---------- headline ---------- */
(function(){
  const bits=[];
  if(D.selection){
    const t=D.selection.totals, r=D.selection.nominal_to_chance_ratio;
    bits.push(`<p><b>The ${A.log_prices?"log-price":"level-price"} screen ran `
      +`${t.tests.toLocaleString()} hypothesis tests.</b> `
      +`${t.nominal.toLocaleString()} pairs passed at a nominal 5%; `
      +`${Math.round(0.05*t.tests).toLocaleString()} would pass by chance alone — a ratio of `
      +`<b>${r.toFixed(2)}&times;</b>. ${t.bh.toLocaleString()} survive a false-discovery-rate `
      +`correction across the whole period.</p>`);
  }
  if(D.placebo){
    const p=D.placebo.totals, pr=D.placebo.nominal_to_chance_ratio;
    bits.push(`<p><b>The placebo</b> — the identical screen on scrambled real prices, where `
      +`no genuine relationship can exist — scored <b>${pr.toFixed(2)}&times;</b> `
      +`with ${p.bh.toLocaleString()} FDR survivors. That is the number the real screen has to beat.</p>`);
  }
  const bd=D.boot_daily;
  if(bd) bits.push(`<p><b>Significance.</b> Block bootstrap on daily excess return: mean `
    +`${spct(bd.mean*252)}/yr, 95% CI [${spct(bd.lo*252)}, ${spct(bd.hi*252)}] — `
    +`<b>${bd.excludes_zero?"excludes":"straddles"} zero</b>.</p>`);
  bits.push(`<p><b>Gross against costs.</b> On deployed exposure the spreads returned `
    +`<b>${spct(S.gross_per_exposure_year)}/yr</b> gross; costs took `
    +`<b>${spct(-(S.gross_per_exposure_year-S.net_per_exposure_year))}/yr</b>; net `
    +`<b class="${cls(S.net_per_exposure_year)}">${spct(S.net_per_exposure_year)}/yr</b>. `
    +`Return on exposure, not CAGR, is the comparison to read — with cash earning the `
    +`policy rate, a book that never trades reports the risk-free rate.</p>`);
  if(D.baseline) bits.push(`<p><b>Date-matched random-pair baseline</b> (screen removed, `
    +`everything else identical): excess ${spct(D.baseline.excess_cagr)} against `
    +`${spct(S.excess_cagr)} for the screened book. Its own trade bootstrap straddles `
    +`zero too, so neither book has been shown to work — not that random is better.</p>`);
  $("#headline").innerHTML=bits.join("");
})();

/* ---------- chart helpers ---------- */
function svg(w,h){return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="height:${h}px">`;}
function path(pts){return pts.map((p,i)=>(i?"L":"M")+p[0].toFixed(1)+" "+p[1].toFixed(1)).join(" ");}
const tip=$("#tip");
function showTip(e,html){tip.innerHTML=html;tip.style.display="block";
  tip.style.left=Math.min(e.clientX+14,innerWidth-190)+"px";tip.style.top=(e.clientY-12)+"px";}
function hideTip(){tip.style.display="none";}

/* ---------- equity ---------- */
function drawEquity(){
  const W=740,H=250,P={l:58,r:12,t:12,b:22};
  const eq=D.equity, rf=D.riskfree, n=eq.length;
  const lo=Math.min(...eq,...rf), hi=Math.max(...eq,...rf);
  const x=i=>P.l+(W-P.l-P.r)*i/(n-1), y=v=>P.t+(H-P.t-P.b)*(1-(v-lo)/(hi-lo||1));
  let g="";
  for(let k=0;k<=4;k++){const v=lo+(hi-lo)*k/4;
    g+=`<line x1="${P.l}" y1="${y(v)}" x2="${W-P.r}" y2="${y(v)}" stroke="${cssv('--grid')}"/>`
      +`<text x="${P.l-6}" y="${y(v)+4}" text-anchor="end">${(v/1e5).toFixed(1)}L</text>`;}
  const yrs={}; D.dates.forEach((d,i)=>{const yv=d.slice(0,4); if(!(yv in yrs)) yrs[yv]=i;});
  Object.entries(yrs).forEach(([yv,i])=>{g+=`<text x="${x(i)}" y="${H-6}" text-anchor="middle">${yv}</text>`;});
  let s=svg(W,H)+g
    +`<path d="${path(rf.map((v,i)=>[x(i),y(v)]))}" fill="none" stroke="${cssv('--muted')}" stroke-width="1.4" stroke-dasharray="4 3"/>`
    +`<path d="${path(eq.map((v,i)=>[x(i),y(v)]))}" fill="none" stroke="${cssv('--accent')}" stroke-width="1.8"/>`;
  if(D.baseline_equity&&D.baseline_equity.length===n)
    s+=`<path d="${path(D.baseline_equity.map((v,i)=>[x(i),y(v)]))}" fill="none" stroke="${cssv('--warn')}" stroke-width="1.2" opacity=".8"/>`;
  s+=`<rect x="${P.l}" y="${P.t}" width="${W-P.l-P.r}" height="${H-P.t-P.b}" fill="transparent" id="eqHit"/></svg>`;
  $("#eqChart").innerHTML=s
    +`<div class="legend"><span><i style="background:var(--accent)"></i>strategy</span>
      <span><i style="background:var(--muted)"></i>risk-free (policy rate)</span>
      ${D.baseline_equity?'<span><i style="background:var(--warn)"></i>random-pair baseline</span>':''}</div>`;
  const hit=$("#eqHit");
  hit.onmousemove=e=>{const r=hit.getBoundingClientRect();
    const i=Math.max(0,Math.min(n-1,Math.round((e.clientX-r.left)/r.width*(n-1))));
    showTip(e,`<div class="t-date">${D.dates[i]}</div><b>${fmt0(eq[i])}</b><br>
      <span style="color:var(--muted)">cash ${fmt0(rf[i])}</span><br>
      <span style="color:var(--muted)">${D.open[i]} open</span>`);};
  hit.onmouseleave=hideTip;
}

function drawDD(){
  const W=740,H=110,P={l:58,r:12,t:10,b:18};
  const eq=D.equity,n=eq.length;let pk=-1e18;
  const dd=eq.map(v=>{pk=Math.max(pk,v);return v/pk-1;});
  const lo=Math.min(...dd,-0.01);
  const x=i=>P.l+(W-P.l-P.r)*i/(n-1), y=v=>P.t+(H-P.t-P.b)*(v/lo);
  let g="";
  for(let k=0;k<=2;k++){const v=lo*k/2;
    g+=`<line x1="${P.l}" y1="${y(v)}" x2="${W-P.r}" y2="${y(v)}" stroke="${cssv('--grid')}"/>`
      +`<text x="${P.l-6}" y="${y(v)+4}" text-anchor="end">${(v*100).toFixed(0)}%</text>`;}
  const pts=dd.map((v,i)=>[x(i),y(v)]);
  $("#ddChart").innerHTML=svg(W,H)+g
    +`<path d="${path(pts)} L ${x(n-1)} ${y(0)} L ${x(0)} ${y(0)} Z" fill="${cssv('--loss-soft')}" stroke="none"/>`
    +`<path d="${path(pts)}" fill="none" stroke="${cssv('--loss')}" stroke-width="1.3"/></svg>`;
}

function drawYears(){
  const W=380,H=250,P={l:46,r:12,t:12,b:22};
  const byY={};
  D.dates.forEach((d,i)=>{const y=d.slice(0,4);(byY[y]=byY[y]||[]).push(i);});
  const yrs=Object.keys(byY).sort();
  const rows=yrs.map(y=>{const ix=byY[y];
    const a=D.equity[Math.max(0,ix[0]-1)], b=D.equity[ix[ix.length-1]];
    const ra=D.riskfree[Math.max(0,ix[0]-1)], rb=D.riskfree[ix[ix.length-1]];
    return {y, r:b/a-1, rf:rb/ra-1};});
  const m=Math.max(0.02,...rows.map(r=>Math.max(Math.abs(r.r),Math.abs(r.rf))));
  const bw=(H-P.t-P.b)/rows.length;
  const x=v=>P.l+(W-P.l-P.r)*(0.5+v/(2*m));
  let s=svg(W,H)+`<line x1="${x(0)}" y1="${P.t}" x2="${x(0)}" y2="${H-P.b}" stroke="${cssv('--border')}"/>`;
  rows.forEach((r,i)=>{const yy=P.t+i*bw+2, h=bw-6;
    s+=`<rect x="${Math.min(x(0),x(r.r))}" y="${yy}" width="${Math.abs(x(r.r)-x(0))}" height="${h}"
        fill="${r.r>=0?cssv('--gain'):cssv('--loss')}" rx="2"/>`
      +`<line x1="${x(r.rf)}" y1="${yy-1}" x2="${x(r.rf)}" y2="${yy+h+1}" stroke="${cssv('--muted')}" stroke-width="1.6" stroke-dasharray="2 2"/>`
      +`<text x="${P.l-6}" y="${yy+h/2+4}" text-anchor="end">${r.y}</text>`;});
  s+=`<text x="${W-P.r}" y="${H-6}" text-anchor="end">dashed = risk-free</text></svg>`;
  $("#yrChart").innerHTML=`<div style="font-size:13px;color:var(--muted);margin-bottom:6px">Year by year</div>`+s;
}

function drawExposure(){
  const W=740,H=200,P={l:64,r:12,t:12,b:22};
  const g0=D.gross,ne=D.net,n=g0.length;
  const hi=Math.max(1,...g0), lo=Math.min(0,...ne);
  const x=i=>P.l+(W-P.l-P.r)*i/(n-1), y=v=>P.t+(H-P.t-P.b)*(1-(v-lo)/((hi-lo)||1));
  let g="";
  for(let k=0;k<=4;k++){const v=lo+(hi-lo)*k/4;
    g+=`<line x1="${P.l}" y1="${y(v)}" x2="${W-P.r}" y2="${y(v)}" stroke="${cssv('--grid')}"/>`
      +`<text x="${P.l-6}" y="${y(v)+4}" text-anchor="end">${(v/1e5).toFixed(1)}L</text>`;}
  const yrs={}; D.dates.forEach((d,i)=>{const yv=d.slice(0,4); if(!(yv in yrs)) yrs[yv]=i;});
  Object.entries(yrs).forEach(([yv,i])=>{g+=`<text x="${x(i)}" y="${H-6}" text-anchor="middle">${yv}</text>`;});
  $("#expChart").innerHTML=svg(W,H)+g
    +`<path d="${path(g0.map((v,i)=>[x(i),y(v)]))}" fill="none" stroke="${cssv('--accent')}" stroke-width="1.3"/>`
    +`<path d="${path(ne.map((v,i)=>[x(i),y(v)]))}" fill="none" stroke="${cssv('--loss')}" stroke-width="1.2"/>`
    +`<line x1="${P.l}" y1="${y(0)}" x2="${W-P.r}" y2="${y(0)}" stroke="${cssv('--border')}"/></svg>`;
}

function drawSelection(){
  if(!D.selection){$("#selChart").innerHTML="<p class='note'>no selection audit</p>";return;}
  const rows=D.selection.rows, plc=D.placebo?D.placebo.rows:null;
  const W=740,H=230,P={l:52,r:12,t:12,b:40};
  const hi=Math.max(1,...rows.map(r=>Math.max(r.nominal_5pct,r.expected_by_chance)),
                    ...(plc?plc.map(r=>r.nominal_5pct):[0]));
  const bw=(W-P.l-P.r)/rows.length;
  const y=v=>P.t+(H-P.t-P.b)*(1-v/hi);
  let s=svg(W,H);
  for(let k=0;k<=4;k++){const v=hi*k/4;
    s+=`<line x1="${P.l}" y1="${y(v)}" x2="${W-P.r}" y2="${y(v)}" stroke="${cssv('--grid')}"/>`
      +`<text x="${P.l-6}" y="${y(v)+4}" text-anchor="end">${Math.round(v)}</text>`;}
  rows.forEach((r,i)=>{const cx=P.l+i*bw;
    s+=`<rect x="${cx+bw*0.10}" y="${y(r.expected_by_chance)}" width="${bw*0.8}"
         height="${y(0)-y(r.expected_by_chance)}" fill="${cssv('--muted')}" opacity=".35" rx="2"/>`
      +`<rect x="${cx+bw*0.26}" y="${y(r.nominal_5pct)}" width="${bw*0.48}"
         height="${y(0)-y(r.nominal_5pct)}" fill="${cssv('--accent')}" rx="2"/>`;
    if(plc&&plc[i]) s+=`<line x1="${cx+bw*0.10}" y1="${y(plc[i].nominal_5pct)}"
         x2="${cx+bw*0.90}" y2="${y(plc[i].nominal_5pct)}" stroke="${cssv('--warn')}" stroke-width="2"/>`;
    if(i%2===0) s+=`<text x="${cx+bw/2}" y="${H-22}" text-anchor="middle">${r.date.slice(2,7)}</text>`;
    s+=`<text x="${cx+bw/2}" y="${H-8}" text-anchor="middle" style="font-size:9.5px">${r.bh_pass}</text>`;});
  s+=`<text x="${P.l}" y="${H-8}" text-anchor="start" style="font-size:9.5px;fill:${cssv('--muted')}">FDR:</text></svg>`;
  $("#selChart").innerHTML=s;
}

function drawSpread(){
  const sel=$("#fPair"); if(!D.spreads.length){$("#spChart").innerHTML="<p class='note'>no trades</p>";return;}
  const sp=D.spreads[+sel.value||0];
  const W=740,H=250,P={l:44,r:12,t:12,b:22};
  const zs=sp.z.filter(v=>v!==null);
  const m=Math.max(4,...zs.map(Math.abs));
  const n=sp.z.length;
  const x=i=>P.l+(W-P.l-P.r)*i/(n-1), y=v=>P.t+(H-P.t-P.b)*(1-(v+m)/(2*m));
  let g="";
  [-3.5,-2,0,2,3.5].forEach(v=>{ if(Math.abs(v)<=m){
    g+=`<line x1="${P.l}" y1="${y(v)}" x2="${W-P.r}" y2="${y(v)}"
        stroke="${v===0?cssv('--border'):cssv('--grid')}" ${v!==0?'stroke-dasharray="3 3"':''}/>`
      +`<text x="${P.l-6}" y="${y(v)+4}" text-anchor="end">${v}</text>`;}});
  let segs=[],cur=[];
  sp.z.forEach((v,i)=>{ if(v===null){ if(cur.length>1)segs.push(cur); cur=[]; } else cur.push([x(i),y(v)]); });
  if(cur.length>1)segs.push(cur);
  let s=svg(W,H)+g+segs.map(p=>`<path d="${path(p)}" fill="none" stroke="${cssv('--accent')}" stroke-width="1.4"/>`).join("");
  const di={}; sp.dates.forEach((d,i)=>di[d]=i);
  sp.marks.forEach(mk=>{ const i=di[mk.d]; if(i===undefined)return;
    const col = mk.k==="in"?cssv('--ink'):(mk.k==="stop"?cssv('--loss'):(mk.k==="target"?cssv('--gain'):cssv('--muted')));
    s+=`<circle cx="${x(i)}" cy="${y(mk.z)}" r="4" fill="${mk.k==="in"?"none":col}" stroke="${col}" stroke-width="1.6"/>`;});
  s+=`</svg>`;
  $("#spChart").innerHTML=s;
  const L = A.log_prices;
  $("#pairInfo").textContent=(L ? `ln ${sp.b} = ${sp.beta.toFixed(3)} × ln ${sp.a} + spread`
                                : `${sp.b} = ${sp.beta.toFixed(3)} × ${sp.a} + spread`)
    + `  ·  `
    +`${sp.marks.filter(m=>m.k==="in").length} entries  ·  hollow = entry, filled = exit`;
}

function drawCosts(){
  const c=D.costs, keys=Object.keys(c); if(!keys.length)return;
  const W=740,H=40+keys.length*26,P={l:190,r:70,t:10,b:10};
  const hi=Math.max(...keys.map(k=>c[k]), Math.abs(D.summary.total_gross_pnl));
  const x=v=>P.l+(W-P.l-P.r)*v/(hi||1);
  let s=svg(W,H);
  s+=`<rect x="${P.l}" y="${P.t}" width="${x(Math.abs(D.summary.total_gross_pnl))-P.l}" height="18"
       fill="${D.summary.total_gross_pnl>=0?cssv('--gain'):cssv('--loss')}" opacity=".35" rx="2"/>`
    +`<text x="${P.l-8}" y="${P.t+13}" text-anchor="end">gross P&amp;L</text>`
    +`<text x="${x(Math.abs(D.summary.total_gross_pnl))+6}" y="${P.t+13}">${fmt0(D.summary.total_gross_pnl)}</text>`;
  keys.forEach((k,i)=>{const yy=P.t+26+i*26;
    s+=`<rect x="${P.l}" y="${yy}" width="${Math.max(1,x(c[k])-P.l)}" height="16" fill="${cssv('--loss')}" rx="2"/>`
      +`<text x="${P.l-8}" y="${yy+12}" text-anchor="end">${k.replace(/_/g," ")}</text>`
      +`<text x="${Math.max(x(c[k]),P.l)+6}" y="${yy+12}">${fmt0(c[k])}</text>`;});
  s+=`</svg>`;
  $("#costChart").innerHTML=s;
}

function drawSweep(){
  if(!D.sweep){$("#sweepSec").style.display="none";return;}
  let h="";
  for(const [axis,rows] of Object.entries(D.sweep)){
    h+=`<h3>${axis.replace(/_/g," ")}</h3><table><thead><tr><th class="txt">value</th>
      <th>pairs</th><th>trades</th><th>win</th><th>CAGR</th><th>excess</th>
      <th>max DD</th><th>Sharpe</th><th>costs/gross</th><th>CI&gt;0</th></tr></thead><tbody>`;
    rows.forEach(r=>{h+=`<tr><td class="txt mono">${r.value}</td><td>${r.pairs_selected}</td>
      <td>${r.n_trades}</td><td>${pct(r.win_rate,1)}</td>
      <td class="${cls(r.cagr)}">${pct(r.cagr)}</td>
      <td class="${cls(r.excess_cagr)}">${spct(r.excess_cagr)}</td>
      <td class="neg">${pct(r.max_drawdown)}</td><td>${r.sharpe_vs_rf.toFixed(2)}</td>
      <td>${r.cost_share_of_gross!=null?pct(r.cost_share_of_gross,0):"-"}</td>
      <td>${r.excess_excludes_zero?"yes":"no"}</td></tr>`;});
    h+=`</tbody></table>`;
  }
  $("#sweepTbl").innerHTML=h;
}

/* ---------- trades table ---------- */
let sortK="entry_date", sortAsc=true;
const T = D.trades.map(t=>({...t, r: t.gross? t.net_pnl/t.gross : 0}));
(function initFilters(){
  const yrs=[...new Set(T.map(t=>t.entry_date.slice(0,4)))].sort();
  $("#fYear").innerHTML='<option value="">all years</option>'+yrs.map(y=>`<option>${y}</option>`).join("");
  $("#fPair").innerHTML=D.spreads.map((s,i)=>`<option value="${i}">${s.pair}</option>`).join("");
  ["#fYear","#fReason","#fSide","#fSym"].forEach(s=>$(s).oninput=renderTrades);
  $("#fPair").onchange=drawSpread;
  document.querySelectorAll("th.sortable").forEach(th=>{
    const go=()=>{const k=th.dataset.k; sortAsc = (k===sortK)?!sortAsc:true; sortK=k; renderTrades();};
    th.onclick=go; th.onkeydown=e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();go();}};});
})();
function renderTrades(){
  const y=$("#fYear").value, rs=$("#fReason").value, sd=$("#fSide").value,
        q=$("#fSym").value.trim().toUpperCase();
  let rows=T.filter(t=>(!y||t.entry_date.startsWith(y))&&(!rs||t.reason===rs)
    &&(!sd||(sd==="w"?t.net_pnl>0:t.net_pnl<=0))&&(!q||t.pair.includes(q)));
  rows.sort((a,b)=>{const va=a[sortK],vb=b[sortK];
    return (va<vb?-1:va>vb?1:0)*(sortAsc?1:-1);});
  $("#tbody").innerHTML=rows.map(t=>`<tr>
    <td class="txt sym">${t.pair}</td><td class="txt mono">${t.entry_date}</td>
    <td class="txt mono">${t.exit_date}</td><td>${t.bars}</td>
    <td class="txt rsn-${t.reason}">${t.reason}</td>
    <td class="mono">${t.entry_z.toFixed(2)}</td><td class="mono">${t.exit_z.toFixed(2)}</td>
    <td class="mono">${t.half_life.toFixed(1)}</td>
    <td class="mono">${t.pvalue<0.0001?t.pvalue.toExponential(1):t.pvalue.toFixed(4)}</td>
    <td class="mono">${fmt0(t.gross)}</td><td class="mono neg">${fmt0(t.cost)}</td>
    <td class="mono ${cls(t.net_pnl)}">${fmt0(t.net_pnl)}</td>
    <td class="mono ${cls(t.r)}">${(t.r*100).toFixed(2)}%</td></tr>`).join("");
  const net=rows.reduce((s,t)=>s+t.net_pnl,0), w=rows.filter(t=>t.net_pnl>0).length;
  $("#fsum").textContent=`${rows.length} trades · ${rows.length?(100*w/rows.length).toFixed(0):0}% winners · net ${fmt0(net)}`;
}

/* ---------- caveats ---------- */
$("#caveats").innerHTML=[
 "<b>Survivorship.</b> The panel is today's index constituency held back to 2015. Nothing "
 +"delisted, merged away or suspended is in it — and a pairs book's catastrophic case is "
 +"exactly the leg that stops trading. Every number here is flattered by an unknown amount.",
 "<b>The short leg is modelled, not measured.</b> An overnight short in Indian cash equity "
 +"is not permitted, so the short leg must be a single-stock future — and this repository "
 +"has no futures price history. Futures prices are taken from cash, charged at the F&O rate "
 +"card. Basis noise, roll slippage and lot-size granularity are not in any number here.",
 "<b>No impact or liquidity model.</b> Fills are at the open, in unlimited size, above a "
 +"turnover floor whose sensitivity is swept. A real second-tier pair would pay more.",
 "<b>Delivery brokerage is today's zero throughout.</b> Retail delivery brokerage was not "
 +"zero in 2015, which flatters the early years of the cash-cost comparison.",
 "<b>The rules were chosen while looking at this data.</b> The sweeps show the shape rather "
 +"than the best cell, and the synthetic benchmark is genuinely out of sample — but the "
 +"entry, exit and stop bands were not selected on a held-out period.",
 "<b>Corporate actions are only partly handled.</b> Splits and bonuses are vendor-adjusted; "
 +"demergers and re-listings are not, and the six unadjusted breaks found in the panel are "
 +"excluded by date rather than corrected.",
].map(s=>`<li>${s}</li>`).join("");

/* ---------- go ---------- */
function draw(){drawEquity();drawDD();drawYears();drawExposure();drawSelection();
  drawSpread();drawCosts();drawSweep();}
draw(); renderTrades();
addEventListener("resize",()=>{});
</script>
"""

if __name__ == "__main__":
    main()
