# How it works, and the bugs worth remembering

`RESEARCH.md` is where the ideas come from; `FEASIBILITY.md` is what the data
supports; `README.md` has the results. This file is the machinery, and the
four mistakes that were made building it — all four found by a control rather
than by reading the code.

---

## 1. The order things were built in

Costs first, then statistics, then selection, then the book. Deliberately.

A market-neutral spread is often worth under 1% and is paid for with two
round trips, so the cost model decides the answer. Building the strategy
first and the cost model afterwards means discovering the costs at the point
where it is expensive to believe them. `COSTS.md` was finished, validated
against a published broker calculator and against longhand arithmetic, before
a single pair was screened.

---

## 2. The critical values are simulated, not looked up

Published ADF and Engle-Granger tables are asymptotic and assume a fixed lag
order. The procedure actually run here selects its lag by AIC on a sample of
a specific finite length, and the null distribution of *that* statistic is
not the one in the table.

So `arblib/stats.py` simulates it: 200,000 draws of the exact statistic under
the exact null, cached to disk (`scripts/build_nulls.py`, about ten minutes
across ten cores). Measured against the published asymptotic values the
simulated ones are consistently **more negative** — at a 756-bar window the
Engle-Granger 5% point is −3.57 against a tabulated −3.34 — which is the
expected finite-sample direction. Reading the table would have made the
screen anti-conservative, which over 200,000 hypothesis tests is the wrong
way to be wrong.

It also fixes the p-value **resolution** problem. A 20,000-draw null cannot
report a p-value below 5×10⁻⁵, and Benjamini-Hochberg's threshold for the
strongest of 16,000 pairs is 3×10⁻⁶ — below the floor, so the correction
would have been reading an artefact of the simulation. 200,000 draws move the
floor to 5×10⁻⁶ and the audit reports how many survivors sit on it.

---

## 3. The four bugs

### 3.1 Taking the better of two directions doubles the false-positive rate

Engle-Granger is not symmetric: regressing `y` on `x` and `x` on `y` give
different statistics on a finite sample. The screen takes the stronger of the
two, which is what the literature does — and which is a **selection step**
that the p-value does not know about.

Scored against the one-direction null, the procedure rejected **11.0% of
independent random-walk pairs at a nominal 5%**. Exactly double, which is
what taking a minimum of two correlated statistics should do.

The fix is not a correction factor. It is to simulate the null of the
procedure that is actually run — `null_distribution("eg_best", ...)` takes the
minimum of both directions on each draw. That shifts the 5% critical value
from −3.39 to −3.58, and the measured size returns to 5.8%, 5.2% and 5.8% on
the three negative controls.

**The general lesson is the expensive one.** Any step that picks the best of
several options belongs inside the null. A screen that tries two
specifications, or three formation lengths, or both raw and log prices, and
reports the best, has to be calibrated against a null that does the same.

### 3.2 AIC compared across models fitted on different samples

The lag-order search fitted each candidate lag on its own maximal sample, so
a longer lag was scored on fewer observations. An information criterion
computed on different numbers of rows is not comparable between models.

The symptom was quiet: the statistic correlated 0.987 with `statsmodels`'
`adfuller` and differed by as much as **0.72**, which at the 5% critical
value is the difference between a pair and no pair. Fitting every lag to the
same `n - maxlag - 1` observations, then refitting the chosen lag on its own
full sample, brings agreement with statsmodels to correlation **1.000000** and
a maximum absolute difference of **0.00000**.

`statsmodels` is a development dependency only (`requirements-dev.txt`);
`arblib` does not import it at runtime.

### 3.3 The dependent and independent legs were swapped

`engle_granger_best(u, v)` regresses `u` on `v` and reports `flipped=False`.
`screen()` read that backwards, so for every pair the fitted `beta` and
`alpha` belonged to one ordering and the prices to the other.

**The p-values were unaffected** — the test statistic is the same number
whichever leg is called which — so the multiple-comparison audit stayed
correct while every single trade was built from the wrong spread.

It was not found by reading the code. It was found by
`scripts/validate_backtest.py`, which plants twelve cointegrated pairs with a
known half-life in a synthetic panel and checks the whole pipeline end to
end. The engine **found the planted pairs and then lost money on them** —
which is not a result any market can produce. Tracing one pair showed its
z-score had mean −3.2 and standard deviation 1.3 *over its own formation
window*, where it must have mean 0 and standard deviation 1 by construction.
That is not a standardisation error; it is a different series.

After the fix the same test gives a **92.9% win rate** and +4.0% CAGR on the
planted pairs, and still finds nothing on random walks.

**The lesson: a backtest that reports no edge is only informative if it would
have reported one.** That check is cheap and it is the difference between a
null result and a broken pipeline.

### 3.4 Neither leg was tested for its own unit root

Cointegration is a property of two **I(1)** series: it says a linear
combination of two individually non-stationary series is stationary. If one
leg is *already* stationary over the window — a share that traded sideways
for three years — then the residual of any regression on it is stationary
too, and the test fires on a pair with no long-run relationship, only two
flat lines.

