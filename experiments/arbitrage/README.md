# Arbitrage on the NSE: research, costs, and an honest measurement

Take the arbitrage strategies, work out which of them this repository's data
can actually test, build the cost model *first*, measure the rest honestly,
and finish with a backtest dashboard.

Nothing here is imported by the paper-trading application, nothing places or
simulates a broker order, and the application's database is opened read-only.
It is self-contained under `experiments/arbitrage/`.

| File | What it is |
|---|---|
| `FEASIBILITY.md` | **Step 0** — what the data supports, and why futures arbitrage is off the table |
| `RESEARCH.md` | the strategies: the arithmetic, the frictions, every claim sourced |
| `COSTS.md` | the charges engine, validated three ways, and what a pair round trip pays |
| `METHOD.md` | how it works, and the four bugs worth remembering |
| `NEGATIVE_CONTROLS.md` | how often the procedure is fooled — size, power, and the placebo |
| `PAIRS_SELECTION.md` | **the central result**: does the universe contain cointegrated pairs? |
| `PAIRS_SWEEP.md` | nine axes, one at a time — the shape, not the best cell |
| `FINAL_LOGIC.md` | **the decided rule set**, and the decision not to trade it |
| `arblib/` | the library |
| `scripts/` | charges, statistics, screens, backtest, sweeps, dashboard |
| `out/` | every result quoted below, plus the two dashboards |

```bash
cd experiments/arbitrage
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

.venv/bin/python scripts/run_all.py              # everything, ~60-75 min
.venv/bin/python scripts/run_all.py --quick      # smaller samples, ~12 min
.venv/bin/python scripts/feasibility.py          # Step 0 on its own
.venv/bin/python scripts/charges_check.py        # the cost model, validated
.venv/bin/python scripts/validate_stats.py       # size, power, negative controls
.venv/bin/python scripts/validate_backtest.py    # can the engine find a planted edge?
```

---

## 0. Findings at a glance

**Step 0 settled what could be measured.** Dhan's instrument master carries
**three** futures expiries — near, next and far month — and no expired contract
has a security id anywhere. So no futures history can be obtained, and index
arbitrage, cash-futures basis and calendar spreads are **not testable here**.
No futures series was synthesised from a carry assumption, because the basis
*is* the subject of those strategies. Pairs trading is what the data supports.

**A short cannot be held overnight in Indian cash equity.** Not a cost — a
constraint. It restricts the universe to the 210 F&O-eligible names and makes
the short leg a futures position, which is the data that does not exist. The
futures leg is therefore *modelled* and labelled as such everywhere.

**The cost model was built first, and it decides the strategy's shape.** A
complete pair round trip — two instruments, four fills — costs **0.22–0.25%**
of gross notional in the cash segment and **0.031–0.097%** in futures. Cash
delivery STT alone is 0.2%; against a spread worth well under 1% that settles
it before the shorting rule does.

**And the largest single charge is not a tax — it is brokerage**, ₹53,463 of
the ₹90,585 the log book paid, because a flat ₹20 per order is charged four
times per pair round trip and binds at any realistic position size. Scaling
positions up cuts costs from 183% of gross P&L to 72%, and raises the
drawdown from −2.2% to −41.3% on the same capital: size levers the edge, it
does not create one.

**The screen ran 179,673 hypothesis tests. About 9,000 pairs pass by chance.**
That number, not zero, is what any claim of "finding cointegrated pairs" has
to beat. On price levels, **one pair** survives a false-discovery-rate
correction in eight years.

**But a real-data placebo shows the test is conservative on share prices, and
against that floor there IS a signal.** Replacing every symbol's formation
window with a window of its own history from a different date — same fat
tails, same volatility clustering, no possible relationship — gives 0.58×
the chance rate. The real panel gives 0.81×, and on log prices 0.95×. Paired
across 16 formation dates the real panel beats the placebo on 15 of them,
**p = 0.0008**.

**So cointegration exists in aggregate and cannot be localised pair by pair.**
That is the result. Roughly 2,200 of the 7,307 nominal passes are genuine and
roughly 5,100 are noise, and the p-values do not separate them.

**What a book on it earns: the edge is real, small, and smaller than the
costs.** On log prices, over 687 trades:

| | |
|---|---:|
| gross P&L on deployed exposure | **+0.69%/yr** |
| costs | **−0.91%/yr** |
| **net** | **−0.22%/yr** |

That is the textbook outcome and it is the honest one: *a costs problem
wearing a strategy costume.* On price levels there is not even that — gross
P&L is **negative before a single charge**.

**The sweeps split by specification, and the split is informative.** Nine axes,
37 configurations each. On price levels **1 of 37 is positive** and the four
cells whose confidence intervals actually resolve all resolve *against* the
strategy; the least-bad cells are the ones that trade least. On log prices
**10 of 37 are positive**, the best at +3.38% excess, and the shape reverses —
trading *more* helps, entry at 1.0 sigma gives a 54.7% win rate, and `z_in`
responds monotonically across all five cells. **Not one log cell has a
confidence interval that excludes zero.**

