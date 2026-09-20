# Handoff: Arbitrage trading — research, backtest, dashboard

Do for arbitrage what `experiments/pattern-detection/` did for the Volatility
Contraction Pattern: research it properly, turn it into arithmetic, measure it
honestly, optimise only where a sweep says it is safe to, and finish with a
backtest dashboard.

Work in **`experiments/arbitrage/`** — self-contained, outside `backend/`,
imported by nothing. The app's database is opened **read-only**. No broker
surface, no order path, not even a stub. This matters more here than
anywhere: arbitrage is the strategy most likely to tempt someone into wiring
up execution, and the repo's one inviolable rule is that it cannot place a
real order.

## Read first

- `experiments/pattern-detection/README.md` §0 and `METHOD.md`.
- `experiments/pattern-detection/FINAL_LOGIC.md` §5 — how limitations were
  stated rather than buried.
- `backend/conf/charges/` — the existing rate-card convention: every rate has
  a primary source URL, an as-of date and a confidence marker. **You will
  live in this directory.** See "the real subject" below.

---

## STEP 0 — the feasibility gate. Do this first.

"Arbitrage" covers several strategies with completely different data needs,
and **the repo can support some and not others.** `daily_bars` contains
exactly two things: 500 NSE_EQ symbols and the NIFTY index. There is **no
futures history and no options history**.

| Strategy | Needs | Status |
|---|---|---|
| **Pairs / statistical arbitrage** | two cash equities | **feasible today** |
| **Index arbitrage** | index futures history | needs sourcing |
| **Cash-futures basis (cash & carry)** | per-stock futures history | needs sourcing |
| **Calendar spreads** | two futures expiries | needs sourcing |
| **ETF / NAV arbitrage** | ETF prices + NAV | not in the DB |

Before choosing, establish whether futures history is obtainable:

1. `dhan_charts_client` fetches `/charts/historical` for any instrument with a
   `security_id`. Check whether the `instruments` table resolves **expired**
   contracts, or only live ones. Almost certainly only live ones — in which
   case a continuous futures series cannot be stitched from Dhan, and basis
   arbitrage is off the table without an external source.
2. If it is off the table, **say so and start with pairs trading**, which the
   existing data fully supports. Do not fake a futures series by assuming a
   constant basis; that assumes precisely the thing under study.

Report the decision before building anything.

---

## The real subject: this is a costs problem wearing a strategy costume

Take this seriously or the project produces a confident, wrong answer.

A cash-futures basis trade earns the **implied financing rate**: buy cash,
sell the future, hold to expiry, collect the convergence. In India the gross
annualised basis is typically a few points above the repo rate. Against that,
a round trip pays: brokerage on both legs, **STT** (different rates for
delivery cash, futures, and on which side), **stamp duty**, exchange
transaction charges, **SEBI turnover fee**, **GST on brokerage and charges**,
plus the margin blocked on the futures leg and the impact cost of getting
both legs on at the quoted prices.

**The honest prior is that retail cash-futures arbitrage nets roughly a
treasury-bill return with materially more operational risk.** If your backtest
says otherwise, the costs are wrong before the alpha is right. Build the
charges model *first*, from `backend/conf/charges/` conventions, with a source
URL and as-of date per rate — then run the strategy. Not the other way round.

The same applies to pairs trading, where the spread captured is often under
1% and two round trips are paid to capture it.

---

## The research phase

Produce `RESEARCH.md`, every claim sourced:

**Cash-futures basis**: fair value and cost-of-carry, why the basis goes
negative, dividend adjustment, the expiry-day convergence mechanic, and how
the annualised return is actually computed (a common place to fool yourself
by annualising a 5-day hold).

**Pairs / statistical arbitrage**: cointegration (Engle-Granger and Johansen)
versus naive correlation and why the distinction matters; the spread's
**half-life of mean reversion** (Ornstein-Uhlenbeck) and how it should set the
holding period; z-score entry and exit bands; hedge ratio estimation and
whether it is re-estimated or frozen; what a **structural break** looks like
and how a pair that has stopped cointegrating is retired.

**Calendar spreads and index arbitrage**, briefly, so the map is complete.

**The reality check**: who the counterparty is. Retail basis arbitrage
competes with desks that see the same spread with lower costs and faster
execution. What is left for a daily-bar, EOD participant is a genuine and
answerable question, and the answer may be "the roll, held to expiry, and
nothing intraday."

---

## The method, in the order that worked

