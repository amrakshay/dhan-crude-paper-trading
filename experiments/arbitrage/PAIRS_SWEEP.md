# Parameter sweeps: the shape, not the best cell

Nine axes, one at a time, everything else at the base configuration. Both
specifications swept.

```bash
.venv/bin/python scripts/sweep.py                              # levels -> out/sweep.json
.venv/bin/python scripts/sweep.py --log-base --tag sweep_log   # logs   -> out/sweep_log.json
```

Base cell: 756-bar formation, 126-bar trading windows, **no
multiple-comparison correction** (the corrected book has 7–10 trades in eight
years and there is nothing to sweep), 20 pairs per window, z 2.0 / 0.5 / 3.5,
holding cap 4 × half-life, ₹2 lakh gross per pair on ₹10 lakh, futures rate
card, collateral earning the policy rate.

**Nothing below is a recommendation.** The best cell of a grid run on one
sample is the luckiest cell, and quoting it is the same error as quoting the
strongest pair out of 180,000 tests. A sweep is for the shape.

---

## 0. The headline: the two specifications behave oppositely

| | cells | positive excess | CI excludes zero |
|---|---:|---:|---:|
| **price levels** | 37 | **1** | 4 — all of them **negative** |
| **log prices** | 37 | **10** | **0** |

On levels, essentially everything loses, and the four cells whose confidence
intervals actually resolve all resolve *against* the strategy. On logs, a
quarter of the cells are positive and not one of them resolves at all.

| axis | levels: best | | logs: best | |
|---|---|---:|---|---:|
| entry `z_in` | 2.5 | −3.39% | **1.0** | **+2.93%** |
| exit `z_out` | 1.0 | −3.49% | 1.0 | −0.24% |
| stop `z_stop` | **none** | **+0.50%** | 4.5 | **+0.32%** |
| holding cap | 8 × HL | −4.40% | **1 × HL** | **+0.27%** |
| pairs per window | 5 | −2.49% | **80** | **+3.38%** |
| correction | BH | −0.02% | **Bonferroni** | **+0.31%** |
| rate card | futures | −4.64% | futures | −0.32% |
| collateral yield | 1.0 | −4.64% | 1.0 | −0.32% |
| max half-life | 10 | −1.32% | **10** | **+0.52%** |

### The reversal is the interesting part

On **levels**, every knob that reduces activity improves the result: fewer
pairs, a wider stop, a tighter half-life filter. That is what you see when the
positions are noise and the only thing they reliably do is pay costs.

On **logs**, the knobs that *increase* activity improve it: entry at 1.0 sigma
rather than 2.0 (a 54.7% win rate against 36.1%), 80 pairs per window rather
than 20, a holding cap of one half-life rather than four. That is what you see
when there is a small genuine edge and more independent bets average it out of
the noise.

The two shapes are qualitatively different, and the difference matches
`PAIRS_SELECTION.md`: log prices score 0.95× against the placebo's 0.58× and
yield six FDR survivors; levels score 0.81× and yield one.

**None of this makes `z_in = 1.0` or `top_k = 80` a recommendation.** Two
specifications, nine axes each — 74 configurations — and the best has a
confidence interval straddling zero. Picking it would be exactly the error
this experiment measures at the pair level, committed at the strategy level.

---

## 1. Pairs per window: monotone, and in opposite directions

| pairs | **levels** trades / excess / maxDD | **logs** trades / excess / maxDD |
|---:|---|---|
| 5 | 312 / −2.49% / −5.6% | 201 / **+0.34%** / −7.9% |
| 10 | 577 / −2.99% / −11.7% | 373 / −0.45% / −10.9% |
| 20 | 1,004 / −4.64% / −28.6% | 687 / −0.32% / −16.7% |
| 40 | 1,308 / −10.24% / −49.8% | 857 / **+2.27%** / −18.1% |
| 80 | 1,285 / −11.27% / −50.6% | 855 / **+3.38%** / −16.0% |

