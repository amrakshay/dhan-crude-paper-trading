# METHOD — how this was measured, and what was done to stop it lying

The default output of a hedged-option-selling backtest is a smooth equity curve
with a 90% win rate and no disaster in the sample, presented as a strategy.
Everything below exists to stop that.

The order matters and is the order the work was actually done in.

---

## 0. The feasibility gate, before any strategy rule

Three things had to exist first: option price history, a margin model, and an
NSE F&O charge card. `FEASIBILITY.md` records what was probed, what answered and
what did not.

The headline is that **real traded option prices were obtainable**, so the
entire Black-Scholes-synthesis hazard the handoff warned about does not apply.
The variance risk premium, the volatility skew and the true cost of the bought
wing are all *in the data*.

---

## 1. Data

### Prices

NSE F&O bhavcopy, two formats spliced at 2024-07-05:

- **Legacy** `archives.nseindia.com/.../DERIVATIVES/`, 2016-01-01 → 2024-07-05
- **UDiFF** `nsearchives.nseindia.com/content/fo/`, 2024-01-01 → 2026-09-17

Result: **2,643 NIFTY option sessions**, 12.4 million contract-days, no gaps,
456 holidays correctly identified and recorded as holidays so they are never
refetched.

The splice is validated rather than trusted. The two formats overlap for
2024-01-01 → 2024-07-05, and across **seven sessions** in that window they agree
on the contract set, every close, every settlement price and every open-interest
figure to the last digit — 284,688 contract-rows, zero mismatches. The same
script scores the derived lot size against UDiFF's published one: **exact on all
203 index contract-groups**. `scripts/validate_bhavcopy.py` re-runs both.

### Two prices, never interchanged

An untraded strike still gets a bhavcopy row. Its close is a stale carry-forward;
its settlement is the exchange's theoretical value. They diverge badly — on
2025-08-01 the NIFTY 07-Aug 23550 CE showed a close of 1297.10 against a
settlement of 1042.05, on zero volume. So:

| price | used for | guard |
|---|---|---|
| `close` | what you could have **traded** at | only from a contract that traded that session |
| `settle` | what a position is **marked** at | defined for every contract |

Entering on a stale close is a lookahead-shaped error: it fills you at a price
nobody was quoting, and it biases a premium seller towards selling wings at
yesterday's richer price.

### The forward

Not assumed, and not taken from the index. Recovered from **put-call parity on
the day's own chain**, using only strikes where both the call and the put
actually traded: `F = K + (C − P)·e^{rT}` at the strike with the smallest
`|C − P|`. Dividends, the repo rate and any futures basis are therefore absorbed
into an observed number instead of a modelled one.

Sanity check on real data: NIFTY 2025-08-01, 27 DTE — spot 24,565.35, recovered
forward 24,635.65, implied basis +70.30 points ≈ 3.9% annualised carry.

### Implied volatility and delta

Backed out of real traded prices with Black-76 on that forward. Two refusals
rather than a fabricated number:

- a price below intrinsic or above the forward returns `None`;
- **vega below a floor returns `None`**. A one-week option 1,500 points in the
  money is worth its intrinsic at any volatility, so its "implied volatility" is
  whichever bracket the solver landed in. The floor is set from the tick: for IV
  to be identifiable to half a vol point off a price quoted in 0.05 steps, vega
  must exceed roughly 10.

The skew this produces is real and is the thing a flat-IV synthetic model would
have destroyed — NIFTY 2025-08-01: 16-delta put at 12.8% IV against the 16-delta
call at 10.6%.

---

## 2. Charges and margin, before the strategy

### Charges

`conf/charges/nse-index-options.yaml`, every rate date-keyed and sourced to an
NSE circular or the Finance Act. **Five STT regimes** live inside the sample.

Validated in `scripts/validate_charges.py` against hand-computed worked
examples, with one divergence **recorded rather than tuned**: NSE's own
pre-Oct-2024 schedule gives 0.0505% where Zerodha published 0.0495% to clients,
a member-slab artefact from before SEBI's "True to Label" rule. The primary
source is used.

Two modelling points that change results rather than decorate them:

- **Expiry is not a transaction.** A spread allowed to expire worthless pays no
  brokerage, no transaction charge, no GST and no STT. Letting it expire is
  genuinely cheaper than closing it.
- **Exercise STT is charged on INTRINSIC value.** A long wing finishing deep in
  the money pays 0.15% of intrinsic, which on a disaster trade can exceed every
  other charge combined. Omitting it flatters exactly the left-tail cases that
  decide the outcome.

### Margin

`conf/margin/nse-index-options-margin.yaml` — a SPAN-**shaped** model, marked
`APPROXIMATION`, driven by **NSE's own published daily volatility** (the FOVOLT
file, verified available 2016-01-04 → 2026-09-17). Two properties that a flat
percentage-of-notional model does not have:

1. **Both legs are repriced under every scenario**, so the wing's margin relief
   is measured rather than assumed — and that relief is the entire economic
   reason to buy a wing.
2. **The requirement expands in a crisis**, because the scan range is driven by
   the exchange's own sigma. A constant margin model flatters the strategy
   precisely where it is most dangerous.

It is not real SPAN, and cannot be: NSE's historical par files are not archived
(404 at every path probed), so no historical backtest by anyone can use the
exchange's actual number. Stated on the dashboard, not buried.

One structural choice could not be read off a rule and was **calibrated**:
whether ELM nets a long option against a short at a different strike. See
`RESEARCH.md` §7 — it moves return on margin by a factor of 2.5 and is the
assumption in this study most likely to be wrong. The alternative is carried
through the sweep.

---

## 3. Synthetic ground truth and negative controls

Run before any real data is touched: `scripts/run_negative_controls.py`.

**Level 1 — the identity.** With zero drift, a short option held to expiry has
`E[P&L] = BlackPrice(IV) − BlackPrice(RV)`. Monte-Carlo with 200,000 paths across
ten (IV, RV) combinations; every cell must land within four standard errors.
Validates the pricer.

**Level 2 — the engine.** A synthetic market is written into the **same SQLite
schema** the real bhavcopy uses, so the engine runs on it unmodified. Selling
must earn nothing when IV = RV, earn when IV > RV, and **lose** when IV < RV,
monotonically. The zero row is the one that matters: an engine with a sign error
or a lookahead makes money in every regime, and would have shown an "edge" on
real data too.

**Level 3 — regimes where selling should lose.** Fat tails, jumps, a volatility
expansion, a COVID-shaped 35% crash, and a strong gap-heavy **upward trend**.

That last one turned out to be the most damaging of the six — mean −₹16,072 a
trade against the crash's +₹826, and a worst trade of −₹173,000. It is the
control that separates *volatility* from *direction*: a crash is one event that
hurts two trades out of sixty, while a trend breaches the short call side again
and again and each replacement is sold into the same move. A negative control
suite without it would have missed the regime that actually breaks a condor.

**Level 4 — bounds.** Every trade's P&L must lie between its own maximum loss
and its own maximum profit. This catches sign errors that the aggregate tests
average away.

**Seven bugs were found by these controls, the bounds checks and one direct
question about the results**, not by reading the code. Two of them produced a *better-looking* result, which is the
dangerous direction. All are recorded because they are the kind that survive into
a published result:

- **A sign error on the cost-to-close** made net P&L exceed the credit received
  — i.e. exceed the structure's maximum possible profit. Caught by the bounds
  check on the first smoke run.
- **The synthetic strike grid re-centred daily**, so a wing bought at one
  forward was silently delisted after a large move and the position could never
  be closed. The engine now *counts and surfaces* an unmarkable live position
  (`Backtest.data_faults`) instead of holding it open forever and quietly
  dropping it from the completed-trade statistics. On real NSE data the count is
  structurally zero, because strikes are not delisted.
- **Expiry settled every leg at the same price**, because on expiry day the
  bhavcopy's `SttlmPric` is the *underlying's* settlement rather than the
  option's. A four-legged structure then cancels to zero cost and every trade
  books its full credit: a **100% win rate** through a month the index fell
  11.3%. `DATA_QUIRKS.md` §1.
- **Eight and a half years were silently skipped** because the legacy format
  carries no lot size and `build()` refused those chains without saying so.
  `DATA_QUIRKS.md` §2.
- **Net option premium was added with the wrong sign**, so the credit received
  was added to the margin of every credit structure — and the calibration case
  looked *better* while it was wrong. `MARGIN.md` §3.
- **A missing volatility file produced zero margin** for one trade, and a zero
  denominator gave a return-on-margin of several hundred times that poisoned
  every tail statistic derived from it. The previous session's sigma is now
  carried forward and the substitution counted.
- **Expiry selection picked one candidate and skipped the session if its chain
  could not be built** — so whenever the nearest-to-target expiry was an
  illiquid weekly, that session produced no trade at all even though a liquid
  monthly sat a day further out. Found only because the results prompted the
  question "weekly or monthly?". It changed no rule, and it moved nearly every
  number: most of the sweep's best cells reversed, and it removed an objection
  the write-up had called the strongest evidence against its one positive
  finding. `EXPIRY_MIX.md`.

**That last one is the most instructive.** It was not caught by any control,
because every control still passed with it present — the engine was internally
consistent, it was just quietly answering a slightly different question than the
one stated. It was caught by someone asking what the strategy had actually
traded. A passing test suite bounds how wrong a study can be; it does not make
it right.

---

## 4. The strategy, causally

- Every decision on day *t* uses only bars up to and including *t*, and fills at
  day *t*'s own close. That is what an end-of-day trader does.
