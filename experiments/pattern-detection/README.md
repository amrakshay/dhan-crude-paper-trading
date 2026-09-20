# Detecting Cup-and-Handle, VCP and Triangles from candles alone

An experiment: take three chart patterns that are normally identified by
eye, turn their published definitions into arithmetic over OHLCV bars, and
measure honestly how well that works.

Nothing here is imported by the paper-trading application, nothing places or
simulates an order, and the app's database is opened read-only. It is
self-contained under `experiments/pattern-detection/`.

| File | What it is |
|---|---|
| `RESEARCH.md` | the patterns: how they work, their variations, every criterion with a source |
| `METHOD.md` | how the detectors work, and the bugs worth remembering |
| `README.md` | this file — results, and what they mean |
| `VCP_PREBREAKOUT.md` | entering a VCP *before* the breakout: can it be seen, and is it worth it |
| `VCP_EXITS.md` | exit rules — why a 9 EMA trail underperforms, and what beats it |
| `FINAL_LOGIC.md` | **the decided rule set**, with its portfolio result and what is still unproven |
| `VCP_RANKING.md` | does it matter which candidate gets the slot? (no) |
| `VCP_REGIME.md` | does an index 200-day regime filter help? (no — it hurts) |
| `VCP_LOSSES.md` | reducing the losing trades: what the losses are, and the one filter that helps |
| `patlib/` | the library |
| `scripts/` | benchmark, sweeps, real-data scan, plots |
| `out/` | every result quoted below |

```bash
cd experiments/pattern-detection
python3 -m venv .venv && .venv/bin/pip install numpy pandas matplotlib

.venv/bin/python scripts/run_synthetic_eval.py --tag mine        # accuracy
.venv/bin/python scripts/sweep.py --pattern cup_and_handle       # sensitivity
.venv/bin/python scripts/scan_real.py --limit 50 --step 5        # real data
.venv/bin/python scripts/analyse_signals.py --tag real_strict_D  # significance
.venv/bin/python scripts/run_all.py                              # everything (~20 min)
.venv/bin/python scripts/plot_samples.py --symbols INFY TITAN --pattern cup_and_handle
```

---

## 0. Findings at a glance

Detection, measured on a synthetic benchmark where the answer is known:

- **Cup-and-Handle** is the most *specific* pattern — 97% precision, ~1 false
  alarm per 1,000 bars scanned — and misses about a quarter of real ones.
- **VCP** is the best balanced at 89/89, and the only one whose criteria are
  hard to satisfy by accident.
- **Triangles are weak, and not because of the code.** Two converging lines
  occur constantly in noise. Bulkowski ranks the symmetrical triangle 36th of
  39; the detector agreeing is correct behaviour.
- **Every widely-published cup-and-handle example checked** — AAPL 2018-19,
  NVDA and POOL 2020 — is a 30-43% crash-and-V-recovery that fails O'Neil's
  own depth and roundness rules. A faithful detector finds none of them.

Trading, measured on 474 NSE symbols, 2016-2026:

- **VCP is the only pattern with an edge that survives a block bootstrap**
  (+1.07% excess over 20 sessions, CI clear of zero). Cup-and-handle
  underperformed; triangles were flat.
- **A VCP is visible ~5 sessions and 7% before its breakout**, and entering
  there has more than twice the risk-adjusted expectancy of waiting — but
  only 34% of complete bases ever reach the pivot.
- **A 9 EMA trail is too tight**; it caps the 95th-percentile trade at 2.0R
  where a 50 EMA reaches 5.5R. In a strategy where the top 5% of trades make
  more than 100% of the profit, clipping winners is fatal.
- **Detector scores predict nothing** (correlation +0.02 with forward
  return). They measure textbook conformity, which did not pay.
- **Two "obvious improvements" were tested and both failed**: ranking
  same-day candidates makes no reliable difference, and an index 200-day
  regime filter actively hurts (it does not even remove the losing years).
- **One filter helped**: requiring the stop to sit ≥0.5 ATR from entry. Win
  rate 20% → 27%, CAGR 19.8% → 21.8%, drawdown −21.0% → −17.1%.
- **The strategy loses to buy-and-hold on return** (22.9% CAGR) and wins by
  roughly 3× on risk-adjusted return. That is the honest claim.

The decided rule set is in [`FINAL_LOGIC.md`](FINAL_LOGIC.md); the backtest
dashboard is `out/vcp_dashboard.html`.

---

## 1. Headline: how accurate is it?

Measured on a synthetic benchmark of **333 drawn patterns and 330 negative
controls**, scanned causally (every other bar, using only bars up to that
point). Full definitions in §2.

### Strict profile — the textbook, as written

