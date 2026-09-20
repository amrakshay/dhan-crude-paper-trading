# Arbitrage: the strategies, and what each one actually earns

The theory, with a source for every claim, written before anything was
measured. `FEASIBILITY.md` says which of these this repository can test;
`METHOD.md` says how; `README.md` has the results.

A note on the shape of the field. "Arbitrage" in a textbook means a riskless
profit from a price inconsistency. Nothing below is that. Every strategy here
is a *convergence* trade: two prices that a model says should be related, a
position that pays when the relation reasserts itself, and a real risk that it
does not. The word survives because the positions are market-neutral, not
because they are riskless.

---

## 1. Cash-futures basis (cash and carry)

### The arithmetic

A futures contract on a non-dividend-paying share, with `T` years to expiry,
has a no-arbitrage fair value

```
F = S * e^(r*T)                      (continuous compounding)
F = S * (1 + r*T)                    (simple, which is how a 30-day Indian
                                      contract is normally quoted)
```

and with a dividend of `D` going ex at time `t < T`,

```
F = (S - D * e^(-r*t)) * e^(r*T)
```

because the long futures position does not receive the dividend and the cash
holder does. The **basis** is `F - S`. The **implied financing rate** —
the number that decides whether the trade is worth doing — is

```
r_implied = (F / S - 1) * (365 / days_to_expiry)
```

Buy the share, sell the future, hold to expiry: at expiry `F = S` by the
settlement mechanism, so the position converges and the profit is the basis
you locked in, less costs. That is the whole trade. It is a **lending**
trade dressed as an equity trade: you are financing someone's long futures
position at `r_implied` and being repaid at expiry.

### The trap in annualising

The `365/days` term is where people fool themselves. A 0.4% basis captured
over 5 days annualises to 29%, and quoting that is meaningless unless the
opportunity actually recurs 73 times a year at the same size with the same
costs. It does not: the basis is widest right after a contract lists and
converges towards expiry, so the 5-day captures are available only near the
end, when the absolute amount is small and the cost is unchanged. **Annualise
only a rate you could actually roll into.** The honest statement of a
cash-and-carry result is the annualised rate over a *full* contract cycle,
net of one complete set of round-trip costs and one roll.

### Why the basis goes negative

Textbook `F > S` whenever `r > 0`. In practice Indian single-stock futures
trade below spot regularly, and the reasons are all frictions the formula
omits:

- **A dividend the formula did not know about.** Any ex-date inside the
  contract's life pushes fair value down by the discounted dividend. A large
  special dividend can put the basis firmly negative and it is not an
  opportunity.
- **Short-selling demand.** Futures are the only practical way for most
  participants to short an Indian share (see §4). When shorting pressure
  exceeds financing demand the future is bid below fair value, and the
  cash-and-carry trade *reverses*: sell cash, buy futures — which requires
  owning the share already, because you cannot short the cash leg.
- **Stock lending scarcity.** The reverse trade needs the share, and if it is
  hard to borrow the arbitrage does not close.
- **Position limits and margin.** Both legs consume capital that has
  alternative uses; near limits the spread stays open because nobody can put
  it on.

### What it pays, honestly

The gross annualised basis on liquid Indian single-stock futures runs a few
points above the repo rate. Against that a retail participant pays, on a
complete cycle: brokerage on both legs both ways, STT on the cash buy *and*
the cash sell at 0.1% each (delivery), STT on the futures sale at 0.02%,
stamp duty on both buys, exchange transaction charges, SEBI turnover fee, GST
on the brokerage and charges, a depository charge on the cash sell — plus
margin blocked on the futures leg for the whole holding period, and the
impact cost of getting two legs on at quoted prices.

This experiment's charges engine puts the statutory-and-commercial part of
that at roughly **0.22–0.25% of gross notional** for the cash leg round trip
(`COSTS.md`). On a one-month contract that is about **2.7–3.0 percentage
points annualised**, consumed before any basis is earned.

**The honest prior is therefore that retail cash-futures arbitrage nets
approximately a treasury-bill return, with materially more operational risk
than a treasury bill.** If a backtest says otherwise, the costs are wrong
before the alpha is right. This experiment could not test it — there is no
futures history (`FEASIBILITY.md` §2) — and that prior is left standing as a
prior, not converted into a measurement it does not have.

