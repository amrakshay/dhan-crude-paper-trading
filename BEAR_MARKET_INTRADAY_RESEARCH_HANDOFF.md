# Handoff: Making money when the Indian market falls — intraday shorts and puts

Find out, from history and before anything is built, whether there is a
repeatable way to profit **in a falling Indian market** by
**intraday short selling of stocks** or **buying PUT options**, with a
**daily shortlisting method** that picks which names to trade. The aim is not
to trade every day. The aim is to be **profitable month by month**.

Work in **`experiments/bear-intraday/`**: self-contained, outside `backend/`,
imported by nothing. The application's database is opened **read-only**. Same
shape as `experiments/intraday-screener/`, `experiments/order-block/` and
`experiments/hedged-option-selling/`. Read their READMEs first. Most of what
you need already exists and has been verified.

This is research on historical data, plus at most a signal-only shadow
journal. It is not investment advice and it places nothing.

---

## STEP 0a — invariants you must not cross

- **No order path of any kind.** No broker order, funds or holdings endpoint,
  no `dhanhq`, and no `place_order` / `cancel_order` family names anywhere.
  `CLAUDE.md` §1 applies in full, inside `experiments/` too.
- **Nothing here graduates into `backend/src/` by itself.** The app has no
  cash-short path, and its chart trading deliberately turns a "Sell" into a
  long put. Adding intraday cash shorts or a bear strategy module to the
  application would be a separate, explicit operator decision, with its own
  handoff. Say this in the README.
- **Selling options to open is out of scope.** It has been researched already
  (`experiments/hedged-option-selling/`). The bearish half of that research,
  the **bear call spread, returned −88% a year on capital at risk**, because
  of index skew. Do not re-propose it as "the bearish options trade".
  Defined-risk *debit* structures (a bear put spread) are in scope.
- **Every Dhan URL used must be read-only market data.** If a new one is
  needed (see 0d), use it only from `experiments/`, name it in exactly one
  module, and report it. Adding it to the backend allowlist is the operator's
  call, not yours.

## STEP 0b — check the premise before using it

The operator says India is "in a bear market". Measured on 2026-10-03 from
`intraday-screener/data/clean/5m/NIFTY50.parquet`:

| Fact | Value |
|---|---|
| Last close in the local data | 23,140.5 (2026-09-25) |
| High | 26,335.7 (2026-01-02) |
| Drawdown | **−12.1%** |
| 50-day / 200-day SMA | 23,994 / 24,419. Price is below both. |
| Monthly returns, 2026 | Jan −3.2, Feb −0.5, **Mar −11.1**, Apr +7.4, May −1.8, Jun +1.3, Jul +1.9, Aug −1.2, Sep −3.9 |

On the index, that is a **correction in a downtrend, not a −20% bear
market**. Mid- and small-caps may be much worse. Measure breadth across the
210 F&O names and the Nifty 500 `daily_bars` (the share below their 50-day
and 200-day averages, and the median drawdown from the 52-week high), and
write `REGIME.md` before anything else. The strategy must not depend on the
word "bear". It depends on a **causal regime definition** (below) that a
machine can evaluate every morning from data up to the previous close.

## STEP 0c — what already exists. Reuse it read-only, don't rebuild it.

Import through `sys.path`, the way `order-block/` does. Never edit another
experiment's files.

