# RESEARCH — defined-risk option selling on NSE index options

What the structures are, why short premium has a positive expected return at
all, what the published management rules claim, and the Indian specifics that
are the subject of this study rather than a footnote to it.

Every number that came from outside this repository is sourced. Where a claim is
widely repeated but not independently verified, it says so — several of the
best-known management rules are in that category.

---

## 1. Why short premium is paid at all

### The variance risk premium

Option implied volatility is, on average, above the volatility the underlying
subsequently realises. A seller who is paid `IV` and gives back `RV` earns the
difference. With zero drift and zero rates the identity is exact:

```
E[P&L of a short option held to expiry]  =  BlackPrice(IV) − BlackPrice(RV)
```

This is the whole strategy in one line, and it is the identity
`scripts/run_negative_controls.py` verifies by Monte Carlo before any real data
is touched. It has three consequences that shape everything below:

- **The edge is a volatility spread, not a direction.** Being right about
  direction is a way of losing less, not a way of being paid.
- **The edge is paid in exchange for a liability.** The premium exists because
  someone is buying insurance, and insurance is priced above expected loss
  because the seller bears the tail. There is no version of this where the tail
  goes away and the premium stays.
- **It inverts.** When realised volatility exceeds implied, selling is simply
  the wrong side of the trade. This is not a failure mode to be engineered
  around; it is the same mechanism running backwards.

### When it disappears or inverts

The premium is compensation for bearing gap risk, so it is smallest or negative
exactly when gaps happen:

- **Volatility expansions.** IV rises but RV rises faster and first. A seller is
  marked down on the position they hold *and* has to sell the next one into a
  regime they have mispriced.
- **Crashes.** Realised volatility in March 2020 exceeded any IV quoted in
  February 2020.
- **Post-event crush is the opposite case** and is where the premium is largest:
  IV elevated into a known event, realised volatility modest after it.

A backtest sample that happens to contain few expansions will measure a premium
that is too large and a tail that is too small, and both errors point the same
way. This is the central sampling problem of the whole exercise, and
`README.md` reports the count of tail events explicitly for that reason.

---

## 2. The structures

Throughout: `W` = distance between the short strike and its wing, `C` = net
credit received per unit, `L` = lot size. All are cash-settled European index
options.

### Bull put spread (credit)

Sell a put at `K_s`, buy a put at `K_l < K_s`.

| | |
|---|---|
| Max profit | `C × L`, when settlement ≥ `K_s` |
| Max loss | `(W − C) × L`, when settlement ≤ `K_l` |
| Breakeven | `K_s − C` |
| Which Greek pays | **theta**, financed by short vega and short gamma |
| Directional bias | bullish / neutral |
| Margin profile | scan risk collapses to roughly the width; ELM on the net short |

The workhorse. On an equity index it also collects the **skew**: the put sold is
on a steeper part of the smile than the put bought, so the structure is short the
richer option and long the cheaper one in volatility terms. Measured on real
data — NIFTY, 2025-08-01, 27 DTE — the 16-delta put carried 12.8% IV against the
16-delta call's 10.6%. That 2.2-point asymmetry is *why* put spreads out-earn
call spreads on an index, and it is observed here rather than assumed.

### Bear call spread (credit)

The mirror: sell a call at `K_s`, buy a call at `K_l > K_s`. Same arithmetic.
Earns less than the put spread at equal delta on an index, for the skew reason
above — and covers the tail that actually kills equity portfolios less often,
because indices crash down and drift up.

### Iron condor (credit)

A bull put spread and a bear call spread on the same expiry.

| | |
|---|---|
| Max profit | total credit, when settlement lands between the short strikes |
| Max loss | `(W − C) × L` — **only one side can lose** |
| Breakevens | `K_sp − C` and `K_sc + C` |
| Which Greek pays | theta, doubled; delta roughly neutral at entry |

The key arithmetic, and the reason condors are attractive on paper: **two
credits, one maximum loss**. The margin does not double either, because the two
spreads cannot both finish in the money. What *does* double is the leg count,
and at ₹20 per executed order that is ₹160 per round trip rather than ₹80.

### Iron fly (credit)

Short straddle at the money with both wings bought. Maximum credit, narrowest
profit zone, highest gamma. Effectively a bet that realised volatility will be
below implied *right now*, with none of the directional cushion a condor has.

### Short strangle with wings

An iron condor described from the other direction. Identical position.

### Ratio spreads

