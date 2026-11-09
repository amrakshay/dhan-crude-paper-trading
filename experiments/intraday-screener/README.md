# Intraday screener: research

Can a screener running on live 5-minute data pick liquid NSE stocks for
intraday trades, with an entry, a stop and a target, and make money **after
costs**? This directory answers that from history **before** anything is built
into the application.

Self-contained, same shape as `experiments/pattern-detection/` and
`experiments/arbitrage/`. It has no order path; the only Dhan contact is the
application's own read-only `DhanChartsClient`.

## Ground rules (agreed 2026-09-27)

- **5-minute candles only.**
- **Nothing is decided before 09:30.** The first decision is made on the close
  of the 09:25 bar.
- **Liquid stocks only**, from the F&O list, cut at each point in time by
  trailing turnover.
- **Leverage exists** (MIS, up to 5x). See below for how it's modelled.
- **Manual first, automated later.** The first live version gives signals a
  person acts on. The rules are written so that a signal is complete (symbol,
  side, entry, stop, target, time exit) and could later be submitted by the
  scheduler with no human judgement added.
- **Every position is flat by the 15:10 bar close** (the closing auction; see
  FEASIBILITY.md trap 5).

## Status

| Step | State |
|---|---|
| 0. Feasibility | **Done**, see [FEASIBILITY.md](FEASIBILITY.md). 9.5 years of 5-min bars exist. |
| 1. Download 210 F&O names + Nifty 50 | Running (`scripts/download_bars.py`, resumable) |
| 2. Clean (grid, corporate actions, glitches) | **Done**, see [CLEANING.md](CLEANING.md). 99.7% of days match `daily_bars` within 0.5%. Re-run once the download finishes. |
| 3. Backtest harness + cost model | **Done**, see [HARNESS.md](HARNESS.md). 28 tests; H1 runs end to end in about 11 s. |
| 4. Test pre-registered ideas vs controls | **Done.** All five pre-registered ideas fail. One exploratory variant (H3c) passes on the development period and awaits the holdout. |
| 5. Held-out run (2023-01 → 2026-09), once | **Done, 2026-09-27: H3c FAILS** (below). Logged in `out/holdout_log.json`. |
| 6. Live screener in the app (manual) | **Not justified** by these results |
| 7. Automation (armed, like BTST) | Only after paper-trading step 6 |

## Results (development period 2017-04 → 2022-12, 210 F&O names, top 100 liquid, 5 bps slippage)

Net bps per trade, with the 95% day-bootstrap CI:

| Idea | Trades/day | Gross | Net @ ₹1L | Net @ ₹5L | Years + | Beats all 3 controls? | Pass bar |
|---|---|---|---|---|---|---|---|
| H1 ORB + volume | 20.1 | −5.6 | −13.8 [−18.5, −8.6] | −10.1 [−14.7, −4.9] | 0/6 | no | **FAIL** |
| H2 relative strength @10:00 | 17.7 | −2.1 | −10.3 [−13.0, −7.6] | −6.5 [−9.2, −3.8] | 0/6 | yes, but net < 0 | **FAIL** |
| H3a gap continuation | 7.5 | −7.3 | −15.6 [−35.2, 0.8] | −11.8 [−31.5, 4.6] | 0–1/6 | no | **FAIL** |
| H3b gap fade | 10.5 | +14.0 | +5.7 [−3.9, 15.3] | +9.5 [−0.1, 19.1] | 4–5/6 | yes | **FAIL** (CI touches 0) |
| H4 VWAP pullback | 15.3 | −6.3 | −14.5 [−16.7, −12.2] | −10.8 [−12.9, −8.4] | 0/6 | no | **FAIL** |
| *H3c gap fade, top 3/day* | *2.8* | *+31.6* | *+23.4 [14.5, 32.7]* | *+27.1 [18.3, 36.5]* | *6/6* | *yes, by 24–35* | *passes dev, **exploratory*** |
| H5 breakout checklist (long) | 23.5 | −10.6 | −18.8 [−20.3, −17.3] | −15.1 [−16.5, −13.5] | 0/6 | no — **worse than a coin** (−3.0 [−4.5, −1.6]) | **FAIL** |

"Gross" is after slippage, before charges.

- **Continuation and breakout ideas lose; gap reversal gains.** Every
  momentum-style idea (H1, H2, H3a, H4) is negative even before charges. The
  only direction that carries information is **against** an overnight gap
  that has already started to reverse.