This was found by the real-data placebo (§4). A panel of **scrambled** real
prices, in which no genuine relationship can exist, produced *more*
FDR-surviving "cointegrated" pairs than the real panel did. The excess was
sideways windows pairing with each other.

The screen now requires each leg to fail to reject a unit root at 10% before
any pair containing it is tested. The level is deliberately loose: a false
rejection costs a candidate, a false acceptance manufactures one.

---

## 4. The controls, in increasing order of what they prove

**Synthetic, with the answer known** (`arblib/synth.py`). Four generators:
genuinely cointegrated pairs with a known half-life and hedge ratio, two
independent random walks, two *drifting* independent random walks, and — the
hardest — two series sharing a slow common factor plus independent
idiosyncratic walks, which is what two sector peers actually look like.

**The correlation comparison, on the same series.** This is where the
distinction between correlation and cointegration stops being pedantry: on
the common-trend control, naive `|corr| > 0.8` fires on roughly a third of
pairs and `> 0.9` on a sixth, while the cointegration test correctly rejects
about 95% of them.

**Power against half-life, and against formation length.** Measured, not
assumed, because it sets the formation window. At 252 bars the test has 40%
power against a 10-bar half-life and 10% against a 20-bar one; at 756 bars it
has 100% and 81%. A one-year formation window does not find slow pairs — it
reports them as random walks.

**The real-data placebo** (`select_pairs.py --placebo-seed`). Every symbol's
formation window is replaced by a window of **its own** history from a
different, randomly chosen date. Each series keeps the properties that a
Gaussian random walk does not have — fat tails, volatility clustering, the
actual drift and price level of that name — and no two series can be
genuinely related any more, because they are no longer contemporaneous.

This is the control the synthetic ones cannot provide, and it is the number
the real screen has to beat. It found bug 3.4.

**The date-matched random-pair baseline** (`backtest_pairs.py`). Identical
universe, dates, sizing and costs, with the cointegration screen removed and
pairs drawn at random. It isolates what the screen contributes, as against
what the trading rules contribute.

---

## 5. Causality

- A pair is selected on a **formation window** and traded on a **disjoint,
  strictly later window**. Windows are non-overlapping and walk forward.
- `eligible()` and `screen()` take `end_idx` and index only `0..end_idx`.
- The z-score's `mu` and `sigma` are **frozen at formation** and never move.
  A trailing re-estimate lets a spread that is drifting apart redefine its own
  mean until it looks normal again, which hides exactly the structural break
  the stop exists to catch.
- **Fills are at the next open.** A signal read off the close of day `t` is
  executed at the open of `t+1`. Filling at the signal bar's own close is a
  one-bar lookahead, and for a mean-reversion strategy it is the worst kind —
  the signal fires precisely at the extreme, so the fill would always be at
  the best price of the move. The one exception is a position closed on the
  window's last bar, where there is no next open inside the window; those
  fills are at the close and the trade is flagged `window`.
- Forward-filling happens **inside the formation window only**, where a stale
  price biases the hedge ratio slightly and cannot manufacture a trade. The
  trading window is never filled: a missing bar there means no fill, which is
  the truth.
- Every position is closed at its window's last bar. A pair's selection
  expires with its window, and carrying it under a formation that has not been
  re-tested is how a backtest holds a broken spread to a happy ending.

---

## 6. Things the book refuses to do, and why

- **A negative hedge ratio is refused.** `beta < 0` puts both legs on the same
  side. That is not a hedge; it is a leveraged directional bet with a
  market-neutral label. How many are refused is reported.
- **A pair slower than `max_half_life` is refused.** Not because slow pairs
  cannot work, but because the test cannot distinguish a slow OU process from
  a random walk at this formation length (§4), so a "slow pair" is most often
  a pair that is not one.
- **Both legs must clear a turnover floor**, applied on the rolling median
  over the formation window, and the result's sensitivity to that floor is
  swept. A spread between a liquid and an illiquid name is not tradeable at
  the prices in the data.
- **A symbol whose formation or trading window spans an unadjusted corporate
  break is excluded** — the six dates in `arblib/data.EXTREME_MOVES`,
  established in `FEASIBILITY.md` §4. A demerger is a step change in the
  level, and a regression reads it as the most spectacular mean-reversion
  opportunity in the panel.
- **A symbol appears in at most one pair at a time.** Without that cap the
  screen returns the same two or three names paired with everything, and a
  book of twenty "diversified" pairs is one bet.

---

## 7. Units, and which currency a table is in

- **R** is a trade's net P&L divided by its gross notional at entry. Use it to
  compare trades of different sizes.
- **Rupees** are rupees, on the stated capital and the stated size per pair.
- **Gross exposure** is the sum of the absolute value of both legs. **Net
  exposure** is their signed sum, and it is not zero: the hedge ratio equalises
  *share counts* in the cointegrating relation, not rupee values. Both are
  reported, every day, and the mean ratio of one to the other is the honest
  measure of how market-neutral the book actually was.
- Costs are quoted as a percentage of **gross** notional unless stated
  otherwise, because that is the same denominator the spread's own return uses.