Sell more contracts than are bought — e.g. buy one put, sell two lower puts. The
extra short is **naked**, so the structure is not defined-risk and is out of
scope for this study. Included here only to be explicit that it is excluded and
why: its appeal is a credit with a wider profit zone, and its cost is exactly the
unbounded tail the entire project is about avoiding.

### Calendars and diagonals

Sell a near expiry, buy a further one at the same (calendar) or a different
(diagonal) strike. Long vega, short near-dated theta.

**Researched but not backtested here**, for two honest reasons:

1. They depend on the term structure of implied volatility, and pricing the
   far leg correctly matters more than any entry rule.
2. **Their margin depends on calendar-spread benefit**, which SEBI changed
   inside the sample — from 2025-02-10 the benefit is withdrawn on expiry day
   for index contracts (NCL FAQ Q33). The margin model here does not implement
   inter-month spread charges, so a calendar backtest would report a margin
   number that is not defensible, and return on margin is the metric.

### The collar

Long the underlying, long a protective put, short a covered call. Not a premium
*selling* strategy — it is a hedging overlay whose short call finances the put.
Out of scope: there is no underlying position in this study.

---

## 3. Strike selection

### By delta

The convention, and the one this study uses. Delta is roughly the
risk-neutral probability of finishing in the money, so a 16-delta short is
approximately a "sell the one-standard-deviation strike" rule that
self-adjusts as volatility changes — the strike moves further out when the
market is more dangerous, which is the behaviour you want and which a fixed
percentage rule does not have.

- **16-delta** ≈ one standard deviation. The most-cited convention.
- **30-delta** ≈ 0.5 SD. More premium, materially more assignment.
- **5–10 delta** the usual wing.

Delta must be computed from an implied volatility that is actually identifiable.
Deep in-the-money quotes carry essentially no vega, so their "implied
volatility" is solver noise — `hedgelib/blackscholes.py` refuses to return one
below a vega floor rather than passing the noise into a delta.

### By percentage of spot

Simple and wrong in the way that matters: a fixed 5% OTM strike is a 30-delta
option in a calm market and a 10-delta option in a panic. The rule tightens
exactly when it should loosen.

### By expected move

`S × IV × √(T/365)`. Nearly equivalent to delta selection by construction, and
preferred when comparing across expiries because it normalises for time.

### Wing width — the real trade-off

The wing does two jobs and they pull in opposite directions:

- **Narrower wing** → cheaper protection given up, smaller maximum loss, less
  margin — but a much smaller net credit, so charges eat a larger share of it
  and the strategy needs a higher win rate to break even.
- **Wider wing** → larger credit, better credit-to-charge ratio — but a larger
  maximum loss and more margin, so return *on margin* need not improve.

This is the single most important parameter sweep in the study, because the
naive intuition ("wider is better, you keep more premium") ignores that margin
is the denominator.

---

## 4. Expiry selection

| DTE at entry | Theta | Gamma | What it means in practice |
|---|---|---|---|
| 0DTE | enormous | enormous | Not testable on daily data. Excluded, loudly. |
| ~7 (weekly) | high | high | Little time to be wrong; a gap is unrecoverable |
| ~30 | moderate | moderate | The common compromise |
| ~45 | lower per day | lowest | The most-cited "optimal" window |

Theta grows as expiry approaches; so does gamma, and gamma is what converts an
adverse move into a loss that cannot be managed. The published preference for
**30–45 DTE entry with an exit before expiry week** is an attempt to harvest
decay while leaving before gamma dominates. It is a claim, it is testable, and
it is tested in `SWEEP.md` rather than assumed.

**0DTE and intraday management are out of reach of this data.** Daily bars
cannot represent a strategy whose entire life is one session. Any claim about
them from this study would be invented, and none is made.

---

## 5. Management rules — which matter more than entry rules

This is the part of the option-selling literature with the highest ratio of
confident claims to independent verification.

### Close at a fraction of maximum profit

The best-known rule: close at **50%** of maximum profit rather than holding to
expiry. The rationale is sound in shape — the last fraction of the credit is
earned slowly and against rising gamma, so the return *per day of risk* is
worse at the end.

Widely attributed to tastytrade's research. **Not independently verified**, and
this study's reproduction is on a different market, a different index and a
different charge regime. Treated as a hypothesis, swept across
25/50/75/hold-to-expiry.

### Rolling

