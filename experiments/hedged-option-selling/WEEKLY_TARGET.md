# WEEKLY_TARGET — "+2% a week or −2% and out", tested

The stated goal: **a minimum 2% return per week on capital employed, then exit;
or a 2% loss, then exit.**

Tested directly. Reproduce with `python scripts/weekly_target.py`; full output in
`out/weekly_target.txt`.

**Short answer: the stop never once delivered −2%, and the rule turned a
−3.3%/yr strategy into a −67.8%/yr one.** The details are more useful than the
headline, and one variant of it is the best thing tested at this holding period.

---

## 1. The arithmetic, before any backtest

| | per year | per week |
|---|---|---|
| **the target** | **~180%** | **2.00%** |
| best structure measured here (bull put, capital at risk) | 26.0% | 0.45% |
| risk-free (RBI repo) | 5.64% | 0.11% |

2% a week compounds to **180% a year** — roughly 4.4× the best result in this
study and 32× the risk-free rate.

**That gap is not an exit-rule problem.** An exit rule decides *when you take
what the market offered*; it cannot decide *what the market offers*. A +2% target
only fires in the weeks when +2% was there to take.

---

## 2. The stop never delivered −2%. Not once.

~7 DTE entries, exit at ±2% of capital at risk, bull put spread, 2016–2026:

| | |
|---|---|
| trades | 1,438 |
| mean holding period | **2.1 days** |
| exits | target 995, stop 428, expiry 15 |
| **stop fired** | **428 times** |
| **of those, worse than −2.5%** | **428 — 100%** |
| worse than −5% | 279 (65%) |
| **mean realised loss when stopped** | **−9.79%** |
| median realised loss | −6.89% |
| **worst** | **−48.63%** |

On the margin basis it is worse still: mean **−13.49%**, worst **−197.60%** — a
single trade losing nearly twice the capital it was supposedly risking.

### Why

A stop is an instruction, not a guarantee. The position is marked at the
**close**, and the next price you can actually transact at is the *next* close.
At ~7 DTE gamma is enormous, so between the mark that breaches −2% and the price
you get out at, the position has moved a long way further.

This is the same mechanism as the tail finding in `TAILS.md`: the sample contains
**7 sessions worse than −5%**, one at −12.98%. A 2% stop in front of a −12.98%
day is a 2% intention and a −12.98% outcome.

**A tighter stop does not fix this — it makes it worse**, because it fires more
often and each firing still slips by whatever the market gapped.

---

## 3. The rule made the strategy much worse

Bull put spread, ~7 DTE, full sample:

| variant | trades | total P&L | **R/risk /yr** | worst trade |
|---|---|---|---|---|
| plain: take 50% of max profit | 761 | −₹5,931 | **−3.31%** | −₹35,797 |
| **+2% / −2% of capital at risk** | 1,438 | −₹118,559 | **−67.75%** | −₹14,689 |
| +2% / −2% of margin | 1,649 | −₹145,991 | −85.12% | −₹14,689 |
| +2% target only, no stop | 933 | −₹78,996 | −44.31% | −₹35,797 |
| **−2% stop only, winners run** | **349** | **+₹9,163** | **+8.65%** | **−₹11,150** |
| +5% / −5% of capital at risk | 434 | −₹9,382 | −8.38% | −₹23,137 |

### The asymmetry is the problem, not the stop

Look at the two exit legs of the ±2% rule:

- the **target** fired 995 times at a mean of **+3.52%**
- the **stop** fired 428 times at a mean of **−9.79%**

995 × 3.52 = +3,502 against 428 × 9.79 = −4,190. The rule cuts winners small and
lets losers out large — **exactly backwards for a premium seller**, whose payoff
is already negatively skewed before any rule is applied. Capping the upside at 2%
removes the only thing paying for the tail.

It also nearly doubles the trade count (761 → 1,438), and each round trip is
**8 executed orders** of brokerage on a four-legged structure.

### The one variant that worked

**"−2% stop only, let winners run to expiry" is the best result at this holding
period**: +8.65% a year, and the **smallest worst trade of any variant tested**
(−₹11,150 against −₹35,797 for the plain version).

That is the defensible half of your rule. Stopping losers is sound risk control —
it is the same conclusion `SWEEP.md` §5 reaches about stops generally. Capping
winners at +2% is what destroys it.

Note the honest caveat: +8.65% against a risk-free 5.64% is a 3-point edge on 349
trades, which is not significance.

---

## 4. Was 2% a week even available?

Per calendar week, bull put spread with the ±2% rule, 470 weeks with a completed
trade:

| | |
|---|---|
| **weeks reaching +2% or better** | **240 (51%)** |
| weeks at −2% or worse | 173 (37%) |
| median weekly return | **+2.21%** |
| **mean weekly return** | **−1.56%** |
| worst week | **−100.19%** |

Two things to read off this.

**The target was available about half the time.** A *minimum* of 2% a week needs
essentially every week to clear it. 51% did.

**The median week hits the target and the average week loses money.** +2.21%
median against −1.56% mean is the entire shape of this strategy in two numbers:
the typical week looks like the plan is working, and the average week is
underwater because of the weeks that are not typical. A rule designed around the
typical week misprices the book — which is the same error the 82% win rate
invites, in a different costume.

---

## 5. What would have to be true

For "2% a week, floor" to be a plan rather than a target, one of these would have
to hold, and none of them does on this data:

1. **The market offers ~180% a year to this structure.** It offered 26% at best,
   and −29% since the SEBI rules changed (`BULL_PUT.md` §2).
2. **A stop can cap the loss at 2%.** It cannot on daily data, and the reason —
   overnight gaps — is not a data-resolution artefact. Intraday data would tighten
   the slippage but not remove it; the −12.98% session was a gap, not a drift.
3. **Cutting winners at 2% is cheap.** It is the most expensive thing in the test.

The nearest honest version of what you asked for is:

> **No profit cap. A stop on the loser. Size on maximum loss, not margin. And
> expect something in the region of 0.2–0.5% a week, not 2%.**

That is the "−2% stop only" row, and it clears the risk-free rate by about three
points on a sample too small to be sure of.

---

## 6. And the denominator matters more than the rule

The same rule on the same trades, only the definition of "capital employed"
changing:

| basis | total P&L | R/risk /yr | worst stop realisation |
|---|---|---|---|
| capital at risk (max loss) | −₹118,559 | −67.75% | −48.63% |
| margin | −₹145,991 | −85.12% | **−197.60%** |

Margin runs at a **median 26% of maximum loss** (`MARGIN.md` §4), so "2% of
margin" is a far smaller move than "2% of capital at risk" — the rule trades ~15%
more often and stops out harder.

**Set the target against maximum loss, never against margin.** A −197.60%
realisation on a 2% stop is what margin-based sizing looks like when the tail
arrives.