**Applying the correction the arithmetic demands ends the strategy.** FDR at
q=5% takes the book from 687 trades to 10, and from −0.22%/yr to the risk-free
rate plus 27 basis points — with a bootstrap CI that straddles zero. Six pairs
in eight years is not a strategy; it is the statistics saying there is nothing
here, and the book obeying.

**The date-matched random-pair baseline is not beaten.** Same universe, same
dates, same sizing and costs, pairs drawn at random: +0.87% excess CAGR
against the screened book's −0.32%. Its own bootstrap also straddles zero, so
the correct statement is that **neither book has been shown to work** — not
that random is better.

**Four bugs, all found by controls rather than by reading code**
(`METHOD.md` §3), and one of them is the reason to trust the rest: the engine
once *found* twelve planted cointegrated pairs and then *lost money on them*,
which no market can do. After the fix the same test returns a 92.9% win rate.
A backtest that reports no edge is only informative if it would have reported
one.

---

## 1. What can be tested, and what cannot

| Strategy | Needs | Status |
|---|---|---|
| Pairs / statistical arbitrage | two cash equities | **feasible — measured below** |
| Index arbitrage | index futures history | not feasible |
| Cash-futures basis | per-stock futures history | not feasible |
| Calendar spreads | two futures expiries over time | not feasible |
| ETF / NAV arbitrage | ETF prices + daily NAV | not in the database |

NSE `FUTSTK` in the instrument master: 647 rows, **3 distinct expiries**.
NSE `FUTIDX`: 18 rows, 3 expiries. MCX `FUTCOM`: 157 rows, 45 expiries, all in
the future. Crude has traded on MCX for a decade and the master holds not one
expired contract. The most that could ever be stitched is about three months,
and that window shrinks by a month every month.

Full evidence, and the unadjusted-corporate-action survey, in
[`FEASIBILITY.md`](FEASIBILITY.md).

## 2. What a pair costs to trade

Percentage of gross notional for one complete round trip — two instruments,
four fills.

| notional per leg | cash delivery | cash intraday | **futures** |
|---:|---:|---:|---:|
| ₹50,000 | 0.2520% | 0.1063% | **0.0974%** |
| ₹2,50,000 | 0.2284% | 0.0544% | **0.0454%** |
| ₹10,00,000 | 0.2240% | 0.0402% | **0.0313%** |

Validated against a published broker calculator to **+₹0.25** on a ₹1 lakh
intraday round trip, and the divergence *attributed* — it is the NSE IPFT
contribution of ₹10 per crore, which the calculator omits. Reproducing the
calculator's rate reproduces its total to a paisa. The card is not changed to
match; a cost model that leaves a levy out is wrong in the direction that
flatters. Details in [`COSTS.md`](COSTS.md).

## 3. Does the universe contain cointegrated pairs?

| | nominal 5% passes | expected by chance | ratio | FDR survivors |
|---|---:|---:|---:|---:|
| real panel, price levels | 7,307 | 8,984 | 0.81× | **1** |
| real panel, log prices | 8,227 | 8,641 | 0.95× | **6** |
| **real-data placebo** | 5,115 | 8,799 | **0.58×** | **0** |

Paired across the 16 formation dates, real against placebo: mean ratio 0.830
vs 0.581, **1.43×**, paired t p = 0.0008, Wilcoxon p = 0.0008, higher on 15 of
16 dates.

Read [`PAIRS_SELECTION.md`](PAIRS_SELECTION.md) for why 1.0 is the wrong
baseline and the placebo is the right one.

## 4. What a book on it earns

Trading 2018-07-18 to 2026-09-04, ₹10 lakh, futures rate card, cash and margin
earning the policy rate.

| | levels, nominal 5% | levels, FDR | **log, nominal 5%** | **log, FDR** | random baseline |
|---|---:|---:|---:|---:|---:|
| pairs | 320 | 1 | 320 | 6 | 320 random |
| trades | 1,004 | 7 | 687 | 10 | 83 |
| win rate | 31.6% | 28.6% | 36.1% | 40.0% | 36.1% |
| CAGR | 0.96% | 5.58% | 5.27% | 5.86% | 6.46% |
| **excess over cash** | **−4.64%** | −0.02% | **−0.32%** | **+0.27%** | +0.87% |
| max drawdown | −28.6% | −1.3% | −16.7% | −1.3% | −4.6% |
| gross P&L | −₹2,73,420 | +₹2,790 | **+₹68,647** | +₹27,591 | +₹1,01,392 |
| costs | ₹1,34,309 | ₹903 | **₹90,585** | ₹1,378 | ₹10,938 |
| net P&L | −₹4,07,729 | +₹1,887 | −₹21,938 | +₹26,213 | +₹90,454 |
| gross return on exposure | −2.72%/yr | +4.82%/yr | **+0.69%/yr** | +19.31%/yr | +7.53%/yr |
| net return on exposure | −4.05%/yr | +3.26%/yr | **−0.22%/yr** | +18.35%/yr | +6.72%/yr |
| deployment | 1.24× | 0.01× | 1.22× | 0.02× | 0.17× |