On levels the result degrades steeply and the drawdown multiplies by nine.
If the ranked list carried information, diluting it would degrade things
gradually; it collapses, which is what a list ordered by luck looks like.

On logs it *improves* with depth. Note the trade counts stop rising after 40
pairs — the book is capacity-constrained at ten open positions, so what
changes past that point is which pairs get the slots, not how many trades
happen. Deeper selection filling the same ten slots produced a better book,
which is the opposite of the levels result and hard to explain as noise
alone.

## 2. The stop is the rule that hurts most on levels — and it is kept

| `z_stop` | **levels** trades / win / excess | **logs** trades / win / excess |
|---:|---|---|
| 2.5 | 1,042 / 25.2% / −4.94% | 850 / 23.9% / −2.32% |
| 3.0 | 1,016 / 29.3% / −3.26% | 798 / 30.2% / −1.06% |
| 3.5 | 1,004 / 31.6% / −4.64% | 687 / 36.1% / −0.32% |
| 4.5 | 814 / 38.9% / −2.48% | 585 / 40.9% / **+0.32%** |
| **none** | 521 / **52.4%** / **+0.50%** | 475 / 47.8% / −0.80% |

On levels, removing the stop entirely is the only positive cell in the whole
sweep, and it takes the win rate from 31.6% to 52.4%.

**The stop is kept anyway**, and the reason is methodological rather than
empirical. On data containing no genuine pairs, *any* stop looks like a pure
cost: every stop-out is noise that would have wandered back, so removing the
stop converts realised losses into unrealised ones and lets the window-end
exit tidy up. On data containing genuine pairs, the stop is the only thing
between the book and the one spread that never comes back — a leg taken over
overnight, a demerger, a fraud. **The panel contains no such events, by
construction** (`FEASIBILITY.md` §5), so this sweep cannot see the thing the
stop exists for.

Choosing an exit rule on a sample stripped of the disaster it protects against
is choosing on noise. Note also that the log sweep disagrees — there, removing
the stop is the *worst* cell on the axis.

## 3. The exit band wants to be further from the mean

| `z_out` | levels excess | logs excess |
|---:|---:|---:|
| −0.5 (past the mean) | −6.50% | −0.74% |
| 0.0 (at the mean) | −4.58% | −0.67% |
| 0.5 | −4.64% | −0.32% |
| 1.0 | −3.49% | −0.24% |

Monotone on both, and in the same direction: the earlier you take the money,
the better. The classic "exit at z = −0.5 after a long entry" — hold through
the mean and out the other side — is the worst cell on both. On a spread that
reverts, the last half-sigma is the slowest part of the move; on one that
does not, it never arrives.

## 4. Faster pairs are better pairs, on both

| `max_half_life` | levels trades / excess | logs trades / excess |
|---:|---|---|
| 10 bars | 263 / −1.32% | 151 / **+0.52%** |
| 20 | 973 / −3.78% | 680 / −0.58% |
| 30 | 1,004 / −4.64% | 687 / −0.32% |
| 60 | 1,007 / −5.05% | 686 / −0.18% |

Consistent with the power measurement: at a 756-bar formation window the test
has full power out to a ten-bar half-life and 81% at twenty
(`NEGATIVE_CONTROLS.md` §2). Pairs it reports as slow are disproportionately
pairs it has misidentified, so restricting to fast ones restricts to the ones
the test was competent to judge.

## 5. Costs: the cash segment is disqualifying

| rate card | levels excess | logs excess | logs: costs vs gross |
|---|---:|---:|---:|
| equity futures | −4.64% | −0.32% | 132% |
| equity intraday | −5.14% | −0.56% | 164% |
| **equity delivery** | **−10.66%** | **−3.10%** | **476%** |

