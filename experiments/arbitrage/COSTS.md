# Costs: the engine, its validation, and what a pair round trip actually pays

Built before the strategy, because a market-neutral spread is often worth
under 1% and is paid for with two round trips. If the cost model is wrong the
answer is decided before it is measured.

Reproduce with:

```bash
.venv/bin/python scripts/charges_check.py      # writes out/charges_check.json
```

---

## 1. The cards

Three rate cards under `conf/charges/`, in the same format as the
application's own (`backend/conf/charges/`): every rate carries a primary
source URL, an as-of date and a confidence marker, and no rate is hardcoded
in Python.

One addition the live application does not need and a backtest cannot do
without: **rates are date-aware.** A rate may be a list of
`{from: YYYY-MM-DD, value: x}` entries and the engine picks the one in force
on the trade date. The sample runs 2015-07 to 2026-09 and several of these
rates changed inside it — STT on futures changed twice, stamp duty's whole
regime changed in July 2020, and NSE's transaction charges were restructured
in October 2024. A single-rate card applied to an eleven-year backtest
silently charges 2026's taxes on a 2016 trade.

| card | scope | why it exists |
|---|---|---|
| `nse-equity-delivery` | cash segment, CNC | the cash-leg counterfactual. **Not executable** for a pair — see `FEASIBILITY.md` §3 |
| `nse-equity-intraday` | cash segment, MIS | the only legal cash short, and not backtestable on daily bars |
| `nse-equity-futures` | equity derivatives, futures | what an EOD pair must actually use |

---

## 2. Validation, three ways

### 2.1 Against a published broker calculator

Equity intraday, buy 100 at ₹1,000 and sell at ₹1,010. Published total
₹82.72, brokerage ₹40.00
(source: <https://accelpix.com/brokerage-calculator/brokers/zerodha.html>,
retrieved 2026-09-20).

| | |
|---|---|
| published | ₹82.72 |
| this engine | **₹82.97** (divergence **+₹0.25**) |
| the same arithmetic at ₹297/crore | ₹82.73 (divergence +₹0.01) |

**The divergence is attributed, not tuned away.** The gap is the NSE IPFT
contribution — ₹10 per crore — which the calculator omits and the card
charges. Reproducing the calculator's rate reproduces its total to a paisa,
which locates the difference precisely.

The card is **not** changed to match. ₹307 per crore is what NSE/FA/64232 and
NSE/FA/73061 actually levy, and a cost model that leaves a levy out is wrong
in the direction that flatters the strategy. This follows the repository's own
rule, which keeps a documented ₹0.01 divergence from Zerodha in the
application's charges tests for the same reason.

### 2.2 Against longhand arithmetic

An engine checked against itself proves nothing, so a delivery round trip
(₹2,50,000 in, ₹2,62,500 out) is computed line by line in
`scripts/charges_check.py`, without the engine: STT ₹512.50, transaction and
IPFT ₹15.73, SEBI ₹0.51, stamp ₹37.50, DP ₹12.50, GST ₹5.17 — **₹583.92**.
The engine returns ₹583.92.

### 2.3 Against the application's own card

Every current delivery rate is compared with
`backend/conf/charges/nse-equity-delivery.yaml`, read as YAML and never
imported, so the experiment stays self-contained. All six agree exactly.

---

## 3. What a pair round trip costs

A pair is **two instruments**, so one complete round trip is **four fills**.
Quoted against gross notional — long leg plus short leg — which is the same
denominator the spread's own return uses.

| notional per leg | cash delivery | cash intraday | **futures** |
|---:|---:|---:|---:|
| ₹50,000 | 0.2520% | 0.1063% | **0.0974%** |
| ₹1,00,000 | 0.2372% | 0.0827% | **0.0738%** |
| ₹2,50,000 | 0.2284% | 0.0544% | **0.0454%** |
| ₹5,00,000 | 0.2254% | 0.0449% | **0.0360%** |
| ₹10,00,000 | 0.2240% | 0.0402% | **0.0313%** |

### Three things this table says

**Cash delivery is disqualifying, and STT is almost all of it.** Of the 0.228%
at ₹2.5 lakh a leg, 0.200% is STT — 0.1% on each side of each leg, and a pair
has two legs going both ways. Against a spread that is typically worth well
under 1% that is a quarter of the gross before anything else, and it barely
falls with size because it is a pure percentage. It would not matter that the
cash short is illegal overnight; the tax alone would settle it.

