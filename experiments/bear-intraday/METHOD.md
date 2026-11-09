# METHOD — every rule, written before it was run

Fixed 2026-10-03, after `REGIME.md` and before any strategy result. Anything
added later is marked **post hoc** and counted in the README's variant table.

## Conventions (inherited from `intraday-screener/HARNESS.md`, unchanged)

- **Bar *b*** is the 5-minute window starting 09:15 + 5*b*.
- A decision is made on the **close** of bar *k* and fills at the **open of the
  next bar that has one**, plus adverse slippage.
- **First decision:** close of bar 2 (09:30). **Last new decision:** close of
  bar 56 (14:00). **Forced exit:** close of bar 71 (15:10–15:15), every year.
- **Stop:** a stop-market order. It fills at the stop, or at the bar's open if
  the bar gapped through, then slippage. A bar that touches both stop and
  target counts as the **stop**.
- **One trade per name per day.** Slippage is reported at **2 / 5 / 10 bps**
  per side, and the pass bar uses 5.
- **Costs:** Dhan MIS card (`intraday-screener/conf/intraday_costs.yaml`) at
  **₹5 L notional per trade** for the per-trade metric.
- **Universe:** the top 100 F&O names by trailing 20-session median turnover,
  judged at each point in time (`islib.features.Features.liquid`).
- **Regime:** rule B, as known at the previous close (`REGIME.md`). It is
  recomputed from the panel's own daily closes, so the synthetic tests run
  through the same code. A check asserts that it matches `out/regime_daily.csv`.

## Locked limits (the handoff's circuit rule)

F&O stocks have no fixed circuit. They do have dynamic price bands, so a bar
can still be locked. A bar is **locked down** if `high == low` and its close is
≥ 9.5% below the previous day's close. It is **locked up** if `high == low` and
its close is ≥ 9.5% above. The rules:

- **No short entry into a locked-down bar.** The order rolls to the next bar
  that is not locked. If there is none before the exit, the trade is not taken.
- **No cover in a locked-up bar.** Stop and time exits wait for the next
  unlocked bar. If the lock lasts through the close of bar 71, the exit is
  priced at the bar-71 close **plus 20%**, a stand-in for the auction
  close-out, and flagged `exit_locked`.

Index trades are never blocked by this rule. The index's own market-wide halts
show as missing bars, and fills move to the next bar that exists.

## Hypotheses (short only, regime-on days only)

**Count: 5** (B1, B2, B3, B4, B6). B5 isn't run (decision 3).

### B1 Breakdown short
- **Who:** the day's shortlist (`SHORTLIST.md`, N = 5).
- **Trigger:** the first bar *k* in [3, 56] whose close is below the opening
  range low (bars 0–2), with relative weakness confirmed:
  `ret_since_open(stock, k) − ret_since_open(NIFTY, k) < 0`.
- **Initial stop:** the opening-range high.
- **Trailing stop:** after each bar *j* from the entry bar on,
  `stop = min(stop, close_j + 3 × ATR5_j)`. The new level applies from bar
  *j*+1.
- **Target:** none. Time exit.

### B2 Failed relief rally
- **Who:** the day's shortlist.
- **Rally:** the first bar *j* in [1, 17] (by 10:45) whose high reaches
  `T_j = min(previous close, VWAP_{j−1})` while bar *j*−1 closed below
  `T_j`. That is, the price rallied **into** the level from below.
- **Lower high, then breakdown:** let *r* be the bar with the highest high from
  *j* through *m*, and *H* that high. The trigger is the first bar *m* in
  [max(*j*+2, 2), 56] with *r* ≤ *m* − 2, a close below the low of bar *r*,
  and a close below VWAP.
- **Stop:** *H*, the rally high.
- **Target:** none. Time exit.