| Pattern | Recall | Precision | Signal precision | False alarms / 1,000 bar-scans |
|---|---|---|---|---|
| **Cup and Handle** | 77.1% | **97.4%** | **98.5%** | **1.1** |
| **VCP** | **88.9%** | 88.9% | 90.6% | 2.4 |
| **Triangle** (all kinds) | **95.6%** | 55.6% | 54.0% | 19.1 |

### Relaxed profile — how the patterns are labelled in practice

| Pattern | Recall | Precision | Signal precision | False alarms / 1,000 |
|---|---|---|---|---|
| Cup and Handle | 86.1% | 48.6% | 58.9% | 215.5 |
| VCP | 100.0% | 25.7% | 64.8% | 42.2 |
| Triangle | 100.0% | 40.4% | 29.9% | 111.0 |

**The two tables together are the main result.** Loosening a cup detector
from the textbook rules to the practitioner ones buys 9 percentage points of
recall and costs a **200-fold** increase in false alarms. The strictness of
O'Neil's criteria is not fussiness; it is the entire reason the pattern
means anything.

### What each pattern is actually good for

- **Cup and Handle** is the most *specific* pattern of the three. When the
  strict detector says cup, it is a cup — 97% precision, roughly one false
  alarm per thousand bars scanned. It misses about a quarter of real ones,
  which for a screener over 500 symbols is the right side of the trade.
- **VCP** is the best balanced: 89/89, and it is the only one whose
  criteria (a *sequence* of shrinking contractions, plus contracting volume
  and range) are hard to produce by accident.
- **Triangles are weak, and not because of the code.** Two converging
  trendlines occur constantly in noise: the detector fires on 52 of 55 random
  walks before tightening and 36 of 55 after. This is the same conclusion
  Bulkowski reached with real money — the symmetrical triangle ranks **36th
  of 39** patterns on upward breakouts, and the rising wedge is **last of 36**
  bearish patterns with a 51% break-even failure rate. A triangle should be
  read as weak evidence, and the detector reporting it as such is correct
  behaviour, not a defect.

---

## 2. What "recall" and "precision" mean here

They are not free-floating. Precisely:

- **Hit** — a detection of the right pattern, reported no later than the
  true breakout + 4 bars, whose start bar is within 35% of the pattern's
  length of the true start. Both halves matter: firing at the right time on
  the wrong geometry is luck, and firing on the right geometry three weeks
  late is a post-mortem.
- **Recall** — hits / drawn patterns.
- **False positive** — any detection of any of the three patterns on a
  **negative control**.
- **Precision** — hits / (hits + false positives).
- **Signal precision** — the same, restricted to detections in the
  `breakout` state: the ones that would have produced a trade. A `forming`
  detection that never breaks out costs a line on a watchlist, not money.
- **False alarms per 1,000 bar-scans** — because the binary per-sample rate
  above is harsh on its own: a negative sample is ~200 bars scanned at
  several zigzag resolutions, so it is thousands of chances to say yes. One
  yes in 200 bars of noise is a very different failure from a yes on every
  bar, and only the rate separates them.

### The negative controls

They matter more than the positives — a detector that says yes to everything
scores 100% recall. 330 samples, six kinds, all of which *look* like
something:

| Control | Why it is there |
|---|---|
| Random walk | the null hypothesis |
| Steady trend | no pattern, strong direction |
| **V-bottom** | the classic false positive for a cup (literally the cup generator with the roundness dialled out) |
| **Rectangle** | parallel, not converging: must not read as a triangle |
| **Expanding volatility** | a VCP run backwards |
| **Head and shoulders** | its left half genuinely resembles a cup |

### Why synthetic data, and what it cannot tell you

On a real chart nobody can say which bar the cup starts on, so recall and
precision are *undefined* without hand-labelling first. The synthetic
generator knows the answer because it drew it.

The honest limitation: **a synthetic benchmark measures whether the code
implements the definition, not whether the definition finds money.** Section
4 is the other half.

Three times during this project the *generator* was wrong and the detector
was being blamed — each is recorded in `patlib/synth.py`:

1. Noise was a cumulative random walk, which drifted 10-20% over a cup and
   silently tilted the right rim away from the left. Replaced with a
   stationary AR(1).
2. Handle depth was parameterised as a fraction of *price*, so a 14%-deep
   cup could be drawn with a 13% handle — which violates O'Neil's own
   "upper half of the cup" rule. Re-parameterised as a fraction of cup depth.
3. Intrabar range was constant across a tightening base, so the synthetic
   VCPs had no volatility contraction — the one thing the pattern is named
   for. Now scaled per bar.

If a benchmark is never wrong, it is probably not being read.

---