**Return on exposure, not CAGR, is the comparison to read.** With cash earning
the policy rate, a book that never trades reports the risk-free rate and a
respectable Sharpe; CAGR rewards sitting out. Dividing trading P&L by the
average gross exposure actually carried gives the rate the *deployed* rupees
earned, and it reverses the apparent ranking of these books.

**Every bootstrap CI straddles zero.** The log book's daily excess return is
+0.08%/yr with a 95% interval of [−5.85%, +5.96%]; its per-trade mean is −₹32
with [−₹797, +₹739]; the random baseline's is +₹1,090 with [−₹1,480, +₹3,784].
Nothing here has been shown to work, in either direction.

And a meta-level caution that applies to the whole table: **two
specifications were tried, nine axes swept on each, and the best is being
looked at.** That is 74 configurations. The same arithmetic this experiment
applies to 179,673 pair tests applies to them, and the appropriate discount on
a +3.38% best cell whose own CI straddles zero is most of it.

## 5. The sweeps

Nine axes, 37 configurations per specification. Three shapes are worth
reading.

**On price levels, more pairs is monotonically worse** — 5 pairs per window
gives −2.49% excess, 80 gives −11.27%, and the drawdown goes from −5.6% to
−50.6%. If the ranked list carried information, diluting it would degrade the
result gradually. It collapses, which is what a list ordered by luck looks
like.

**On log prices the shape reverses** — 5 pairs gives +0.34%, 80 gives +3.38%,
and a *lower* entry threshold helps monotonically across all five cells
(z = 1.0 gives +2.93% at a 54.7% win rate). Where levels wanted to trade less,
logs want to trade more. That is consistent with a small genuine edge averaged
over more independent bets, and hard to produce from pure noise — but no cell's
CI excludes zero, so it is a shape, not a result.

**Applying the multiple-comparison correction is the axis with the clearest
answer on both** — and on both its answer is to stop trading.

Full tables, and why the stop is kept even though removing it measures better,
in [`PAIRS_SWEEP.md`](PAIRS_SWEEP.md).

## 6. Known gaps

Kept honest and current rather than quietly dropped.

- **Survivorship, and it cannot be fixed with this dataset.** The 500 symbols
  are today's index constituency held back to 2015. A pairs book's
  catastrophic case is the leg that stops trading, and by construction none
  are present. Every number here is flattered by an unknown amount.
- **The short leg is modelled, not measured.** No futures price history exists
  (`FEASIBILITY.md` §2), so the futures leg is priced off cash and only its
  *charges* come from the futures card. Basis noise, roll slippage and
  lot-size granularity are absent. All three would make the result worse.
- **The log-spread book is not rebalanced.** A log spread is a fixed *value*
  ratio and drifts out of hedge as prices move. Over a ~18-bar hold the drift
  is small, but not rebalancing is an optimism in favour of the specification
  that did best.
- **Two specifications were tried and the better one is reported.** That is
  itself a selection over two, at the meta level, and the appropriate discount
  is small but not zero.
- **No impact or liquidity model.** Fills are at the open in unlimited size
  above a turnover floor whose sensitivity is swept.
- **Delivery brokerage is today's zero throughout**, which flatters the early
  years of the cash-cost comparison. No sourced historical retail schedule was
  obtained.
- **Johansen's critical values are asymptotic and over-sized** — it rejects at
  14–15% against a nominal 5% on the controls. It is used only as a secondary
  opinion, never as a selector, and the number is reported rather than fixed.
- **Daily bars only.** Statistical arbitrage as practised is largely intraday,
  where half-lives are minutes. This experiment says nothing about that.
- **The unverified Quote/Full packet field mapping** in the root `CLAUDE.md`
  does not affect anything here — this experiment reads `daily_bars`, which
  comes from the chart endpoint, not from the feed.

## 7. The most promising thing not done

Cut the universe with **economics before statistics**. Same-sector pairs,
dual-listed share classes, a holding company against its subsidiary — a prior
imposed *before* the screen is the legitimate way to take 180,000 hypothesis
tests down to 200, and at 200 tests an FDR correction admits something. The
whole difficulty measured here is that an unrestricted screen over a
large-cap universe is mostly a machine for generating coincidences.

---

## 8. The dashboards

Two, because the two specifications tell different stories and showing only
the better one would be the selection this experiment spends its length
warning about.

- [`out/arbitrage_dashboard.html`](out/arbitrage_dashboard.html) — the
  **log-price** book: the screen audit against its placebo, equity against the
  risk-free rate and against the random-pair baseline, gross and net exposure
  daily, the z-score path of every traded pair with its entries and exits
  marked, the cost breakdown by line item, all nine sweeps, and every trade.
- [`out/arbitrage_dashboard_levels.html`](out/arbitrage_dashboard_levels.html)
  — the same for **price levels**.

The pair chart is the one to look at first. Picking `M&M/TCS` — the most
traded pair in the book — shows a z-score that starts at zero, walks down to
−5 over two years and never comes back, with ten entries on the way down and
almost every one of them stopped out. That is what a pair that passed a
cointegration test at 5% and was not a pair actually looks like.
