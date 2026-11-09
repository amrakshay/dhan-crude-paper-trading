# Handoff: IPO Breakout strategy — research, backtest, dashboard

Do for IPO breakouts what `experiments/pattern-detection/` did for the
Volatility Contraction Pattern: research the setup properly, turn it into
arithmetic over candles, measure it honestly against ground truth and a
baseline, optimise only where a sweep says it is safe to, and finish with a
backtest dashboard.

Work in **`experiments/ipo-breakout/`** — self-contained, outside `backend/`,
imported by nothing. The paper-trading app's database is opened **read-only**
(`sqlite3 ... mode=ro`). No broker surface, no order path, not even a stub.

## Read first

- `experiments/pattern-detection/README.md` §0 — what was found and how it was
  framed.
- `experiments/pattern-detection/METHOD.md` — the detector design, and the
  bugs that cost the most time. Several will recur.
- `experiments/pattern-detection/FINAL_LOGIC.md` §5 — how limitations were
  written down rather than buried. Match that tone.
- Reuse `patlib/` where it fits (`bars.py`, `indicators.py`, `pivots.py`,
  `shapes.py`, `evaluate.py` are all domain-neutral). Do not fork it silently;
  import it or move the shared parts up a level.

---

## STEP 0 — the feasibility gate. Do this before writing a detector.

**This project has a data problem that can kill it, and you should find out on
day one, not day three.**

`daily_bars` holds 500 NSE_EQ symbols. 151 of them start after 2018, which is
effectively their listing date — so there is real post-listing history to work
with. But those 500 are **today's Nifty 500 constituents**. Every IPO that
listed and sank without ever joining the index is absent. For a momentum
strategy that bias is uncomfortable; **for an IPO strategy it is potentially
fatal**, because "IPOs that later became index members" is close to a
definition of the winners.

So, first:

1. Count the usable listings. `SELECT symbol, MIN(bar_date) FROM daily_bars
   WHERE exchange_segment='NSE_EQ' GROUP BY symbol HAVING MIN(bar_date) >
   '2016-01-01'` — expect ~151, spread across years. Report the per-year
   counts, because a strategy validated on 12 listings a year is not
   validated.
2. Establish how badly the survivor set is skewed. Get the true count of
   mainboard NSE IPOs per year (public information) and compare. If the
   database holds 151 of ~400, you are looking at roughly the top third.
3. Decide, and **tell the user before proceeding**, which of these you are
   doing:
   - **(a) Work the survivor set and label every number as an upper bound.**
     Cheapest, and honest if the labelling is done everywhere, not once.
   - **(b) Build a proper universe.** `dhan_charts_client` fetches
     `/charts/historical` "back to instrument inception" for any instrument
     with a `security_id`, and the `instruments` table covers far more than
     the Nifty 500. If delisted or non-index names can be resolved to a
     security_id, a much better dataset is buildable. **Verify this on three
     known non-index listings before committing to the plan** — the
     instrument master may only carry live instruments, which would make (b)
     impossible via Dhan and push you to an external source.
   - **(c) Stop.** A defensible outcome if (b) fails and (a) is too thin.

The repo's own `ipos` / `ipo_gmp_readings` tables (from the IPO dashboard)
are a **live forward feed with ~12 rows** — useful for paper-trading later,
useless as history. Do not mistake them for a dataset.

---

## The research phase

Produce `RESEARCH.md` with every criterion sourced, the way
`pattern-detection/RESEARCH.md` does. Cover at least:

**The IPO base (O'Neil / IBD).** Shorter than a normal base — five weeks
minimum rather than seven — and allowed to be much deeper (to 50%+). Why the
usual rules are relaxed, and what the buy point is.

**The first breakout.** Above the listing-day high; above the first-week
range; above the first proper base. These are different setups with different
statistics — find out which one the literature actually supports.

**India-specific supply events, which have no US analogue and matter a lot:**
- **Anchor investor lock-in**: 50% released at 30 days, the rest at 90 days.
- **Pre-IPO shareholder and promoter lock-ins** at 6 and 18 months.
These are scheduled, predictable supply shocks. A breakout three days before
an anchor unlock is not the same trade as one three days after, and this is
testable with listing dates alone.

**Grey Market Premium.** The repo now collects it (`ipo_gmp_readings`). The
literature on whether it predicts anything past listing day is thin and
mostly promotional — treat every claim as unverified and say so.

**Subscription data** (QIB / HNI / retail multiples) as a predictor. Public
at close. Whether it survives as a signal is an open question worth testing.

**Listing-day mechanics**: the special pre-open session, the price band on day
one, and what "listing price" means versus first traded price. Getting this
wrong silently shifts every entry by a day.

---

## The method, in the order that worked

1. **Bars, pivots, indicators.** Reuse `patlib`. Everything causal: a detector
   takes bars `0..t` and returns what was knowable at `t`.
2. **Synthetic ground truth with negative controls.** This is what made the
   VCP accuracy numbers meaningful and it is non-negotiable. Draw IPO bases
   with known parameters, then draw the things that look like one and are not
   — a listing that gaps up and bleeds, a flat post-listing drift, a random
   walk starting at a listing price. *Expect your generator to be wrong at
   least once; three times it was, and the detector was blamed each time.*
3. **Detector**, with hard gates for structural impossibilities and a weighted
   score for the soft criteria. Report `state` (`forming` / `breakout`), never
   a bare boolean.
4. **Parameter sweep** (`scripts/sweep.py` is reusable). One threshold at a
   time, reporting recall against the false-alarm rate. If recall is flat
   across every value, say so — that is a finding about the pattern.
5. **Real data, causally**, walking the as-of bar forward.
6. **A date-matched baseline.** For every signal on date D, the average
   forward return of every other symbol with data on D. Without it a bull
   market makes everything look clever. For IPOs consider a second baseline:
   *other IPOs of similar vintage*, which controls for the IPO cohort effect.
7. **Block bootstrap by calendar month** for significance. Naive t-stats are
   too generous because forward windows overlap.
8. **Portfolio simulation** with cash accounting, fixed notional, costs, and
   daily mark-to-market. Per-trade expectancy is not a return.
9. **Dashboard** — copy `scripts/vcp_backtest.py`. It already has the KPI
   tiles, equity/drawdown, positions-held, monthly heatmap and a filterable
   ledger, and the subtitle is generated from the flags so it cannot go stale.

---

## Traps, carried over at full price

- **Lookahead through the pivot.** The VCP detector defined its pivot as the
  highest high over a window that ran to `t`, which silently included the
  breakout bar, so no close could ever exceed it and the detector produced
  zero signals. Walk the window instead.
- **Volume dry-up measured through the breakout.** The breakout bar's volume
  is 2-3x by definition; include it and the confirming expansion reads as a
  failure to contract.
- **Smoothed indicators over short windows.** A Wilder ATR(14) has barely
  adapted to a 12-bar leg by the time the leg is over. Use raw true range for
  anything measured inside a short window. This one fix took VCP recall from
  52% to 89%.
- **R and rupees diverge under fixed-notional sizing.** A trade with a 1% stop
  earning 10R makes 10% in cash; one with a 5% stop earning 3R makes 15%.
  Decide which currency each table is in and label it.
- **Compare selective strategies at matched EXPOSURE, not matched position
  size.** A filter that halves the signal count leaves the book in cash and
  looks worse than it is. This single correction reversed a conclusion.
- **Win rate is a vanity metric.** Roughly 25 filters were tried on the VCP;
  most raised the win rate and lowered the return. Judge every filter on
  return, drawdown and win rate together, and prefer the one with a
  *mechanism* over the one with the best number.

## Traps specific to this problem

- **Survivorship, again and worse.** See Step 0. Say it in the README, on the
  dashboard, and next to every headline number.
- **No history means no indicators.** A stock listed six weeks ago has no
  200-day average, no 52-week high, no ATR seed. Every criterion inherited
  from a trend-following framework needs re-deriving or dropping. Decide
  explicitly what the minimum viable history is.
- **The listing-day bar is not a normal bar.** Special pre-open, a different
  price band, and volume that will never recur. Consider excluding it from
  every average, and check whether the feed's OHLC for it is even sane.
- **Tiny samples.** ~151 listings over a decade, perhaps 15 a year. Any
  filter that keeps a third of them leaves 50 trades. Report confidence
  intervals, not point estimates, and resist reporting a year-by-year table
  that has four trades in some years.
- **The anchor unlock is a date you know in advance.** That makes it a rare
  genuinely causal, non-price feature. Worth testing early because it is
  cheap and, if it works, it is the most defensible thing in the project.

## Deliverables

`RESEARCH.md`, `METHOD.md`, `README.md` (findings at a glance first),
`FINAL_LOGIC.md` (the decided rules, with a full stop-loss spec), a document
per investigation including **the negative ones**, and
`out/ipo_dashboard.html`. A `scripts/run_all.py` that reproduces every number.

## What "done" honestly looks like

Possibly: "on the only data available this cannot be validated, here is what
the logic would be and what it would take to test it properly." That is a
real result. The VCP work produced two flat negatives (ranking, regime
filter) and they are written up as carefully as the positives, because the
alternative is someone re-investigating them in six months.

Do not tune until something looks good. Report the gap.