| Asset | Where | Notes |
|---|---|---|
| 5-min bars, 210 F&O names + NIFTY50, 2017-04-03 → 2026-09-25 | `intraday-screener/data/clean/5m/*.parquet` | Cleaned. Read `CLEANING.md` and the traps in `FEASIBILITY.md`, including the 15:10 closing-auction rule and index volume = 0. |
| Backtest engine, controls, day bootstrap, synthetic data | `intraday-screener/islib/` (`engine`, `stats`, `synth`, `costs`) | Has 28 tests. `HARNESS.md` describes two control designs that leaked the answer. Read it. |
| MIS cost card (STT 0.025% sell side, 5x cap) | `intraday-screener/conf/intraday_costs.yaml` | Slippage reported at 2 / 5 / 10 bps per side |
| Live spread sample | `intraday-screener/README.md` | ~1.6 bps/side at ₹1L, ~2.2 at ₹5L at 14:30. There is no 09:30 sample. |
| NSE F&O bhavcopy, every contract, daily OHLC + OI + settle, 2016-01 → 2026-09-17 | `hedged-option-selling/data/cache` | Includes **stock futures OI**, which is usable for a short-buildup filter. Daily only, with no intraday option prices. |
| NSE F&O options charge card, including the STT rises of 2024-10-01 and 2026-04-01 | `hedged-option-selling/CHARGES.md` + `conf/` | Reuse it for put trades |
| Sector map for all 210 names | `order-block/data/sectors.csv` | From NSE Indices' Nifty 500 industry list |
| India VIX history | via the `ind_close_all` index files, see `hedged-option-selling/FEASIBILITY.md` | |

### Prior findings that bear directly on this. Don't rediscover them.

1. **Shorts did much better than longs** in the intraday screener (H2 relative
   strength: shorts +2.5 bps, longs −23 bps). This was observed on the
   **development period**, so a short-only hypothesis is already partly
   informed by dev data. Dev results for it are weaker evidence than usual,
   and the holdout matters more.
2. **Every momentum/breakout intraday idea lost even before charges** (H1 ORB,
   H2, H3a, H4 VWAP pullback), over all regimes. A bear-regime-conditional
   version is a new hypothesis, but expect the same unless the regime
   genuinely changes the behaviour.
3. **Gap fade (H3c) carried real directional information in both periods but
   did not survive costs**, and it **has already used the 2023+ holdout**.
   The gap-fade family is contaminated. You may report a short-only gap-up
   fade, but it cannot be validated here and must be labelled so.
4. **Tight stops kill intraday R-based systems through charges**
   (`order-block`: median stop 0.28% of price, so charges are 0.13 R per
   trade). Check stop distance against round-trip cost before trusting any
   R figure.
5. **Index put skew is real and measured.** A 16-delta NIFTY put traded at
   12.8% IV against 10.6% for the 16-delta call. **A put buyer pays the rich
   side of the smile.** A put must beat shorting the future on the same
   signal, after theta, IV and charges, or it isn't worth its premium.
6. **Since SEBI's 2024-11-20 index-derivatives framework, option behaviour is
   a different market** (bigger lots, one weekly expiry per exchange, higher
   STT). Treat 2024-11-20 and 2026-04-01 as structural breaks for every
   option result.
7. **Lessons from the harness** (`HARNESS.md`, the order-block memo):
   - Controls keep the stop **distance**, not the stop **rule**.
   - Random entry times are drawn only at or after the signal.
   - R is fat-tailed, so **pool 5 control reps and several seeds** in null tests.
     One seed once showed a fake +0.067 R edge on a random walk.

## STEP 0d — feasibility gate: intraday option prices. Report before building option tests.

The bhavcopy is **daily**. An intraday put trade needs intraday option
prices. Establish the path, verify it by fetching, and **stop and report**:

1. **Dhan's expired-options data endpoint.** It is believed to exist in Dhan
   API v2: minute bars for expired index and stock options, addressed by
   strike relative to ATM. **This is UNVERIFIED.** Find the documentation,
   confirm the exact URL and that it is read-only, and probe three expired
   NIFTY weekly contracts across 2019, 2023 and 2025. Record how far back it
   goes and whether it carries OI and IV. If it works, it is the realistic
   path. Report the URL to the operator before bulk-downloading.
2. **Live contracts via `/charts/intraday`.** These work only for contracts in
   today's instrument master, so they help **forward collection**, not
   history.
3. **Synthesis from the 5-min underlying** with IV anchored to the daily
   bhavcopy settle, with skew interpolated. It is possible but dangerous: it
   assumes the intraday IV path, and in a selloff IV rises, which is half of
   what a put buyer is paid for. If you end up here, every option result is
   labelled SYNTHETIC on every page. The cash-short and futures-short tracks
   do not need any of this. Run them first, whatever 0d finds.

