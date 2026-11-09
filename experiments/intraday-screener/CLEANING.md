# Step 2: Cleaning the 5-minute bars

`scripts/clean_bars.py` turns `data/raw/5m/` (vendor output, untouched) into
`data/clean/5m/<SYMBOL>.parquet` plus `data/clean/days.parquet`, which records
every (symbol, day) and either `kept` or the reason it was dropped.
`scripts/validate_clean.py` checks the result against an independent source,
the app's own `daily_bars`.

Every rule came from looking at the data. Several first guesses turned out to
be wrong, and those are recorded too, so nobody repeats them.

## The rules, and the evidence for each

### 1. Off-grid timestamps: round down, don't drop

A first look (FEASIBILITY.md) suggested off-grid timestamps were an Aug–Sep
2021 problem. **That was wrong.** They appear on 10–25% of pre-2022 days in
every name. Dhan stamps a bar at the first trade *inside* its 5-minute window.
ABB on 2019-01-14 has a `10:41:00` bar with 374 shares and a `12:22:00` bar
with 112, and none of them sit exactly on a 5-minute boundary. Rounding down
to the window start recovers the window exactly. Across 16 M raw bars this
produced 61 collisions.

### 2. Collisions: keep the larger bar

The collisions are single-share stray prints, for example 2023-10-17 12:06 in
several names at once, 2–5% away from the real bar. The bar with more volume
is kept.

### 3. Session windows only (09:15–15:25)

This drops the post-close session (15:30–16:00, present in Aug 2021), the
trailing 15:30 closing print, and Muhurat evening sessions.

### 4. Impossible OHLC bars are dropped, never repaired

There were 5 in total.

### 5. Trading days come from the Nifty 50's own calendar

A day is kept only if:
- it's a valid Nifty day;
- it has a bar at or before 09:25 (the 09:30 decision needs the opening
  range);
- it has at least 90% of its expected windows: 75 per day, or 72 for F&O names
  after the closing auction started on 2026-08-03.

Every day the index itself fails is a non-regular session: DR-drill Saturdays,
Muhurat, and a 13-bar vendor gap on 2022-03-03. Three month-ends (2017-12-29,
2018-07-31, 2019-04-30) have only a 15:25 bar for about 65 names and are
dropped.

### 6. Missing windows are not filled

A window with no bar had no trades.

### 7. Corporate actions: rescale from `daily_bars`

**Dhan's intraday bars are not adjusted for older corporate actions.**
EICHERMOT reads 21,240 on 2020-08-21 and 2,199 on 2020-08-24, its 1:10
split. `daily_bars` is adjusted, so the ratio of daily close to intraday close
is flat between actions and steps at each one.

- **The step is found with medians**, not a day-on-day jump, for two reasons:
  - Rights issues give ratios that aren't whole numbers. BHARTIARTL steps
    0.902 → 0.980 (2019) and 0.982 → 1.000 (2021). A first version that
    snapped to simple fractions and needed a 3% step got BHARTIARTL 10% wrong
    for two years.
  - `daily_bars` itself contains days that were never adjusted (see below).
- **Two false "events" are removed:**
  - pairs of steps that cancel within 20 sessions, which are stretches where
    the two sources disagree (CGPOWER and BEL in March 2020);
  - a segment under 5 sessions at the end of history.
- **An event within the first 5 days of a name's history** can't be measured,
  so those days are dropped. This happens for CONCOR.
- **Results:** 35 events in the first 98 names. Every split/bonus date
  matches the ex-date. EICHERMOT 10.00, HDFCBANK 2.00, BIOCON 3.00, CHOLAFIN
  5.00, CONCOR 2.00 and 1.25. CGPOWER's large limit moves correctly produce
  no event.
- **Dhan does adjust recent actions itself.** GODFRYPHLP's Sept 2025 bonus
  shows no price step, but its intraday volume is exactly 3× the imported
  daily volume for its whole history. Volume continuity is therefore checked
  on the intraday series itself. Across all 35 events the 20-day median
  volume after/before is 0.28–1.6, with no ×2, ×5 or ×10 jumps.

