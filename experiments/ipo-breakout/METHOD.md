# How the detector works, and the bugs worth remembering

`RESEARCH.md` is what the setup IS. This is how it was turned into arithmetic,
what was measured, and — at the end — every mistake that cost real time, because
those recur and the next person should not pay for them twice.

---

## 1. Shape of the code

```
ipolib/
  listings.py    the study population: Dhan's first-bar dates joined to an
                 IPO calendar, so a demerger is not counted as an IPO
  lockins.py     India's statutory unlock calendar, as a function of the
                 issue's own date -- two regime breaks inside the sample
  ipo_base.py    the detector: hard gates, a weighted score, forming/breakout
  synth.py       drawn bases with known truth, plus six negative controls
  evaluate.py    recall, precision, signal precision, false-alarm rate
  panel.py       the cross-section: forward returns and the two baselines
  trades.py      signal -> trade: entry, stop, trail, exit
scripts/
  build_universe.py     the Dhan pull (the only step that talks to a broker API)
  fetch_ipo_calendar.py which listings were IPOs, and the issue price
  run_synthetic_eval.py accuracy against known truth
  sweep.py              one threshold at a time
  scan_real.py          the real listings, causally, plus the funnel
  analyse_signals.py    excess over two baselines, block bootstrap
  lockin_study.py       the causal, non-price test
  exit_grid.py          which exit, measured in both currencies
  ipo_backtest.py       cash-accounted portfolio and the dashboard
  run_all.py            all of it, in order
```

`patlib/` is **imported from `pattern-detection/`, not forked**: `bars`,
`indicators`, `base.Detection` and `synth._assemble` are domain-neutral and are
used verbatim. `scripts/_bootstrap.py` puts both experiment roots on `sys.path`.
The dashboard's HTML/JS and `summarise()` are likewise imported from
`pattern-detection/scripts/vcp_backtest.py` rather than copied, so a fix to
either lands in both.

---

## 2. Measuring the listing bar instead of assuming it

`RESEARCH.md` §4.2 says the listing-day band is ±5% (issue ≤ ₹250 crore) or
±20% (above it), measured from the pre-open equilibrium price. That claim is
sourced but the *frequency* with which it binds is not, and the frequency is
what decides whether "break above the listing-day high" is a supply level or an
arithmetic fact.

So `listings.listing_bar_stats()` measures it: for every matched listing, the
listing bar's high and low as a percentage of its open, whether the close sits
exactly on a 5% or 20% band, and its volume against the median of the following
twenty sessions. `README.md` §2 reports the result.

The one worked example that motivated it: **Paras Defence**, listed 1 October
2021, issue ₹170.8 crore, so ±5%. Dhan's first bar is open 234.50, high 246.23,
close 246.23 — and 246.23 / 234.50 = 1.0500 exactly. The close equals the high
because the stock was frozen at the circuit. That bar's high is the rulebook.

---

## 3. The detector

### 3.1 Hard gates, then a weighted score

A **gate** is a structural impossibility: below `effective_min_history` there
are not enough bars for a base to exist; a pivot outside `left_high_window` is
not a left-side high; a base outside `[min_base_len, max_base_len]` is not that
pattern; depth past `max_depth_pct` exceeds the pattern's own tolerance; a base
whose pivot was already cleared by an earlier close has already broken out.

A **score** is how textbook the rest of it is: depth inside the normal band,
volume dry-up through the base, range tightening towards the highs, the base
low not arriving at the very end, a typical length. Weighted, reported 0..1, and
deliberately *not* used to gate — the VCP work found detector scores correlated
+0.02 with forward return, and this project re-tests that rather than assuming
it carries over.

One criterion moved from score to gate and the benchmark is why: **breakout
volume**. See §6.2.

### 3.2 `state`, never a bare boolean

`forming` means a base exists and price has not cleared the pivot; `breakout`
means this bar's close cleared it. Collapsing the two into a boolean throws away
the only warning the pattern gives.

**And then the real data made `forming` almost worthless.** A base "forms" on
99% of usable listings — any early high followed by any pullback satisfies the
structure — so `forming` carries close to no information and only `breakout`
does. That is reported in `README.md` §3 rather than quietly dropped, because it
is a fact about the pattern, not about the code.

### 3.3 Causality, stated as invariants

`detect(bars, t)` sees `bars[0..t]`. Two windows must close **strictly before**
`t`:

- **the pivot**, or the breakout bar is inside its own reference and no close
  can ever exceed it;
- **the volume baseline**, or the confirming expansion is averaged into the
  thing it is being compared against.

The IPO base is structurally immune to the first, because its pivot is the
*left-side* high — formed early, by definition in the past. That is a reason to
implement the published rule rather than a plausible variant of it.