## STEP 0e — decisions to get from the operator before Step 2 (recommendation first)

Ask these in one message, with the recommendation first, and record the
answers in the README:

1. **Capital.** **₹5L (recommended)**, matching earlier experiments so costs
   are comparable, or ₹1L or ₹10L.
2. **Instruments.**
   - **(Recommended)** Cash MIS shorts on the liquid F&O names, plus NIFTY /
     BANKNIFTY index puts.
   - Add stock futures shorts.
   - Add stock options. Not recommended at first: thin books and wide
     spreads on exactly the OTM puts you'd buy.
3. **Holding period.**
   - **Strictly intraday, flat by the 15:10 bar (recommended)**, as the
     operator asked.
   - Also a separate overnight track (a short futures or long put held to
     the next morning, the bearish mirror of BTST). Overnight shorts in cash
     are impossible in India.
4. **What "profitable month by month" means.** Recommended:
   - A month with no trades counts as **flat, not a win**.
   - The bar is **≥ 70% of traded months net positive**.
   - **Worst month ≥ −4% of capital.**
   - **No more than 2 consecutive losing months.**

   Fix this before seeing results, and never move it afterwards.

---

## The research phase — `RESEARCH.md`, every claim sourced

- **Base rates first.** SEBI's studies of F&O traders found that roughly
  **9 in 10 individuals lost money** (FY22–FY24, and an updated FY25 study).
  Cite the exact figures and circulars. This is the prior that any result
  here has to overcome.
- **How falling markets behave intraday.** Higher realised volatility,
  larger gaps, and the fact that **the sharpest up-days happen inside bear
  markets** (relief rallies and short covering). Those days are the left
  tail of a short book. Look at what the research says about intraday
  momentum versus reversal, and about whether that changes by regime. Use
  published papers, preferably on Indian data, and flag US-only evidence as
  such.
- **Indian short-selling mechanics (these are constraints, not footnotes):**
  - An MIS cash short must be squared off the same day. A short that fails
    to deliver goes to the **exchange auction with a penalty**.
  - No naked overnight short in cash. SLB exists but is niche.
  - **Circuit limits:** you can't short at the lower circuit, and you can't
    cover at the upper one. Model both.
  - The **F&O ban list** (MWPL > 95%) means no new F&O positions in that name.
  - Peak-margin rules, and whether Dhan allows MIS shorts on every F&O name.
    The last point is unverified, so verify it.
- **Put buying mechanics.** Theta per hour on the weekly expiry, vega gain in
  a selloff against IV crush on the bounce, skew, expiry-day gamma, the
  current weekly expiry day for NIFTY and BANKNIFTY (verify it, because it
  has changed), lot sizes across the 2024-11 break, STT on exercise versus
  square-off, and strike choice (ATM, 1-ITM, OTM) for an intraday move.
- **Shortlisting literature.** Relative weakness, sector rotation, futures
  OI-based "short buildup" (price down with OI up), prior-day close location,
  gap behaviour and beta. Separate what has evidence from what is folklore,
  and say which is which.

## Regime — `REGIME.md`, pre-registered

Define **at most three** causal regime rules, computed every morning from
data up to the previous close only. For example:

- (a) NIFTY below a falling 200-day SMA;
- (b) breadth, where more than 60% of the F&O names are below their 50-day SMA;
- (c) NIFTY more than 10% below its 252-session high.

For each rule, report:

- the dates it was on, and **how many separate bear episodes the sample
  contains**. Expect around five: 2018 mid/small caps, Mar 2020,
  Oct 2021 – Jun 2022, Oct 2024 – Mar 2025, and 2026. Check them.
  **Five episodes is a small sample, and that is the headline caveat for
  everything that follows.**
- how often it flips (whipsaw), and how late it switches on and off.
- what each strategy does when the regime is **off**. A bear strategy that
  bleeds in rising markets is only safe if the switch catches the turn.
  Measure the cost of the switch being late.

