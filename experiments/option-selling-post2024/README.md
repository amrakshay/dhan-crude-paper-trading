# Option selling after SEBI's 2024-11-20 rules: different, or merely short?

Asked 2026-10-03, as "option 1" of `../profitable-traders/RESEARCH.md` §5.
Selling volatility is the best-evidenced retail-accessible style. The one test
of it here, `hedged-option-selling`'s NIFTY bull put spread, earned +26% a
year on capital at risk before 2024-11-20 and **−29% a year after it**, on 48
trades. The question it left open: did the new market break it, or is 48
trades just too few?

**Everything below is DESCRIPTIVE and uses data that has already been seen.**
The base rule (`hedged-option-selling/BULL_PUT.md`, 16-delta short, 5-delta
wing, ~30 DTE, close at 50% of max profit) is rerun **unchanged** through that
experiment's own engine. The bhavcopy cache is opened read-only. No rule was
changed and nothing was tuned. Reproduce with:

```bash
cd experiments/option-selling-post2024 && ../hedged-option-selling/.venv/bin/python scripts/post_break_trades.py
```

## Answer

**The post-2024 loss is consistent with bad luck: the trade itself did not get
worse. But the pre-2024 "edge" was never statistically established either, and
neither a backtest nor a forward paper run can establish it on any horizon
worth waiting for.**

**Recommendation: don't trade it, and don't build a forward journal for it.**
A forward run would take decades to tell this edge from zero. It would be
monitoring, not validation.

## 1. The trade's economics did not change at the break

`out/pre_post_economics.json`:

| | Before 2024-11-20 | After |
|---|---|---|
| Trades | 260 | 48 closed, plus 1 still open at the data's end (−₹28,507 marked) |
| Credit ÷ max loss (median) | 7.07% | 7.05% |
| Win rate needed to break even | 92.0% | 93.0% |
| **Actual win rate** | **94.6%** | **91.7%** (44/48) |
| Average win / average loss | +₹1,481 / −₹17,139 | +₹2,409 / −₹32,152 |
| Median IV of the short leg | 16.4% | 13.9% |
| Median days held | 9 | 12 |

The structure pays the same premium for the same risk after the break. What
changed is that **four expiries out of 48 went wrong**, and the losses are
larger in rupees because the index is higher and lots are bigger.

- The 48 closed trades total −₹26,269, matching `BULL_PUT.md`.
- Including the still-open trade, the total is −₹54,776.
- The five worst post-break trades:
  - 2026-02-10 → 2026-03-10, −₹48,812 (the March 2026 fall);
  - 2024-12-13, −₹32,300;
  - 2026-08-25, still open, −₹28,507;
  - 2025-02-05, −₹26,507;
  - 2025-12-23, −₹24,636.
- Dropping the worst two leaves the post-break total at **+₹26,335**.

## 2. Is 4 losses in 48 unusual? No.

`out/post_break_consistency.json`:

| Test | Result |
|---|---|
| Before 2024, share of trades that lost | 5.38% |
| P(at least 4 losses in 48 trades) at that rate (binomial) | **0.26** |
| Mean return on max loss: before / after | +0.75% / −2.05% per trade |
| P(48 trades drawn from the pre-2024 record average this badly or worse), bootstrap | **0.12** |

Nothing here says the regime broke. A 1-in-4 and a 1-in-8 event are ordinary.

## 3. And the pre-2024 edge was never established

| | Value |
|---|---|
| Return on max loss per trade, pre-2024 | mean **+0.75%**, SD **16.7%** |
| t-statistic over 260 trades | **0.72** |
| Trades needed to separate it from zero (5% two-sided, 80% power), on returns | **about 3,900** |
| ... on win rate alone (94.6% vs 92% break-even; ignores loss size, so optimistic) | about 761 |
| Trades per month at this rule's pace | 2.4 |
| **Forward time needed** | **about 26 years** (win rate) to **over 130 years** (returns) |

That squares with `hedged-option-selling`'s own caveats:
- its bootstrap gave the full-sample result a 20% chance of being a loss;
- removing three trades more than doubled it;
- the parameter surface was not a plateau.

The "+26% a year" headline is the annualised return on capital at risk of a
per-trade mean that is statistically indistinguishable from zero. The −29% is
the same mean, sampled 48 times.

**More concurrent positions don't fix this.** A new 30-day position opened every
week gives about 4× the trades, but the overlapping trades share the same
expiry outcomes, so the effective sample barely grows. **Shorter-dated entries
are no fix either:** 7 DTE was the worst cell in `SWEEP.md`.

## 4. What this means for "do what the profitable traders do"

Selling volatility is how institutions and the rare winning individuals earn
(`../profitable-traders/RESEARCH.md`). On this data, for a retail-sized,
defined-risk, end-of-day version, **the premium left after the wing, charges and
spreads is too small to see through its own tail risk.** That fits the SEBI
detail that "majorly option sellers" have the only positive *median* but lose
as a group: the median is the 9-in-10 small wins, and the mean is decided by
the tail.

Two kinds of option seller plausibly do capture the premium, and neither is
available here:
- **Scale and speed:** prop firms selling into retail demand intraday, with
  market-making economics. We have daily data and no queue.
- **Bearing undefined tail risk:** unhedged strangles. In the earlier study
  that was +₹134,865 with a worst trade of −₹135,030. The repo's own rule
  forbids that risk in the app, and it isn't a recommendation.

## What would change this verdict

Only a **much larger per-trade edge relative to its spread**, which means a
different mechanism. More data on the same rule won't do it. Any new idea would
have to be pre-registered and judged on data it hasn't seen. None is proposed
here: proposing one after this result would be the selection problem
`hedged-option-selling/SWEEP.md` §7 warns about.