### Expiry-day convergence

Indian equity futures are **cash-settled** against the settlement price of
the underlying, which for NSE is the volume-weighted average of the last 30
minutes of the cash market on expiry day. So convergence is not an
approximate market phenomenon; it is imposed by the settlement formula. That
is what makes the trade a financing trade rather than a bet. The residual
risk is that the cash leg's own exit does not print at the same VWAP, which
is a real execution risk on an illiquid name.

---

## 2. Pairs and statistical arbitrage

### Correlation is the wrong test, and the reason is not pedantry

Two series are **correlated** when their *returns* move together. Two series
are **cointegrated** when some linear combination of their *levels* is
stationary. These are different properties and neither implies the other.

The distinction is the whole strategy. A pairs trade is long one leg and
short a multiple of the other and it profits when the *spread* returns to
its mean. If the spread is not mean-reverting there is nothing to return to,
and high return-correlation gives no such guarantee: two shares can move
together every day and drift apart forever, which is precisely what two
independent random walks with drift do. Correlation measures co-movement;
cointegration measures co-*integration*, a shared long-run level.

This experiment measures the gap directly rather than asserting it. On
synthetic pairs with a shared trend but no cointegration — the realistic
model of two sector peers — naive correlation exceeds 0.8 in **43%** of
cases while the cointegration test correctly rejects; see `NEGATIVE_CONTROLS.md`.

### Engle-Granger

Two steps.

1. Regress one leg's level on the other's, with an intercept:
   `y_t = alpha + beta * x_t + e_t`. OLS is *super-consistent* here when the
   pair really is cointegrated — `beta` converges at rate `T` rather than
   `sqrt(T)` — which is why the crude first stage is acceptable.
2. Test `e_t` for a unit root with an augmented Dickey-Fuller regression. If
   the residual is stationary the pair is cointegrated and `beta` is the
   hedge ratio.

Two properties that matter in practice:

- **The critical values are not the ADF ones.** `e_t` is a fitted residual
  chosen to minimise its own variance, so it looks more stationary than an
  arbitrary series does and the test must be harder to pass. The
  Engle-Granger critical value for two variables is about `-3.34` at 5%
  against `-2.86` for a plain ADF.
- **It is not symmetric.** Regressing `y` on `x` and `x` on `y` give
  different statistics on a finite sample, and a screen that quietly takes
  the better of the two has performed a selection that its p-value does not
  know about. This turns out to double the false-positive rate; see
  `METHOD.md`.

### Johansen

A vector error-correction model estimated by reduced-rank regression, testing
how many independent cointegrating vectors a system of `n` series has via the
trace statistic. For `n = 2` it answers the same question as Engle-Granger,
with two advantages: it is **symmetric** in the two legs, and it estimates
the cointegrating vector by maximum likelihood rather than by a first-stage
OLS whose direction was chosen arbitrarily. Its cost is more machinery and
critical values that depend on the deterministic specification.

Here it is used as a **cross-check**, not a replacement: where the two
disagree on a pair, the pair is marginal, and marginal pairs are exactly the
ones a screen over 125,000 candidates is full of.

### The half-life, and what it is for

Model the spread as Ornstein-Uhlenbeck:

```
ds = theta * (mu - s) dt + sigma dW
```

whose discrete analogue is an AR(1), `Δs_t = lambda * (s_{t-1} - mu) + eps`,
with

```
half-life = -ln(2) / ln(1 + lambda)      bars
```

This is the single most useful number about a pair and it is routinely
ignored. It should set:

- **the holding cap.** A spread with a 10-day half-life that has not reverted
  in 40 days is not slow, it is broken; holding it to a happy ending is how a
  pairs book acquires its tail.
- **the formation window.** A test cannot distinguish a slow OU process from
  a random walk without enough data. This experiment measures the boundary:
  at a 504-bar (two-year) formation window the test has 80%+ power out to a
  half-life of about 10 bars and essentially none beyond 40. So a two-year
  window does not find slow pairs — it finds only fast ones, and reports the
  rest as random walks. That is a statement about the window, not the pair.