- **H3b as registered fails, and why matters.** Its edge is concentrated:
  - The top 12 days produce more than 100% of its profit (2020-06-12 alone is
    26%).
  - Eight of them are COVID-era market-wide reversals with 50–97 signals on
    one day.
  - Dropping the best 10 of 1,355 days takes it from +9.5 to −1.0 bps.
  - As registered it's one market bet placed 97 times, and it needs ₹4.85 crore
    of exposure on its best day.
- **H3c is H3b made tradeable:** keep only the 3 largest gaps each day.
  - It survives every check: +16 bps after dropping the best 20 days, every
    year positive, +17 bps even at 10 bps slippage.
  - **But it's post hoc.** The ranking and the cap were chosen after seeing
    H3b's breakdown (caps of 1, 3 and 5 were looked at).
  - Only the untouched **2023-01 → 2026-09 holdout** can validate it, and it
    gets one run.
- **H5 (2026-11-09), the six-condition bullish checklist, fails on every
  test,** as written down beforehand.
  - It fires on 23.5 names a day, because the six conditions largely restate
    one fact: a stock that is up today.
  - Before slippage it is about flat (−0.6 bps). The 2R target is hit 28% of
    the time, below the 33% that 2R needs to break even. The median stop is
    only 0.45% away, so 5 bps of slippage on each side plus 8 bps of charges
    take it to −18.8 bps.
  - Every year is negative, from −15 to −21 bps.
  - Taking the trade on a coin flip for direction does BETTER than going long
    (−3.0 bps, CI clear of zero). Taking it later, or in a random liquid name,
    does no worse. The checklist adds nothing to plain H1.
  - Not run on the holdout, which is only for ideas that pass development.
- **Leverage helps costs only a little.** ₹5L per trade cuts charges from
  8.3 to 4.5 bps, but slippage is unchanged.
- **Longs vs shorts:** for the breakout and strength ideas, shorts did much
  better than longs (H2: +2.5 vs −23 bps).

## Holdout verdict (2023-01 → 2026-09, run once, 2026-09-27)

**H3c (fade the 3 biggest reversing gaps each day) fails.**

| | Development | **Holdout** |
|---|---|---|
| Gross/trade after slippage | +31.6 bps | **+10.5** |
| Net @ ₹1L | +23.4 [14.5, 32.7] | **+2.3 [−6.6, 10.7]** |
| Net @ ₹5L | +27.1 [18.3, 36.5] | **+6.0 [−2.8, 14.5]** |
| Net @ ₹5L, 2 / 10 bps slippage | — | +12.0 / −4.0 |
| Years positive @ ₹5L | 6/6 | **2/4** (2023 −1.7, 2024 +20.0, 2025 +3.4, 2026 −0.7) |
| Beats random side / time / name | by 35 / 24 / 31 | **by 19 / 11 / 15, CIs clear of 0** |

**What this says:**

1. **Fading a reversing gap carries real directional information.** It beat
   all three controls in *both* periods, including data no rule had seen. It's
   the only one of the six ideas where that's true.
2. **It isn't a tradeable edge at the costs assumed.** The edge shrank by
   about two-thirds out of sample, which is typical for a rule chosen after
   the fact. What's left (about 10 bps gross) is roughly what slippage plus
   charges cost.
3. **Whether it's worth anything depends on one unmeasured number:
   slippage.** At 2 bps per side it nets +12 bps at ₹5L; at 10 bps, −4. The 5
   bps assumption is a guess, not a measurement, and paper trading can't
   measure it: this app's fills are simulated.

The holdout has been used. Any further change to H3c would be judged on data
that has now been seen.

## Live spread measurement (first sample, 2026-09-28 14:30 IST)

`scripts/sample_spreads.py` subscribes the top 100 liquid names in Full mode
and walks five levels of real depth for an immediate market order.
500 snapshots over 30 s. Cost per side, in bps of mid:

| Liquidity rank | Half-spread | ₹1L buy / sell | ₹5L buy / sell |
|---|---|---|---|
| 1–25 | 0.96 | 1.04 / 0.96 | 1.53 / 1.39 |
| 26–50 | 1.30 | 1.78 / 1.56 | 2.11 / 2.00 |
| 51–75 | 1.40 | 1.76 / 1.90 | 2.35 / 2.65 |
| 76–100 | 1.24 | 1.94 / 1.91 | 2.68 / 2.81 |
| **All, median / 90th pct** | 1.24 / 1.98 | 1.6 / 2.6 | **2.2 / 3.6** |

- **Mid-afternoon, a market order costs well under the backtest's 5 bps**,
  around 2 bps at ₹5L.
- **It's a lower bound, for two reasons:**
  - In 14–20% of snapshots five levels couldn't fill ₹5L, and those thin
    moments are excluded.
  - **H3c trades at 09:30, not 14:30.** Early-session spreads are normally
    wider, and that has **not been measured yet**.