1. **Charges engine first.** Rate cards under the experiment, in the repo's
   existing format. Assert them against a published broker calculator and
   record the divergence rather than tuning to match — the VCP work keeps a
   documented ₹0.01 gap for exactly this reason.
2. **Synthetic ground truth with negative controls.** For pairs: generate
   genuinely cointegrated series with a known half-life, and — the important
   half — **two independent random walks**, which will look cointegrated
   often enough to be alarming. Spurious regression is the whole hazard of
   this field; your negative controls exist to measure how often your
   selection procedure falls for it.
3. **Pair selection, causally.** Select pairs on a formation window and trade
   them on a disjoint later window. **Never select on the full history** — the
   single most common error in published pairs research.
4. **A multiple-comparison correction, stated explicitly.** Screening 500
   symbols means ~125,000 candidate pairs. At p<0.05 you get ~6,000 spurious
   cointegrations by chance. Report how you handled this; it dominates
   everything else.
5. **Parameter sweep** — entry z, exit z, stop z, formation length, holding
   cap — one at a time, reporting the trade-off rather than the best cell.
6. **Baseline.** A date-matched baseline as in the VCP work, plus a
   **risk-free comparator**: for a market-neutral strategy the benchmark is
   T-bills, not the index. A 6% "market-neutral" return in a period when
   T-bills paid 6.5% is a loss.
7. **Block bootstrap** for significance.
8. **Portfolio simulation** with cash accounting, **both legs**, margin on the
   short leg, costs, and daily mark-to-market. Gross exposure and net exposure
   both reported.
9. **Dashboard** — copy `scripts/vcp_backtest.py`. Add a spread/z-score chart
   and a gross-vs-net exposure chart; drop the ones that do not apply.

---

## Traps, carried over at full price

- **Lookahead**, in every form. A detector takes bars `0..t`. Pair selection
  uses only the formation window. Hedge ratios use only data up to `t`.
- **Smoothed indicators over short windows** lag badly; use raw measures
  inside short windows.
- **R and rupees diverge** under fixed-notional sizing — label which currency
  each table is in.
- **Compare at matched exposure, not matched position size.**
- **Win rate is a vanity metric.** Pairs trading will show a *high* win rate —
  85%+ is normal — with occasional large losses when a spread never reverts.
  That is the mirror image of the VCP, and the same warning applies: judge on
  return, drawdown and tail together. Report the worst 1% of trades
  explicitly.

## Traps specific to this problem

- **Survivorship and delisting.** The 500 symbols are today's index members.
  A pairs strategy is unusually exposed: the catastrophic case is the leg that
  gets delisted or halted, and by construction none of those are in the data.
  This will flatter the results and you cannot fix it with this dataset — say
  so.
- **Corporate actions.** Splits, bonuses and demergers break a spread
  instantly and will look like a spectacular mean-reversion opportunity or an
  unrecoverable loss. Check whether `daily_bars` is adjusted; if you cannot
  establish that it is, treat every extreme spread move as suspect and
  filter it, documenting the filter.
- **Both legs must be executable.** A spread between a liquid and an illiquid
  name is not tradeable at the prices in the data. Apply a turnover floor to
  *both* legs and test its sensitivity.
- **Short selling is not free and often not possible.** In Indian cash equity
  you cannot hold a short position overnight without SLB. If the strategy
  requires a short cash leg, that is a hard constraint, not a cost — state it.
  If it shorts futures instead, you need the futures data from Step 0.
- **Expiry effects.** Basis, spreads and liquidity all behave differently in
  the last few sessions of an expiry. Do not average across them silently.
- **A spread that stops reverting.** Define, in advance, how a pair is
  retired, and make sure the backtest actually retires it rather than holding
  to a happy ending.

## Deliverables

`RESEARCH.md`, `METHOD.md`, `README.md` (findings at a glance first),
`FINAL_LOGIC.md` (decided rules, with an explicit exit and stop spec), a
document per investigation **including the negative ones**, a charges rate
card with sources, and `out/arbitrage_dashboard.html`. A
`scripts/run_all.py` that reproduces every number.

## What "done" honestly looks like

Plausibly: "after realistic costs this earns a little over the risk-free rate,
which is what theory predicts, and here is the cost breakdown that proves it."
That is a genuinely useful result and should be reported as a success of the
method, not a failure of the strategy.

The failure mode to avoid is a backtest showing 30% annualised on a
market-neutral book. If you see that, the costs, the shorting assumption, or
the pair selection is wrong. Go and find which before writing it up.
