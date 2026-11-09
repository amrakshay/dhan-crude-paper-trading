# Step 0 — What the data can support

Run 2026-09-27 (a Sunday) with `scripts/feasibility.py`, plus two follow-up
probes. 159 + ~20 requests against `/charts/intraday`, 0 errors. The results
are in `out/feasibility.json`.

## Answer

| Question | Finding | How verified |
|---|---|---|
| How far back does 5-min history go? | **2017-04-03** for long-listed names, about 9.5 years. The window ending 2017-03-26 is empty. | HDFCBANK walked back until empty |
| Later listings | Start at their listing date (JIOFIN 2023-08-21, SONACOMS 2021-06-24) | probe |
| Does history thin out for less liquid names? | No. IEX (#210 of 210 by turnover) also goes back to 2017-10 | probe |
| Cost of a full pull | About 40 requests and 40 s per name, so 210 names take about 2.3 h at 1 req/s | measured |
| Rate limiting | None seen at about 0.97 req/s | 0 × 429 |

## Data traps (the cleaning step must handle all of these)

1. **Timestamps off the 5-minute grid.** *Corrected in step 2:* this is not
   an Aug–Sep 2021 problem, as first written here. It happens on 10–25% of
   pre-2022 days in every name, because Dhan stamps a bar at its first trade.
   Rounding down to the window start is correct; see CLEANING.md rule 1. What
   IS specific to Aug 2021 is the post-close session (bars to 15:55) and
   short September days.
2. **Muhurat sessions.** For example 2024-11-01, 18:00–18:55, 12 bars. Dropped.
3. **Odd weekend sessions.** 2021-08-07 (a Saturday) starts at 11:15 with 41
   bars. It's unverified whether this was a real special session. Dropped.
4. **A trailing `15:30:00` bar** appears in some years (76 bars/day in 2024).
   It is the closing print, not a tradeable 5-minute bar. Dropped.
5. **Closing Auction Session.** Since August 2026, F&O names' last bar is
   **15:10** (72 bars/day, verified on 2026-09-24/25). Continuous trading ends
   15:15. Every intraday exit in the backtest is forced by **15:10 bar close**
   for all years, so old and new periods are tested under the same rule.
6. **Zero-volume bars.** HDFCBANK has 373 in about 1,800 sessions; IEX has
   1,608. They're real (no trades in the bar) but they mean volume filters
   must use rolling sums, not single bars.
7. **The index has volume 0 everywhere.** Nifty 50 bars are price only.

## Consequences for the design

- **Universe:** the 210 current F&O names. This has survivorship bias: names
  that dropped out of F&O since 2017 are missing. It's stated, not fixed; the
  instrument master cannot give us expired listings (see
  `experiments/arbitrage/FEASIBILITY.md`).
- **Liquidity is judged at each point in time** from each name's own trailing
  20-session turnover in the 5-minute bars, not from today's ranking. Today's
  top 25 cut off at about ₹390 cr/day median turnover, top 50 at ₹270 cr, and
  top 100 at ₹160 cr (`out/liquidity_ranking.json`, from `daily_bars`).
- **Scanning starts at 09:30:** first decision on the close of the 09:25 bar,
  as agreed.
- **Test split:** 2017-04 → 2022-12 is used to develop rules;
  2023-01 → 2026-09 is held out and run **once**.

## Found in step 2 (see CLEANING.md)

8. **Old corporate actions are unadjusted** in the intraday bars (EICHERMOT
   1:10 split, 2020-08-24). Rescaled from `daily_bars`.
9. **Holes in the intraday history** (CGPOWER: two gaps of about 3 months in
   2021). Counted, and never measured across.
10. **Vendor glitch prints** shared across names at one timestamp, when the
    index doesn't move (2021-08-03 15:20). Dropped.
