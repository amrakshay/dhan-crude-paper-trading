# Bear-market intraday: shorts and puts

Is there a repeatable way to make money in a **falling** Indian market by
**intraday short selling** or **buying puts**, with a **daily shortlist**, that
is profitable **month by month**? Answered from history before anything is
built. The brief is
[`BEAR_MARKET_INTRADAY_RESEARCH_HANDOFF.md`](../../BEAR_MARKET_INTRADAY_RESEARCH_HANDOFF.md).

Self-contained, outside `backend/`, imported by nothing. It has **no order
path**: no broker order, funds or holdings endpoint, no `dhanhq`, no
`place_order` family names. The application's database is opened read-only.
Not investment advice.

**Nothing here graduates into `backend/src/` by itself.** The application has
no cash-short path, and its chart trading deliberately turns a "Sell" into a
long put. Adding intraday cash shorts or a bear strategy module to the app is a
separate, explicit operator decision, with its own handoff.

## Findings at a glance

Development period 2017-04 → 2022-12, primary regime **rule B (breadth)**. The
decisions, pass bar and hypotheses below were all fixed before any of these
results. Open `out/bear_dashboard.html` for the charts.

**Verdict: nothing passes development. The holdout was not run.** (One
index-level descriptive figure from it was seen by accident; see "Known gaps".)

| Rule | Net per trade | 95% CI | Episodes + | Drop best 10 days | Months + · worst · streak | Verdict |
|---|---|---|---|---|---|---|
| **Index short every regime day** (NIFTY futures, 09:30 → 15:10) | **−8.5 bps** @ 1 bp | [−23, +6] | 3/8 | −18.3 | 30% · −31% · 4 | **FAIL** |
| B1 breakdown short | +0.4 @ 5 bps | [−16, +17] | 5/8 | −15.5 | 41% · −3.6% · 7 | **FAIL** |
| B2 failed relief rally | +8.0 | [−12, +30] | 5/8 | −13.9 | 32% · −4.3% · 7 | **FAIL** |
| B6 gap-up fade (contaminated) | +5.3 | [−19, +32] | 6/8 | −25.6 | 43% · −4.7% · 5 | **FAIL** |
| B3 / B4 index puts | — | — | — | — | — | **untested** (0d blocked) |

What that adds up to, in the handoff's "done honestly" terms:

1. **"Shorting NIFTY futures on regime-on days is the whole edge" is false
   here. The index short loses.** On bear-regime days the fall happens
   overnight and in the first 15 minutes (−8.8 bps from 09:15 to 09:30). The
   part of the session an intraday short can hold (09:30 → 15:10) drifts **up**
   (+4.2 bps). The relief rallies are inside the regime: 14 of the decade's 19
   days of +3% fall on the 24% of sessions rule B is on (`REGIME.md`). With 5×
   sizing and no stop, the index short loses 135% of capital.
   `INDEX_SHORT.md`.
2. **The shortlist carries real information, but not an edge after costs.**
   On the same triggers the 5-name shortlist beats 5 random liquid names by
   **+14.6 (B1) and +18.0 (B2) bps per trade, CI clear of 0**. That lifts a
   losing trigger to about break-even. Nothing survives the best-days test or
   the monthly bar. `SHORTLIST.md`.
3. **"Profitable month by month" is nowhere near.** The best traded-month hit
   rate is 41% (bar: 70%), and losing streaks run 5–7 months (bar: 2). The
   stocks' worst months (−3.6% to −4.7%) are held there partly by the
   pre-registered −3% monthly stop. At 1% risk, B1's worst month is −5.2%.
4. **The averages lean on March–May 2020.** B2 earned +46.8 bps a trade in
   2020 and about 0 in every other year. B1 *lost* in the 2020 episode. Remove
   2020-02 → 05 and every positive mean shrinks toward zero.
5. **Later regime definitions make it worse.** Under rules A (falling 200-day)
   and C (−10% from the high), the index short loses about 21 bps a day gross:
   those rules are on through the rebounds. B1 and B2 lose or break even, and
   their CIs widen.
6. **Puts are untested, and the futures data argues against them.** A put held
   09:30 → 15:10 on these days pays theta and the rich side of the skew, for a
   window that drifts up. `FEASIBILITY.md`, `B3_B4_PUTS.md`.