- It measures resting spread only. A stop filling into a fast move pays more.
- One 30-second sample on one day. It's an indication, not an estimate.

At the measured 14:30 cost (about 2 bps), H3c's holdout figure would sit near
the +12 bps (₹5L) row of the slippage table. A 09:30 measurement over several
days decides whether that holds when it trades.

## What trading costs (`conf/intraday_costs.yaml`, from Dhan's pricing page)

For one round trip, buy then sell:

| Notional | Charges | + slippage 5 bps/side | Total |
|---|---|---|---|
| ₹1,00,000 | ≈ ₹83 (0.083%) | ₹100 | **≈ 0.18%** |
| ₹5,00,000 | ≈ ₹225 (0.045%) | ₹500 | **≈ 0.145%** |

Brokerage is a flat ₹20 per order, so it matters less as trades get bigger.
Slippage doesn't shrink, and it's the biggest unknown. Results are reported at
2, 5 and 10 bps per side, never at one flattering figure.

## How leverage is treated

Leverage doesn't create an edge. It scales the result of each trade, good or
bad, relative to capital. The backtest works in two layers:

1. **Per trade, in % of notional, after costs.** This decides whether an edge
   exists. Leverage plays no part.
2. **Per portfolio, as the money you'd actually see.** Gross exposure is capped
   at N × equity with N ∈ {1, 2, 3, 5}, and the report shows returns, maximum
   drawdown and the worst day at each N. The question leverage answers is "how
   much drawdown can I stand", not "how much more can I make".

Per-stock margin (VaR + ELM, which can be above 20%) is **not modelled**. The
cap is applied to the whole book.

## Ideas to test (written down before any results)

Each idea is judged on the development period first. Candidates must be in
the day's liquid set. All times are bar closes, IST.

| # | Idea | Signal (from 09:30 on) | Why it might work |
|---|---|---|---|
| H1 | **Opening-range breakout with volume** | Price closes above/below the 09:15–09:30 range, and cumulative volume is ≥ 1.5× this name's average **for the same time of day** | Classic day-trade setup; the volume condition is what the idea claims separates real breaks from false ones |
| H2 | **Relative strength at 10:00** | The name is in the top/bottom decile of return since the open **minus the Nifty 50**, and above/below VWAP | Momentum persists within a day for names with news or institutional flow |
| H3 | **Gap continuation vs. fade** | Gap ≥ 1% at the open. Go with the gap if the 09:15–09:30 range holds in the gap's direction; fade it if the range fails | Tests both directions, since popular sources disagree about which one works |
| H4 | **VWAP pullback in a trend day** | Price is above VWAP all morning, pulls back to VWAP after 10:30, then turns up | A common discretionary setup; worth knowing if it is real |

### Exact definitions for H2–H4 (fixed 2026-09-27, after H1's result, before running any of them)

The table above described these in words; these are the rules as coded.
Harness defaults apply to all of them: liquid top 100, one trade per name per
day, no decision after the 13:55 bar, flat by 15:15, time exit (no target).

- **H2 relative strength:**
  - **When:** decided once, on the close of the 09:55 bar (10:00).
  - **Score:** return since the open, minus the Nifty's return since the open.
  - **Trades:** long the top decile of that day's liquid names *if* above
    VWAP; short the bottom decile *if* below VWAP.
  - **Stop:** 3 × the 5-minute ATR from the decision price. (H2 has no
    natural structure level, so it gets the ATR stop the README already
    listed.)
- **H3 gap:** |overnight gap| ≥ 1%, gap-flagged days excluded (CLEANING.md,
  IDEA). Decided at 09:30 (close of bar 2).
  - **H3a continuation:** the 09:30 close is beyond the day's open in the
    gap's direction. Trade with the gap; stop on the far side of the opening
    range.
  - **H3b fade:** the 09:30 close is back through the day's open. Trade
    against the gap; stop on the far side of the opening range.
  - Reported as two hypotheses. That makes five tests in total, and a pass
    needs to be clear enough to survive that.
- **H4 VWAP pullback:**
  - **Trend:** every close from 09:30 to 10:30 (bars 2–14) is above VWAP (for
    a long) or below it (for a short).
  - **Trigger:** after 10:30, within the last 3 bars, a low touches VWAP (a
    high, for a short), and then a bar closes above the previous bar's high
    and above VWAP (mirror for a short).
  - **Stop:** the extreme of those 3 bars.

### H5 "bullish breakout checklist" (fixed 2026-11-09, before running it)

