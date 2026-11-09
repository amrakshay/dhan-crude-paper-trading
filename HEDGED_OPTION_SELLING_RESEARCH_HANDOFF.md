# Handoff: Hedged option selling — research, backtest, dashboard

Do for defined-risk option selling what `experiments/pattern-detection/` did
for the Volatility Contraction Pattern: research the structures properly,
turn them into arithmetic, measure them honestly against a baseline,
optimise only where a sweep says it is safe to, and finish with a backtest
dashboard.

Scope: **short premium with a bought wing** — credit spreads, iron condors,
short strangles with protection — on NIFTY / BANKNIFTY index options or on
stock options. Undefined-risk naked selling is out of scope.

Work in **`experiments/hedged-option-selling/`** — self-contained, outside
`backend/`, imported by nothing. The app's database is opened **read-only**.

---

## STEP 0a — the repo invariant you must not quietly cross

`CLAUDE.md` states, as a non-negotiable rule of this codebase:

> A "Sell" is a long put, never a short call, so the worst case stays the
> premium paid. **Do not add a code path that sells an option to open.**

This research is about selling options to open. That is fine **inside
`experiments/`**, which is outside `backend/` and outside the safety
scanners, and which places nothing. It is *not* fine to let any of it leak
into `backend/src/`, and it is not your call to relax that invariant if the
research goes well. Graduating any of this into the application would be a
deliberate decision by the operator, taken explicitly, with the rule in
`CLAUDE.md` rewritten in the same change. Say so in the README rather than
leaving it implied.

Everything else the repo forbids still applies: no broker order endpoint, no
`dhanhq`, no `place_order`-family names anywhere.

---

## STEP 0b — the feasibility gate. Do this before anything else.

**There is no option price history in this repo, at all.** Verified:

- `daily_bars` holds 500 NSE_EQ symbols and the NIFTY index. Nothing else.
- The `instruments` table carries `MCX_COMM/OPTFUT` (1,230 live crude
  options), `NSE_EQ/EQUITY` (499) and `MCX_COMM/FUTCOM` (10). **No NSE F&O
  instruments are ingested at all.**
- `conf/charges/` has `mcx-commodity-options.yaml` and
  `nse-equity-delivery.yaml`. **There is no NSE F&O rate card.**
- The only margin model in the repo is a `10% of notional` order-of-magnitude
  estimate for MCX, explicitly labelled an approximation. **There is no
  SPAN-style model, and nothing that models the margin BENEFIT of a hedge** —
  which is the entire economic reason a retail seller buys the wing.

So three things must be built or sourced before a single strategy rule is
written. Establish the path for each and **report before proceeding**:

**1. Option price history.** In order of realism:
   - **NSE F&O bhavcopy** — daily settlement price, OHLC and open interest
     for every contract, published publicly and available back many years.
     This is the realistic path and it is a data-engineering project before
     it is a strategy project. Budget for it honestly.
   - **Dhan `/charts/historical` per contract** — works on a `security_id`,
     but the instrument master almost certainly carries only *live*
     contracts, so expired series are probably unreachable. **Verify on three
     expired contracts before relying on it.**
   - **Black-Scholes synthesis** from the underlying plus an implied
     volatility assumption. Possible, and dangerous — see below. If you end
     up here, say so on every page of output.

**2. A margin model.** Not optional. For a hedged seller **return on margin
is the metric**, not return on notional, and the hedge exists precisely to
collapse the margin. A backtest that reports return on notional has measured
the wrong thing. SPAN+exposure is complex; an explicit, sourced, clearly
labelled approximation of *the hedged case* is acceptable, a silent one is
not. Follow the `conf/charges/` convention: source URL, as-of date,
confidence marker.

**3. An NSE F&O charge card.** STT on option *sales* is charged on premium
(and differently on exercised contracts), and the rate rose in late 2024.
Plus brokerage, exchange transaction charges, SEBI turnover fee, stamp duty
and GST. Build it first, from sources, and assert it against a published
broker calculator — recording any divergence rather than tuning to match, as
the VCP charges work did.

### The trap inside Black-Scholes synthesis

Selling options is paid by the **variance risk premium**: implied volatility
is, on average, above subsequently realised volatility. If you price your
synthetic options with *realised* volatility you have erased the entire edge
and will conclude the strategy does not work. If you price them with a fixed
IV you have assumed the answer. India VIX has history and is the least-bad
anchor for the index; for a hedged structure you also need **skew**, because
the wing you buy is further OTM than the leg you sell and is priced on a
different part of the smile. A flat-IV model systematically misprices the
hedge, which is the one thing this project is about. If you cannot model
skew, the honest conclusion is that synthesis is not good enough here.

---

## The research phase

Produce `RESEARCH.md`, every criterion sourced.

**The structures**, and what each actually trades: bull put spread, bear call
spread, iron condor, iron fly, short strangle with wings, ratio spreads,
calendars and diagonals, the collar. For each: max profit, max loss,
breakevens, margin profile, and *which Greek pays you*.

**Why short premium has a positive expected return** — the variance risk
premium — and the conditions under which it disappears or inverts.

**Strike selection**: by delta (the 16-delta and 30-delta conventions) versus
by percentage-of-spot versus by expected move. Wing width, and the trade-off
between premium captured and margin released.

**Expiry selection**: days-to-expiry at entry (0DTE, weekly, ~30, ~45), and
how theta and gamma trade off across that range.

