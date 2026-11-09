"""out/bear_dashboard.html: one self-contained page (inline SVG, no CDN).

Re-simulates the development runs (seconds) so every figure on the page comes
from the same code as the JSON results. Development period only.

    .venv/bin/python scripts/dashboard.py
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import html
import json
from datetime import date

import numpy as np
import pandas as pd

from islib import panel as PN

from bearlib import OUT
from bearlib import portfolio as P
from bearlib.engine import index_bars, simulate_index
from bearlib.hyp import HYPOTHESES
from bearlib.run import Context, index_short_signals
from bearlib.universe import Universe

DEV_END = date(2022, 12, 31)
NAMES = {"B1_breakdown": "B1 breakdown", "B2_failed_rally": "B2 failed rally",
         "B6_gap_up_fade": "B6 gap-up fade*", "index_short": "Index short"}
SERIES = {"B1_breakdown": "var(--s1)", "B2_failed_rally": "var(--s2)", "B6_gap_up_fade": "var(--s3)"}


def esc(x) -> str:
    return html.escape(str(x), quote=True)


# --- data -------------------------------------------------------------------
p = PN.load()
U = Universe(p)
ctx = Context(p, U, index_bars(p))
dates = np.array(p.dates)
period = dates <= DEV_END
on = U.regime_b & period
months = pd.period_range(pd.Timestamp(dates[period][0]), pd.Timestamp(dates[period][-1]), freq="M")

trades, books = {}, {}
for name, h in HYPOTHESES.items():
    t = ctx.sim(h.signals(U, on, ctx.names(name)))
    trades[name] = t[t["net_ret"].notna()]
    books[name] = P.run_stocks(t, risk=0.005)
it = simulate_index(p, ctx.ib, index_short_signals(on), slippage=0.0001)
trades["index_short"] = it
books["index_short"] = P.run_index(it, one_lot=True)
res = {k: json.loads((OUT / f"dev_{k}.json").read_text()) for k in NAMES}
decomp = json.loads((OUT / "decompose_day.json").read_text())

on_month = pd.Series(on, index=pd.to_datetime(dates)).loc[: str(DEV_END)].groupby(
    pd.to_datetime(dates[period]).to_period("M")).mean()


# --- svg helpers ------------------------------------------------------------
def line_chart(series: dict[str, pd.Series], shade: pd.Series, w=900, h=300, unit="%") -> str:
    """Cumulative % of capital, one axis; regime-on sessions shaded."""
    pad_l, pad_r, pad_t, pad_b = 52, 170, 12, 28
    x0, x1 = shade.index.min(), shade.index.max()
    span = (x1 - x0).days or 1
    allv = pd.concat(series.values())
    lo, hi = min(0.0, allv.min()), max(0.0, allv.max())
    lo, hi = lo - 0.05 * (hi - lo), hi + 0.05 * (hi - lo)
    X = lambda d: pad_l + (w - pad_l - pad_r) * (d - x0).days / span
    Y = lambda v: pad_t + (h - pad_t - pad_b) * (hi - v) / (hi - lo)
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" class="chart">']
    # regime shading: contiguous on-runs
    s = shade.astype(bool)
    runs = (s != s.shift()).cumsum()
    for _, g in s[s].groupby(runs[s]):
        a, b = X(g.index[0]), X(g.index[-1]) + 2
        out.append(f'<rect x="{a:.1f}" y="{pad_t}" width="{max(b - a, 1.5):.1f}" '
                   f'height="{h - pad_t - pad_b}" class="shade"/>')
    for v in np.linspace(lo, hi, 5):
        out.append(f'<line x1="{pad_l}" x2="{w - pad_r}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" class="grid"/>'
                   f'<text x="{pad_l - 6}" y="{Y(v) + 4:.1f}" class="tick" text-anchor="end">{0 if abs(v) < 0.5 else round(v):+d}{unit}</text>'.replace("+0", "0"))
    out.append(f'<line x1="{pad_l}" x2="{w - pad_r}" y1="{Y(0):.1f}" y2="{Y(0):.1f}" class="zero"/>')
    for yr in range(x0.year + 1, x1.year + 1):
        xx = X(pd.Timestamp(yr, 1, 1))
        out.append(f'<text x="{xx:.1f}" y="{h - 8}" class="tick" text-anchor="middle">{yr}</text>')
    for name, ser in series.items():
        pts = " ".join(f"{X(d):.1f},{Y(v):.1f}" for d, v in ser.items())
        out.append(f'<polyline points="{pts}" fill="none" stroke="{SERIES[name]}" stroke-width="2"/>')
        last = ser.iloc[-1]
        out.append(f'<text x="{w - pad_r + 6}" y="{Y(last) + 4:.1f}" class="lbl">{esc(NAMES[name])} {last:+.1f}{unit}</text>')
    # hover targets: one invisible column per month
    for m in months:
        d0 = pd.Timestamp(m.start_time)
        tip = f"{m}: " + " · ".join(
            f"{NAMES[n]} {ser.loc[:m.end_time].iloc[-1] if len(ser.loc[:m.end_time]) else 0:+.1f}{unit}"
            for n, ser in series.items())
        out.append(f'<rect x="{X(d0):.1f}" y="{pad_t}" width="{(w - pad_l - pad_r) / len(months):.1f}" '
                   f'height="{h - pad_t - pad_b}" class="hit" data-tip="{esc(tip)}"/>')
    out.append("</svg>")
    return "".join(out)


def hist(values: np.ndarray, bins: np.ndarray, tail_q=0.05, w=430, h=200, unit="bps", label="") -> str:
    pad_l, pad_r, pad_t, pad_b = 24, 24, 10, 30
    cnt, edges = np.histogram(np.clip(values, bins[0], bins[-1]), bins=bins)
    tail = np.quantile(values, tail_q)
    X = lambda v: pad_l + (w - pad_l - pad_r) * (v - bins[0]) / (bins[-1] - bins[0])
    Y = lambda c: pad_t + (h - pad_t - pad_b) * (1 - c / max(cnt.max(), 1))
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" class="chart" aria-label="{esc(label)}">']
    for c, a, b in zip(cnt, edges[:-1], edges[1:]):
        cls = "bar tail" if a < tail else "bar"
        out.append(f'<rect x="{X(a) + 1:.1f}" y="{Y(c):.1f}" width="{max(X(b) - X(a) - 2, 1):.1f}" '
                   f'height="{h - pad_b - Y(c):.1f}" class="{cls}" rx="1" '
                   f'data-tip="{a:+.0f} to {b:+.0f} {unit}: {c}"/>')
    out.append(f'<line x1="{X(0):.1f}" x2="{X(0):.1f}" y1="{pad_t}" y2="{h - pad_b}" class="zero"/>')
    out.append(f'<line x1="{X(tail):.1f}" x2="{X(tail):.1f}" y1="{pad_t}" y2="{h - pad_b}" class="tailline" '
               f'data-tip="5th percentile {tail:+.1f} {unit}"/>')
    for v in np.linspace(bins[0], bins[-1], 5):
        out.append(f'<text x="{X(v):.1f}" y="{h - 10}" class="tick" text-anchor="middle">{v:+.0f}</text>')
    out.append("</svg>")
    return "".join(out)


def calendar(m: pd.Series, regime_share: pd.Series, vmax: float) -> str:
    years = sorted({p.year for p in m.index})
    rows = ['<table class="cal"><thead><tr><th></th>' +
            "".join(f"<th>{mn}</th>" for mn in "JFMAMJJASOND") + "<th>Year</th></tr></thead><tbody>"]
    for y in years:
        cells = []
        for mo in range(1, 13):
            k = pd.Period(year=y, month=mo, freq="M")
            if k not in m.index:
                cells.append('<td class="na"></td>')
                continue
            v = m[k]
            r = regime_share.get(k, 0.0)
            traded = abs(v) > 1e-12
            if not traded:
                cells.append(f'<td class="flat{" on" if r > 0 else ""}" data-tip="{k}: no trades">·</td>')
                continue
            a = min(abs(v) / vmax, 1.0)
            col = "var(--pos)" if v > 0 else "var(--neg)"
            cells.append(f'<td class="{"on" if r > 0 else ""}" style="background: color-mix(in oklab, {col} '
                         f'{int(15 + 75 * a)}%, var(--surface))" data-tip="{k}: {100 * v:+.2f}% of capital; '
                         f'regime on {100 * r:.0f}% of sessions">{100 * v:+.1f}</td>')
        tot = m[[k for k in m.index if k.year == y]].sum()
        rows.append(f"<tr><th>{y}</th>{''.join(cells)}<td class='yr'>{100 * tot:+.1f}</td></tr>")
    rows.append("</tbody></table>")
    return "".join(rows)


# --- page -------------------------------------------------------------------
def pass_row(name):
    r = res[name]
    if name == "index_short":
        net = r["net_1bps"]
        mb = r["portfolio"]["cap_5x"]["monthly_bar"]
        beats = "n/a (control)"
    else:
        net = r["net_5bps"]
        mb = r["portfolio"]["risk_0.005"]["monthly_bar"]
        beats = "yes" if r["beats_all_controls"] else "no"
    ci = net["ci95_bps"]
    return (f"<tr><td>{esc(NAMES[name])}</td><td class='num'>{net['mean_bps']:+.1f}</td>"
            f"<td class='num'>[{ci[0]:+.1f}, {ci[1]:+.1f}]</td><td>{beats}</td>"
            f"<td class='num'>{r['episodes_positive']}</td><td class='num'>{r['drop_best_10_days_bps']:+.1f}</td>"
            f"<td class='num'>{100 * mb['share_positive']:.0f}% · {100 * mb['worst_month']:+.1f}% · {mb['longest_losing_streak']}</td>"
            f"<td><b>FAIL</b></td></tr>")


cum = {}
for name in ("B1_breakdown", "B2_failed_rally", "B6_gap_up_fade"):
    b = books[name]
    d = b.groupby("date")["pnl_rs"].sum().sort_index().cumsum() / P.CAPITAL * 100
    cum[name] = d.reindex(pd.to_datetime(dates[period]), method="ffill").fillna(0.0)
shade = pd.Series(on[period], index=pd.to_datetime(dates[period]))
idx_cum = (books["index_short"].groupby("date")["pnl_rs"].sum().sort_index().cumsum() / P.CAPITAL * 100
           ).reindex(shade.index, method="ffill").fillna(0.0)

calendars = {}
for name in NAMES:
    m = P.monthly(books[name], months)
    calendars[name] = calendar(m, on_month, vmax=0.06 if name != "index_short" else 0.15)

per_trade = {n: 1e4 * trades[n]["net_ret"].to_numpy() for n in ("B1_breakdown", "B2_failed_rally")}
per_month = {n: 100 * P.monthly(books[n], months)[P.monthly(books[n], months) != 0].to_numpy()
             for n in ("B1_breakdown", "B2_failed_rally")}

dec_rows = "".join(
    f"<tr><td>{esc(k.replace('_', ' '))}</td><td class='num'>{decomp['regime_on_dev'][k]:+.1f}</td>"
    f"<td class='num'>{decomp['regime_off_dev'][k]:+.1f}</td></tr>"
    for k in ("overnight", "open_to_0930", "session_0930_1510", "close_auction_tail"))

tabs = "".join(f'<button data-cal="{k}"{" aria-pressed=true" if k == "B1_breakdown" else ""}>{esc(v)}</button>'
               for k, v in NAMES.items())
cal_divs = "".join(f'<div class="calwrap" id="cal-{k}"{"" if k == "B1_breakdown" else " hidden"}>{v}</div>'
                   for k, v in calendars.items())

page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bear Intraday Results</title>
<style>
:root {{ color-scheme: light; --surface:#fcfcfb; --surface-2:#f3f2ef; --ink:#0b0b0b; --ink-2:#52514e;
  --grid:#e4e3df; --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --pos:#2a78d6; --neg:#e34948;
  --shade:rgba(120,118,110,.13); --tail:#e34948; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ color-scheme: dark;
  --surface:#1a1a19; --surface-2:#242422; --ink:#fff; --ink-2:#c3c2b7; --grid:#383835;
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --pos:#3987e5; --neg:#e66767; --shade:rgba(200,198,190,.10); --tail:#e66767; }} }}
:root[data-theme="dark"] {{ color-scheme: dark; --surface:#1a1a19; --surface-2:#242422; --ink:#fff; --ink-2:#c3c2b7;
  --grid:#383835; --s1:#3987e5; --s2:#d95926; --s3:#199e70; --pos:#3987e5; --neg:#e66767; --shade:rgba(200,198,190,.10); --tail:#e66767; }}
body {{ margin:0; background:var(--surface); color:var(--ink); font:15px/1.5 system-ui, -apple-system, sans-serif; }}
main {{ max-width:960px; margin:0 auto; padding:24px 16px 64px; }}
h1 {{ font-size:24px; margin:0 0 4px; }} h2 {{ font-size:18px; margin:36px 0 8px; }}
p.sub, .note {{ color:var(--ink-2); }} .note {{ font-size:13px; }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:12px; margin:16px 0; }}
.tile {{ background:var(--surface-2); border-radius:8px; padding:12px 14px; }}
.tile b {{ display:block; font-size:20px; }} .tile span {{ color:var(--ink-2); font-size:13px; }}
.scroll {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-size:13px; }}
th, td {{ padding:6px 8px; border-bottom:1px solid var(--grid); text-align:left; }}
td.num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
.chart {{ width:100%; height:auto; display:block; }}
.chart .grid {{ stroke:var(--grid); }} .chart .zero {{ stroke:var(--ink-2); stroke-width:1; }}
.chart .tick, .chart .lbl {{ fill:var(--ink-2); font-size:11px; }} .chart .lbl {{ fill:var(--ink); }}
.chart .shade {{ fill:var(--shade); }} .chart .hit {{ fill:transparent; }}
.chart .bar {{ fill:var(--s1); }} .chart .bar.tail {{ fill:var(--tail); }}
.chart .tailline {{ stroke:var(--tail); stroke-dasharray:3 3; }}
.legend {{ display:flex; gap:16px; flex-wrap:wrap; font-size:13px; color:var(--ink-2); margin:4px 0; }}
.legend i {{ display:inline-block; width:14px; height:3px; vertical-align:middle; margin-right:6px; border-radius:2px; }}
.two {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:16px; }}
.cal td {{ text-align:center; font-variant-numeric:tabular-nums; font-size:12px; padding:5px 2px; min-width:34px; }}
.cal td.on {{ box-shadow: inset 0 -3px 0 var(--ink-2); }} .cal td.flat {{ color:var(--ink-2); }}
.cal td.yr {{ font-weight:600; }}
.tabs {{ display:flex; gap:6px; flex-wrap:wrap; margin:8px 0; }}
.tabs button {{ font:inherit; font-size:13px; padding:4px 10px; border-radius:6px; border:1px solid var(--grid);
  background:var(--surface); color:var(--ink); cursor:pointer; }}
.tabs button[aria-pressed=true] {{ background:var(--ink); color:var(--surface); }}
#tip {{ position:fixed; pointer-events:none; background:var(--ink); color:var(--surface); font-size:12px;
  padding:4px 8px; border-radius:4px; display:none; max-width:320px; z-index:9; }}
</style></head><body><main>
<h1>Bear-market intraday: shorts and puts</h1>
<p class="sub">Development period 2017-04 → 2022-12, regime rule B (breadth). The holdout (2023-01 →) was not run.
Not investment advice; no order path exists.</p>
<div class="tiles">
 <div class="tile"><b>0 of 4</b><span>rules pass the development bar</span></div>
 <div class="tile"><b>−6.2 bps</b><span>gross per day, shorting NIFTY 09:30→15:10 on every regime day</span></div>
 <div class="tile"><b>+15–18 bps</b><span>shortlist vs random names, same trigger (CI clear of 0), still ≈ 0 after costs</span></div>
 <div class="tile"><b>Untested</b><span>put track: Dhan token expired, intraday option history not yet fetched</span></div>
</div>

<h2>Pass bar</h2>
<div class="scroll"><table><thead><tr><th>Rule</th><th>Net bps/trade</th><th>95% CI</th><th>Beats all controls</th>
<th>Episodes +</th><th>Drop best 10 d</th><th>Months + · worst · streak</th><th>Verdict</th></tr></thead>
<tbody>{''.join(pass_row(n) for n in NAMES)}</tbody></table></div>
<p class="note">Stocks at 5 bps/side and ₹5 L per trade; index short at 1 bp/side, real lot size. Bar: ≥70% traded months positive,
worst month ≥ −4%, ≤ 2 losing months in a row. *B6 is contaminated (gap-fade family has seen the holdout).</p>

<h2>Monthly calendar, % of ₹5 L</h2>
<div class="tabs" role="group">{tabs}</div>{cal_divs}
<p class="note">Stocks: 0.5% risk per trade with the pre-registered limits. Index short: 1 lot. Underlined cells: the regime was on
for part of the month. “·” = no trades (flat, not a win).</p>

<h2>Equity, % of capital, with regime-on sessions shaded</h2>
<div class="legend"><span><i style="background:var(--s1)"></i>B1 breakdown</span><span><i style="background:var(--s2)"></i>B2 failed rally</span>
<span><i style="background:var(--s3)"></i>B6 gap-up fade*</span><span><i style="background:var(--shade);height:10px"></i>regime B on</span></div>
{line_chart(cum, shade)}
<h2>The key control: index short, 1 lot, % of capital</h2>
{line_chart({"B1_breakdown": idx_cum}, shade).replace(esc(NAMES["B1_breakdown"]), "Index short")}
<p class="note">Its own chart because its scale differs; drawn in slot 1 colour. At the 5× cap (not drawn) it loses 135% of capital.</p>

<h2>Per-trade net, bps</h2>
<p class="note">Left tail in red; dashed line = 5th percentile. Values beyond ±400 are piled into the end bins.</p>
<div class="two">
<div><p class="note">B1 breakdown, {len(per_trade['B1_breakdown'])} trades; 5th pct {np.quantile(per_trade['B1_breakdown'], .05):+.0f}</p>{hist(per_trade['B1_breakdown'], np.linspace(-400, 400, 33), label='B1 per trade')}</div>
<div><p class="note">B2 failed rally, {len(per_trade['B2_failed_rally'])} trades; 5th pct {np.quantile(per_trade['B2_failed_rally'], .05):+.0f}</p>{hist(per_trade['B2_failed_rally'], np.linspace(-400, 400, 33), label='B2 per trade')}</div>
</div>
<h2>Per traded month, % of capital</h2>
<div class="two">
<div><p class="note">B1, {len(per_month['B1_breakdown'])} traded months; 5th pct {np.quantile(per_month['B1_breakdown'], .05):+.1f}%</p>{hist(per_month['B1_breakdown'], np.linspace(-6, 8, 15), unit='%', label='B1 per month')}</div>
<div><p class="note">B2, {len(per_month['B2_failed_rally'])} traded months; 5th pct {np.quantile(per_month['B2_failed_rally'], .05):+.1f}%</p>{hist(per_month['B2_failed_rally'], np.linspace(-6, 8, 15), unit='%', label='B2 per month')}</div>
</div>

<h2>Put vs future</h2>
<p><b>Untested.</b> Intraday option prices were not obtained (FEASIBILITY.md). What the futures data already says about the window a
put would be held in: NIFTY spot, mean bps per day, development.</p>
<div class="scroll"><table><thead><tr><th>Leg</th><th>Regime on (364 d)</th><th>Regime off (1,055 d)</th></tr></thead>
<tbody>{dec_rows}</tbody></table></div>
<p class="note">On bear days the fall is in the first 15 minutes, before the 09:30 decision; the held session drifts up. A put adds theta and
pays the rich side of the skew on top of that.</p>
</main><div id="tip"></div>
<script>
const tip = document.getElementById('tip');
document.addEventListener('mousemove', e => {{
  const t = e.target.closest('[data-tip]');
  if (!t) {{ tip.style.display = 'none'; return; }}
  tip.textContent = t.getAttribute('data-tip'); tip.style.display = 'block';
  const x = Math.min(e.clientX + 12, window.innerWidth - tip.offsetWidth - 8);
  tip.style.left = x + 'px'; tip.style.top = (e.clientY + 14) + 'px';
}});
document.querySelectorAll('.tabs button').forEach(b => b.addEventListener('click', () => {{
  document.querySelectorAll('.tabs button').forEach(x => x.setAttribute('aria-pressed', x === b));
  document.querySelectorAll('.calwrap').forEach(d => d.hidden = d.id !== 'cal-' + b.dataset.cal);
}}));
</script></body></html>"""
(OUT / "bear_dashboard.html").write_text(page)
print("wrote", OUT / "bear_dashboard.html", len(page) // 1024, "KB")
