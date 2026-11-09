# DIAGONALS — the two-expiry structure, built and measured

`CALENDARS.md` assessed the idea and stopped short of a backtest because the
engine could not hold two expiries in one position. **It can now.** This is the
result.

Reproduce with `python scripts/diagonals.py`; full output in
`out/diagonals.txt`, and a section on the dashboard.

---

## The verdict in three lines

- **The mechanism is real.** The long-dated leg genuinely cushions: the diagonal's
  worst trade cost **30.8%** of its own maximum loss where the vertical's cost
  **100.6%** — the vertical hit its bound exactly, the diagonal never came close.
- **It beat the vertical over the test window**, +25.4% against −42.8% a year on
  capital at risk.
- **And the sign flips to −42.0% on a slippage assumption that is well inside
  the plausible range** for a leg trading 400× less than the one it hedges. The
  objection `CALENDARS.md` §3 raised survived contact with the data.

**Promising, mechanistically confirmed, and not established.**

---

## 1. Head to head

Same near-leg delta (16Δ), same DTE (~7), same window, same charges, same
slippage model. 2024-07-01 → 2026-09-17, 549 sessions.

| structure | n | total P&L | **return on risk /yr** | worst | credit ÷ max loss | win |
|---|---|---|---|---|---|---|
| bull put spread (same expiry) | 204 | −₹23,886 | **−42.82%** | −₹24,188 | 6.6% | 88.7% |
| **put diagonal (expiry+3)** | 225 | **+₹38,300** | **+25.37%** | −₹29,797 | **1.0%** | 91.6% |
| iron condor (same expiry) | 148 | −₹24,302 | −45.87% | −₹27,197 | 13.8% | 78.4% |
| double diagonal (expiry+3) | 189 | −₹37,175 | −23.68% | −₹45,604 | 2.1% | 83.1% |

Two things jump out.

**The put diagonal wins and the double diagonal loses.** Adding the call side
costs 49 percentage points a year — the same asymmetry `SWEEP.md` §1 found for
verticals, from the same cause: the index volatility skew makes the put side
richer and the call side poorer. Whatever structure you wrap around it, **the put
side is where the premium is.**

**The diagonal is paid far less per unit of nominal capital.** 1.0% of maximum
loss as credit against the vertical's 6.6%. A 5-delta strike three expiries out
is a long way from the money, and *the strike gap is the maximum loss*. Buying
further out in **time** does not narrow the gap in **price** — and the gap is
what you can lose.

That last point is why it still wins despite the thin credit: it very rarely gets
anywhere near that nominal loss.

---

## 2. The mechanism check — the important result

The claim was that a longer-dated hedge holds value where a near-dated wing can
expire worthless. Directly measurable:

| | worst trade | mean worst 1% | skew | **worst as share of its own max loss** |
|---|---|---|---|---|
| vertical | −₹24,188 | −₹21,799 | −3.47 | **100.6%** |
| diagonal | −₹29,797 | −₹22,734 | −4.42 | **30.8%** |

**The vertical hit its maximum loss exactly** (100.6% — the excess is charges
paid on top of the payoff bound). **The diagonal's worst trade used less than a
third of its.**

That is the cushion, and it is the one claim in the whole calendar idea that the
data supports cleanly. The far leg retains time value and its vega rises when
volatility does, so the position stops losing before the bound.

**The practical consequence is about sizing.** A defined-risk structure is
normally sized on maximum loss (`TAILS.md` §4), because that is what you can
lose. For the diagonal the nominal maximum is a number you should not expect to
reach — which means sizing on it wastes capital, and sizing on anything else
means guessing. That is an awkward position to be in and it is not resolved here.

---

## 3. The parameter sweeps

**How far out the bought leg sits:**

| | n | total P&L | return on risk /yr | credit ÷ max loss |
|---|---|---|---|---|
| expiry+1 | 225 | +₹5,075 | +4.94% | 2.8% |
| expiry+2 | 242 | +₹14,143 | +10.44% | 1.7% |
| expiry+3 | 225 | +₹38,300 | +25.37% | 1.0% |
| expiry+4 | 210 | +₹46,724 | +32.41% | 0.6% |

Monotone — further out is better on every rung. **Treat that with suspicion.**
Monotone improvement toward the thinnest, least-traded contract in the chain is
exactly the shape a *pricing artefact* takes: the further out you go, the more
the modelled fill price is doing the work and the less the market is. It is also
what genuine time-value retention would look like. This data cannot separate
those two, and §4 is the reason.

**Delta of the bought leg:**