Switching the same trades from the futures card to cash delivery costs
**6.0 points a year** on levels and **2.8** on logs. Delivery STT at 0.1% per
side, on two legs, both directions, is most of it. The delivery cell is one of
only four in either sweep whose confidence interval excludes zero — and it
excludes it on the losing side.

The cost ratios also show the problem in its purest form. At `z_out = −0.5` on
logs, costs are **315%** of gross P&L; on the delivery card, **476%**. These
are not strategies with expensive execution. They are strategies whose entire
gross return is a rounding error beside the charges.

## 6. Idle cash is 5.5 points of the headline

| `collateral_yield` | levels excess | logs excess |
|---:|---:|---:|
| 0.0 (cash earns nothing) | −11.83% | −5.86% |
| 0.5 | −8.19% | −3.13% |
| 1.0 (cash and margin earn the policy rate) | −4.64% | −0.32% |

Separated out because it is an accounting choice, not a strategy result. The
book carries about 1.2× capital in gross futures exposure but sits flat much
of the time, so what happens to unused cash moves the reported CAGR by more
than five points. The base case credits it at the policy rate — realistic for
an Indian futures book, where margin can be met with pledged liquid-fund or
T-bill units — and that is what makes the *excess* figure mean the trading
contribution and nothing else.

Both zero-yield cells resolve: their confidence intervals exclude zero, on the
losing side.

## 7. Entry threshold and holding cap

On **levels** neither has a readable shape. `z_in` runs −2.48%, −3.84%,
−4.64%, −3.39%, −4.93% from 1.0 to 3.0 — no trend, and the "best" cell sits
between two worse ones, which is what a flat noisy surface looks like. The
holding cap is flat from 2 × upward, and the 8 × and 99 × cells are identical
because the 60-bar absolute ceiling binds first.

On **logs** `z_in` is strongly monotone — +2.93%, +1.52%, −0.32%, −2.48%,
−2.66% — and the win rate tracks it exactly, 54.7% down to 23.0%. A monotone
response across five cells is more than a flat surface has, and it is the
single most suggestive result in either sweep. It is still not significant:
the confidence interval at `z_in = 1.0` straddles zero.

## 8. Position size levers the edge, it does not create one

| gross per pair | trades | costs vs gross | excess | max DD |
|---:|---:|---:|---:|---:|
| ₹50,000 | 687 | 183% | −0.20% | −2.2% |
| ₹1,00,000 | 687 | 180% | −0.36% | −7.1% |
| ₹2,00,000 | 687 | 132% | −0.32% | −16.7% |
| ₹5,00,000 | 681 | **72%** | **+0.40%** | **−41.3%** |

Worth reading carefully, because the temptation is to call the last row an
improvement. Cost efficiency genuinely does improve — flat brokerage
amortises, and it is 59% of all charges at the base size (`COSTS.md` §3). But
the excess return moves six tenths of a point while the drawdown grows
eighteen-fold, because the capital is fixed at ₹10 lakh: raising gross per
pair is raising leverage.

The finding that survives is the constraint, not the opportunity: small
positions pay roughly three times the cost rate of large ones, so "more pairs,
smaller" is not free diversification.

## 9. The axis with the clearest answer

| correction | levels: pairs / trades / excess | logs: pairs / trades / excess |
|---|---|---|
| none (nominal 5%) | 320 / 1,004 / −4.64% | 320 / 687 / −0.32% |
| Benjamini-Hochberg q=5% | **1 / 7 / −0.02%** | **5 / 10 / +0.27%** |
| Bonferroni α=5% | 1 / 7 / −0.02% | 1 / 3 / +0.31% |

Applying the correction the 179,673 hypothesis tests demand takes the levels
book from 1,004 trades to 7 and the log book from 687 to 10, and both land
within a third of a point of the risk-free rate.

That is not a parameter choice. It is the experiment answering its own
question: **once you account for how many pairs you looked at, there is
almost nothing left to trade**, and a book that obeys the arithmetic holds
cash — which over this period cost nothing and, on levels, saved 4.6 points a
year.