### B3 Index put on breakdown days *(gated on 0d)*
- **Trigger:** B1's trigger applied to NIFTY itself: the first close below its
  opening range, from bar 3 to bar 56, on a regime-on day. (Relative weakness
  isn't defined for the index.)
- **Trade:** buy the ATM weekly NIFTY put (and, as a reported variant, the
  1-ITM put) at the next bar's open. Exit at the stop or the bar-71 close.
- **Compared with:** shorting NIFTY futures on the **identical** signal, with
  the same exit times.
- **Stop:** NIFTY trades back above its opening-range high.

### B4 Bear put spread vs naked put *(gated on 0d)*
- On B3's signal: long ATM put, short the put two strikes lower (ATM−2), with
  B3's exits.

### B6 Gap-up fade, short only — **cannot be validated**
- **Rule:** the screener's H3b short side (gap ≥ +1%, `gap_flag` excluded, the
  09:30 close back below the day's open), on regime-on days, from the liquid
  100 (not the shortlist).
- **Stop:** the opening-range high. Time exit.
- **Labelled contaminated.** The gap-fade family has already used the
  2023-onward holdout (screener README). B6 is reported for completeness only.
  It cannot pass.

## Controls

Each control replaces **one** ingredient and keeps the same stop
**distance**, never the stop rule (`HARNESS.md` leak 1). Every control is
pooled over **5 reps**.

| Control | Replaces | Question |
|---|---|---|
| `index_short` | The whole strategy. NIFTY futures short, decided at the 09:30 close, filled at the bar-3 open, covered at the bar-71 close, no stop, on every regime-on day | Is the regime the whole edge? |
| `random_name` | The stock, with a random other liquid name at the same moment | Does the selection plus trigger carry anything? |
| `random_time` | The entry bar, with a random bar **at or after** the signal (`HARNESS.md` leak 2) | Is the edge in the trigger moment? |
| `mirror_long` | The direction, traded long | Is the short side doing the work? |
| `regime_off` | The days: the same rules on regime-**off** days in development | Is the regime doing the work, and what does a late switch cost? |
| `random_shortlist` *(diagnostic, `SHORTLIST.md`)* | The shortlist, with 5 random liquid names | Does the shortlist add anything? |

**Index short costs:** NIFTY futures at the actual lot size from the bhavcopy
(`fo_bhav` IDF `lot_size`), with the date-keyed futures card
(`conf/index_futures_costs.yaml`). Slippage is **1 bp per side**. That is
deliberately **lower** than the stocks' 5 bps: a cheaper control is harder to
beat, which is the pessimistic direction for the pass bar. It is also shown at
2 and 5 bps. The intraday spot index stands in for the future. The basis
moves very little within a day, and that is stated here, not modelled.

## Statistics

- **Per-trade CI:** the day bootstrap from `islib.stats`, 2,000 resamples.
- **Strategy minus control:** the same days resampled for both
  (`islib.stats.diff_bootstrap`).
- **Monthly CI:** a month-block bootstrap of the portfolio's monthly P&L
  (`bearlib.stats.month_bootstrap`).
- **Also reported:** results without 2020-02 → 2020-05; by episode; after
  dropping the best 10 days; and the median stop distance in % of price,
  together with what one round trip costs in R.

## Portfolio simulation (pre-registered risk rules, not tuned)

| Rule | Value |
|---|---|
| Capital | ₹5,00,000. No compounding: P&L is quoted in % of ₹5 L. |
| Risk per trade | **0.5%** (₹2,500) of capital, with 1% also reported. Quantity = risk ÷ stop distance at entry. |
| Notional cap per position | ₹5 L (= 5× capital ÷ 5 positions), so the book never exceeds 5× |
| Max concurrent positions | 5 |
| Daily loss limit | Realised day P&L ≤ **−1.5%** → no new entries that day |
| Monthly loss limit | Realised month P&L ≤ **−3%** → no new entries for the rest of the month |
| Charges | Every leg, at the actual notional |
| Index-short portfolio | floor(₹25 L ÷ lot notional) NIFTY lots, at least 1, every regime-on day. 1 lot is also reported. |

**Reported:**
- the monthly calendar;
- the share of positive traded months;
- the worst month and the longest losing streak;
- CAGR on capital, max drawdown and exposure;
- regime-on vs regime-off months;
- for anything with options, before and after 2024-11-20.

## Harness proof (`tests/`)

1. **Random-walk panels** with a random regime: every hypothesis and every
   control comes out at about zero gross, pooled over seeds and reps
   (|t| < 3).
2. **A planted bear edge:** on regime-on days the weakest shortlisted names
   drift down after a trigger. B1 must find it (CI clear of 0) and beat
   `random_time` and `mirror_long`.
3. **Causality:** the shortlist and regime for day *d* are unchanged when every
   bar of day *d* and later is corrupted.
4. **Locked limits:** a synthetic locked-up bar delays a cover, and a
   locked-down bar refuses an entry.