- Nothing reads a price from *t+1* to decide something on *t*.
- **Expiry is settled at each leg's own `SttlmPric`** from the expiry-day
  bhavcopy — the exchange's settlement value for that contract, not a modelled
  intrinsic and not an assumption about where the index closed. Using it is not
  hindsight; it is the fact.
- A structure is built **whole or not at all**. If any leg is missing or
  illiquid the trade is skipped, because a wing that could not be bought is an
  unhedged short.
- Early exits require every leg to be *tradeable* that session. A spread you
  cannot trade out of is a real risk, and the position stays open until it can
  actually be closed.

### Slippage

The bhavcopy carries no bid-ask. Modelled as a half-spread with two limbs — a
proportion of price, floored at a number of ticks — and **widened for contracts
with few executed trades**. Swept, never assumed zero.

This matters more here than in most studies: on NIFTY 2025-08-01 the near-money
24000 put showed 40,466 executed trades and the 23050 put near the 5-delta wing
showed **101**. The wing is two to three orders of magnitude thinner than the leg
it protects, and a zero-slippage backtest would systematically understate the
cost of being hedged.

---

## 5. Sweeps

`scripts/sweep.py`, one dimension at a time, reporting the **trade-off** rather
than the best cell: DTE, short-strike delta, wing width, and the management rule.

A best cell chosen from a grid this size is a selection artefact. What is
reported is the shape of the surface and whether the chosen point sits on a
plateau or a spike.

---

## 6. Baselines the strategy must beat

Three, and the third is the real question:

1. **The risk-free rate** — the opportunity cost of blocked margin. A 7% return
   while bills paid 6.5% is not a strategy. Uses the RBI policy repo rate as a
   dated step function, a documented substitute for the 91-day T-bill (see the
   card's own header for the size of that error).

   **Idle cash is credited at that rate before the comparison is made.** At one
   lot the book is ~97% uncommitted, and a raw equity curve earns nothing on it —
   so comparing that curve with bills charges the strategy for capital it never
   deployed. Uncredited the shortfall reads −6.25% a year; credited it is
   **−0.21%**. The first number is not wrong arithmetic, it is the wrong
   question, and the scale-free answer is return on capital at risk.
2. **Buy and hold NIFTY** — what the money would have done with no cleverness.
3. **The UNHEDGED twin** — the same strategy with the wing removed, run through
   the same engine, the same charge card and the same slippage model, so the two
   cannot drift apart. If the wing costs more than the tail it removes, the
   honest answer is that the structure does not work at these prices. **That is a
   finding, not a failure.**

---

## 7. Significance

**Block bootstrap resampling whole calendar months**, 10,000 draws.

Months and not trades, because losses in this strategy arrive *clustered*: one
adverse move hits every open position, and the next position is sold into the
same regime. An independent-trade bootstrap treats a crash as many independent
draws and reports a confidence interval far too tight — which is the specific
way this strategy family gets oversold.

---

## 8. Portfolio simulation

**Margin is the binding constraint, not cash.** A credit structure receives money
at entry and blocks margin against it, so the question is never "can I afford
it" but "how much of the book is already committed".

The engine is capital-aware: it marks every open position daily, computes margin
used from the same SPAN-shaped model, refuses an entry that would breach the
utilisation cap, and **counts the refusals**. Reported: return on margin
employed, peak margin utilisation, and the daily equity curve.

---

## 9. Reporting rules

Enforced by the shape of `hedgelib/metrics.py` rather than by discipline:

- **The win rate is printed once and immediately labelled meaningless.** 80–90%
  is what selling OTM options produces by construction.
- **No summary without the tail beside it**: worst single trade, mean of the
  worst 1% and 5%, worst month, and maximum loss per unit of margin.
- **`tail_events` counts what the sample actually contained** — sessions falling
  more than 3% and more than 5%, the worst single session and the worst
  20-session stretch. If the count of 5% down days is zero, that is printed as
  the headline, because a backtest with no disaster in it is undersampled, not
  safe.
- Results are **split at 2024-11-20**, SEBI's index-derivatives framework, as
  well as pooled. A backtest spanning it is measuring two different markets.

---

## 10. What this method cannot answer

Stated here and repeated on the dashboard, rather than left for a reader to
discover:

1. **Anything intraday.** This is daily data. 0DTE, intraday stop-losses, and
   the entire "defend the tested side" school are out of reach, and no claim is
   made about them.
2. **Single-stock options.** Physically settled; a short leg in the money at
   expiry is a delivery obligation, not a cash difference. Excluded, loudly.
3. **The true margin number for any past day.** Historical SPAN par files are
   not published by anyone.
4. **Real bid-ask.** Modelled and swept, not observed.
5. **Liquidity before 2024-07** at full strength — the legacy bhavcopy carries no
   executed-trade count, so the gate falls back to contract volume on the older
   8.5 years.