### 3.4 Entry is the next open, not the signal close

The signal is known only once the breakout bar has closed. Entering at that
close is lookahead worth about a day of the move, and on a breakout bar that is
the most expensive day there is. `trades.build_trade` enters at `open[t+1]`.

---

## 4. The synthetic benchmark

150 drawn bases across five lengths (8–30 bars) and five depths (5–47% as
measured), plus 36 harder ones with a late shakeout, against 108 negative
controls in six families:

| Control | What it tests |
|---|---|
| `gap_and_bleed` | the classic IPO failure: pops, then bleeds. Has a left-side high and no base. |
| `flat_drift` | lists and goes nowhere |
| `random_walk` | the honest null |
| `expanding_base` | the right outline with the volume story **inverted** |
| `base_that_fails` | a real base that never clears its pivot — `forming` is correct, `breakout` is an error |
| `deep_collapse` | runs, then falls past the depth limit and stays there |

**`base_that_fails` is not special-cased.** A `forming` detection on it is the
right answer, but exempting it from the false-positive count would be marking
one's own homework. It is counted like every other negative and then broken out
in the per-control table, where the forming/breakout split is visible. Signal
precision isolates the error that matters.

**The generator is validated, not trusted.** Every drawn positive is checked
against its own truth dict before the benchmark is handed back, and
`build_benchmark` raises rather than returning a sample that fails its own
specification. See §6.1 for why.

---

## 5. Measuring the real thing

### 5.1 Two baselines, and which is softer

For a signal on date D, the market baseline is the mean forward return of every
other symbol with data on D; the cohort baseline restricts that to other IPOs
listed within six months either side. Both are in `ipolib/panel.py`.

The direction matters and is stated next to every table: Ritter's result is that
IPOs *underperform* as a class, so **the cohort baseline is the lower bar, not
the higher one**. Beating other IPOs of the same vintage is the more relevant
comparison and the weaker claim at the same time.

### 5.2 Block bootstrap by calendar month

Forward windows overlap — a signal on Monday and one on Tuesday share 19 of
their 20 forward sessions — so a naive t-statistic counts the same market move
many times. `panel.block_bootstrap` resamples whole calendar months, keeping
signals that shared a market inside the same draw, and reports the interval plus
the share of resamples at or below zero.

### 5.3 The lock-in study needs a placebo and a prediction

"Returns are poor around day 30" proves nothing on its own: IPOs may simply
drift in their second month. So `lockin_study.py` runs the identical measurement
at four offsets where no statutory unlock falls, and the unlock rows are only
interesting insofar as they differ from those.

It also makes a falsifiable prediction from the mechanism. The rules changed on
1 April 2022: before it the whole anchor tranche unlocked at day 30, after it
half at 30 and half at 90. If the effect is supply, the day-30 effect should be
larger in the earlier regime. A pooled average cannot be refused by the data;
this can.

### 5.4 The portfolio simulation is the arbiter, not the per-trade table

`exit_grid.py` reports every exit rule in **both** currencies — R and rupees —
because under fixed-notional sizing they disagree, and a table in R alone picks
the wrong rule. But neither currency prices **exposure**: "stop only" earns far
more per trade than any trail while holding for 142 sessions instead of 17, and
those are not the same bet. Only a cash-accounted book with a finite number of
slots can compare them, which is what `ipo_backtest.py` is for.

---

## 6. The bugs that cost the most time

### 6.1 The generator was wrong, and it said so this time

`pattern-detection/METHOD.md` records a generator that was wrong three times
with the detector blamed each time. So this one validates every drawn positive
against its own truth dict — and on the **first run it failed 28 of 150**:

```
AssertionError: the GENERATOR is wrong, not the detector -- 28 of 150 drawn
positives fail their own truth dict:
  d12_l8:  breakout bar does not close above the pivot
  d20_l8:  an earlier close in the base already cleared the pivot
  ...
```

Cause: the close path is drawn first and the intrabar range added afterwards, so
a noise draw could put the pivot's *intrabar high* above the breakout bar's
*close*. The sample was then labelled `ipo_base` and contained no breakout.

The fix was **not** to shrink the noise until it stopped happening — that hides
the class of bug rather than removing it. `synth._enforce` imposes the geometry
after assembly: nothing in the base closes above the pivot, the breakout bar
closes a fixed fraction above it, the tail is re-based onto that close. Depth is
then *measured* rather than asserted, and the benchmark reports cells by the
measured value.

**Cost without the validator: unknown, and that is the point.** It would have
surfaced as a recall number in the eighties that looked plausible enough to tune
against.

### 6.2 `min_history` silently excluded the bases the pattern is about

