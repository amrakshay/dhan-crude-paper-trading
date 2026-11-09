# BACKSPREADS — buy more wings than you sell

Asked: *can't we sell and buy hedge lots in such proportion that we minimise the
losses?*

**Yes, and it is the best risk result in this study.** At a 1:2 ratio the return
is essentially unchanged while every tail measure improves, and the statistical
significance nearly doubles.

Reproduce with `python scripts/backspreads.py`; output in `out/backspreads.txt`.

---

## What the structure does

Sell one put at the 16Δ strike, buy `ratio` puts at the 5Δ strike below it.

| where the index lands | what happens |
|---|---|
| above the short strike | keep the credit |
| between the strikes | the short bleeds, the longs are still worthless |
| **at the long strike** | **maximum loss — a point, not a plateau** |
| below the long strike | the longs outrun the short: the loss shrinks, then turns to profit |

A vertical's loss *plateaus* below the long strike. A backspread's loss *peaks*
there and then reverses. Worked at NIFTY 24,000, short 24000 / long 23500:

| settlement | 1:1 | 1:2 | 1:3 |
|---|---|---|---|
| 24,000 | +4,474 | +1,339 | −1,796 |
| 23,500 | **−33,026** | **−36,161** | **−39,296** |
| 23,000 | −33,026 | +1,339 | +35,704 |
| 22,000 | −33,026 | **+76,339** | **+185,704** |
| 21,000 | −33,026 | **+151,339** | **+335,704** |

**Ratios below 1.0 are refused.** Selling two and buying one leaves a naked short
below the long strike — undefined risk, out of scope, and `is_unbounded()` flags
it so it can never be sized or reported as though it were bounded.

---

## The sweep — 2016-01-01 → 2026-09-17

| ratio | n | total P&L | **return /yr** | **worst** | worst 1% | credit | win |
|---|---|---|---|---|---|---|---|
| **1.00** | 308 | +98,180 | **+26.00%** | **−48,812** | −38,224 | 2,721 | 94.2% |
| 1.25 | 306 | +79,481 | +21.04% | −45,472 | −33,740 | 2,384 | 93.8% |
| 1.50 | 313 | +92,497 | +24.13% | −41,924 | −33,370 | 2,146 | 94.9% |
| **2.00** | 313 | +97,787 | **+24.69%** | **−35,487** | **−30,316** | 1,575 | **96.2%** |
| 2.50 | 318 | +79,809 | +19.50% | **−28,796** | **−24,610** | 1,077 | 95.9% |
| 3.00 | 316 | **−12,667** | **−3.15%** | −42,779 | −31,232 | 594 | 91.1% |

**1:2 gives up 1.3 points of annual return and takes 27% off the worst trade.**

The credit column shows what is being paid for that: 2,721 → 1,575. The extra
wing costs 42% of the credit, every trade, whether or not it is needed.

**Ratio 3.0 breaks down** — the third wing costs more than it returns, the credit
falls to 594, and the strategy goes negative. That the surface has a *peak*
rather than improving monotonically is mildly reassuring: a result that got
better all the way to the extreme would more likely be a pricing artefact. It
also means the optimum is fitted, and 1:2 is one cell of six.

---

## In the crashes — the mechanism confirmed

Trades whose holding period contained a session that fell more than 3%:

| ratio | trades | **mean P&L** | **worst** | best |
|---|---|---|---|---|
| 1.00 | 12 | **−5,602** | **−45,278** | +4,954 |
| 1.50 | 13 | +986 | −17,187 | +20,369 |
| 2.00 | 13 | **+1,704** | **−17,748** | +12,146 |
| 2.50 | 12 | **+2,913** | **+304** | +9,404 |

**At 1:1 a crash costs an average of ₹5,602. At 1:2 it makes ₹1,704. At 1:2.5 it
never lost at all** across the twelve such trades.

That is the whole point of the structure and the data supports it. Caveat: those
are 12–13 trades. The sample contains 20 sessions worse than −3% and only 7 worse
than −5%, so this rests on very few observations.

---

## Where the losses went — and a prediction of mine that was wrong