**Management rules**, which matter more here than entry rules: closing at a
fraction of max profit, rolling, the "defend the tested side" school, and
time-based exits. Each is testable and most of the published claims are not
independently verified.

**Indian market specifics — these are not footnotes, they are the subject:**
- **The SEBI index-derivatives changes from late 2024**: one weekly expiry
  per exchange, larger lot sizes, upfront premium collection, intraday
  position limits, and changes to calendar-spread margin benefit. Date each
  one and treat it as a structural break; a backtest spanning it is
  measuring two different markets.
- **STT on option sales rose in October 2024.** Same point.
- **Stock F&O is physically settled.** A short stock option left in the money
  at expiry becomes a delivery obligation, not a cash difference. For a
  hedged seller on single stocks this is a first-order hazard and the
  backtest must model it or exclude ITM-at-expiry cases loudly.
- **Liquidity.** NIFTY and BANKNIFTY options are deep; single-stock options
  are thin with wide spreads, and the far-OTM wing you want to buy is
  precisely where the book is thinnest. Model the spread, do not assume mid.

---

## The method, in the order that worked

1. **Data, charges and margin first**, as above. Nothing else is meaningful
   until those three exist.
2. **Synthetic ground truth with negative controls.** Generate underlying
   paths with known volatility — GBM, then fat-tailed, then with jumps — and
   verify your option pricing and P&L accounting reproduce the known answer
   before pointing any of it at real data. The negative controls here are
   regimes where selling *should* lose: a volatility expansion, a trending
   gap-heavy path, a crash.
3. **The strategy, causally.** Entry decisions use only information available
   at the decision bar. Assignment, expiry and settlement are simulated by
   rule, not by hindsight.
4. **Parameter sweep** (`experiments/pattern-detection/scripts/sweep.py` is a
   reusable shape) over DTE, short-strike delta, wing width and the
   management rule — one at a time, reporting the trade-off rather than the
   best cell.
5. **Baselines that must be beaten.** A market-neutral premium seller is not
   competing with the index. Report against: the risk-free rate, buy-and-hold
   NIFTY, and — the important one — **an unhedged version of the same
   strategy**, so the wing has to justify its cost.
6. **Block bootstrap** for significance, resampling whole months.
7. **Portfolio simulation** with margin as the binding constraint, both legs,
   real charges, and daily mark-to-market. Report **return on margin
   employed** and peak margin utilisation.
8. **Dashboard** — copy `experiments/pattern-detection/scripts/vcp_backtest.py`.
   Replace the positions chart with margin utilisation, and add a payoff
   diagram and a per-trade P&L histogram with the left tail called out.

---

## The traps invert here. Read this twice.

The VCP is a **positively skewed** strategy: 78% losers, and the top 5% of
trades produce more than 100% of the profit. Hedged option selling is its
**mirror** — a high win rate with a rare large loss — and every instinct
built on the VCP work points the wrong way.

- **A high win rate is normal and means nothing.** 80-90% is typical and
  tells you only that you sold OTM options. Never report it as evidence.
- **The left tail is the entire question.** Report the worst 1% of trades,
  the worst single month, and the maximum loss per unit of margin, on every
  summary. A mean and a win rate together can describe a strategy that goes
  to zero.
- **A backtest with no disaster in it is undersampled, not safe.** If the
  sample contains no gap through the long wing, no limit-move day, no
  volatility spike, you have not tested the thing that decides the outcome.
  Say how many such events the sample contains. If the answer is zero,
  that is the headline.
- **Ruin risk, not expectancy, is the decision variable.** Positive
  expectancy with occasional 10x-premium losses is a strategy that works
  until it does not. Simulate position sizing against the observed left tail
  explicitly.
- **The wing is not decoration.** Compare against the unhedged version on
  every metric. If the hedge costs more than the tail it removes, the honest
  answer is that this structure does not work at these prices — and that is
  a finding, not a failure.

## Traps carried over from the VCP work, at full price

- **Lookahead**, in every form. Decisions use bars `0..t`.
- **Exits that peek.** The VCP detector defined its pivot over a window that
  included the exit bar and therefore never produced a signal. The analogue
  here is settling a position with prices from after the decision.
- **Compare at matched exposure**, or in this case **matched margin**, not
  matched contract count.
- **Label the currency.** Premium, notional, margin and rupees are four
  different denominators and mixing them produces confident nonsense.
- **Do not tune constants to make a number agree.** Report the gap.

## Deliverables

`RESEARCH.md`, `METHOD.md`, `README.md` (findings at a glance first),
`FINAL_LOGIC.md` (the decided rules, with explicit entry, adjustment, exit
and disaster-stop specs), a document per investigation **including the
negative ones**, an NSE F&O charge card and a margin model with sources, and
`out/option_selling_dashboard.html`. A `scripts/run_all.py` that reproduces
every number.

## What "done" honestly looks like

Any of these is a real result:

- "Option price history is not obtainable at acceptable quality, here is what
  it would take" — stop there and say so.
- "After realistic charges and margin, the hedged structure returns a little
  over the risk-free rate on margin employed, with a left tail of X" — which
  is roughly what theory predicts and is worth knowing precisely.
- "The unhedged version earns more and the wing does not pay for itself in
  this sample, which contains N tail events" — with the caveat that N is
  probably too small to conclude from.

The failure mode to avoid is a smooth equity curve with a 90% win rate and no
disaster in the sample, presented as a strategy. That is what this kind of
backtest produces by default, and the entire structure above exists to stop
it.
