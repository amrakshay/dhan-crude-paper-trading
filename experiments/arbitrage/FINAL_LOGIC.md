# The decided rule set — and the decision not to trade it

Everything below was selected by the measurements in `NEGATIVE_CONTROLS.md`,
`PAIRS_SELECTION.md` and `PAIRS_SWEEP.md`. The rules are specified in full,
because a specification you cannot read is not a result. **Read §5 before
using any of it**: the conclusion of this experiment is that this rule set
should not be traded, and the rules are written down so that the claim can be
checked rather than taken on trust.

---

## 1. Universe

At every formation date, a symbol is eligible only if all of the following
hold, using bars up to and including the formation date and no later:

- **NSE_EQ, and F&O-eligible.** 210 of the 500 panel names. Not a preference —
  an overnight short in Indian cash equity is not permitted, so the short leg
  must be a single-stock future, which exists only for these
  (`FEASIBILITY.md` §3).
- **756 bars (three years) of history**, with at most 2% of the window missing.
- **Median close ≥ ₹30** over the window.
- **Median daily turnover ≥ ₹5 crore** over the window, computed as
  `close × volume`.
- **No unadjusted corporate break** in the window — the six dates in
  `arblib/data.EXTREME_MOVES`.
- **The symbol is itself I(1)**: an ADF test with a constant must FAIL to
  reject a unit root at 10%. A leg that is already stationary makes any
  regression on it produce a stationary residual, so the pair test fires on
  two flat lines with no relationship. Omitting this was a bug
  (`METHOD.md` §3.4).

This yields 110–190 symbols per date, rising over the sample as more names
acquire three years of history.

## 2. Selection

- **Engle-Granger on LOG prices**, both directions, taking the stronger.
  Levels were screened and traded too and did materially worse on every
  measure — 0.81× the chance rate against the placebo's 0.58×, one FDR
  survivor against six, and gross P&L negative before costs against positive.
  Choosing logs on that evidence is a selection over two specifications made
  on this data, and is discounted accordingly in §5.
- **The p-value is read off a null simulated from that exact procedure**,
  200,000 draws — including the better-of-two-directions selection, which
  doubles the size if ignored (`METHOD.md` §3.1).
- **Benjamini-Hochberg at q = 5%** across every pair tested on that date.
  Bonferroni is also computed; on this data they agree, because the number of
  discoveries is one.
- **At most one pair per symbol**, greedily, strongest first — otherwise the
  screen returns the same few names paired with everything and a book of
  twenty pairs is one bet.
- **Cap at 20 pairs** per formation window.

## 3. Trading

Windows are disjoint: a pair selected on the formation window ending at `t` is
traded over the next 126 bars (about six months) and nothing else.

| | |
|---|---|
| spread | `s = ln P_b − β·ln P_a − α`, β and α frozen at formation |
| z-score | `z = (s − μ) / σ`, μ and σ **frozen at formation** |
| entry | first bar with `2.0 ≤ \|z\| < 3.5`; long the spread if `z < 0`, short it if `z > 0` |
| fill | the **next day's open**, on both legs |
| sizing | ₹2,00,000 gross per pair. A log spread is a fixed VALUE ratio: `V_b = gross/(1+β)`, `q_b = V_b/P_b`, `q_a = β·V_b/P_a`. (A level spread is a fixed SHARE ratio: `q_b = gross/(P_b + β·P_a)`, `q_a = β·q_b`.) |
| rebalancing | **none** — the value ratio drifts out of hedge as prices move. Over an 18-bar mean hold the drift is small; not modelling it is an optimism, stated in §6. |
| refused | `β ≤ 0` (both legs the same side — a directional bet, not a hedge); `β < 0.05`; estimated half-life outside (0, 30] bars |
| exit — target | `\|z\| ≤ 0.5` |
| exit — stop | `\|z\| ≥ 3.5` in the direction of entry |
| exit — cap | `4 × estimated half-life` bars, floored at 3 and capped at 60 |
| exit — window | every position is closed on the window's last bar |
| margin | 20% of gross notional per pair, on the futures legs |
| cash | unencumbered cash and posted margin earn the policy rate |

