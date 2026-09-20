# Negative controls: how often is the procedure fooled?

The half of the work that decides whether anything else here means anything.
Measuring how well a detector finds what is there is the easy and flattering
half; measuring how often it finds what is not there is the half the field
gets wrong, and for a screen over 200,000 candidate pairs it is the only
number that matters.

```bash
.venv/bin/python scripts/validate_stats.py --trials 400   # out/validate_stats.json
```

Formation window 504 bars, nominal level 5%, null simulated with 200,000
draws. Four generators (`arblib/synth.py`), three of which have no
cointegration in them at all.

---

## 1. Size, and the number that should alarm you

| generator | rejects at 5% | \|corr\| > 0.8 | \|corr\| > 0.9 | median \|corr\| |
|---|---:|---:|---:|---:|
| two independent random walks | **5.8%** | 9.2% | 2.0% | 0.395 |
| two independent walks **with drift** | **5.2%** | 31.5% | 10.5% | 0.657 |
| shared slow factor + independent walks | **5.8%** | 34.0% | 16.8% | 0.683 |
| genuinely cointegrated (half-life 10) | 94.8% | 100.0% | 98.5% | 0.974 |

**The left column is the test behaving.** It says "pair" about 5% of the time
when there is none, which is what a 5% level means — and it only does that
because the null was simulated from exactly this procedure rather than read
off a table (see §3).

**The right three columns are the warning.** Read row three, which is the
realistic one: two sector peers driven by the same sector move, each carrying
its own idiosyncratic random walk. No linear combination of them is
stationary — there is nothing to trade — and yet naive correlation exceeds
0.8 in **a third** of cases and 0.9 in **a sixth**. A screen built on
correlation would have selected them.

This is what the distinction between correlation and cointegration buys, and
it is why "these two stocks move together" is not a reason to trade a spread
between them.

## 2. Power, and what it costs

| true half-life (bars) | detected at 5% | median p |
|---:|---:|---:|
| 2 | 100.0% | 0.0000 |
| 5 | 100.0% | 0.0000 |
| 10 | 95.2% | 0.0045 |
| 20 | 40.0% | 0.0749 |
| 40 | 12.5% | 0.3024 |
| 80 | 8.0% | 0.4486 |
| 160 | 5.8% | 0.5088 |

At a two-year formation window the test loses the thread somewhere around a
twenty-bar half-life. Past forty bars its detection rate is indistinguishable
from its false-positive rate — it is not finding slow pairs and reporting
them weakly, it is not finding them at all.

**This is a statement about the window, not about the pairs.** So the window
is a design decision and was measured as one:

| formation window | hl = 5 | hl = 10 | hl = 20 | hl = 40 |
|---:|---:|---:|---:|---:|
| 252 bars (1 year) | 94% | 40% | 10% | 6% |
| 504 bars (2 years) | 100% | 96% | 34% | 14% |
| **756 bars (3 years)** | **100%** | **100%** | **81%** | 24% |
| 1008 bars (4 years) | 100% | 100% | 96% | 47% |
| 1260 bars (5 years) | 100% | 100% | 98% | 58% |

A one-year formation window — the most common choice in the retail literature
— has 40% power against a ten-bar half-life and none at all against a
twenty-bar one. It does not find slow pairs; it reports them as random walks.

**756 bars is the choice made here**, as the shortest window with usable power
against a twenty-bar half-life. It costs universe: a symbol needs three years
of history *before* the trading window to be eligible, which is part of why
only ~120–190 of the 210 F&O names are screenable on any given date.

## 3. Why the critical values are simulated

| test | level | simulated (n=504) | published asymptotic | gap |
|---|---:|---:|---:|---:|
| Engle-Granger residual | 5% | −3.389 | −3.34 | −0.049 |
| ADF with constant | 5% | −2.891 | −2.86 | −0.031 |
| **EG, better of two directions** | **5%** | **−3.583** | *no table exists* | **−0.194** |

The first two rows are the ordinary finite-sample correction: at n=504 with an
AIC-chosen lag, the tabulated asymptotic value rejects slightly too easily.

**The third row is the one that mattered.** Engle-Granger is not symmetric, so
the screen takes the stronger of the two regression directions — which is a
selection step, and one the p-value does not know about. Scored against the
one-direction table the procedure rejected **11.0%** of independent
random-walk pairs at a nominal 5%. Exactly double, which is what taking a
minimum of two correlated statistics does.

Simulating the null of the procedure that is actually run fixes it, and the
measured size returns to the 5.2–5.8% in §1.

The general form of this is the expensive lesson: **any step that picks the
best of several options belongs inside the null.**

## 4. Recovery of the two numbers the strategy trades on

400 synthetic pairs with random true hedge ratios (0.4–3.0) and half-lives
(4–30), restricted to those the test detected.

| | median error | 90th percentile \|error\| |
|---|---:|---:|
| hedge ratio | **+0.11%** | 15.1% |
| half-life | **−24.6%** | 51.0% |

The hedge ratio is estimated essentially without bias, which is the
super-consistency that makes the crude OLS first stage acceptable.

The half-life is **biased low by about a quarter** — an OU process observed
through a finite window looks faster than it is. Since the holding cap is set
as a multiple of the estimated half-life, the cap is systematically too short.
That is the safe direction (positions are cut early rather than held hoping),
but it is a bias and the sweep on the cap multiple exists partly because of
it.

## 5. Johansen against Engle-Granger

| generator | EG rejects | Johansen trace r>0 | both | neither |
|---|---:|---:|---:|---:|
| cointegrated | 95.5% | 96.0% | 94.0% | 2.5% |
| independent random walks | 6.0% | **15.5%** | 5.5% | 84.0% |
| common trend | 5.5% | **14.0%** | 5.5% | 86.0% |

Johansen agrees with Engle-Granger on genuine pairs and is **badly over-sized
on the controls** — it rejects at 14–15% against a nominal 5%. The cause is
that its critical values here are the published asymptotic
Osterwald-Lenum ones, not simulated, so it has exactly the problem §3
describes and no correction.

This is why Johansen is used only as a **secondary opinion** on a pair
Engle-Granger has already selected, and never as a selector of its own.
Simulating a Johansen null for the exact procedure would fix it; it was not
done, because nothing in the strategy depends on the trace test's own
p-value, and saying so is more useful than quietly reporting a number that is
three times too generous.

## 6. The control the synthetic ones cannot provide

All of the above tests the procedure against **Gaussian** random walks. Real
equity prices are not Gaussian — fat tails, volatility clustering, drift that
varies by name — and a test calibrated on Gaussian walks could be mis-sized
on real ones in either direction.

So the screen is also run on a **real-data placebo**: every symbol's formation
window replaced by a window of *its own* history from a different, randomly
chosen date. Each series keeps everything that makes it a real share price,
and no two series can be genuinely related any more, because they are no
longer contemporaneous.

That control found a fourth bug — see `METHOD.md` §3.4 — and its results sit
alongside the real screen's in `PAIRS_SELECTION.md`, which is where the two
have to be read together.
