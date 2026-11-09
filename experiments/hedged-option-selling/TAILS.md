# TAILS — the left tail, tail dominance, and what size the evidence supports

This strategy family is the mirror of a breakout system: a high win rate with a
rare large loss. Every instinct built on the VCP work points the wrong way here,
so this document exists to put the tail in front of the return rather than
behind it.

Reproduce with `python scripts/tail_report.py`; full output in
`out/tail_report.txt`.

---

## 1. What the sample actually contained

| | |
|---|---|
| sessions | 2,642 |
| single-day falls over 3% | 20 |
| **single-day falls over 5%** | **7** |
| single-day rises over 3% | 18 |
| worst single session | **−12.98%** (2020-03-23) |
| worst 20-session stretch | **−37.01%** (to 2020-03-23) |

**Seven.** Every conclusion about the tail in this study rests on seven
observations out of 2,642 sessions.

That is not nothing — a backtest containing *no* 5% down day would be
undersampled to the point of being uninformative, and this one does contain
March 2020, which is as bad as Indian equities have been in the modern era. But
seven is few enough that the tail estimate is dominated by which particular seven
happened to fall inside the window.

**Every ruin probability below is therefore a lower bound.**

---

## 2. Tail dominance — the result is a handful of trades

Iron condor, 205 trades, total **−₹95,632**:

| excluding | total P&L | change |
|---|---|---|
| nothing | −95,632 | |
| the worst 1 trade | −52,547 | +43,086 |
| the worst 2 trades | −13,989 | +81,643 |
| **the worst 3 trades** | **+22,958** | **+118,590** |
| the worst 5 trades | +94,521 | +190,154 |
| the worst 10 trades | +235,319 | +330,951 |

**Three trades out of 205 are the difference between a losing strategy and a
winning one.** Ten of them swing the result by ₹330,951 on a book whose total is
−₹96k.

The other 95% of trades decide almost nothing. This is why a mean and a win rate
together are not a description of this strategy, and why the block bootstrap
resamples whole months rather than individual trades — losses here arrive
clustered, and an independent-trade bootstrap would report a confidence interval
far too tight.

---

## 3. The worst ten trades

See `out/tail_report.txt` for the current ten. The shape is unchanged across
every version of this study: losses cluster in 2020 and every one of them runs to
expiry rather than to a managed exit.

Three things to read off it.

**Every one ran to expiry.** Not one was closed by a managed exit. A profit-take
rule cannot save a loser — by construction it only ever fires on winners — so the
management rule shapes the right side of the distribution and leaves the left
side exactly as it was. Only a *stop* touches the left side, which is why the
stop variants in `SWEEP.md` §5 are the ones that move the worst trade.

**Some trades lose slightly MORE than 100% of their maximum loss.** That is not
an error: max loss is the payoff bound, and charges are paid on top of it. A
defined-risk structure's true worst case is max loss *plus* costs — the worst
observed is −100.52% of max loss.

**The `loss ÷ margin` column ranges to −667%.** That is the margin model's
message, and it is the subject of the next section.

---

## 4. Ruin risk — and why margin is the wrong thing to size on

| basis | mean | worst | 5th percentile |
|---|---|---|---|
| return on **margin** | −6.94% | **−666.88%** | −236.86% |
| return on **max loss** | −2.13% | **−100.52%** | −69.58% |

**The two bases disagree, and that is the point.** Sizing by margin is what a
broker's screen invites, and it is incoherent here: the margin model puts the
requirement at a median of **26% of the structure's own maximum loss**, so the
worst trade lost 5.6× what it was blocking. You cannot lose that and stay solvent
at any meaningful size.

Measured against maximum loss the worst trade is −100.92% — bounded by
construction, and max loss *is* the capital at risk for a defined-risk structure.

### Simulated over five years, resampling whole months

**Sized by margin** (fraction of equity committed as margin):

| at risk | median end | 5th pct | P(−50%) | **P(ruin)** |
|---|---|---|---|---|
| 2% | 0.86× | 0.60× | 1.1% | 0.0% |
| 5% | 0.62× | 0.24× | 43.6% | 2.7% |
| 10% | 0.28× | 0.03× | 81.0% | **39.5%** |
| 20% | 0.00× | 0.00× | 96.5% | **87.3%** |

**Sized by max loss** (fraction of equity at risk as maximum loss):

| at risk | median end | 5th pct | P(−50%) | **P(ruin)** |
|---|---|---|---|---|
| 2% | 0.96× | 0.88× | 0.0% | 0.0% |
| 5% | 0.90× | 0.71× | 0.0% | 0.0% |
| 10% | 0.80× | 0.50× | 7.1% | 0.0% |
| 20% | 0.60× | 0.22× | 48.1% | 3.4% |
| 30% | 0.40× | 0.09× | 72.6% | 22.2% |
| 50% | 0.14× | 0.01× | 90.3% | 59.7% |

"Ruin" is ending below 20% of the starting book over five years.

The max-loss table is the meaningful one, and it says what the headline says:
**on this sample the strategy has a negative expectancy after costs, so every
size loses money and larger sizes lose it faster.** The median path at 2% of
equity at risk still ends at 0.96×.

(The bull put spread, the one positive structure, is a different matter — see
`BULL_PUT.md`. This table is the condor, which loses.)

**Position sizing cannot rescue a negative edge.** It only decides how long the
book takes to get there. Sizing is the answer to "how much of a positive edge can
I safely take"; it is not an answer to "is there an edge".

---

## 5. How this differs from the VCP work, exactly

Both experiments sit in the same repository and the traps are mirror images.

| | VCP (`experiments/pattern-detection`) | hedged option selling |
|---|---|---|
| win rate | 22% | **84%** |
| skew of trade P&L | strongly positive | **−2.75** |
| where the result comes from | the best 5% of trades | **the worst 3 trades** |
| the seductive statistic | "look at that one 300% winner" | **"look at that 82% win rate"** |
| what a thin sample hides | how rare the winners are | **how bad the losers get** |
| the honest headline metric | expectancy | **ruin risk** |

The VCP work's instinct — tolerate a low win rate, let the tail run — is exactly
wrong here. The equivalent discipline is: **distrust the win rate, cap the tail,
and size on the bound rather than on the margin**.