- **the sanity check on the entry band.** A 2-sigma entry with a 10-day
  half-life implies roughly a 10-day expected hold; with an 80-day half-life
  it implies a quarter, over which the costs and the risk of a structural
  break are completely different.

The estimate is **biased low** — a finite observation window makes an OU
process look faster than it is — by a median of about 25% in this
experiment's synthetic test. A holding cap derived from it is therefore too
short, which is the safe direction.

### Entry and exit bands

Standardise the spread to `z = (s - mu) / sigma`, both estimated on the
formation window and **frozen**, then:

- **enter** at `|z| >= z_in` (classically 2.0), short the rich leg and long
  the cheap one;
- **exit** at `|z| <= z_out` (classically 0.0 — the mean — or 0.5);
- **stop** at `|z| >= z_stop` (classically 3.0–4.0), on the reading that a
  spread that keeps widening has changed regime rather than become a better
  bargain.

The stop is the part practitioners argue about, because it is where the
strategy's tail lives. Without one, a pairs book has a high win rate and
unbounded left tail; with one set too tight, it converts ordinary noise into
realised losses. This experiment sweeps it rather than assuming.

### Re-estimating the hedge ratio, or freezing it

Two defensible answers and they are different strategies.

- **Frozen** at formation. The spread traded is the one that was tested, the
  z-score means what it says, and a structural break shows up as the spread
  never coming back — which a stop and a holding cap can act on. This is the
  version the classic literature tests.
- **Rolling** (re-estimate `beta` on a trailing window). Adapts to a slowly
  changing relationship, at the cost that the thing you are trading changes
  underneath the position, and a spread that is drifting apart gets its
  hedge ratio redefined until it looks fine again — which hides exactly the
  break you needed to see.

This experiment freezes the ratio at formation and treats a persistent
divergence as information, with re-estimation as a swept alternative.

### Structural breaks, and retiring a pair

A pair stops cointegrating for ordinary corporate reasons: a merger, a
demerger, a change of business mix, a regulatory change that hits one leg,
an index inclusion. What it looks like in the data is a spread that leaves
its band and does not come back, and the failure mode is a backtest that
holds it until it happens to.

The rule has to be set **in advance** and the backtest has to actually
execute it. Three mechanisms, all used here:

1. a **z-stop** — the position closes at `|z| >= z_stop`;
2. a **holding cap** in bars, set as a multiple of the estimated half-life;
3. **re-testing at each formation date** — a pair that no longer passes
   cointegration is not re-selected, so it leaves the book by attrition even
   if no individual position stopped out.

### What the literature found, and when it stopped working

Gatev, Goetzmann and Rouwenhorst (2006), the canonical study, report average
annualised excess returns of up to **11%** for self-financing pairs
portfolios on US equities 1962–2002, exceeding conservative transaction-cost
estimates. Do and Faff (2010) extend the sample and find profitability
**declining materially after 2002**, with the decline concentrated in the
simple distance-based method and strongly sensitive to cost assumptions.

The reading to carry into this experiment is not "it does not work" but
"whatever worked was an execution edge, and it was competed away first where
execution was cheapest". Which leads to the question the handoff asks.

---

## 3. Index arbitrage and calendar spreads, briefly

**Index arbitrage** is the same cash-and-carry trade with a basket for the
cash leg: buy all fifty NIFTY constituents in index weight, sell the index
future, collect convergence. Its problems are not conceptual but
operational — fifty simultaneous fills, weight drift between rebalances, the
dividend stream of fifty names, and a basket whose impact cost is the sum of
fifty impact costs. It is a desk trade with a program-trading system, and the
spread is measured in basis points precisely because those desks exist.

**Calendar spreads** trade the relationship between two expiries of the same
underlying: long the near contract, short the far, or the reverse. The
relationship is the same cost-of-carry formula applied twice, so the spread
is a pure play on the *implied financing rate between the two dates*, and it
is much cheaper to hold than cash-and-carry because both legs are futures —
no delivery STT, no depository charge, and the margin is offset as a
recognised spread. It is the most capital-efficient version of the carry
trade and, correspondingly, the most competed.