`min_history` was set to 30 on the reasoning in `RESEARCH.md` §7 that about
thirty sessions are needed before any criterion can be computed. That reasoning
was wrong, and wrong in the same **shape** as the VCP pivot bug: a window that
silently excludes the thing being looked for.

A 14-bar run into an 8-bar base breaks out around bar 23. With the floor at 30
the detector never looked. The benchmark said so precisely:

```
recall by drawn base length      misses, by the first rule that would explain them
   8 bars   5/30   16.7%            52  breakout before min_history
  12 bars  12/30   40.0%
  16 bars  21/30   70.0%
  22 bars  30/30  100.0%
  30 bars  30/30  100.0%
```

The floor is now **derived** — `1 + min_base_len`, the structural minimum — and
the volume baseline simply uses whatever sessions exist. Recall went 65.3% →
100%.

The lesson is not "30 was too big". It is that a history requirement justified
by the *longest* window any criterion might need will exclude every instance
where the criteria happen to fit in less.

### 6.3 Breakout volume was scored where it had to gate

With volume confirmation as a weighted score and `min_score = 0`, the detector
reported a breakout on **18 of 18 `expanding_base` controls** — the right
outline with the volume story inverted, which is the one negative shape that
geometry alone cannot catch.

O'Neil states the volume rule as a requirement, not a preference, and the
benchmark agreed. A breakout without its volume is now demoted to `forming`
rather than dropped, so the base stays on the watchlist and the caller can see
why it did not trigger. Signal precision went 64.7% → 98.8%.

### 6.4 An IPO setup with no deadline is not an IPO setup

"Above the listing-day high" fired on **WINDLAS in January 2024** — two years
and five months after its August 2021 listing. Nothing in the rule said when a
new issue stops being new, so the detector was happy to call a trade in a
two-year-old stock an IPO breakout, and it would have entered the comparison
between setups as one.

`max_age_sessions` (250, twelve months) now bounds all three setups. Note that
the synthetic benchmark could **never** have found this: its samples are 140
bars long and every drawn breakout happens early. Only real data has stocks that
sit below a level for years.

### 6.5 Setups A and B could not fire before they were allowed to

Having added a deadline, the first comparison run reported *zero* day-1 signals
for DMART — a stock that cleared its listing-day high on day 2. `first_breakout`
started every setup at `min_history`, and because an earlier close above the
pivot disqualifies every later bar, the setup that fires earliest was
disqualified by its own success. `earliest_bar()` now returns a
setup-appropriate index.

A silent zero is the worst failure mode: it looks like evidence.

### 6.6 The lock-in table printed one event as two

`promoter_minimum` appeared as both `+36m` and `+37m`, because the label was
derived from `(unlock_date - listing_date).days / 30` and a 36-month period
rounds either way depending on which months it crossed. Two rows, half the
sample each, neither significant. Labels now come from the statutory period.

`pre_ipo` and `promoter_excess` share a date and a duration, so they printed
identical rows — which reads as two pieces of evidence for one. Merged.

### 6.7 Range tightening was measured through the breakout bar

The handoff warns that volume dry-up must not be measured through the breakout,
because the breakout bar's volume is 2–3× by definition and including it reads
the confirming expansion as a failure to contract. The volume window here was
written correctly from the start (`volume[pivot_idx+1 : t]`).

The **range** window was not. `tighten` compared the last third of the base
against the first, and its late window ran to `t` inclusive — so the breakout
bar's range, which is wide precisely *because* it is a breakout, was averaged
into "has this base gone quiet?". Same trap, different series, and it survived
the synthetic benchmark because a drawn base tightens so hard that one wide bar
does not flip the check.

Both windows now end at `t-1` on a breakout. The base is bars
`pivot_idx+1 .. t-1`; the breakout bar is not part of the base it breaks out
of. DMART's `tighten` went from a value dominated by its breakout bar to 0.704
— a base whose range contracted 30%.

The general form is worth stating because it will recur: **any statistic that
describes the base must exclude the bar that ends it.**

### 6.8 Things that were *not* bugs, and were nearly treated as such

- **The 0.5-ATR stop floor does nothing here.** Rows for `min_stop_atr` 0.0 and
  0.5 are byte-identical, which looks like a broken sweep. It is not: the median
  IPO-base stop sits **18%** below entry, so a floor of half a daily range never
  binds. The VCP needed it because its bases are tight. This one does not.
- **`dryup_ratio` and `normal_depth_pct` are flat in the sweep.** Also not a
  bug: both are *scored*, not gated, and `min_score` is 0, so moving them
  changes the score and nothing else. Reported as a mechanical explanation, not
  as "volume dry-up does not matter".
- **19–21 symbols fail the Dhan pull with `DH-907`.** All are `*NSETEST*`
  exchange test scrips, which sort first alphabetically and are not instruments.