Closing the tested side and reopening it further out, in the same or a later
expiry. Its appeal is that it converts a loss into a new credit. Its hazard is
that it converts a *bounded* loss into a *sequence* of bounded losses, which is
how a defined-risk strategy acquires an undefined one. Not implemented here: a
roll is a new trade, and this study measures trades.

### Defend the tested side

Adjusting the untested side inward, or adding a spread against the move. Requires
intraday decisions and intraday prices. **Not testable on daily data.** Named
here so its absence is explicit rather than silent.

### Time-based exit

Close at N days to expiry regardless of P&L. Mechanical, testable, and the
cleanest way to avoid expiry-week gamma and the +2% expiry-day ELM. Swept.

### Stop-loss at a multiple of the credit

Close when the loss reaches N× the credit received. Popular, and the most
suspect of the set: on a defined-risk spread the maximum loss is already bounded,
so a stop converts a *known* worst case into a *realised* loss, often on a move
that would have reverted. Swept, including against no stop at all.

---

## 6. Indian market specifics — the subject, not a footnote

### 6a. SEBI's index-derivatives framework (circular SEBI/HO/MRD/TPD-1/P/CIR/2024/132, 1 October 2024)

Six measures, phased. Each is a structural break and the sample must be read
accordingly.

| Measure | Effective | Effect on this strategy |
|---|---|---|
| Minimum contract value ₹15 lakh; NIFTY lot 25 → 75 | **2024-11-20** | Position granularity tripled. A one-lot trade is now a ₹15–20 lakh notional. |
| One weekly expiry per exchange — NSE keeps NIFTY | **2024-11-20** | BANKNIFTY/FINNIFTY weeklies end. A continuous weekly study of anything but NIFTY becomes impossible across this date. |
| Additional **2% ELM** on short index options on expiry day | **2024-11-20** | Directly raises the margin denominator for anything held into expiry. |
| Upfront collection of option premium from buyers | **2025-02-01** | Affects buyers, not this strategy directly. |
| Calendar-spread benefit removed on expiry day (index) | **2025-02-10** | Why calendars and diagonals are researched but not backtested. |
| Intraday monitoring of position limits | **2025-04-01** | Not binding at this study's size. |

Sources: SEBI circular SEBI/HO/MRD/TPD-1/P/CIR/2024/132 dated 2024-10-01, as
reported by Zerodha Z-Connect and corroborated by Business Standard
(2024-11-20); NSE circular NSE/FAOP/64625; NSE Clearing Risk Management FAQ Q33
and Q55 for the two margin dates. Confidence: CONFIRMED for the measures and the
two ELM/calendar dates; the 2024-11-20 date is CONFIRMED by two contemporaneous
reports and UNCONFIRMED against the circular's own text.

**A backtest spanning 2024-11-20 is measuring two different markets.** Results
are therefore reported split at that date as well as pooled.

### 6b. STT on option sales — five regimes, and it rose ~9×

| Effective | Sale of option (premium) | Exercised (intrinsic) |
|---|---|---|
| to 2016-05-31 | 0.017% | 0.125% |
| 2016-06-01 | 0.05% | 0.125% |
| 2023-04-01 | 0.0625% | 0.125% |
| **2024-10-01** | **0.10%** | 0.125% |
| **2026-04-01** | **0.15%** | **0.15%** |

A premium seller pays this **on the sell side, on the premium** — on revenue, at
entry, before knowing whether the trade works. Full sourcing in
`conf/charges/nse-index-options.yaml`.

Two things this does that are easy to miss:

1. **It scales with size, and flat brokerage does not.** At one lot, ₹20 per
   order swamps the STT and the 2024/2026 breaks are nearly invisible. At forty
   lots the STT dominates and the same trade's total cost rises 1.85× across the
   sample. A one-lot backtest under-reports the very break it means to measure.
2. **The exercise limb is charged on INTRINSIC, not premium.** A long wing
   finishing deep in the money pays 0.15% of its intrinsic value — which on a
   disaster trade can exceed every other charge on the position combined. A
   model that omits it flatters precisely the left-tail cases that decide the
   outcome.

### 6c. Physical settlement — why single stocks are excluded

Single-stock F&O in India is **physically settled**. A short option left in the
money at expiry is a delivery obligation, not a cash difference: margins
escalate from E-4 (10% / 25% / 45% / 70% of the delivery margin on successive
days, NCL FAQ Q24), and failure to deliver goes to auction.

Modelling that properly is a project of its own. Half-modelling it would produce
exactly the confident nonsense this study exists to avoid. **Single-stock options
are excluded**, and the index study makes no claim about them.

