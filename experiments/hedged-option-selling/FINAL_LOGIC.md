# FINAL_LOGIC — the decided rules, and the decision about them

Two parts, and the second is the one that matters.

**Part A** specifies the rules precisely enough to implement, because the handoff
asked for that and because a specification is how a finding stays checkable.

**Part B** is the verdict: **do not trade this, and do not graduate it into the
application.** The rules below are the best version of a thing that, on this
sample, does not clear its costs.

---

# Part A — the specification

## A.1 Instrument and scope

| | |
|---|---|
| underlying | **NIFTY 50 index options** (NSE, `OPTIDX`, European, cash settled) |
| excluded | single-stock options — physically settled; a short leg in the money at expiry is a delivery obligation, not a cash difference |
| excluded | BANKNIFTY weeklies — SEBI left one weekly expiry per exchange from 2024-11-20 and NSE kept NIFTY's |
| resolution | **daily**. No intraday rule may be specified, because none can be tested on this data |

## A.2 Structure

**Bull put spread.** Sell one put, buy one further out of the money, same expiry,
equal quantity.

Not an iron condor, and this is the single most consequential choice in the
specification. The condor is a bull put spread stapled to a bear call spread, and
on an equity index those two are not symmetric: the call side lost **−102% a
year** on capital at risk against the put side's **+20.5%**, because the
volatility skew makes the put sold richer than the put bought and the call sold
cheaper than the call bought (`SWEEP.md` §1, `BULL_PUT.md`).

Ratio spreads, calendars and diagonals are **out of scope**: the first is not
defined-risk, and the other two depend on inter-month spread margin that this
margin model does not implement.

## A.3 Entry

| rule | value | why |
|---|---|---|
| decision point | the **close** of session *t*; fill at that same close | what an end-of-day trader does; no price from *t+1* informs a decision at *t* |
| expiry selected | **MONTHLY** series only; the one nearest **30 calendar days**, within ±10 | see the note below — this must be declared, not emergent |
| expiry fallback | if that chain cannot build a complete structure, try the next-nearest in the window before giving up | skipping the session instead silently biases which expiries get traded (`EXPIRY_MIX.md` §3) |
| short strike | nearest OTM strike to **0.16 delta** | delta self-adjusts with volatility where a percentage-of-spot rule does not |
| long strike (wing) | nearest OTM strike to **0.05 delta**, strictly below the short | |
| delta source | implied volatility backed out of the leg's **own traded close**, on a forward recovered from put-call parity on the day's own chain | no dividend or repo assumption |
| liquidity gate | every leg must have **≥100 executed trades** that session (pre-2024-07: contract volume > 0, because the legacy bhavcopy carries no trade count) | |
| completeness | **whole structure or nothing.** If any leg is missing or illiquid, skip the trade | a wing that could not be bought is an unhedged short |
| concurrency | at most **one** open position | |
| sizing | by **maximum loss**, never by margin — see A.6 | |

**Refused entries are recorded, not retried.** A skipped trade is a skipped
trade; the next decision is made from fresh data.

### Why the expiry series is named

NIFTY lists weeklies alongside monthlies, so a bare "nearest to 30 DTE" rule
picks whichever happens to be closest — and the answer changed over the sample as
weeklies were introduced (Feb 2019) and became liquid. Left undeclared, the
strategy traded monthlies until ~2021 and weeklies thereafter: **an undeclared
regime change inside the rule**.

Monthly is specified **not because it measures better** — weekly versus monthly
is not distinguishable from noise, and the sign of the difference flips between
structures (`EXPIRY_MIX.md` §4) — but because a declared rule beats an emergent
one, and the monthly series exists across the whole sample.

## A.4 Adjustment

**None.** No rolling, no defending the tested side, no adding to a loser.

This is a decision, not an omission. Rolling converts a *bounded* loss into a
*sequence* of bounded losses, which is how a defined-risk strategy acquires an
undefined one. Defending the tested side requires intraday prices and intraday
decisions, and neither is testable on daily data — specifying it would be
inventing a rule this study cannot evaluate.

## A.5 Exit

In precedence order, evaluated at each session's close:

1. **Disaster stop** — if the mark-to-market loss reaches **2× the credit
   received**, close the whole structure.
2. **Profit target** — if the mark-to-market gain reaches **50% of maximum
   profit**, close the whole structure.
3. **Expiry** — otherwise hold to expiry and settle each leg at its **intrinsic
   value against the underlying's settlement**.

Notes that are load-bearing rather than decorative:

- **The exit decision uses the prices it fills at.** The daily *mark* uses
  settlement prices, which are defined for every contract; the exit *decision*
  uses tradeable closes. Deciding on one number and transacting at another is not
  a lookahead but it is an inconsistency, and it costs nothing to avoid.
- **An early exit requires every leg to be tradeable that session.** If one is
  not, the position stays open and closes on the next session it can. A spread
  you cannot trade out of is a real risk.
- **Expiry is not a transaction.** No brokerage, no exchange charge, no GST, and
  no STT on the premium. A long wing finishing in the money pays STT on its
  **intrinsic value**, which on a disaster trade can exceed every other charge
  combined.