## 3. Sensitivity: is this tuned, or does it work?

`scripts/sweep.py` moves one threshold at a time. The full tables are in
`out/sweep_*.json`; the findings:

**Cup — tightening the rims is free.** Going from `rim_tolerance` 0.10 to
0.06 leaves recall at 76.4% and takes false alarms from 30.1 to 8.7 per
1,000. Level rims are what a real cup has by construction and what noise
does not. Raising `min_score` 0.70 → 0.78 costs 3.5pp of recall and takes
false alarms to 7.6.

**VCP — the "each contraction shallower" test is never the binding
constraint.** Across `tighten_ratio` from 0.75 to 0.95 every single number is
identical. What actually does the filtering is the volume dry-up and the
range contraction. That is worth knowing: the criterion everyone quotes when
describing a VCP is not the one carrying the weight.

**VCP — the full Trend Template is too strict to use as a gate.** Requiring
all 7 computable criteria takes benchmark recall to **zero**. It is a screen
for the market's few strongest names, not a property every good base has.
Six is the operating point.

**Triangle — recall is flat and precision is not.** Across every value of
every threshold tried, recall stays at ~99% while the false-alarm rate moves
by a factor of 2.3. The geometry is easy to find; finding it only when it
means something is the hard part. The chosen values are the low-false-alarm
end of each curve, taken because recall was not paying for them.

---

## 4. Real data

### 4.1 Public examples: the published cups are not cups

Four widely-cited cup-and-handle examples were checked against the strict
detector on data it was never tuned against (`scripts/run_public_eval.py`,
Yahoo Finance daily):

| Example | Claimed | What the candles say |
|---|---|---|
| AAPL 2018-19 | cup through H1 2019, breakout ~$215 | 39% decline into a **sharp V** on 2019-01-03, `u_vs_v` 0.46-0.48. The cited "handle" is a 20% drop. |
| NVDA 2019-20 | multi-month cup, short handle | the COVID crash: 7.9 → 4.5, **-43%**, V-shaped recovery |
| POOL 2020 | Feb high, late-Mar low, late-May breakout | -30% in four weeks, V bottom, straight back up |
| TATAMOTORS 2020-21 | cup Mar-Dec 2020 | no data (Yahoo 404) |

**None was detected, and none should have been.** All three are 30-43%
crash-and-V-recoveries. They fail O'Neil's depth band (12-33%) and fail the
roundness test outright. The chart is in `out/named_cases_raw.png` — they are
V-recoveries by eye as well as by arithmetic.

This is the single most useful thing the exercise turned up, and it cuts
both ways. It is evidence the detector is faithful to the books. It is also
a caution about the books' *examples*: a great deal of published
"cup-and-handle" illustration is a crash that recovered, relabelled after
the fact. The provenance caveat matters too — these came from secondary
write-ups found by search, not from O'Neil or IBD originals, so this is
evidence about how the pattern is popularly illustrated, not about what
O'Neil taught.

### 4.2 NSE data: does it find real ones?

The app's own `daily_bars` — 474 NSE symbols with sufficient history,
1.1M bars, 2015-07 to 2026-09 — scanned causally every 5 bars.

There are no labels here, so **no precision or recall is quoted**. Two things
are: what the detections look like when drawn, and what happened next.

**Drawn.** `out/samples/` holds the highest-scoring detections. INFY,
2021-04-12 to 2021-07-30, is textbook: a rounded 11% base from ₹1,478 down to
₹1,320 and back, a right rim marginally above the left, a genuine
low-volume handle drifting ₹1,590 → ₹1,533, a breakout on 2021-07-26 through
₹1,598, and a 10% run to ₹1,760. Nobody looking at that chart would call it
anything else.

**What happened next.** Every `breakout` signal, 2015-2026, against a
**date-matched baseline**: for each signal on date D, the average forward
return of every other symbol with data on D. Without that, a 4% average
20-day return means nothing in a market that returned 4% to everybody.
Returns are signed to the direction the pattern implies, so a descending
triangle that breaks down is scored as the short it is.

Strict profile, 20 sessions forward, 474 symbols, 195,850 bar-scans:

| Pattern | Signals | Mean excess | Median | t | Block-bootstrap 95% CI | Win rate |
|---|---|---|---|---|---|---|
| **VCP** | 1,398 | **+1.07%** | −0.75% | 3.36 | **[+0.35%, +1.83%]** | 47% |
| Triangle (all) | 1,626 | +0.17% | +0.43% | 0.71 | [−0.31%, +0.63%] | 52% |
| Cup and Handle | 573 | −0.83% | −1.74% | −2.04 | [−1.61%, +0.03%] | 40% |