### 6d. Liquidity

NIFTY options are among the most liquid contracts in the world at the strikes
near the money. That is not true of the strikes this strategy must buy.

From the real chain, NIFTY 2025-08-28 expiry on 2025-08-01:

| Strike | Executed trades | What it is |
|---|---|---|
| 24000 PE | 40,466 | near the money |
| 23800 PE | 9,595 | the 16-delta short |
| 23050 PE | 101 | near the 5-delta wing |

**The wing is two to three orders of magnitude thinner than the leg it
protects.** The bhavcopy carries no bid-ask, so the spread is modelled
(`SlippageModel`) with an explicit widening for low trade counts, and swept. A
zero-slippage backtest would systematically understate the cost of being hedged
— which is the one thing this project is about.

Before 2024-07 the legacy bhavcopy format carries **no executed-trade count at
all**, so the liquidity gate on the older 8.5 years of the sample falls back to
contract volume. That is a real weakening and is reported, not smoothed over.

### 6e. Settlement mechanics

NIFTY options are European and cash settled against the average of the last half
hour of the underlying on expiry day. This study does not need to model that: on
expiry day each contract's own `SttlmPric` in the bhavcopy **is** the exchange's
settlement value for it, so positions are settled at the exchange's number
rather than at a reconstruction of it.

---

## 7. What the margin rules do to the economics

This is where Indian option selling differs most from the literature, which is
overwhelmingly American.

In a US margin account a defined-risk vertical is margined at its **maximum
loss** — you cannot lose more, so you post no more. In India margin is
SPAN + ELM, and **ELM is charged on the NOTIONAL value of the short option**, not
on the structure's risk. At NIFTY 24,500 with a 75 lot, 2% of notional is
₹36,750 per short leg — which for a 500-point spread is larger than the entire
maximum loss of ₹31,113.

Whether a bought wing relieves that ELM is the single most consequential
unresolved question in the margin model. The NCL FAQ says ELM "shall be netted at
client and prop levels" without saying whether a long at a *different* strike
counts. Both readings are implemented:

| Reading | Margin, 500-wide NIFTY spread | vs naked | vs max loss |
|---|---|---|---|
| gross (no netting) | ₹61,198 | 60% | 1.97× |
| netted | ₹24,448 | 24% | 0.79× |

Brokers report hedged index spreads at **₹15,000–30,000 per lot** and at
"roughly a quarter to a third of the margin a naked short needs". The netted
reading lands inside both bands; the gross reading is roughly double the top of
each. The netted reading is therefore used, **as a calibration to reported
figures rather than as a reading of the rule**, and the gross alternative is
carried through the sweep. It is the assumption in this study most likely to be
wrong, and it moves return on margin by a factor of 2.5.

---

## 8. Sources

- NSE Clearing, *Non-Confidential FAQs — Risk Management: Margins*, January 2026.
  <https://www.nseclearing.in/sites/default/files/2026-01/NCL%20-%20FAQ_RISK_MANAGEMENT.pdf>
- NSE Clearing, *NSCCL SPAN*.
  <https://www.nseclearing.in/risk-management/equity-derivatives/nsccl-span>
- NSE circular **NSE/FA/64232**, 27 Sep 2024 — transaction charges from 1 Oct 2024.
  <https://nsearchives.nseindia.com/content/circulars/FA64232.pdf>
- NSE circular **NSE/FA/73061**, 27 Feb 2026 — IPFT roll-back, total unchanged.
  <https://nsearchives.nseindia.com/content/circulars/FA73061.pdf>
- NSE circular **NSE/FA/56129**, 24 Mar 2023 — the pre-Oct-2024 options slab schedule.
  <https://archives.nseindia.com/content/circulars/FA56129.pdf>
- SEBI circular **SEBI/HO/MRD/TPD-1/P/CIR/2024/92**, 1 Jul 2024 — uniform MII charges.
- SEBI circular **SEBI/HO/MRD/TPD-1/P/CIR/2024/132**, 1 Oct 2024 — index derivatives framework.
- Zerodha Z-Connect, *Revision in exchange transaction charges and STT from October 1, 2024*.
- ClearTax, *Securities Transaction Tax* — the April 2026 rates.
- Zerodha, *Charges* — live card, corroborating the currently-in-force rates.
- 5paisa, *Hedged vs Naked Option Selling: Margin, Risk Caps and SEBI Rules* — the
  margin calibration target.

Data sources and their verified coverage are in `FEASIBILITY.md`.