- **Never settle a leg from the expiry-day `SttlmPric` field.** On expiry day
  that field holds the *underlying's* settlement, identically for every strike
  (`DATA_QUIRKS.md` §1). Using it makes a multi-leg structure cancel to zero cost
  and produces a 100% win rate.

### Why a stop — and a correction to an earlier claim

An earlier version of this document justified the stop by saying it was "the
single most effective rule tested" and that the measurement had contradicted
`RESEARCH.md` §5's prior. **The first half of that is no longer true.** After the
expiry-selection fix the same 2× stop cell went from **+₹33,550 to −₹44,583** —
it did not survive a change that touched no strategy rule (`SWEEP.md` §5).

The stop is still specified, on the narrower and more durable ground: it roughly
**halves the worst trade** (−43,086 → −23,295) and nearly **quarters the maximum
loss per unit of margin** (−667% → −302%). That is a risk control, which is what
a disaster stop is for. It costs return and buys tail reduction — exactly the
trade `RESEARCH.md` §5 predicted. What it does not do is turn a losing structure
into a winning one.

## A.6 Position sizing

**Size on maximum loss. Never on margin.**

```
lots = floor( equity x risk_fraction / max_loss_per_lot )
max_loss_per_lot = (wing_width x lot_size) - credit_per_lot
risk_fraction <= 0.05
```

The margin model puts the exchange requirement at a median of **26% of a
structure's own maximum loss**, so margin-based sizing understates the risk by
roughly 4× and the worst trade in this sample lost **667% of the margin it was
blocking** (`MARGIN.md` §4, `TAILS.md` §4).

The 5% cap is what the observed tail supports, and it is a ceiling rather than a
recommendation — at a negative expectancy every size loses.

## A.7 Charges and margin

Charges: `conf/charges/nse-index-options.yaml`, every rate date-keyed. **Five STT
regimes** live inside 2016–2026; the rate a trade pays is resolved at the trade's
own date, never at "today".

Margin: `conf/margin/nse-index-options-margin.yaml`, marked `APPROXIMATION`,
SPAN-shaped and driven by NSE's own published daily volatility.

---

# Part B — the decision

## B.1 What the measurement says

| structure | total P&L | **return on capital at risk /yr** | worst trade |
|---|---|---|---|
| iron condor (the conventional choice) | −₹95,632 | **−25.9%** | −₹43,086 |
| bull put spread (the specification above) | +₹98,180 | **+26.0%** | −₹48,812 |
| bear call spread | −₹253,967 | −88.1% | −₹44,796 |
| risk-free (RBI repo) | — | **+5.64%** | — |

The full specification above — better structure, named expiry series, stop —
was deliberately **not** run as a single joint cell. That cell would be the
product of every choice made after seeing the data, and reporting it as *the*
result is precisely the selection artefact `SWEEP.md` §7 warns about.

## B.2 Why that is not enough

1. **THE CURRENT REGIME IS NEGATIVE.** Since SEBI's index-derivatives framework
   took effect on 2024-11-20 the bull put spread returns **−29.1% a year**, on 48
   trades, against +43.3% before it. Too few trades to conclude from — and it is
   the only window that describes the market as it exists now, and it contains
   tripled lot sizes, one weekly expiry per exchange, the +2% expiry-day ELM and
   the April 2026 STT rise.
2. **One in five bootstrap resamples loses money** (P(total < 0) = 20.1%), with a
   5th percentile of −₹112,795.
3. **Removing three trades more than doubles the result.** The finding rests on a
   handful of observations.
4. **8% slippage drops it below the risk-free rate** (+2.87%), and the bought
   wing traded 101 times on a day the near-money put traded 40,466. The spread
   assumption is modelled, not observed, and it is doing real work.
5. **Seven 5% down days** in the whole sample is a thin basis for any claim about
   the tail, and the true tail is worse than the observed one.
6. **The parameter rankings proved fragile.** A bug fix touching no strategy rule
   reversed most of the sweep's best cells (`SWEEP.md` §0). Any specific
   parameter in Part A should be read as "the least-arbitrary available choice",
   not as an optimum.

## B.3 The verdict

**The direction is real; the magnitude is not established.**

The put-side bias is theory-consistent, its mechanism was measured in the data
rather than assumed, and the sign of the opposite structure confirms it. That is
a genuine finding and it is worth knowing.

It is not a strategy. What this work supports is *"index put skew was worth
something after costs on this sample"* — and notably **not** on the two years
since the market's structure changed. The honest next step is **more data, not
more searching**: searching harder on the same 2,643 sessions only makes the
selection problem worse, and the question that actually matters now is whether
the post-2024 regime is different or merely short.

**Recommendation: do not trade this, and do not build it.**

## B.4 And it stays outside the application

`CLAUDE.md` states, as a non-negotiable rule of this codebase:

> A "Sell" is a long put, never a short call, so the worst case stays the premium
> paid. **Do not add a code path that sells an option to open.**

Everything in Part A sells an option to open. That is fine inside
`experiments/`, which is outside `backend/`, outside the safety scanners, and
places nothing.

**Graduating any of it into `backend/src/` would be a deliberate decision by the
operator, taken explicitly, with that rule rewritten in the same change. It is
not this research's call, and on this evidence there is nothing to graduate.**

The backend safety suites pass unchanged, and nothing here is wired to an order
path.
