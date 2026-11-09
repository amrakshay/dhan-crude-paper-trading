# CALENDARS — sell the near expiry, buy expiry+3 further OTM

Asked directly: does a diagonal — **sell current-expiry CE/PE, buy a further-OTM
CE/PE three expiries out** — solve the tail problem, at 2–3% a month with the
lowest risk possible?

This is the structure `RESEARCH.md` §2 listed as *researched but not backtested*.
Three measurements were run to decide whether that exclusion still holds. Two of
them favour the idea. One is probably fatal, and it is not the one I expected.

> **Superseded in part.** This document was written before the multi-expiry
> engine existed. The structure has since been **built and backtested** —
> see `DIAGONALS.md` and `out/diagonals.txt`. Sections 1–4 below stand; section 5
> described what a test would need, and that test now exists.
>
> **Headline of the actual result:** the put diagonal returned **+25.4% a year**
> on capital at risk against the same-expiry vertical's **−42.8%** over
> 2024-07 → 2026-09, the mechanism in §2 was confirmed (worst trade **30.8%** of
> its own maximum loss against the vertical's **100.6%**) — and the sign flips to
> **−42.0%** at a slippage assumption well inside the plausible range for a leg
> this thin. The liquidity objection in §3 survived contact with the data.

---

## 0. First, the target is now plausible

| | per year |
|---|---|
| 2% a month | **26.8%** |
| 3% a month | 42.6% |
| best structure measured here (bull put, capital at risk) | **26.0%** |
| risk-free | 5.64% |

**2% a month is almost exactly what the best structure in this study delivered.**
That is a completely different proposition from 2% a week (180%/yr), which was
~7× beyond anything measured. 3% a month is still above it.

So the question is no longer "is this arithmetic possible" — it is "can the risk
be reduced without giving up the return", which is the right question.

---

## 1. Volatility term structure — NEUTRAL (better than expected)

The standard objection to selling near and buying far is that you sell cheap
volatility and buy expensive. On NIFTY that is not true.

75 sampled sessions since 2024-07, selling the 16-delta near call and buying the
5-delta call three expiries out:

| | median DTE | median IV |
|---|---|---|
| near expiry, 16-delta | 3 | **12.36%** |
| expiry +3, 5-delta | 24 | **12.19%** |

**Far minus near: +0.08 vol points**, with the far leg more expensive in 51% of
sessions — a coin flip.

And the structure is a **net credit in 84% of sessions** (median +5.65 per unit).
You are paid to put it on, most of the time.

This objection does not hold. Good.

---

## 2. Hedge quality in a crash — FAVOURS THE FAR LEG

The real appeal of the far-dated hedge: at the near leg's expiry the long still
holds time value, and in a volatility spike its vega is worth far more than a
near-dated wing's. Measured on the ten worst sessions in the sample — a ~5-delta
put bought the prior session, held one day:

| session | NIFTY | near-expiry wing | expiry+3 wing |
|---|---|---|---|
| 2020-03-23 | −12.98% | 4.9× | *no data* |
| 2020-03-12 | −8.30% | **71.9×** | *no data* |
| 2024-06-04 | −5.93% | 9.0× | **6.1×** |
| 2020-03-18 | −5.56% | **0.7×** | *no data* |
| 2022-02-24 | −4.78% | **57.5×** | **4.5×** |
| 2020-03-30 | −4.38% | **0.3×** | *no data* |
| 2020-04-01 | −4.00% | **0.0×** | *no data* |

The pattern is clear even at this sample size:

- **The near-dated wing is a lottery ticket.** It ranges from **0.0× to 71.9×**.
  On 2020-04-01 the index fell 4% and a 1-DTE wing expired **worthless** — the
  move was real but did not reach the strike in the time remaining. A hedge that
  pays nothing on a 4% down day is not a hedge on that day.
- **The far-dated wing is consistent.** It never goes to zero, because it always
  has time value and its vega rises when volatility does.

**For "lowest risk possible", that trade-off favours the far leg.** You give up
the occasional 70× payoff and you stop having days where the hedge simply fails.

**Caveat, and it is large: only 2 of the 10 worst sessions have expiry+3 data
at all.** The 2020 crash pre-dates NIFTY having four liquid expiries listed. Two
observations is not evidence; it is a direction.

---

## 3. Liquidity — PROBABLY FATAL

Executed-trade counts, NIFTY, first session of each quarter since 2024-07:

| expiry | median DTE | trades at 16-delta | trades at 5-delta | 10th pct at 5-delta |
|---|---|---|---|---|
| **current** | 3 | **446,419** | 285,492 | 99,383 |
| +1 | 10 | 16,603 | 12,297 | 1,798 |
| +2 | 17 | 435 | 629 | 153 |
| **+3** | 24 | **1,102** | 421 | **34** |

**The leg you must BUY trades roughly 400× less than the leg you sell.** At the
5-delta strike three expiries out, the 10th-percentile session shows **34
executed trades in the whole day**.

This matters more here than anywhere else in the study, for a reason the earlier
work already established: `SWEEP.md` §6 showed the base strategy's result is
highly sensitive to the assumed bid-ask, and `BULL_PUT.md` §3 showed an 8%
effective half-spread drops the one positive structure below the risk-free rate.

That sensitivity was measured on a wing trading **101** times a day. This leg
trades **34**. Whatever the true spread is on a contract that thin, it is not
small, and the net credit is a median of 5.65 points — a spread of a couple of
points on the far leg eats a third of it.

**This is the objection that decides it, and it is a cost objection, not a risk
objection.** The structure may well hedge better; you may not be able to afford
to put it on.

---

## 4. What the structure is, precisely

A note on the description, because the wording matters. "Sell CE **and buy PE**"
would not be a hedge — a long put does nothing about a short call, it is a
directional bet layered on a directional bet. The structure assessed here is the
sensible reading: **same option type, later expiry, further out of the money** —
a *diagonal*, not a calendar proper (a calendar uses the same strike).

Two consequences worth stating:

- **It is bounded, and bounded no worse than a vertical.** At the near expiry the
  worst case is roughly the strike gap minus the credit, and the far leg's
  retained time value makes it *better* than the equivalent vertical. The
  intuition that this is safer is correct as far as it goes.
- **When the near leg expires you are left holding a naked long.** That is
  defined risk, but it is a debit sitting on the book that must be re-sold
  against, and each re-sell is another round of brokerage on the thin leg. The
  strategy is really "buy a long-dated option and sell short-dated ones against
  it" — the economics are dominated by whether the sum of the credits exceeds the
  cost of the long, which is a different question from the one this study
  measured.

---

## 5. Why this is not backtested here, and what it would take

Two blockers, one soft and one hard.

**Soft — the engine assumes one expiry per position.** `Trade` carries a single
`expiry` and `_manage` loads a single chain. Supporting a diagonal means two
chains per position, a roll when the near leg expires, and a max-loss computation
that depends on the far leg's residual value. That is real work but it is
ordinary work.

**Hard — there is no defensible margin number for this structure.** And "2–3% a
month **on capital**" is a question about the denominator:

- The margin model (`MARGIN.md`) implements SPAN scan risk and ELM. It does
  **not** implement inter-month spread charges, which is exactly what governs a
  two-expiry position.
- SEBI **withdrew the calendar-spread benefit on expiry day for index contracts
  from 2025-02-10** (NCL FAQ Q33). That change lands inside the sample and
  directly targets this structure.
- Historical SPAN par files are not archived, so the exchange's actual number for
  any past day is unavailable to anyone.

A backtest could sidestep this by reporting **return on maximum loss** rather
than on margin — max loss is computable and is the honest capital-at-risk
denominator anyway (`MARGIN.md` §4). That would answer "2–3% a month on capital
at risk" without pretending to know the exchange requirement.

**What it would take:** multi-expiry support in the engine, a roll rule, a
max-loss bound for the diagonal, and — the part that actually decides the
answer — **a slippage assumption for a contract trading 34 times a day that I
cannot calibrate from this data**, because the bhavcopy has no quotes.

---

## 6. Verdict

**The idea is better-founded than I expected, and the blocker is not the one you
would guess.**

| | verdict |
|---|---|
| volatility term structure | **neutral** — not selling cheap vol, and a net credit 84% of the time |
| hedge quality in a crash | **favours the far leg** — consistent instead of a lottery ticket, on 2 observations |
| bounded risk | **yes**, and no worse than the equivalent vertical |
| **liquidity** | **400× thinner on the leg you must buy — probably fatal** |
| margin | **unmeasurable** with this model, and SEBI removed the benefit this structure relies on |

It does not "solve the problem" so much as **trade a cost problem for a risk
problem** — and this study's consistent finding is that on NIFTY index options,
after real charges, the cost problem is the one that wins.

The honest recommendation before building anything: **get one week of real
bid-ask data on 3-expiries-out NIFTY strikes.** If the effective half-spread on
that leg is under ~2%, the structure is worth a full backtest. If it is 5%+, the
backtest will only tell you expensively what the liquidity table above already
says.