7. **Round 2 also fails** (`ROUND2.md`): VWAP rejection, prior-day-low
   breakdown, failed breakout, afternoon weakness, opening drive, and the
   published index intraday-momentum effect. They use price action,
   technicals and OHLCV order-flow proxies. Real order flow has no history
   here and can only be recorded forward (`FEASIBILITY.md` §6).

**No `FINAL_LOGIC.md` and no shadow-journal proposal.** Both are reserved for
something that passes. If the shortlist is to be pursued, the honest next step
is a pre-registered **forward** shadow journal of the shortlist alone. That
would be a new hypothesis, not a rescue of these.

---

## Decisions (operator, 2026-10-03, before any result)

| # | Question | Answer |
|---|---|---|
| 1 | Capital | **₹5,00,000.** MIS gross exposure capped at 5×, i.e. ₹25 L. |
| 2 | Instruments | **Cash MIS shorts** on the liquid F&O names, plus **NIFTY / BANKNIFTY index puts** (the put track is gated on 0d). NIFTY futures appear as the key control. No stock futures, no stock options. |
| 3 | Holding period | **Strictly intraday**: flat by the close of the 15:10 bar. B5 (overnight) is **not run**. |
| 4 | "Profitable month by month" | A month with no trades is **flat, not a win**. **≥ 70% of traded months net positive**, **worst month ≥ −4% of capital**, **no more than 2 consecutive losing months**. |
| 5 | Stock futures OI ("short buildup") | **Pending.** The handoff assumed it was in `hedged-option-selling`'s cache. It isn't: that cache kept the 5 index underlyings only. Re-fetching ~2,650 bhavcopy files is deferred. The feature is left out of the shortlist and recorded as untested. |

## Status

| Step | State |
|---|---|
| 0a Invariants | Held. See "Pending list" for the one new Dhan URL. |
| 0b Premise + `REGIME.md` | **Done.** NIFTY −14.1% from its high (2026-09-30). The median Nifty 500 name is −19.8%. All three regime rules are on. |
| 0c Reuse | Bars, features, stats, cost card and sectors are reused read-only. One correction to the handoff: no stock futures OI exists locally (decision 5). |
| 0d Intraday option prices | **Blocked.** Endpoint found in Dhan's docs. The probe was refused (`401 DH-901`) because the stored token expired on 2026-10-02 15:49 UTC. See `FEASIBILITY.md`. |
| 0e Decisions | **Done** (above) |
| Research | `RESEARCH.md` |
| Harness proof | **Done.** 13 tests: random-walk nulls ≈ 0 for every rule and control; the planted edge is found (+125 bps, CI [100, 148]) and beats every control; causality; locks; halts. |
| Cash + futures tracks (B1, B2, B6 and controls) | **Done. All FAIL on development.** The holdout was not run. |
| Put tracks (B3, B4) | Gated on 0d: **untested** |
| Dashboard | `out/bear_dashboard.html` (`scripts/dashboard.py`) |
| Reproduce | `scripts/run_all.py` (about 90 s) |

## Pass bar (fixed here before any result; never moved)

Development period **2017-04-03 → 2022-12-30**. Primary regime **rule B
(breadth)**, chosen in `REGIME.md`. A hypothesis passes only if **all five**
hold:

1. **Net per trade > 0** at 5 bps/side slippage and ₹5 L notional per trade,
   with the day-bootstrap 95% CI clear of 0. It is also shown at 2 and 10 bps.
2. **It beats every control**, each with the CI of (strategy − control) clear
   of 0: index short every regime day, random name, random time, mirror long,
   and regime off. The comparison with the index short is mean net return per
   trade against mean net return per index-short day, with the **same days
   resampled** for both (the union of their days).
3. **Positive in a majority of the development episodes** of rule B (see
   `REGIME.md`): **at least 5 of 8**. An episode with no trades counts as not
   positive.
4. **Not concentrated:** still > 0 after dropping its 10 best days.
5. **The monthly bar** (decision 4) is met in the portfolio simulation on the
   development period.

**Holdout: 2023-01-02 → 2026-09-25.** It is run **once per final candidate**,
only if 1–5 pass, and only after asking the operator. Every run is logged in
`out/holdout_log.json`. If nothing passes development, **the holdout is not
run.**