## Shortlisting — `SHORTLIST.md`

A daily list of **at most N names (pre-register N ≤ 5)**, fixed **before
09:15** from data available by the previous evening:

- **Liquidity:** the top 100 of the F&O names by trailing 20-session turnover,
  judged at each point in time. Exclude ban-list names and any name at or
  near a circuit.
- **Pre-registered ranking features**, scored and combined in a way fixed
  before results:
  - 20-day return relative to NIFTY and relative to its sector;
  - price below a falling 20/50-day SMA;
  - the previous day's close location in its range;
  - short buildup from the bhavcopy (stock futures price down with OI up). It
    is published after the close, so use it the **next** morning only;
  - sector weakness rank;
  - beta.
- **No news or results-calendar filter.** `intraday-screener/NEWS_DATA.md`
  found NSE's terms prohibit automated collection, and licensed feeds were
  not bought. State the gap.
- **Test the shortlist on its own** against a random shortlist of the same
  size from the same liquid universe, in the same regime, with the same
  trigger. If random names do as well, the shortlist adds nothing.

## Pre-registered hypotheses — write them down fully before running any

Each one needs: universe, regime rule, trigger, entry (no decision before
the 09:25 bar closes), stop (with its distance checked against costs),
target, time exit by the 15:10 bar, and sizing. **Six at most. Count every
variant you try and report the count.**

- **B1 Breakdown short.** On a bear-regime day, short a shortlisted name
  when it breaks below its opening range with relative weakness against
  NIFTY confirmed. Stop above the range, then a trailing stop.
- **B2 Failed relief rally.** On a bear-regime day, short a shortlisted
  name that rallies to VWAP or the previous close in the first 90 minutes,
  then prints a lower high. Stop above the rally high.
- **B3 Index put on breakdown days.** At B1's NIFTY-level trigger, buy an
  ATM or 1-ITM weekly NIFTY put. **Compare it with shorting NIFTY futures on
  the identical signal.** The put has to justify its premium. This one is
  gated on 0d.
- **B4 Bear put spread vs naked put** on the B3 signal. The spread sells the
  further-OTM put, which sits higher on the skew, and recovers some of what
  the long put overpays. This is gated on 0d.
- **B5 (optional, only if the operator chose it in 0e) Overnight bear
  carry.** Short futures or buy a put at the close on the weakest
  shortlisted names, and exit at the next morning's open window.
- **B6 Gap-up fade, short only.** Report it, labelled **cannot be validated**
  because the gap-fade family has seen the holdout.

## Controls and baselines — the result is only as good as these

- **Most important: "short NIFTY futures at 09:30 and cover at 15:10 on
  every regime-on day".** If that matches B1/B2, the stock picking and the
  trigger add nothing, and the regime is the whole edge. That would be a
  finding in its own right, and a much simpler strategy.
- **Random name:** same time, same regime, from the liquid universe.
- **Random time:** same name, drawn at or after the signal.
- **Mirror long:** the same signals traded long.
- **Regime off:** the same rules on non-bear days.

Pool 5 control reps and pool seeds. Use the day bootstrap from `islib.stats`,
and add a **month-block bootstrap**, because the goal is monthly.

**Prove the harness first.**
- On synthetic random-walk paths, every hypothesis must come out at about
  zero gross.
- On paths with a planted bear-regime short edge, it must **find** that edge.

A null result is only informative from a harness that would have reported a
real one.

## Pass bar — fixed in the README before any result, never moved

A hypothesis passes on the development period (**2017-04 → 2022-12**) only
if **all** of these hold:

1. Net per trade is > 0 after charges at 5 bps/side slippage, with the
   bootstrap CI clear of 0. Also show the result at 2 and 10 bps.
2. It beats **every** control, including index-short-every-bear-day, with a
   CI clear of 0.
3. It is positive in the **majority of the bear episodes** in dev, not just
   in a majority of years.
4. Profit isn't concentrated: it is still > 0 after dropping the best 10 days.
5. **The monthly bar from 0e is met** in the portfolio simulation.