| | n | total P&L | return on risk /yr |
|---|---|---|---|
| 0.02Δ | 224 | +₹52,510 | +24.63% |
| 0.05Δ | 225 | +₹38,300 | +25.37% |
| 0.10Δ | **47** | +₹29,835 | +125.28% |
| 0.20Δ | **2** | −₹16,729 | −874.20% |

**Ignore the bottom two rows.** At 47 and 2 trades the liquidity gate is
rejecting almost everything — a 10- or 20-delta strike three expiries out mostly
has no usable quote. The +125% cell is not a finding, it is a sample of 47.

---

## 4. The assumption that decides it

The far leg trades ~400× less than the near one. The bhavcopy has no quotes, so
the spread is modelled:

| slippage | n | total P&L | **return on risk /yr** |
|---|---|---|---|
| none (unreal) | 239 | +₹46,584 | +30.19% |
| **1% / 1 tick, 2× illiquid (base)** | 224 | +₹36,479 | **+24.17%** |
| 2% / 2 ticks, 3× illiquid | 210 | +₹2,195 | **+1.47%** |
| 4% / 4 ticks, 4× illiquid | 174 | −₹59,081 | **−42.03%** |
| 8% / 8 ticks, 4× illiquid | 115 | −₹15,622 | −12.84% |

**The answer flips between the base case and the next rung.** Going from a 1%
half-spread to 2% takes the strategy from +24% a year to +1.5%; going to 4% takes
it to −42%.

For a contract with a 10th-percentile day of **34 executed trades**, a 2–4%
effective half-spread is not a pessimistic assumption — it may well be
optimistic. `BULL_PUT.md` §3 established the same fragility on a leg trading
*101* times a day.

**This is the finding that governs everything above.** A backtest whose sign is
decided by an uncalibrated assumption has not answered the question, and no
amount of further parameter work changes that. It needs quote data.

---

## 5. Significance

Block bootstrap over whole months, 20,000 draws:

| | observed | 5th–95th percentile | **P(total < 0)** |
|---|---|---|---|
| vertical | −₹23,886 | [−₹113,870, +₹64,561] | 66.5% |
| **diagonal** | **+₹38,300** | [−₹48,935, +₹127,294] | **23.2%** |

Better than the vertical, and still roughly a one-in-four chance the true
expectation is negative — on a window of about two years.

---

## 6. What the engine now does, and what it still cannot

**Built:**

- `StructLeg` carries its own expiry; price dictionaries are keyed on
  `(strike, is_call, expiry)` so two legs differing only in expiry cannot
  collapse into one.
- Each leg is marked in its own chain, on its own tenor.
- **At the near leg's expiry the short settles and the far leg is *sold*.** Those
  are different events: settlement is not a transaction (no brokerage, no GST, no
  STT on premium), a sale is. Charging the far leg's exit as an expiry would have
  handed the expensive half of the position to the exchange for free.
- The far leg has its own liquidity gate (`far_min_trades`), because applying the
  near leg's 100-trade threshold to it would refuse every diagonal ever built.
- `far_leg_untradeable` counts the times the long could not be sold at exit
  (5 of 225).

**Verified unchanged:** the refactor reproduces the pre-existing results exactly
— iron condor 205 trades / −₹95,632, bull put 308 / +₹98,180, bear call 297 /
−₹253,967 — and all four levels of the negative-control suite still pass.

**Still cannot:**

- **Give a defensible margin number.** The model has no inter-month spread
  charge, and SEBI withdrew the index calendar benefit on expiry day from
  2025-02-10. Every return here is on capital at risk, never on margin.
- **Model the roll.** The trade ends at the near expiry and the far leg is sold.
  A real operator would hold the long and sell the *next* near leg against it —
  which is a different strategy ("buy one long-dated option, sell many short ones
  against it") whose economics turn on whether the credits outrun the decay of
  the long. Not measured.
- **Price the far leg's spread**, which §4 shows is the thing that decides it.

---

## 7. So: does it solve the problem?

**It solves the tail problem and inherits a cost problem.**

The cushion is real and measured — 30.8% of maximum loss against 100.6%. For
"lowest risk possible" that is a genuine improvement over a same-expiry wing, and
it is the first structure in this study where the hedge demonstrably did its job
in the worst cases.

But it is paid 1.0% of maximum loss to do it, it needs a contract trading 34
times on a bad day, and its profitability inverts within a plausible band of the
one input nobody can calibrate from this data.

**The recommendation from `CALENDARS.md` §6 stands and is now sharper:** get real
bid-ask data on 3-expiries-out NIFTY strikes. The backtest exists and will run
against any slippage number you give it. Until that number is observed rather
than assumed, this is an encouraging mechanism attached to an unresolved cost.