**Futures are seven times cheaper at size,** because STT on a futures sale is
0.02% on one side only, there is no depository charge, and stamp duty is
0.002% rather than 0.015%. This is the whole reason the strategy has to be a
futures strategy, quite apart from the shorting rule that forces it there.

**There is a minimum economic trade size, and it is set by brokerage.** At
₹50,000 a leg the futures round trip costs 0.097%; at ₹10,00,000 it costs
0.031%. The difference is almost entirely the flat ₹20 per order, paid four
times. A pairs book running small positions pays three times the rate a large
one does — so "trade more pairs, smaller" is not free diversification, it is
a cost decision.

### And at a realistic position size, the biggest line item is BROKERAGE

Summing every line of every fill in the log-price book — 687 trades, 2,748
fills, ₹2 lakh gross per pair:

| line item | total | share |
|---|---:|---:|
| **brokerage** | **₹53,463** | **59%** |
| STT | ₹17,989 | 20% |
| GST | ₹10,652 | 12% |
| exchange transaction charge | ₹5,414 | 6% |
| stamp duty | ₹2,766 | 3% |
| SEBI turnover fee | ₹300 | 0.3% |
| **total** | **₹90,585** | |

That is not what a discussion of Indian trading costs usually expects, and it
follows directly from the table above: at ₹1 lakh a leg the flat ₹20 per order
binds, and a pair round trip pays it four times. The statutory taxes are the
smaller half of the problem.

It raises the obvious question, so it was measured:

| gross per pair | costs vs gross P&L | excess return | max drawdown |
|---:|---:|---:|---:|
| ₹50,000 | 183% | −0.20% | −2.2% |
| ₹1,00,000 | 180% | −0.36% | −7.1% |
| ₹2,00,000 | 132% | −0.32% | −16.7% |
| ₹5,00,000 | **72%** | **+0.40%** | **−41.3%** |

**Size does not fix the problem; it levers it.** Costs as a share of gross P&L
more than halve, exactly as brokerage amortises — and the excess return moves
by six tenths of a point while the drawdown grows eighteen-fold, because
bigger positions on the same ₹10 lakh is simply more leverage. The cost
efficiency is real and the return improvement is leverage wearing its clothes.

The usable form of the finding is narrower and it is a constraint, not an
opportunity: **a pairs book running ₹50,000 positions pays roughly three times
the cost rate of one running ₹10 lakh positions**, so "more pairs, smaller" is
not free diversification.

---

## 4. Margin is not a cost, and it is not free either

The futures card carries `margin.span_plus_exposure_fraction: 0.20`. It is
marked `APPROXIMATION`, not sourced, because SPAN is contract-specific and no
historical SPAN file is available here; 20% is the round number at the top of
the 12–18% SPAN plus 3–5% exposure band for a liquid single-stock future, so
the book is modelled as *more* constrained than it would be.

It does not reduce P&L. It reduces how much of the book is free, and a pair
posts it on both legs. The backtest reports gross exposure every day, so the
requirement can be re-derived at any other fraction, and the fraction is
swept.

**Margin can be met with pledged collateral.** Liquid-fund or treasury-bill
units can be pledged against exchange margin, so the base case credits cash
and posted margin with the policy rate (`collateral_yield: 1.0`). That is not
a generosity — it is what makes the headline number mean anything. This book
is deployed about a fifth of the time, and with idle cash earning zero the
reported CAGR is mostly a statement about how long the strategy sat out. The
pessimistic zero-yield setting is swept.

---

## 5. What the cost model does not contain

Stated here rather than discovered later.

- **Impact and the bid-ask spread.** Fills are at the open in unlimited size.
  A turnover floor is applied to both legs and its sensitivity is swept, but
  there is no impact model. For a second-tier name this is the largest missing
  cost, and it is missing in the direction that flatters.
- **Futures basis and roll.** The futures leg's *price* is taken from cash;
  only its *charges* come from the futures card. There is no futures history
  in this repository (`FEASIBILITY.md` §2), so basis noise and the cost of
  rolling across an expiry are unmodelled.
- **Lot-size granularity.** Futures trade in lots, so a hedge ratio cannot be
  expressed exactly. Historical lot sizes are not available; the effect is a
  residual hedging error, not a fee.
- **Delivery brokerage is today's zero throughout.** Retail delivery brokerage
  was commonly 0.1–0.5% in 2015. Using zero flatters the early years of the
  cash comparison, and no sourced historical retail schedule was obtained.
- **Securities lending fees**, for the SLB route that this experiment does not
  take.