- **The step location needs a loose tie rule.** The first version picked the
  day with the peak median contrast and broke ties only within 0.001. That put
  WIPRO's 2019 bonus and UPL's 2019 bonus **one day early**, because median
  noise (0.2881 vs 0.2855) beat the real jump. It showed up as +32% / −24%
  "gaps" on consecutive days. Every day with at least half the peak contrast
  is now a candidate, and the biggest single-day jump among them wins.
- **SIEMENS' demerger** (2025-04-07) opened at 09:35 after a special
  pre-open, so that day is dropped (no opening range) and the event lands on
  04-08, the first usable day. That's correct.
- **Some actions are adjusted in neither source.** IDEA falls from 29.00 to
  17.95 overnight on 2019-03-29 in both `daily_bars` and the intraday bars.
  That's consistent with Vodafone Idea's 87:38 rights issue at ₹12.50
  (theoretical ex-rights price ≈ ₹17.5); the exact ex-date is **unverified**.
  The ratio method can't see an action neither source adjusted. The day stays
  gap-flagged, and **gap-based hypotheses must exclude `gap_flag` days**.

### 8. Overnight gaps: only against the previous trading day

**Dhan's intraday history has holes.** CGPOWER has nothing from 2021-06-09 to
2021-09-10, or from 2021-09-16 to 2022-01-03, although `daily_bars` shows it
traded. A "gap" across that hole read +122%. Holes are counted per symbol:
`days_missing_in_history`, 2,546 in the first 98 names.

### 9. Isolated prints

An isolated print is a bar whose high or low is more than 3% beyond the day's
next most extreme bar.

- **At 09:15/09:20 they're kept.** That's the opening auction, and it belongs
  in the opening range.
- **Later in the day, when two or more names have one at the same
  timestamp,** the index decides:
  - **Real market moves are kept.** The DHFL flash crash (2018-09-21 13:05),
    the COVID circuit reopenings, and the 2024 and 2026 Budget speeches move
    the Nifty bar 4–16× its day's median.
  - **Glitches are dropped.** The 2021-08-03 15:20 cluster (9 names, index at
    1.5×) is one.
- **Known cost:** a sector shock the index doesn't feel is dropped along with
  the glitches: the oil marketing companies on 2018-10-04, and election day
  2024-06-04 at 2.4×. That's a few dozen real days, which is cheaper than
  trading a fake breakout.
- A lone isolated print (198 of them) is kept and counted.

## Validation against `daily_bars` (first 98 names)

| Check | Result |
|---|---|
| Day's high within 0.5% of daily high | **99.72%** |
| Day's low within 0.5% of daily low | **99.62%** |
| Days whose price ratio looks like an unadjusted split | **0** |
| Median error, high and low | 0.000 |

**Where the remaining ~0.3% comes from (unresolved, recorded):** complete
days in **2021–2023** (1.3–1.9% of them) where the intraday range sits inside
the daily range by 0.5–2%. It is almost always inside, never outside: the
intraday low is above the daily low 631 times and below it 0 times. It isn't
missing windows, since complete days show it. A plausible but **unverified**
cause is bars built from sampled ticks in those years. The backtest will report
results with and without 2021–2023.

## Found in passing: `daily_bars` has isolated unadjusted days

**This is outside this experiment, but the swing rotation reads this table.**

- BHARTIARTL on 2018-04-20 reads **402** between neighbours of 346 and 356:
  the unadjusted price on an otherwise adjusted series. This one is verified
  by eye against the raw intraday bars, which read 403.7 high.
- There are more runs of 1–2 days in BHARTIARTL (2017-09-05/06, 2017-11-27/28,
  2018-01-04/05).
- The validator counts **about 100 one-day anomalies** across 98 names. Only
  the BHARTIARTL ones are checked by hand. The others are candidates, not
  confirmed errors.
- The imported volumes aren't consistently adjusted either (the GODFRYPHLP
  case above).

The rotation's ATR and momentum inputs come from this table, and the anomalies
are in 2017–2019, so today's decisions (260-session lookback) are probably
unaffected. That is **unverified**.