**Holdout: 2023-01 → 2026-09.** It contains the 2024–25 correction and the
2026 drawdown. Run it **once per final candidate**, and log every run in
`out/holdout_log.json`. Never tune on it, and never re-run a tweaked rule on
it. If nothing passes dev, **do not run the holdout**. Leave it unseen for
future ideas.

## Portfolio simulation

- **Sizing and leverage:** capital from 0e, MIS gross exposure capped at 5x,
  and risk per trade of 0.5% of capital (report 1% as well).
- **Limits:** max concurrent positions, a **daily loss limit** (stop trading
  for the day), and a **monthly loss limit** (stop for the month).
- **Charges:** every leg charged in full, at real lot sizes.
- **Pre-register the risk rules.** Report their effect, and don't tune them
  on dev.

**Report all of the following:**

- **A monthly P&L calendar.** It is the central exhibit, because the
  operator's goal is monthly.
- The share of positive months, the worst month, and the longest losing
  streak.
- CAGR on capital, max drawdown, and exposure.
- Results split by regime-on and regime-off months.
- Results **before and after 2024-11-20** for anything involving options.

## Traps specific to shorting and puts

- **The left tail of a short book is the relief rally.** Report the worst
  day, the worst month, and how many +2% and +3% NIFTY days fall inside
  regime-on periods. If the sample has few short squeezes, that's the
  headline.
- **Survivorship bias cuts against shorts here.** The 210 names are today's
  F&O list. The best historical shorts (YESBANK, DHFL and others that were
  dropped from F&O or collapsed) are missing, and so are their trading halts.
  The direction of the bias is not obvious, so state it, don't fix it
  silently.
- **Circuits and halts:** no fill at a locked limit, ever. That rule is
  pessimistic on purpose.
- **Theta isn't in a futures backtest, and vega isn't in a delta-only option
  model.** Don't let one stand in for the other.
- **Lookahead:** the regime and shortlist use data up to the previous close.
  The bhavcopy OI arrives after the close. The day's open is known at 09:15,
  but nothing trades before the 09:25 bar closes.
- **A high win rate on puts can be an artifact of the expiry choice.** Report
  the P&L distribution, not just the win rate.
- **Do not tune constants to make a number agree.** Report the gap.

## Deliverables (in `experiments/bear-intraday/`)

`README.md` (findings at a glance first), `RESEARCH.md`, `REGIME.md`,
`SHORTLIST.md`, `FEASIBILITY.md` (the 0d answer), `METHOD.md`, one document
per hypothesis **including the failures**, `FINAL_LOGIC.md` only if something
passes the holdout (full entry, stop, target, time exit, sizing and regime
switch, complete enough to automate), `tests/` (synthetic null plus planted
edge), `scripts/run_all.py` reproducing every number, and
`out/bear_dashboard.html`, with:

- the monthly calendar;
- the equity curve with regime shading;
- per-trade and per-month histograms with the left tail marked;
- the put-vs-future comparison.

If something passes, the next step is a **signal-only shadow journal**: a
script that writes each morning's shortlist and each day's triggers to a
file, with no orders, run forward for at least two months before anyone
talks about the app. Propose it. Don't build it into `backend/`.

## What "done" honestly looks like

Any of these is a real result:

- **"Shorting NIFTY futures on regime-on days is the whole edge; stock
  selection and triggers add nothing after costs."** That is simpler and
  more tradeable than what was asked for.
- **"Intraday shorts in bear regimes have an edge after costs, but the
  monthly bar fails because relief-rally months wipe out N good ones."**
  Include the numbers.
- **"Puts lose to the equivalent futures short after theta, skew and STT.
  Use the future, or the put spread."**
- **"Nothing passes dev. The holdout is left unseen."**
- **"Intraday option history could not be obtained honestly; the option
  track is untested."**

The failure mode to avoid is a smooth equity curve built from a few COVID-
and 2022-era crash days, with the relief rallies missing from the sample,
presented as a monthly income strategy. Every safeguard above exists to stop
exactly that.