### The exit spec, in full

There are **three** exits and they answer different questions.

**Target** (`|z| ≤ 0.5`). The spread has reverted to within half a standard
deviation of its formation mean. Not to the mean itself: the sweep shows exit
bands *further* from the mean do better here, and closing at zero exactly
pays for the last half-sigma of a move that may not arrive.

**Stop** (`|z| ≥ 3.5`, same side as entry). A spread that keeps widening has
changed regime rather than become a better bargain. The stop is the only
thing standing between a pairs book and an unbounded left tail, and it is
also — measured — the rule that does the most damage on this data: removing
it entirely takes the win rate from 31.6% to 52.4% and improves excess return
by three points. **It is kept anyway.** On data where the spreads do not
revert, any stop looks like a cost; on data where one leg is taken over
overnight, its absence is the whole loss. Choosing an exit rule on a sample
that contains no genuine pairs is choosing on noise.

**Holding cap** (`4 × half-life`). A spread with a ten-day half-life that has
not reverted in forty days is not slow, it is broken. The multiple is four
because the estimated half-life is biased low by about a quarter
(`NEGATIVE_CONTROLS.md` §4), so four estimated half-lives is roughly three
true ones.

### Retirement

A pair leaves the book by three mechanisms, all of which the backtest
actually executes:

1. it stops out, or hits the holding cap;
2. its trading window ends and every position is closed;
3. **it is not re-selected** at the next formation date, because it no longer
   passes the screen. This is the one that handles a slow structural break —
   a pair that quietly stops cointegrating simply never comes back.

## 4. What it produced

Trading 2018-07-18 to 2026-09-04, ₹10 lakh capital, futures rate card.

| | **logs, nominal 5%** | **logs, FDR** | levels, nominal 5% | levels, FDR | random baseline |
|---|---:|---:|---:|---:|---:|
| pairs selected | 320 | **6** | 320 | 1 | 320 random |
| trades | 687 | **10** | 1,004 | 7 | 83 |
| win rate | 36.1% | 40.0% | 31.6% | 28.6% | 36.1% |
| CAGR | 5.27% | 5.86% | 0.96% | 5.58% | 6.46% |
| risk-free | 5.59% | 5.59% | 5.59% | 5.59% | 5.59% |
| **excess** | **−0.32%** | **+0.27%** | **−4.64%** | −0.02% | +0.87% |
| max drawdown | −16.7% | −1.3% | −28.6% | −1.3% | −4.6% |
| gross P&L | **+₹68,647** | +₹27,591 | **−₹2,73,420** | +₹2,790 | +₹1,01,392 |
| costs | **₹90,585** | ₹1,378 | ₹1,34,309 | ₹903 | ₹10,938 |
| net P&L | −₹21,938 | +₹26,213 | −₹4,07,729 | +₹1,887 | +₹90,454 |
| gross return on exposure | **+0.69%/yr** | +19.31%/yr | −2.72%/yr | +4.82%/yr | +7.53%/yr |
| net return on exposure | **−0.22%/yr** | +18.35%/yr | −4.05%/yr | +3.26%/yr | +6.72%/yr |
| deployment | 1.22× capital | 0.02× | 1.24× | 0.01× | 0.17× |

Four things to read off it.

**On logs, the edge is real and the costs are bigger than it.** Gross return
on deployed exposure is **+0.69%/yr**; costs are **0.91%/yr**; net is
**−0.22%/yr**. That is the textbook outcome for retail relative-value trading
and the one `RESEARCH.md` predicted before anything was measured — the costs
were built first precisely so that this could be stated as a measurement
rather than an excuse.

**On levels there is not even that.** Gross P&L is negative on spreads
selected for mean reversion — no edge at all, with charges on top. The gap
between the two specifications is the single largest effect in the
experiment.