Brought by the owner from a screenshot of a popular six-condition checklist.
It names entry conditions only; the exit, the timeframe and the direction were
pinned down with him before any code ran. **Long only.** Harness defaults
apply: liquid top 100, one trade per name per day, nothing decided before the
09:25 bar closes (bar 2), no decision after the 13:55 bar, flat by 15:15.

- **Signal:** the FIRST bar `k` (2 ≤ k ≤ 56) whose close satisfies all six:
  1. close > **previous day's high** (all of that day's bars);
  2. close > **high of today's first 5-minute candle** (bar 0, 09:15–09:20);
  3. **EMA10 > EMA20 > EMA50** of 5-minute closes;
  4. **ADX(14) > 25** on 5-minute bars (Wilder);
  5. close > **VWAP**;
  6. bar k's volume ≥ **1.5 × the mean volume of the previous 20 five-minute
     bars**.

  EMAs, ADX and the 20-bar volume mean run over the continuous 5-minute
  series, carried across days, as a 5-minute chart shows them, so they are
  warm at 09:30. ADX uses true range including the overnight gap, as the
  charting definition does. "Break" means a bar CLOSES above the level; a
  name already above both levels at 09:30 qualifies on its first bar where
  the other four also hold.
- **Stop:** the low of the signal bar `k`.
- **Target:** 2R, i.e. `close[k] + 2 × (close[k] − low[k])`, measured from
  the signal close because the entry price is not known when the order is
  placed. The engine fills at the next bar's open, so realised R differs a
  little. Signals with `close[k] = low[k]` (zero risk) are dropped.
- **Exit otherwise:** the 15:10 bar close (harness time exit).

Prior expectation, written down so it cannot be revised afterwards: H1 (the
first candle's range broken with volume) failed at −13.8 bps net and was
negative before charges. Every long breakout variant here has failed. H5 adds
trend filters to the same idea, and the expected result is a fail. It counts
as the **seventh test** on the development period. It runs on the holdout
once, and only if it passes on development.

Every result is reported at **₹1L and ₹5L per trade**. ₹5L is ₹1L of capital
at the 5× intraday cap, where the flat brokerage matters least.

Each idea has **one** default parameter set, fixed here. Sweeps come later and
count as extra tests that the statistics must account for.

### Stops, targets and risk-reward

These are **measured, not assumed**. For every signal we record how far price
moved in the trade's favour and against it before the 15:10 exit. The stop and
target are then chosen from those distributions on the development period
only. We compare:

- An ATR-based stop, using a 5-minute ATR computed from the name's own recent
  bars.
- A structure stop (the other side of the opening range, for H1 and H3).
- Fixed 1:1, 1:1.5 and 1:2 targets against a **time exit only**.

A higher reward-to-risk ratio isn't better in itself. 1:2 wins less often
than 1:1, and only expectancy after costs decides.

## What counts as a pass (fixed before looking)

An idea goes forward only if **all** of these hold:

1. Mean return per trade after costs is > 0 at **5 bps** slippage, with a
   block-bootstrap 95% CI clear of zero on the development period.
2. It beats a **random-entry control**: the same names, the same entry times
   and the same exits, but a random direction or a random name from that day's
   liquid set. This is the arbitrage experiment's placebo habit, applied here.
3. It is positive in the majority of calendar years, not carried by one year
   such as 2020.
4. On the **held-out** period (2023-01 → 2026-09), run once, it is still
   positive after costs.

If nothing passes, the answer is "no intraday edge from these rules". That's
worth knowing before building a screener that sounds confident.

## Layout

```
conf/intraday_costs.yaml   Dhan intraday charges + leverage cap, sourced
scripts/feasibility.py     step 0
scripts/download_bars.py   step 1 (runs in backend/.venv: needs the app's client)
scripts/clean_bars.py      step 2
scripts/build_panel.py     step 3: data/clean -> data/panel.npz
scripts/run_hypothesis.py  step 4: one hypothesis vs the pass bar (holdout guarded)
islib/                     panel, features, engine, costs, hypotheses+controls, stats, synth
tests/                     fill rules, no-lookahead, synthetic null + planted edge
scripts/validate_clean.py  step 2's check against daily_bars
data/raw/5m/*.csv.gz       vendor bars, untouched (gitignored)
out/                       results (json/log gitignored)
.venv/                     analysis venv (pandas, pyarrow, scipy)
```

Run the Dhan-facing scripts from `backend/` with `CONFIG_PATH=conf
.venv/bin/python ../experiments/intraday-screener/scripts/<script>.py`. Run
analysis with `experiments/intraday-screener/.venv/bin/python`.