**The index-short baseline is not exempt.** If "short NIFTY futures 09:30 →
15:10 on every regime day" meets bars 1, 3, 4 and 5 by itself, that is reported
as a finding. Bar 2 doesn't apply to it, because it is the control.

## Variant count

Every rule run on development data is counted here, including the failures.
See `METHOD.md` for definitions.

Hypotheses registered: 5 (B1, B2, B3, B4, B6), plus the index-short
control judged as a strategy. **Development runs: 10.** Re-runs after harness
fixes don't count as variants.

| # | Run | Regime | Net @ 5 bps (index: @ 1 bp) | |
|---|---|---|---|---|
| 1 | index_short | B | −8.5 | primary |
| 2 | B1_breakdown | B | +0.4 | primary |
| 3 | B2_failed_rally | B | +8.0 | primary |
| 4 | B6_gap_up_fade | B | +5.3 | primary, contaminated |
| 5–7 | index_short / B1 / B2 | A | −22.7 / −7.3 / +7.1 | robustness |
| 8–10 | index_short / B1 / B2 | C | −23.3 / −11.4 / 0.0 | robustness |
| 11–16 | Round 2: S1 / S2 / S3 / S4 / S6 / S5 (index, net @ 1 bp) | B (S5: all days) | −0.7 / −1.2 / −4.8 / +5.9 / +19.4 / −3.8 | `ROUND2.md`, all FAIL |

Exploratory diagnostics, not variants:
- `scripts/decompose_day.py`;
- B2's portfolio with no limits, and by stop-distance tercile (in
  `B2_FAILED_RALLY.md`).

No parameter was changed after a result.

**Harness fixes found by running, both documented:**
- The vendor prints a market-wide halt as **flat bars**, not missing ones, so
  the first index-short run "entered" during the 2020-03-13 circuit halt.
  `engine.market_halt` now handles it.
- A unit test of the lock rule was wrong, not the engine. A bar that trades
  through the stop fills there.

## Known gaps and biases

- **Accidental holdout exposure (2026-10-03).** The first version of
  `scripts/decompose_day.py` printed NIFTY's spot legs for 2023 onward. That
  makes the **index-short baseline's** holdout gross (about +1.9 bps a day to a
  short) no longer unseen. No stock, shortlist or B-rule figure was computed on
  holdout data. It is logged in `out/holdout_log.json`.
- **Survivorship:** the universe is today's 210 F&O names and today's sector
  map. The best historical shorts (YESBANK, DHFL and others dropped or
  collapsed) are missing, and so are their halts. The direction of the bias
  for a short book is unclear: the best shorts are missing, and so are the
  worst squeezes. It is stated, not fixed.
- **MIS short availability on every F&O name is UNVERIFIED** (`RESEARCH.md`
  §3). Dhan excludes trade-to-trade, restricted and illiquid scrips, and does
  not publish a per-name list.
- **No news or results filter.** Data can't be collected within NSE's terms.
- **Slippage is assumed** (2/5/10 bps). The 09:30–14:00 spread is unmeasured.
  The only sample is from 14:30.
- **Five episodes is a small sample.** Development has about four genuine bear
  phases, and the holdout has two.
- **Spot NIFTY stands in for the future** in the index short. The intraday
  basis drift isn't modelled.
- **Per-stock margin and short-delivery risk aren't modelled** beyond the
  locked-limit rule. No locked exit occurred in any run.

## Pending list

- **Stock futures OI (short buildup):** needs NSE bhavcopy re-fetched with
  stock futures rows (decision 5).
- **0d probe:** needs a fresh Dhan token saved on the Settings page. Then run
  `scripts/probe_expired_options.py` (8 requests).
- **`https://api.dhan.co/v2/charts/rollingoption`:** a new read-only Dhan
  market-data URL, named only in `scripts/probe_expired_options.py`. It is
  **not** in the backend allowlist, and adding it there is the operator's call.
- **F&O ban list history:** not needed for cash shorts, because the ban
  restricts new F&O positions only (see `RESEARCH.md`). It would be needed for
  a stock-futures track.
- **09:30 spread sample:** still unmeasured (see the screener README). B1 and
  B2 enter between 09:35 and 14:00.