Neither is testable here (`FEASIBILITY.md` §2).

---

## 4. The reality check: who is on the other side

### Short selling in India is a constraint, not a cost

This governs everything.

- **Naked short selling is prohibited.** Every investor must honour delivery;
  an unsquared short is bought in through the exchange's auction at a
  penalty.
- **Retail participants can short in the cash segment intraday only**, and
  must square off before the close.
- **An overnight cash short requires SLB** — Securities Lending and
  Borrowing, run through the clearing corporation, available on a limited
  list and thin on most of it.
- **Single-stock futures** are the practical route to an overnight short, and
  exist for 210 of the 500 names in this repository's panel.

So a daily-bar pairs strategy in India is not "a strategy with a shorting
cost". It is a strategy that **cannot be run at all** in the cash segment,
and whose futures version needs data this repository does not have. That is
stated once here and carried through every result.

### Who the counterparty is

Retail basis arbitrage competes with proprietary desks that see the same
spread with lower costs (no brokerage, institutional transaction-charge
slabs before the true-to-label reform, and until recently no STT asymmetry),
faster execution, and co-located infrastructure. Any spread visible on an
end-of-day bar has been visible to them all day.

What is genuinely left for an end-of-day participant is a narrow list:

- **The roll, held to expiry.** A carry trade put on once and held to
  settlement does not compete on speed; it competes on cost of capital, and
  a retail participant's cost of capital is not obviously worse than a
  desk's. The return is the implied financing rate, less costs, and the
  honest expectation is that this lands near the risk-free rate.
- **Pairs in the second tier.** The names the desks do not bother with,
  because the size is too small — which is the same names whose impact cost
  is worst and whose spread is widest for that reason. This is not a free
  lunch; it is a liquidity premium, and it should be measured with a turnover
  floor and reported as a function of it.
- **Nothing intraday.** A participant acting on a daily bar is acting on
  information between fifteen minutes and a day old.

The expected honest conclusion is therefore modest, and a modest result
properly measured is the deliverable — not a failure.

---

## Sources

Charges and taxes, with full citations, are in the rate cards under
`conf/charges/`; each rate carries its own primary URL, as-of date and
confidence marker. The principal ones:

- NSE circular **NSE/FA/64232**, 27 Sep 2024, effective 1 Oct 2024 —
  transaction charges: cash Rs 2.97/lakh, equity futures Rs 1.73/lakh.
  <https://nsearchives.nseindia.com/content/circulars/FA64232.pdf>
- NSE circular **NSE/FA/73061**, 27 Feb 2026, effective 1 Mar 2026 —
  re-split of transaction charge and IPFT contribution.
  <https://nsearchives.nseindia.com/content/circulars/FA73061.pdf>
- **Finance (No. 2) Act 2004**, Chapter VII — STT: delivery purchase and sale
  0.1% each; intraday sale 0.025%.
- **Finance Act 2023** (passed 24 Mar 2023, effective 1 Apr 2023) — STT on
  sale of futures 0.01% → 0.0125%.
- **Finance (No. 2) Act 2024** (effective 1 Oct 2024) — STT on sale of
  futures 0.0125% → 0.02%.
  <https://elplaw.in/wp-content/uploads/2024/07/Budget-Buzz-Increase-in-STT-in-FO-segment.pdf>
- Broker rate cards: <https://dhan.co/pricing/>, <https://zerodha.com/charges/>
- **SEBI short-selling framework** and NSE's SLB scheme —
  <https://www.nseindia.com/market-data/securities-lending-and-borrowing>
- Gatev, Goetzmann & Rouwenhorst, "Pairs Trading: Performance of a
  Relative-Value Arbitrage Rule", *Review of Financial Studies* 19(3), 2006.
  <https://www.nber.org/papers/w7032>
- Do & Faff, "Does Simple Pairs Trading Still Work?", *Financial Analysts
  Journal*, 2010.
- Osterwald-Lenum, "A Note with Quantiles of the Asymptotic Distribution of
  the ML Cointegration Rank Test Statistics", *Oxford Bulletin of Economics
  and Statistics* 54, 1992.