**The correctly corrected book barely trades.** FDR takes the log book to six
pairs and ten trades in eight years, for excess +0.27% with a bootstrap CI of
[−0.32%, +0.94%]. Its +18.35%/yr on exposure is the most attractive number in
the table and also the least trustworthy: ten trades.

**The random-pair baseline is not beaten.** +0.87% excess against the log
book's −0.32%. But its own trade bootstrap is +₹1,090 with a 95% CI of
[−₹1,480, +₹3,784], so the correct statement is that **neither has been shown
to work**, not that random selection is better.

## 5. So: do not trade this

Not because there is nothing there. Because what is there is smaller than what
it costs to reach, and is not separable from noise at the scale this screen
operates.

The log sweep's best cells — entry at 1.0 sigma (+2.93%, 54.7% win rate) and
80 pairs per window (+3.38%) — are the strongest argument against that, and
they are why this section is a judgement rather than an arithmetic. They are
the best of 74 configurations, neither resolves against zero, and taking them
at face value would be the pair-selection error repeated one level up. If
anything here deserves a second look on out-of-sample data, it is those two
cells, and the right way to look is a held-out period, not a re-read of this
one.

The case, in four steps:

1. **There is a population-level signal.** Against the real-data placebo the
   log screen passes 1.64× as often, on 15 of 16 formation dates,
   p = 0.0008 (`PAIRS_SELECTION.md` §3).
2. **It cannot be localised.** Six pairs survive FDR out of 172,912 tests.
   Roughly a third of the nominal passes are genuine and the p-values do not
   say which third.
3. **Traded, the gross edge is +0.69%/yr of deployed exposure and the costs
   are 0.91%/yr.** The strategy is 0.22 points a year short of free, before
   any of the frictions §6 lists as unmodelled — every one of which pushes the
   same way.
4. **Nothing survives its own confidence interval.** Not the log book, not the
   FDR book, not the random baseline, and not one of the 37 swept log
   configurations. The only cells in either sweep whose intervals resolve are
   four on the levels side, and all four resolve against the strategy.

The engine is not the problem: on a synthetic panel with planted pairs it
returns a **92.9% win rate** and +10.4% CAGR (`METHOD.md` §3.3). It finds
edges that exist.

The honest headline is that **retail EOD pairs trading on the NSE large-cap
universe earns approximately the risk-free rate minus its transaction costs**,
which is what theory predicts and what the cost model said before the strategy
was run.

## 6. What is still unproven

Ranked by how much it could change the answer.

1. **The whole result is conditional on the horizon.** Everything here is
   daily bars, a three-year formation window and a six-month trading window.
   Statistical arbitrage as practised is largely intraday, where the spread's
   half-life is measured in minutes and the competition is for speed. This
   experiment says nothing about that, and could not: the repository has
   daily bars.
2. **Survivorship.** The panel is today's index constituency held back to
   2015. For a pairs book the catastrophic case is the leg that stops
   trading, and by construction none are present. This flatters every number
   here and **cannot be fixed with this dataset**.
3. **The short leg is modelled, not measured.** No futures history exists here
   (`FEASIBILITY.md` §2), so the futures leg is priced off cash and charged at
   the F&O card. Basis noise, roll slippage and lot granularity are absent.
   They would make the result worse, not better, so the conclusion is safe in
   this direction — but the *magnitude* is not measured.
4. **No impact model.** Fills are at the open in unlimited size above a
   turnover floor. Again this flatters, and again the direction is safe.
5. **Only the levels specification was traded.** The log-price screen is run
   and reported (`PAIRS_SELECTION.md`), but the book was not re-run on it,
   because a log spread is a fixed *value* ratio that must be rebalanced to
   hold, which is a different strategy with a different cost profile.
6. **The exit rules were chosen on this data.** The sweeps show the shape
   rather than the best cell, and the synthetic benchmark is genuinely out of
   sample — but the bands were not selected on a held-out period. On a sample
   with no signal in it this matters less than usual, which is itself a
   slightly uncomfortable defence.
