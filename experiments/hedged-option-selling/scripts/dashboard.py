"""Build out/option_selling_dashboard.html from out/backtest.json.

Laid out so the LEFT TAIL is the first thing on the page, not the last.  The
win rate is shown once, greyed, with the reason it means nothing beside it.

Three charts the VCP dashboard did not need:
  * MARGIN UTILISATION, in place of the positions chart -- margin is what binds
    this strategy, and peak utilisation is the number that decides whether a
    book survives.
  * a PAYOFF DIAGRAM of the decided structure.
  * a per-trade P&L HISTOGRAM with the worst 1% and 5% shaded and labelled.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "out"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=str(OUT / "backtest.json"))
    ap.add_argument("--sweep", default=str(OUT / "sweep.json"))
    ap.add_argument("--diagonals", default=str(OUT / "diagonals.json"))
    ap.add_argument("--backspreads", default=str(OUT / "backspreads.json"))
    ap.add_argument("--out", default=str(OUT / "option_selling_dashboard.html"))
    a = ap.parse_args()

    d = json.loads(Path(a.json).read_text())
    sweep = {}
    if Path(a.sweep).exists():
        sweep = json.loads(Path(a.sweep).read_text())
    diagonals = {}
    if Path(a.diagonals).exists():
        diagonals = json.loads(Path(a.diagonals).read_text())
    backspreads = {}
    if Path(a.backspreads).exists():
        backspreads = json.loads(Path(a.backspreads).read_text())

    payload = {
        "config": d.get("config", ""),
        "sample": d.get("sample", []),
        "hedged": d.get("hedged", {}),
        "unhedged": d.get("unhedged", {}),
        "equity": d.get("equity", {}),
        "risk_free": d.get("risk_free", {}),
        "buy_hold": d.get("buy_hold", {}),
        "bootstrap": d.get("bootstrap", {}),
        "tail_events": d.get("tail_events", {}),
        "margin_utilisation": d.get("margin_utilisation", {}),
        "split_before": d.get("split_before", {}),
        "split_after": d.get("split_after", {}),
        "trades": d.get("trades", []),
        "unhedged_trades": d.get("unhedged_trades", []),
        "equity_curve": d.get("equity_curve", []),
        "index": d.get("index", []),
        "sweep": sweep,
        "diagonals": diagonals,
        "backspreads": backspreads,
    }
    html = TEMPLATE.replace("__DATA__", json.dumps(payload, separators=(",", ":")))
    html = html.replace("__GENERATED__", date.today().isoformat())
    sample = payload["sample"]
    html = html.replace("__SUBTITLE__",
                        f"{sample[0]} to {sample[1]} · {payload['config']}"
                        if len(sample) == 2 else payload["config"])
    Path(a.out).write_text(html)
    print(f"wrote {a.out}")
    return 0


TEMPLATE = r"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hedged Option Selling — NIFTY</title>
<style>
:root{
  --bg:#F6F8F6; --surface:#FFFFFF; --ink:#182220; --muted:#5A6660; --border:#E3E8E4;
  --accent:#0E7A5F; --gain:#1B8A5A; --loss:#C4483E; --chip:#EDF2EE; --grid:#EAEFEA;
  --warn:#B4740E;
  --gain-soft:rgba(27,138,90,.12); --loss-soft:rgba(196,72,62,.12);
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --bg:#0F1412; --surface:#161C19; --ink:#E7ECE9; --muted:#93A099; --border:#263029;
  --accent:#34B98B; --gain:#279E66; --loss:#E85C50; --chip:#1E2622; --grid:#1F2823;
  --warn:#D9A03C;
  --gain-soft:rgba(39,158,102,.18); --loss-soft:rgba(232,92,80,.18);
}}
:root[data-theme="dark"]{
  --bg:#0F1412; --surface:#161C19; --ink:#E7ECE9; --muted:#93A099; --border:#263029;
  --accent:#34B98B; --gain:#279E66; --loss:#E85C50; --chip:#1E2622; --grid:#1F2823;
  --warn:#D9A03C;
  --gain-soft:rgba(39,158,102,.18); --loss-soft:rgba(232,92,80,.18);
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.5 "Avenir Next","Segoe UI",-apple-system,BlinkMacSystemFont,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:28px 16px 80px;position:relative}
.mono{font-family:ui-monospace,"SF Mono","Cascadia Code",Menlo,Consolas,monospace;
  font-variant-numeric:tabular-nums}
header h1{font-size:26px;font-weight:650;margin:0 0 2px;letter-spacing:-.01em;text-wrap:balance}
header .sub{color:var(--muted);font-size:14px;margin:0}
.eyebrow{text-transform:uppercase;letter-spacing:.09em;font-size:11px;font-weight:600;
  color:var(--accent);margin:0 0 6px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:10px;margin:22px 0 10px}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px 14px}
.kpi.tail{border-left:3px solid var(--loss)}
.kpi.dim{opacity:.55}
.kpi .l{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);font-weight:600}
.kpi .v{font-size:21px;font-weight:650;margin-top:2px}
.kpi .s{font-size:12px;color:var(--muted)}
.pos{color:var(--gain)} .neg{color:var(--loss)} .warn{color:var(--warn)}
section{margin:26px 0}
h2{font-size:16px;font-weight:650;margin:0 0 4px}
.note{color:var(--muted);font-size:13px;margin:0 0 12px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px}
.charts{display:grid;grid-template-columns:1fr;gap:14px}
@media(min-width:900px){.charts{grid-template-columns:3fr 2fr}}
.two{display:grid;grid-template-columns:1fr;gap:14px}
@media(min-width:900px){.two{grid-template-columns:1fr 1fr}}
svg{display:block;width:100%;height:auto}
svg text{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:11px;fill:var(--muted)}
.banner{background:var(--surface);border:1px solid var(--border);border-left:3px solid var(--loss);
  border-radius:8px;padding:14px 16px;font-size:14px;margin:18px 0}
.banner.warn{border-left-color:var(--warn)}
.banner h3{margin:0 0 6px;font-size:14px;font-weight:650}
.banner p{margin:4px 0;color:var(--muted)}
table.t{border-collapse:collapse;width:100%;font-size:13.5px}
.t th{text-align:right;padding:9px 10px;font-size:11px;text-transform:uppercase;
  letter-spacing:.06em;color:var(--muted);border-bottom:1px solid var(--border);white-space:nowrap}
.t th.txt,.t td.txt{text-align:left}
.t td{padding:8px 10px;border-bottom:1px solid var(--grid);text-align:right;white-space:nowrap}
.t tr:hover td{background:var(--chip)}
.tblwrap{overflow-x:auto;border:1px solid var(--border);border-radius:10px;background:var(--surface);max-height:560px}
.tblwrap .t th{position:sticky;top:0;background:var(--surface);z-index:2}
.tooltip{position:fixed;pointer-events:none;background:var(--surface);border:1px solid var(--border);
  border-radius:6px;padding:8px 10px;font-size:12.5px;box-shadow:0 4px 14px rgba(0,0,0,.14);
  z-index:10;display:none;min-width:150px}
.tooltip .t-date{color:var(--muted);font-size:11px}
.caveat{background:var(--surface);border:1px solid var(--border);border-left:3px solid var(--warn);
  border-radius:8px;padding:14px 16px;font-size:13.5px}
.caveat ul{margin:8px 0 0;padding-left:20px} .caveat li{margin:5px 0;color:var(--muted)}
footer{color:var(--muted);font-size:12.5px;margin-top:34px;border-top:1px solid var(--border);padding-top:14px}
footer p{margin:4px 0}
.themebtn{position:absolute;top:24px;right:20px;background:var(--surface);color:var(--muted);
  border:1px solid var(--border);border-radius:6px;padding:6px 11px;font:inherit;font-size:12.5px;cursor:pointer}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--muted);margin-top:8px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
</style>
<div class="wrap">
<button class="themebtn" id="themebtn" type="button">theme</button>
<header>
  <p class="eyebrow">Experiment · hedged-option-selling</p>
  <h1>Defined-risk option selling — NIFTY index options</h1>
  <p class="sub">__SUBTITLE__</p>
</header>

<div id="sampleBanner"></div>

<section>
  <h2>The left tail, first</h2>
  <p class="note">This strategy family has a high win rate and a rare large loss.
     Every instinct built on breakout systems points the wrong way, so the tail is
     shown before the return and the win rate is greyed out.</p>
  <div class="kpis" id="tailKpis"></div>
</section>

<section>
  <h2>Return, and what it was measured against</h2>
  <p class="note">A market-neutral premium seller is not competing with the index.
     Its opportunity cost is the risk-free rate; its real comparator is the same
     strategy without the bought wing.</p>
  <div class="kpis" id="retKpis"></div>
  <div class="card" style="margin-top:12px"><div id="baseTable"></div></div>
</section>

<section>
  <h2>Equity and drawdown</h2>
  <p class="note">Open positions are marked to market daily, so the drawdown is the
     one you would have lived through rather than what shows up at exits.</p>
  <div class="charts">
    <div class="card"><div id="eqChart"></div><div id="ddChart"></div></div>
    <div class="card"><div id="yrChart"></div></div>
  </div>
</section>

<section>
  <h2>Margin utilisation</h2>
  <p class="note">Margin is the binding constraint here, not cash — a credit
     structure receives money at entry and blocks margin against it. Peak
     utilisation is what decides whether a book survives a bad month, because the
     requirement expands exactly when the position is losing.</p>
  <div class="card"><div id="mgnChart"></div></div>
</section>

<section>
  <h2>Per-trade P&amp;L, with the tail shaded</h2>
  <p class="note">The shape to look for is a tall stack of small wins and a thin
     arm reaching left. The mean of the worst 1% and 5% is marked.</p>
  <div class="two">
    <div class="card"><div id="histChart"></div></div>
    <div class="card"><div id="payoffChart"></div></div>
  </div>
</section>

<section>
  <h2>Hedged against unhedged</h2>
  <p class="note">The wing costs premium and releases margin. If the tail it removes
     is worth less than it costs, the honest answer is that this structure does not
     work at these prices — which is a finding, not a failure.</p>
  <div class="card"><div id="hedgeTable"></div></div>
</section>

<section id="backSection" style="display:none">
  <h2>Backspreads — buy more wings than you sell</h2>
  <p class="note">Sell one put at 16Δ, buy <em>ratio</em> puts at 5Δ below it.
     Below the long strike the extra wings outrun the short, so the loss peaks at
     the wing and then reverses: a crash stops being the disaster case. What is
     paid for it is a smaller credit every trade, and a lower median return.</p>
  <div class="card"><div id="backTable"></div></div>
  <div class="two" style="margin-top:14px">
    <div class="card"><div id="backChart"></div></div>
    <div class="card"><div id="backPayoff"></div></div>
  </div>
</section>

<section id="diagSection" style="display:none">
  <h2>Diagonals — sell the near expiry, buy further out in time</h2>
  <p class="note">A two-expiry structure: the bought leg is further out of the
     money <em>and</em> longer dated, so it still holds time value when the short
     leg expires. Measured on a shorter window, because NIFTY did not carry four
     liquid expiries until weeklies matured.</p>
  <div class="banner warn" id="diagCaveat"></div>
  <div class="card"><div id="diagTables"></div></div>
</section>

<section id="sweepSection" style="display:none">
  <h2>Parameter sweeps</h2>
  <p class="note">One dimension at a time, showing the trade-off rather than the best
     cell. A best cell picked from a grid this size is a selection artefact.</p>
  <div class="card"><div id="sweepTables"></div></div>
</section>

<section>
  <h2>Every trade</h2>
  <div class="tblwrap"><div id="tradeTable"></div></div>
</section>

<section>
  <h2>What this cannot tell you</h2>
  <div class="caveat" id="caveats"></div>
</section>

<footer>
  <p>Generated __GENERATED__ · experiments/hedged-option-selling · reproduce with <span class="mono">scripts/run_all.py</span></p>
  <p>Research only. Nothing here is a recommendation, and none of it is wired to any order path.</p>
</footer>
</div>
<div class="tooltip" id="tip"></div>
<script>
const D = __DATA__;
const $ = s => document.querySelector(s);
const tip = $('#tip');

// theme
const tb = $('#themebtn');
tb.onclick = () => {
  const cur = document.documentElement.getAttribute('data-theme');
  const dark = cur ? cur === 'dark'
    : matchMedia('(prefers-color-scheme: dark)').matches;
  document.documentElement.setAttribute('data-theme', dark ? 'light' : 'dark');
  drawAll();
};

const cssv = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const fmt = n => (n===null||n===undefined||!isFinite(n)) ? '—'
  : Math.abs(n) >= 1e7 ? (n/1e7).toFixed(2)+' cr'
  : Math.abs(n) >= 1e5 ? (n/1e5).toFixed(2)+' L'
  : Math.round(n).toLocaleString('en-IN');
const pct = (n,d=1) => (n===null||n===undefined||!isFinite(n)) ? '—' : (n*100).toFixed(d)+'%';

function kpi(l, v, s, cls, extra){
  return `<div class="kpi ${extra||''}"><div class="l">${l}</div>`+
         `<div class="v mono ${cls||''}">${v}</div><div class="s">${s||''}</div></div>`;
}

// ---- sample banner: how many disasters were actually in the data ----------
(function(){
  const te = D.tail_events || {};
  const five = te.down_moves_over_5pct ?? 0;
  const three = te.down_moves_over_3pct ?? 0;
  const cls = five === 0 ? 'banner' : 'banner warn';
  let msg;
  if (five === 0){
    msg = `<h3>The sample contains no 5% down day.</h3>
      <p>That is the headline, not a footnote. This backtest has not tested the
      event that decides whether a premium seller survives, so every number
      below is conditional on a disaster that never happened in it.</p>`;
  } else {
    msg = `<h3>What the sample actually contained</h3>
      <p><b>${five}</b> sessions fell more than 5% and <b>${three}</b> fell more
      than 3%. The worst single session was
      <b>${pct(te.worst_session,2)}</b> (${te.worst_session_date||''}) and the
      worst 20-session stretch <b>${pct(te.worst_20_session_move,2)}</b>
      (to ${te.worst_20_session_end||''}).</p>
      <p>A backtest with no disaster in it is undersampled, not safe. Judge every
      figure below against how few of these there were.</p>`;
  }
  $('#sampleBanner').innerHTML = `<div class="${cls}">${msg}</div>`;
})();

// ---- KPIs ----------------------------------------------------------------
(function(){
  const h = D.hedged || {};
  const mu = D.margin_utilisation || {};
  $('#tailKpis').innerHTML = [
    kpi('Worst trade', fmt(h.worst_trade), 'single largest loss', 'neg', 'tail'),
    kpi('Mean worst 1%', fmt(h.worst_1pct), 'conditional tail mean', 'neg', 'tail'),
    kpi('Mean worst 5%', fmt(h.worst_5pct), '', 'neg', 'tail'),
    kpi('Worst month', fmt(h.worst_month), h.worst_month_label||'', 'neg', 'tail'),
    kpi('Max loss / margin', pct(h.max_loss_per_margin),
        'largest loss per rupee blocked', 'neg', 'tail'),
    kpi('Loss / win size', (h.loss_tail_ratio||0).toFixed(2)+'x',
        'mean loss ÷ mean win', 'neg', 'tail'),
    kpi('Peak margin use', pct(mu.peak), 'of equity', '', 'tail'),
    kpi('Win rate', pct(h.win_rate),
        'means nothing on its own', '', 'dim'),
  ].join('');

  const eq = D.equity || {}, rf = D.risk_free || {}, bh = D.buy_hold || {};
  const ex = (eq.cagr ?? 0) - (rf.cagr ?? 0);
  $('#retKpis').innerHTML = [
    kpi('Net P&L', fmt(h.total_pnl), `${h.n||0} completed trades`,
        (h.total_pnl||0) >= 0 ? 'pos':'neg'),
    kpi('Return on margin', pct(h.total_return_on_margin,2),
        'sum P&L ÷ sum margin blocked'),
    kpi('CAGR', pct(eq.cagr,2), 'on deployed capital'),
    kpi('Excess over cash', pct(ex,2), 'CAGR less the repo rate',
        ex >= 0 ? 'pos':'neg'),
    kpi('Max drawdown', pct(eq.max_drawdown,2), eq.max_drawdown_date||'', 'neg'),
    kpi('Sharpe', (eq.sharpe??0).toFixed(2), 'daily, annualised'),
    kpi('Charges', fmt(-(h.total_charges||0)),
        h.gross_pnl ? pct((h.total_charges||0)/Math.abs(h.gross_pnl)) + ' of gross' : '', 'neg'),
    kpi('Mean hold', (h.mean_days_held||0).toFixed(1)+' d', ''),
  ].join('');
})();

// ---- baseline + bootstrap table ------------------------------------------
(function(){
  const eq=D.equity||{}, rf=D.risk_free||{}, bh=D.buy_hold||{}, bb=D.bootstrap||{};
  let rows = `<table class="t"><tr><th class="txt">Comparator</th><th>CAGR</th>
    <th>Max DD</th><th>MAR</th><th>Sharpe</th></tr>`;
  const r=(n,m)=>`<tr><td class="txt">${n}</td><td class="mono">${pct(m.cagr,2)}</td>
     <td class="mono neg">${pct(m.max_drawdown,2)}</td>
     <td class="mono">${isFinite(m.mar)?(m.mar||0).toFixed(2):'—'}</td>
     <td class="mono">${isFinite(m.sharpe)?(m.sharpe||0).toFixed(2):'—'}</td></tr>`;
  rows += r('Hedged seller', eq) + r('Risk-free (RBI repo)', rf);
  if (bh && bh.cagr !== undefined) rows += r('Buy and hold NIFTY', bh);
  rows += `</table>`;
  if (bb && bb.n_months){
    rows += `<p class="note" style="margin-top:14px">Block bootstrap, ${bb.n_boot.toLocaleString()} draws
      resampling ${bb.n_months} whole months — months, not trades, because losses
      here arrive clustered.</p>
      <table class="t"><tr><th class="txt">Total P&L</th><th>5th pct</th><th>median</th>
      <th>95th pct</th><th>P(total &lt; 0)</th></tr>
      <tr><td class="txt">observed ${fmt(bb.observed_total)}</td>
      <td class="mono">${fmt(bb.total_p05)}</td><td class="mono">${fmt(bb.total_p50)}</td>
      <td class="mono">${fmt(bb.total_p95)}</td>
      <td class="mono ${bb.prob_total_negative>0.05?'warn':''}">${pct(bb.prob_total_negative)}</td></tr></table>`;
  }
  $('#baseTable').innerHTML = rows;
})();

// ---- hedged vs unhedged --------------------------------------------------
(function(){
  const h=D.hedged||{}, u=D.unhedged||{};
  if (!u || !u.n){ $('#hedgeTable').innerHTML='<p class="note">No unhedged comparator for this structure.</p>'; return; }
  const row=(l,f,inv)=>{
    const a=h[f], b=u[f];
    const better = inv ? (a<b) : (a>b);
    return `<tr><td class="txt">${l}</td>
      <td class="mono ${better?'pos':''}">${typeof a==='number'&&Math.abs(a)<3?pct(a,2):fmt(a)}</td>
      <td class="mono ${better?'':'pos'}">${typeof b==='number'&&Math.abs(b)<3?pct(b,2):fmt(b)}</td></tr>`;
  };
  $('#hedgeTable').innerHTML = `<table class="t">
    <tr><th class="txt">Metric</th><th>Hedged</th><th>Unhedged</th></tr>
    <tr><td class="txt">Trades</td><td class="mono">${h.n}</td><td class="mono">${u.n}</td></tr>
    ${row('Net P&L','total_pnl',false)}
    ${row('Return on margin','total_return_on_margin',false)}
    ${row('Worst trade','worst_trade',false)}
    ${row('Mean worst 1%','worst_1pct',false)}
    ${row('Max loss / margin','max_loss_per_margin',false)}
    ${row('Mean margin blocked','mean_margin',true)}
    <tr><td class="txt">Win rate</td><td class="mono" style="opacity:.55">${pct(h.win_rate)}</td>
        <td class="mono" style="opacity:.55">${pct(u.win_rate)}</td></tr>
  </table>
  <p class="note" style="margin-top:12px">Green marks the better cell. Note that the
  win rates are nearly identical — the difference between these two is entirely in
  the tail, which is the point.</p>`;
})();

// ---- charts --------------------------------------------------------------
function svg(w,h,inner){return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${inner}</svg>`;}

function drawEquity(){
  const c = D.equity_curve||[];
  const el=$('#eqChart'), dl=$('#ddChart');
  if(!c.length){el.innerHTML='<p class="note">No equity curve.</p>';dl.innerHTML='';return;}
  const W=760,H=240,P=34;
  const vals=c.map(r=>r[1]), n=c.length;
  const lo=Math.min(...vals), hi=Math.max(...vals);
  const x=i=>P+(W-P-8)*i/Math.max(n-1,1);
  const y=v=>H-24-(H-34-24)*((v-lo)/Math.max(hi-lo,1e-9));
  let pts=c.map((r,i)=>`${x(i).toFixed(1)},${y(r[1]).toFixed(1)}`).join(' ');
  // risk-free reference: flat line at start (visual anchor only)
  let g='';
  for(let k=0;k<=4;k++){const v=lo+(hi-lo)*k/4;
    g+=`<line x1="${P}" y1="${y(v)}" x2="${W-8}" y2="${y(v)}" stroke="${cssv('--grid')}" stroke-width="1"/>`
     +`<text x="4" y="${y(v)+4}">${fmt(v)}</text>`;}
  let ticks='';
  for(let k=0;k<=5;k++){const i=Math.round((n-1)*k/5);
    ticks+=`<text x="${x(i)}" y="${H-6}" text-anchor="middle">${(c[i][0]||'').slice(0,7)}</text>`;}
  el.innerHTML=`<div style="font-size:12px;color:var(--muted);margin-bottom:6px">Equity (marked daily)</div>`
    +svg(W,H,g+ticks+`<polyline points="${pts}" fill="none" stroke="${cssv('--accent')}" stroke-width="1.8"/>`);
  // drawdown
  let peak=-1e18; const dd=vals.map(v=>{peak=Math.max(peak,v);return v/peak-1;});
  const H2=110, mind=Math.min(...dd,-0.0001);
  const y2=v=>8+(H2-26)*(v/mind);
  let dpts=dd.map((v,i)=>`${x(i).toFixed(1)},${y2(v).toFixed(1)}`).join(' ');
  dl.innerHTML=`<div style="font-size:12px;color:var(--muted);margin:12px 0 6px">Drawdown (min ${pct(mind,2)})</div>`
    +svg(W,H2,`<polyline points="${P},8 ${dpts} ${W-8},8" fill="${cssv('--loss-soft')}" stroke="${cssv('--loss')}" stroke-width="1.2"/>`);
}

function drawMargin(){
  const c=D.equity_curve||[]; const el=$('#mgnChart');
  if(!c.length){el.innerHTML='<p class="note">No margin series.</p>';return;}
  const W=760,H=220,P=40;
  const u=c.map(r=>r[3]||0), n=c.length;
  const hi=Math.max(...u,0.02);
  const x=i=>P+(W-P-8)*i/Math.max(n-1,1);
  const y=v=>H-24-(H-14-24)*(v/hi);
  let g='';
  for(let k=0;k<=4;k++){const v=hi*k/4;
    g+=`<line x1="${P}" y1="${y(v)}" x2="${W-8}" y2="${y(v)}" stroke="${cssv('--grid')}"/>`
     +`<text x="4" y="${y(v)+4}">${pct(v,0)}</text>`;}
  let ticks='';
  for(let k=0;k<=5;k++){const i=Math.round((n-1)*k/5);
    ticks+=`<text x="${x(i)}" y="${H-6}" text-anchor="middle">${(c[i][0]||'').slice(0,7)}</text>`;}
  const pts=u.map((v,i)=>`${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  el.innerHTML=`<div style="font-size:12px;color:var(--muted);margin-bottom:6px">Margin blocked as a share of equity</div>`
    +svg(W,H,g+ticks+`<polyline points="${P},${y(0)} ${pts} ${W-8},${y(0)}" fill="${cssv('--gain-soft')}" stroke="${cssv('--accent')}" stroke-width="1.4"/>`)
    +`<div class="legend"><span>peak ${pct((D.margin_utilisation||{}).peak)}</span>
      <span>mean ${pct((D.margin_utilisation||{}).mean)}</span></div>`;
}

function drawHist(){
  const t=(D.trades||[]).filter(x=>x.exit_reason!=='still_open_at_sample_end');
  const el=$('#histChart');
  if(!t.length){el.innerHTML='<p class="note">No trades.</p>';return;}
  const p=t.map(x=>x.net_pnl).sort((a,b)=>a-b);
  const lo=Math.min(...p), hi=Math.max(...p);
  const NB=28, w=(hi-lo)/NB||1;
  const bins=new Array(NB).fill(0);
  p.forEach(v=>{let i=Math.min(NB-1,Math.floor((v-lo)/w));bins[i]++;});
  const W=520,H=250,P=34, maxc=Math.max(...bins);
  const x=i=>P+(W-P-8)*i/NB, bw=(W-P-8)/NB;
  const y=c=>H-26-(H-16-26)*(c/maxc);
  const h=D.hedged||{};
  const xv=v=>P+(W-P-8)*((v-lo)/(hi-lo||1));
  let bars='';
  for(let i=0;i<NB;i++){
    const centre=lo+w*(i+0.5);
    const col = centre<0 ? cssv('--loss') : cssv('--gain');
    if(!bins[i])continue;
    bars+=`<rect x="${x(i)+0.6}" y="${y(bins[i])}" width="${bw-1.2}" height="${H-26-y(bins[i])}" fill="${col}" opacity=".78"/>`;
  }
  // shade the worst 5%
  const k5=Math.max(1,Math.ceil(p.length*0.05)), cut5=p[k5-1];
  let shade=`<rect x="${P}" y="14" width="${Math.max(xv(cut5)-P,0)}" height="${H-40}" fill="${cssv('--loss-soft')}"/>`;
  let marks='';
  [['worst 1%',h.worst_1pct],['worst 5%',h.worst_5pct]].forEach((m,i)=>{
    if(m[1]===undefined||m[1]===null)return;
    const xx=xv(m[1]);
    marks+=`<line x1="${xx}" y1="14" x2="${xx}" y2="${H-26}" stroke="${cssv('--loss')}" stroke-width="1" stroke-dasharray="3 3"/>`
      +`<text x="${xx+3}" y="${26+i*13}" fill="${cssv('--loss')}">${m[0]} ${fmt(m[1])}</text>`;
  });
  let ax='';
  for(let k=0;k<=4;k++){const v=lo+(hi-lo)*k/4;
    ax+=`<text x="${xv(v)}" y="${H-8}" text-anchor="middle">${fmt(v)}</text>`;}
  el.innerHTML=`<div style="font-size:12px;color:var(--muted);margin-bottom:6px">Net P&amp;L per trade (${p.length} trades)</div>`
    +svg(W,H,shade+bars+marks+ax)
    +`<div class="legend"><span><i style="background:${cssv('--loss-soft')}"></i>worst 5% of trades</span>
      <span>skew ${(h.skew||0).toFixed(2)}</span></div>`;
}

function drawPayoff(){
  const t=(D.trades||[]).filter(x=>x.legs&&x.legs.length);
  const el=$('#payoffChart');
  if(!t.length){el.innerHTML='<p class="note">No structure to draw.</p>';return;}
  // use the median-credit trade as the representative structure
  const s=t.slice().sort((a,b)=>a.entry_credit-b.entry_credit)[Math.floor(t.length/2)];
  const legs=s.legs, F=s.entry_forward, credit=s.entry_credit;
  const ks=legs.map(l=>l[0]);
  const lo=Math.min(...ks)*0.97, hi=Math.max(...ks)*1.03;
  const N=240, xs=[], ys=[];
  for(let i=0;i<=N;i++){
    const S=lo+(hi-lo)*i/N;
    let v=credit;
    legs.forEach(l=>{const [k,isC,q]=l;
      v += q*(isC?Math.max(S-k,0):Math.max(k-S,0));});
    xs.push(S); ys.push(v);
  }
  const W=520,H=250,P=44;
  const ylo=Math.min(...ys), yhi=Math.max(...ys);
  const X=v=>P+(W-P-10)*((v-lo)/(hi-lo));
  const Y=v=>H-26-(H-16-26)*((v-ylo)/Math.max(yhi-ylo,1e-9));
  let g=`<line x1="${P}" y1="${Y(0)}" x2="${W-10}" y2="${Y(0)}" stroke="${cssv('--border')}" stroke-width="1"/>`;
  g+=`<line x1="${X(F)}" y1="14" x2="${X(F)}" y2="${H-26}" stroke="${cssv('--muted')}" stroke-width="1" stroke-dasharray="2 4"/>`;
  g+=`<text x="${X(F)+3}" y="24">spot at entry</text>`;
  // positive area / negative area
  let up='',dn='';
  for(let i=0;i<N;i++){
    const x1=X(xs[i]),x2=X(xs[i+1]),y1=Y(ys[i]),y2=Y(ys[i+1]);
    const col = (ys[i]+ys[i+1])/2 >= 0 ? cssv('--gain-soft') : cssv('--loss-soft');
    up+=`<polygon points="${x1},${Y(0)} ${x1},${y1} ${x2},${y2} ${x2},${Y(0)}" fill="${col}"/>`;
  }
  const line=xs.map((v,i)=>`${X(v).toFixed(1)},${Y(ys[i]).toFixed(1)}`).join(' ');
  let ax='';
  for(let k=0;k<=4;k++){const v=lo+(hi-lo)*k/4;
    ax+=`<text x="${X(v)}" y="${H-8}" text-anchor="middle">${Math.round(v).toLocaleString('en-IN')}</text>`;}
  [ylo,0,yhi].forEach(v=>{ax+=`<text x="4" y="${Y(v)+4}">${fmt(v)}</text>`;});
  el.innerHTML=`<div style="font-size:12px;color:var(--muted);margin-bottom:6px">Payoff at expiry — representative trade (${s.entry_date})</div>`
    +svg(W,H,up+g+`<polyline points="${line}" fill="none" stroke="${cssv('--ink')}" stroke-width="1.6"/>`+ax)
    +`<div class="legend"><span>credit ${fmt(credit)}</span><span>max loss ${fmt(s.max_loss)}</span>
      <span>margin ${fmt(s.margin_at_entry)}</span></div>`;
}

function drawYears(){
  const t=(D.trades||[]).filter(x=>x.exit_reason!=='still_open_at_sample_end');
  const el=$('#yrChart'); if(!t.length){el.innerHTML='';return;}
  const by={};
  t.forEach(x=>{const y=(x.exit_date||x.entry_date).slice(0,4); by[y]=(by[y]||0)+x.net_pnl;});
  const ys=Object.keys(by).sort();
  const W=360,H=250,P=46;
  const vals=ys.map(y=>by[y]);
  const lo=Math.min(0,...vals), hi=Math.max(0,...vals);
  const bh=(H-40)/ys.length;
  const X=v=>P+(W-P-14)*((v-lo)/Math.max(hi-lo,1e-9));
  let out=`<line x1="${X(0)}" y1="10" x2="${X(0)}" y2="${H-14}" stroke="${cssv('--border')}"/>`;
  ys.forEach((y,i)=>{
    const v=by[y], x0=X(Math.min(0,v)), x1=X(Math.max(0,v));
    out+=`<rect x="${x0}" y="${14+i*bh}" width="${Math.max(x1-x0,1)}" height="${bh-6}"
      fill="${v>=0?cssv('--gain'):cssv('--loss')}" opacity=".82"/>`;
    out+=`<text x="4" y="${14+i*bh+bh/2}" >${y}</text>`;
    out+=`<text x="${x1+4}" y="${14+i*bh+bh/2}" fill="${cssv('--muted')}">${fmt(v)}</text>`;
  });
  el.innerHTML=`<div style="font-size:12px;color:var(--muted);margin-bottom:6px">Net P&amp;L by year</div>`+svg(W,H,out);
}

function drawBackspreads(){
  const B = D.backspreads || {}; const R = B.ratios || {};
  const keys = Object.keys(R).sort((a,b)=>parseFloat(a)-parseFloat(b));
  if (!keys.length) return;
  $('#backSection').style.display = '';

  let h = `<table class="t"><tr><th class="txt">ratio</th><th>n</th>
    <th>total P&L</th><th>return /yr</th><th>worst</th><th>worst 1%</th>
    <th>5th pct/trade</th><th>losing &gt;10%</th><th>crash mean</th>
    <th>P(&lt;0)</th><th style="opacity:.55">win</th></tr>`;
  const best = keys.reduce((a,k)=> (R[k].worst||-1e18) > (R[a].worst||-1e18) &&
                 (R[k].return_on_risk_annual||-9) > 0.15 ? k : a, keys[0]);
  keys.forEach(k=>{const c=R[k];
    const hi = k==='2.00' ? ' style="background:var(--gain-soft)"' : '';
    h+=`<tr${hi}><td class="txt mono">1 : ${parseFloat(k).toFixed(2)}</td>
      <td class="mono">${c.n}</td>
      <td class="mono ${c.total_pnl>=0?'pos':'neg'}">${fmt(c.total_pnl)}</td>
      <td class="mono ${c.return_on_risk_annual>=0?'pos':'neg'}">${pct(c.return_on_risk_annual,2)}</td>
      <td class="mono neg">${fmt(c.worst)}</td>
      <td class="mono neg">${fmt(c.worst_1pct)}</td>
      <td class="mono ${(c.p5||0)>=0?'pos':'neg'}">${pct(c.p5,1)}</td>
      <td class="mono">${(c.pct_losing_10||0).toFixed(1)}%</td>
      <td class="mono ${(c.crash_mean||0)>=0?'pos':'neg'}">${fmt(c.crash_mean)}</td>
      <td class="mono ${(c.prob_negative||0)<0.15?'pos':''}">${pct(c.prob_negative)}</td>
      <td class="mono" style="opacity:.55">${pct(c.win_rate)}</td></tr>`;});
  h+='</table>';
  h+=`<p class="note" style="margin-top:12px">Highlighted row is 1:2 — the best
    risk trade-off found: essentially the same return as the plain vertical, a
    27% smaller worst trade, a 5th percentile that turns positive, and
    P(total&lt;0) nearly halved. Ratio 3.00 breaks down, which is reassuring: a
    surface that improved all the way to the extreme would more likely be a
    pricing artefact than an edge.</p>`;
  $('#backTable').innerHTML = h;

  // return vs worst-trade scatter
  const W=520,H=250,P=52;
  const rets=keys.map(k=>R[k].return_on_risk_annual||0);
  const wor =keys.map(k=>Math.abs(R[k].worst||0));
  const rlo=Math.min(...rets,0), rhi=Math.max(...rets);
  const wlo=Math.min(...wor), whi=Math.max(...wor);
  const X=v=>P+(W-P-14)*((v-wlo)/Math.max(whi-wlo,1e-9));
  const Y=v=>H-30-(H-18-30)*((v-rlo)/Math.max(rhi-rlo,1e-9));
  let g=`<line x1="${P}" y1="${Y(0)}" x2="${W-14}" y2="${Y(0)}" stroke="${cssv('--border')}"/>`;
  keys.forEach((k,i)=>{
    const x=X(wor[i]), y=Y(rets[i]);
    g+=`<circle cx="${x}" cy="${y}" r="${k==='2.00'?6:4}" fill="${rets[i]>=0?cssv('--accent'):cssv('--loss')}"/>`;
    g+=`<text x="${x+8}" y="${y+4}">1:${parseFloat(k).toFixed(2)}</text>`;});
  let ax='';
  for(let j=0;j<=3;j++){const v=wlo+(whi-wlo)*j/3;
    ax+=`<text x="${X(v)}" y="${H-10}" text-anchor="middle">${fmt(v)}</text>`;}
  [rlo,rhi].forEach(v=>{ax+=`<text x="4" y="${Y(v)+4}">${pct(v,0)}</text>`;});
  $('#backChart').innerHTML =
    `<div style="font-size:12px;color:var(--muted);margin-bottom:6px">Return per year vs size of the worst trade — up and left is better</div>`
    + svg(W,H,g+ax);

  // payoff shapes at expiry, schematic from the ratios
  const Ks=24000, Kl=23500, lot=75;
  const W2=520,H2=250,P2=52;
  const lo2=Kl*0.94, hi2=Ks*1.03;
  const series=[['1.00',1],['2.00',2],['3.00',3]];
  const cred={1:4474,2:1339,3:-1796};
  let pts=[], allv=[];
  series.forEach(([lbl,rr])=>{
    const arr=[];
    for(let i=0;i<=180;i++){
      const S=lo2+(hi2-lo2)*i/180;
      const v=cred[rr] + lot*(rr*Math.max(Kl-S,0) - Math.max(Ks-S,0));
      arr.push([S,v]); allv.push(v);}
    pts.push([lbl,rr,arr]);});
  const ylo=Math.min(...allv), yhi=Math.max(...allv);
  const X2=v=>P2+(W2-P2-10)*((v-lo2)/(hi2-lo2));
  const Y2=v=>H2-30-(H2-18-30)*((v-ylo)/Math.max(yhi-ylo,1e-9));
  let g2=`<line x1="${P2}" y1="${Y2(0)}" x2="${W2-10}" y2="${Y2(0)}" stroke="${cssv('--border')}"/>`;
  const cols=[cssv('--muted'),cssv('--accent'),cssv('--gain')];
  pts.forEach(([lbl,rr,arr],i)=>{
    g2+=`<polyline points="${arr.map(([S,v])=>`${X2(S).toFixed(1)},${Y2(v).toFixed(1)}`).join(' ')}"
         fill="none" stroke="${cols[i]}" stroke-width="1.8"/>`;});
  let ax2='';
  for(let j=0;j<=3;j++){const v=lo2+(hi2-lo2)*j/3;
    ax2+=`<text x="${X2(v)}" y="${H2-10}" text-anchor="middle">${Math.round(v).toLocaleString('en-IN')}</text>`;}
  [ylo,0,yhi].forEach(v=>{ax2+=`<text x="4" y="${Y2(v)+4}">${fmt(v)}</text>`;});
  $('#backPayoff').innerHTML =
    `<div style="font-size:12px;color:var(--muted);margin-bottom:6px">Payoff at expiry — short 24000 / long 23500, one lot</div>`
    + svg(W2,H2,g2+ax2)
    + `<div class="legend">`
    + series.map(([lbl],i)=>`<span><i style="background:${cols[i]}"></i>1 : ${parseFloat(lbl).toFixed(0)}</span>`).join('')
    + `<span>the 1:1 line flattens; the others turn back up</span></div>`;
}

function drawDiagonals(){
  const D2 = D.diagonals || {};
  const g = D2.groups || {};
  if (!Object.keys(g).length) return;
  $('#diagSection').style.display = '';

  const hh = g.head_to_head || {};
  const dg = hh['put diagonal (expiry+3)'];
  const ve = hh['bull put spread (same expiry)'];
  const slip = g.slippage || {};
  const flips = Object.values(slip).some(v=>v.return_on_risk_annual<0)
             && Object.values(slip).some(v=>v.return_on_risk_annual>0);

  let cav = `<h3>Read this before the tables</h3>`;
  cav += `<p>Window ${(D2.window||[]).join(' to ')} — about two years. Capital at
    risk is the <b>conservative bound</b> (the far leg assumed worthless at the
    near expiry), so these returns are understated for the diagonal and fair for
    the vertical: the comparison is biased against the diagonal on purpose.</p>`;
  if (flips) cav += `<p><b>The sign flips inside the slippage range.</b> The
    bought leg trades roughly 400× less than the leg it hedges and the bhavcopy
    has no quotes, so the spread is modelled. An assumption that decides the
    answer has not answered it.</p>`;
  cav += `<p><b>No margin figure is shown.</b> The margin model has no
    inter-month spread charge, and SEBI withdrew the index calendar benefit on
    expiry day from 2025-02-10. A margin number here would be invented.</p>`;
  $('#diagCaveat').innerHTML = cav;

  const titles = {head_to_head:'Head to head, same window',
                  far_offset:'How far out the bought leg sits',
                  far_delta:'Delta of the bought leg',
                  slippage:'Slippage on the thin leg — the decisive assumption'};
  let html='';
  Object.keys(g).forEach(k=>{
    const cells=g[k]; const names=Object.keys(cells); if(!names.length)return;
    html+=`<h3 style="font-size:13px;margin:16px 0 6px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)">${titles[k]||k.replace(/_/g,' ')}</h3>`;
    html+=`<table class="t"><tr><th class="txt">variant</th><th>n</th><th>total P&L</th>
      <th>return on risk /yr</th><th>worst</th><th>credit ÷ max loss</th>
      <th style="opacity:.55">win</th></tr>`;
    names.forEach(n=>{const c=cells[n];
      html+=`<tr><td class="txt">${n}</td><td class="mono">${c.n}</td>
        <td class="mono ${c.total_pnl>=0?'pos':'neg'}">${fmt(c.total_pnl)}</td>
        <td class="mono ${c.return_on_risk_annual>=0?'pos':'neg'}">${pct(c.return_on_risk_annual,2)}</td>
        <td class="mono neg">${fmt(c.worst)}</td>
        <td class="mono">${pct(c.credit_over_maxloss,1)}</td>
        <td class="mono" style="opacity:.55">${pct(c.win_rate)}</td></tr>`;});
    html+='</table>';
  });

  if (dg && ve){
    html += `<p class="note" style="margin-top:16px"><b>The mechanism check.</b>
      The diagonal's claim is that a longer-dated hedge holds value where a
      near-dated wing can expire worthless. On the worst trade the vertical lost
      <b>100.6%</b> of its own maximum loss — it hit the bound exactly — while the
      diagonal lost <b>30.8%</b> of its. The cushion is real. What it is paid for
      that cushion is the other half of the question: the diagonal collects
      <b>${pct(dg.credit_over_maxloss,1)}</b> of maximum loss as credit against the
      vertical's <b>${pct(ve.credit_over_maxloss,1)}</b>.</p>`;
  }
  $('#diagTables').innerHTML = html;
}

function drawSweeps(){
  const s=D.sweep||{}; const keys=Object.keys(s).filter(k=>typeof s[k]==='object'&&!Array.isArray(s[k])&&k!=='base'&&k!=='sample');
  if(!keys.length)return;
  $('#sweepSection').style.display='';
  let html='';
  keys.forEach(k=>{
    const cells=s[k]; if(!cells||typeof cells!=='object')return;
    const names=Object.keys(cells); if(!names.length||typeof cells[names[0]]!=='object')return;
    html+=`<h3 style="font-size:13px;margin:16px 0 6px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)">${k.replace(/_/g,' ')}</h3>`;
    html+=`<table class="t"><tr><th class="txt">cell</th><th>n</th><th>total P&L</th>
      <th>RoM</th><th>worst</th><th>worst 1%</th><th>maxL/margin</th><th style="opacity:.55">win</th></tr>`;
    names.forEach(n=>{const c=cells[n];
      html+=`<tr><td class="txt">${n}</td><td class="mono">${c.n}</td>
        <td class="mono ${c.total_pnl>=0?'pos':'neg'}">${fmt(c.total_pnl)}</td>
        <td class="mono">${pct(c.rom_total,2)}</td>
        <td class="mono neg">${fmt(c.worst)}</td>
        <td class="mono neg">${fmt(c.worst_1pct)}</td>
        <td class="mono neg">${pct(c.max_loss_per_margin)}</td>
        <td class="mono" style="opacity:.55">${pct(c.win_rate)}</td></tr>`;});
    html+='</table>';
  });
  $('#sweepTables').innerHTML=html;
}

function drawTrades(){
  const t=D.trades||[];
  let h=`<table class="t"><tr><th class="txt">entry</th><th class="txt">exit</th>
    <th class="txt">reason</th><th>days</th><th>credit</th><th>max loss</th>
    <th>margin</th><th>charges</th><th>net P&L</th><th>RoM</th></tr>`;
  t.slice().sort((a,b)=>a.net_pnl-b.net_pnl).forEach(x=>{
    h+=`<tr><td class="txt mono">${x.entry_date}</td><td class="txt mono">${x.exit_date||'—'}</td>
      <td class="txt">${(x.exit_reason||'').replace(/_/g,' ')}</td>
      <td class="mono">${x.days_held}</td>
      <td class="mono">${fmt(x.entry_credit)}</td>
      <td class="mono">${fmt(x.max_loss)}</td>
      <td class="mono">${fmt(x.margin_at_entry)}</td>
      <td class="mono neg">${fmt(-x.charges)}</td>
      <td class="mono ${x.net_pnl>=0?'pos':'neg'}">${fmt(x.net_pnl)}</td>
      <td class="mono ${x.return_on_margin>=0?'pos':'neg'}">${pct(x.return_on_margin,1)}</td></tr>`;});
  h+='</table>';
  $('#tradeTable').innerHTML=h;
}

$('#caveats').innerHTML = `<b>Limits of this study, stated rather than buried.</b><ul>
  <li><b>Daily data only.</b> 0DTE, intraday stop-losses and the whole
      "defend the tested side" school cannot be tested here, and no claim is made
      about them.</li>
  <li><b>The margin model is an approximation.</b> NSE's historical SPAN parameter
      files are not published, so no backtest by anyone can use the exchange's
      actual number for a past day. It is SPAN-shaped and driven by NSE's own
      published daily volatility, and it was not calibrated against a live broker
      calculator.</li>
  <li><b>Whether ELM nets a long against a short at a different strike</b> is not
      stated in the rules and was calibrated against reported broker figures. It
      moves return on margin by a factor of about 2.5 and is the assumption here
      most likely to be wrong.</li>
  <li><b>Bid-ask is modelled, not observed.</b> The bhavcopy has no quotes. The
      bought wing is two to three orders of magnitude thinner than the leg it
      protects, so this is the assumption most able to flip the answer.</li>
  <li><b>Single-stock options are excluded.</b> They are physically settled; a
      short leg in the money at expiry is a delivery obligation, not a cash
      difference.</li>
  <li><b>Before July 2024 there is no executed-trade count</b> in the bhavcopy, so
      the liquidity filter is weaker over the older part of the sample.</li>
  <li><b>None of this is wired to an order path.</b> The application this
      experiment sits beside cannot place a real order, and selling an option to
      open is forbidden in its production code. Graduating any of this would be a
      deliberate decision taken explicitly, with that rule rewritten in the same
      change.</li>
</ul>`;

function drawAll(){drawEquity();drawMargin();drawHist();drawPayoff();drawYears();drawBackspreads();drawDiagonals();drawSweeps();}
drawAll(); drawTrades();
</script>
"""

if __name__ == "__main__":
    raise SystemExit(main())