At 10 sessions the picture is the same: VCP +0.87% [+0.36%, +1.40%], triangle
+0.04%, cup −0.65% [−1.20%, −0.08%].

The t-statistics are too generous — forward windows overlap within a symbol
and across symbols on the same day, so the observations are not independent.
The bootstrap resamples whole calendar months and keeps that clustering
intact. Where they disagree, the CI is the one to believe.

**Four things follow, and only one of them is comfortable.**

1. **VCP is the only pattern with an edge that survives.** +1.07% over 20
   sessions against a same-day baseline, CI clear of zero, at both horizons,
   and at **both profiles** (relaxed: +1.11%, CI [+0.70%, +1.49%], n=4,976).
   Robustness across a parameter change that quadruples the signal count is
   worth more than the point estimate.
2. **The win rate is 47%, and the mean is positive anyway.** The median VCP
   signal *loses* 0.75% relative to the market. The edge is entirely in the
   right tail — a minority of large winners. That is what a breakout strategy
   is, and it is invisible in any summary that reports only a hit rate.
3. **The cup-and-handle did not work on this data**, and at 10 sessions its
   underperformance is statistically significant. That is a strong claim
   against a pattern Bulkowski ranks 3rd of 39, so the caveats matter: his
   numbers are hand-picked perfect examples in US equities, this is every
   detection a screener produced on the Nifty 500 over a decade, and the
   samples are not comparable. What this does show is that *screening* for
   cups is not the same activity as *choosing* them.
4. **Loosening the definition destroys what little there is.** The relaxed
   cup profile produces 9,671 signals instead of 575 — 17 times as many —
   and their mean excess is +0.05% with a CI of [−0.23%, +0.31%]. That is
   not a weaker edge, it is the absence of one, measured precisely.

**The scores predict nothing.** The correlation between a detection's score
and its 20-day excess return is +0.007 (cup), +0.034 (triangle), +0.024
(VCP) — zero, three times over. The scores measure conformity to the
textbook, and conformity to the textbook did not pay. Splitting VCP at the
median score gives +1.25% for the better half against +0.92% for the worse,
which is the right direction and well inside the noise.

**Context that cuts against all of it**: NSE 2015-2026 was a long bull
market. Every bearish variant is fighting the tape — the descending
triangle's −1.57% [−2.87%, −0.35%] says shorting breakdowns lost money,
which in this sample is close to a tautology.

### 4.3 Entering before the breakout

The scan above counts only `breakout` signals. The detector also reports
`forming` — the pattern complete, the pivot not yet cleared — which is a real
pre-breakout signal because the zigzag's provisional pivot makes the final
contraction visible the day its low is made.

Measured over 3,188 forming VCPs: the median base is visible **5 sessions and
7% below** its breakout close, only **34% ever reach the pivot** before the
stop, and entering immediately returns **0.91R against 0.40R** for the same
bases entered at the breakout — but with a −1.00R median trade and 131% of
total profit in the top 5% of trades. Waiting for price to coil closer to the
pivot is worse than either. Full analysis, costs and gap risk in
[`VCP_PREBREAKOUT.md`](VCP_PREBREAKOUT.md).

### 4.4 Weekly bars

Same scan on weekly aggregates: 276 triangle, 69 VCP and 19 cup signals in
eleven years. The counts are too small to conclude anything, and they are
reported (`out/real_strict_W_summary.txt`) mainly to record that the
detectors run unchanged on a different timeframe — the duration bands are
the only thing that moves.

---

## 5. Honest limitations

- **Recall on real data is unknown**, and unknowable without hand-labelling.
  Everything in §1 is measured on drawn patterns.
- **The forward-return study is not a backtest.** No costs, no slippage, no
  position sizing, no survivorship correction — the 474 symbols are today's
  index members, so the sample is biased towards names that did well. The
  date-matched baseline removes the market's drift but not that.
- **The parameters were chosen on the same synthetic benchmark they are
  scored against.** The sweeps show the results are not knife-edge, and the
  NSE scan and the public examples are out-of-sample, but the headline
  numbers are in-sample and should be read as an upper bound.
- **Weekly-timeframe bands are converted, not independently sourced.** A
  trading week is 5 sessions, so O'Neil's 7-65 weeks becomes 35-325 daily
  bars. Real weekly bars are not identical to 5-day aggregates.
- **Not implemented**: double-bottom base, high tight flag, the "cheat"
  entry, Power Play. They are different geometry, not variations of these.
- **`dhanhq`, order placement and every broker endpoint remain untouched.**
  This directory is outside `backend/`, so the repository's safety scanners
  (`test_no_real_orders.py`, `test_outbound_hosts.py`) do not cover it — and
  it deliberately contains nothing for them to find.