Per-trade return on capital at risk:

| ratio | **p5** | p25 | median | p75 | p95 | **% losing >10%** |
|---|---|---|---|---|---|---|
| 1.00 | **−9.5%** | 3.2% | **3.9%** | 4.7% | 6.7% | **5.2%** |
| 1.50 | +0.2% | 2.3% | 3.0% | 3.7% | 5.6% | 4.5% |
| **2.00** | **+0.5%** | 1.6% | **2.1%** | 2.8% | 5.0% | **3.2%** |
| 2.50 | +0.1% | 0.9% | 1.4% | 2.0% | 4.8% | 2.8% |

**I predicted this would get worse and it got better.** The reasoning in the
script's own docstring was: a backspread relocates the maximum loss from a large
move to a moderate one, and moderate moves are commoner, so the loss moves
*towards* the observations.

That was wrong, and the reason is worth keeping. **The long strike is at 5 delta
— about 8.2% out of the money at 30 DTE.** The move that parks you exactly at the
maximum-loss point is not a moderate move at all; it is a rare one. So the peak
was relocated to a place the index almost never lands, and the 5th percentile
went from −9.5% to **+0.5%**.

What you actually pay is the **median**: 3.9% → 2.1%. You give up about half the
bread-and-butter return on the typical trade. The total return barely moves
because the losses shrink by more than the wins do.

---

## Significance

| ratio | observed | 5th–95th | **P(total < 0)** |
|---|---|---|---|
| 1.00 | +98,180 | [−109,189, +285,829] | **19.6%** |
| 1.50 | +92,497 | [−66,923, +243,503] | 16.5% |
| **2.00** | +97,787 | [−33,476, +219,960] | **10.8%** |
| 2.50 | +79,809 | [−32,259, +179,326] | 11.5% |
| 3.00 | −12,667 | [−147,453, +114,663] | 54.5% |

**At 1:2 the probability the true expectation is negative falls from 19.6% to
10.8%**, and the 5th percentile of the bootstrap improves from −₹109,189 to
−₹33,476. That is a real narrowing of the distribution, not just a better point
estimate.

Still not significance. 10.8% is one chance in nine.

---

## The bug this nearly hid

The first version of this table showed ratios 1.25–3.00 with **133 trades** each
against the vertical's 308, and apparently much better returns.

The cause: the profit target was a fraction of `max_profit`. For a vertical,
max_profit **is** the credit. For a backspread it is the payoff at spot near zero
— **₹840,000 against a ₹1,600 credit** — so "take profit at 50% of max profit"
was unreachable and every backspread silently ran to expiry (29.4 days) while the
vertical took profit at 50% (11.8 days).

I was comparing two different management rules and calling it a ratio comparison.

Fixed: the profit target is now a fraction of the **credit**, which is identical
for every non-backspread structure in the study — verified by regression, all
prior results reproduce exactly (condor 205/−₹95,632, bull put 308/+₹98,180,
bear call 297/−₹253,967).

---

## Verdict

**This is the answer to "minimise the losses", and it is the most useful thing
found in the whole study.**

At 1:2 against the plain vertical:

| | 1:1 | **1:2** |
|---|---|---|
| return on capital at risk | +26.00% | **+24.69%** |
| worst trade | −48,812 | **−35,487** |
| mean of worst 1% | −38,224 | **−30,316** |
| 5th percentile per trade | −9.5% | **+0.5%** |
| trades losing >10% | 5.2% | **3.2%** |
| mean P&L in a >3% crash | −5,602 | **+1,704** |
| P(total P&L < 0) | 19.6% | **10.8%** |

Better on every risk measure, 1.3 points worse on return.

**What it does not fix.** The same caveats as everything else here: one market,
one underlying, 2,643 sessions with 7 days worse than −5%; the crash evidence
rests on 12 trades; 1:2 is a fitted cell; and the post-2024 regime question in
`BULL_PUT.md` §2 applies unchanged. It also does nothing about the mechanism in
`WHY_BIG_LOSSES.md` — the loss zone is still narrower than a day's move, so a
stop still will not hold.

**It is a better-shaped bet, not a different game.**
