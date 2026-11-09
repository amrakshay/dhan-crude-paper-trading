# Zen Credit Spread Overnight — trade audit

Source: Dhan Algos listing for Stratzy's "Zen Credit Spread Overnight"
(<https://algos.dhan.co/managers/stratzy/zen-credit-spread-overnight/68596cd26aa2cba24bbb67da?tab=past-trades>),
pulled 2026-11-05.

**This is Dhan's BACKTEST, not a broker statement.** The page shows a disclaimer
before revealing it: ideal conditions, not actual trades or live execution.

## How the data was pulled

The page is Angular; logged out, it calls unauthenticated POST endpoints on
`algo-api.dhan.co/algo/sub/` with the body wrapped as
`{"entity_id":"OpenAlgo","source":"W","iv":"","aes_key":"","ip":"","data":{...}}`.
`data` is `{strategy_id, partner_name:"STRATZY"}`, plus `from_date`/`to_date`
for `GetHistoricalTrades` (what a click on a calendar day calls).

```bash
python3 -I scripts/fetch.py     # -> data/raw/*.json, data/raw/trades/YYYY-MM.json
python3 -I scripts/flatten.py   # -> data/signals.csv, data/legs.csv, + reconciliation
```

## What is in it (verified)

- 228 signals, 2025-07-09 → 2026-11-04, every one a 2-leg NIFTY credit spread
  (118 PE, 110 CE), always 400 points wide, one exit each.
- Per-signal P&L summed by close date matches the calendar's daily P&L
  (`GetHistoricalPnlBreakdown`) on all 206 days, ₹8,43,886.75 total.
- `pnl` is for `lots` lots (2–6, mostly 4–5); `signal_pnl_1lot` is the same trade
  at one lot. `pnl_pct` is on ₹3,20,000 (the listing's max amount);
  `signal_pnl_pct` is on that trade's `margin`.
- 83 of 228 "overnight" trades closed the same day they opened.
- Lot size changes from 75 to 65 within the period (NSE's NIFTY lot revision).

- 2025-09-02's record is malformed: it "closes" a day after its own expiry,
  reports a 1-lot P&L of 0, and its rupee P&L disagrees with its legs. The audit
  recomputes it from its legs as settled at expiry (+₹2,576 at 1 lot).

## Honest-return audit on ₹1,00,000 (2026-11-05)

```bash
python3 -I scripts/audit.py     # -> out/summary.json, out/ledger_<sizing>_<slippage>.csv
```

**Method.** Every trade is recomputed from its leg prices (Dhan's rupee P&L is
not used). Charges come from `../hedged-option-selling/conf/charges/nse-index-options.yaml`
via its `hedgelib.charges` engine — brokerage ₹20/order, STT on the sell side at
the rate in force that day (0.10%, then **0.15% from 2026-04-01**), NSE
transaction charge, SEBI fee, stamp duty, GST — each sourced and dated in that
card. A trade held to expiry pays no exit brokerage or exit slippage, only STT on
an in-the-money long leg. Margin is blocked from entry to exit at **Dhan's own
per-lot `marginRequired`** (₹52.6k–₹108.4k), and lots are bought with free cash.

Slippage is a half-spread per fill, `max(rel × price, ticks × ₹0.05)`:
none / low (0.25%, 1 tick) / base (0.5%, 2 ticks) / high (1%, 4 ticks).

**Results, 9 Jul 2025 → 4 Nov 2026 (1.32 years)**

| | Fixed 1 lot, base slippage | Compounding, base slippage |
|---|---|---|
| Trades taken / skipped | 224 / 4 | 224 / 4 |
| Final equity | ₹2,51,819 | ₹4,72,342 |
| Return / CAGR | +151.8% / 101.1% | +372.3% / 223.5% |
| Max drawdown | 12.1% | 29.6% |
| Win rate | 56.3% | 56.7% |
| Profit factor | 1.46 | 1.28 |
| Gross → slippage → charges | ₹1,95,879 → ₹19,308 → ₹24,751 | ₹4,83,925 → ₹74,816 → ₹36,767 |
| Costs as % of gross | 22.5% | 23.1% |
| Worst trade | −₹13,470 | −₹96,058 |

Fixed 1 lot across the slippage sweep: return 171% (no slippage) / 161% / 152% /
133% (high). Charges alone take 12.6% of gross at 1 lot; brokerage is 72% of
those charges (₹17,720), because ₹20 × 4 orders is large next to one lot's
credit.

The 4 skipped trades are July–August 2025 entries whose per-lot margin
(₹1.0–1.06L) exceeded the free cash in a ₹1L account.

**Period split (fixed 1 lot, base):** before 1 Apr 2026, 122 trades netted
₹96,726. Since then, 102 trades netted ₹55,094. Aug–Nov 2026: 47 trades netted
only ₹6,247, with October negative. Losing months: 4 of 17.

**What this still does not cover.**
- The prices are Dhan's backtest prices. Whether they were executable at the
  stamped minute is unknown, and slippage is a modelled assumption, not a measurement.
- Margin uses Dhan's figure. Real NSE hedged margin for a 400-point spread may be
  lower, which would allow more lots, with more risk.
- Income tax on F&O profits is not deducted.
- 16 months is a short sample with one market regime. The compounding row's 30%
  drawdown is recent: from the 1 Oct 2026 peak to 23 Oct 2026, holding 5–11 lots.

## Rebuilt entry and exit rules (2026-11-05)

```bash
../intraday-screener/.venv/bin/python -I scripts/features.py       # -> data/features.csv
../intraday-screener/.venv/bin/python -I scripts/rebuild_rules.py  # prints every fit number below
```

NIFTY context comes from `../intraday-screener`'s 5-min bars (end 2026-09-25,
so 208 of 228 trades are covered) and `../hedged-option-selling`'s daily VIX and
option bhavcopy (to 2026-09-17). `scripts/fetch_index_bars.py` was written to top
these up, but Dhan's chart API refused it: `DH-902 — requires a Data APIs Plan.
Current plan: Free Plan`.

Each rule is scored against the recorded trades. **This is not a replay**: a
replay needs intraday option prices, which are not available.

### What the strategy is

Despite "credit spread overnight", it is a **directional, ATM, momentum** trade:
it sells the at-the-money option and buys a wing 400 points further out, then
holds it to the next afternoon.

| Rule | Fit |
|---|---|
| **One position at a time.** On a flat day, enter after the 10:10–10:15 bar closes | traded 173 of 198 flat days; 96 of 121 untraded days already held a position |
| **Direction:** last closed 5-min bar fell → sell CE (bear call); rose → sell PE (bull put) | 85% (177/208); **93% when that bar's body ≥ 0.03%** — misses are ~4-point candles |
| **Short strike:** nearest 50 to NIFTY spot | 86% exact; 100% within 50 points |
| **Long strike:** 400 points further OTM | 100% |
| **Expiry:** nearest weekly, including 0-DTE on expiry day | 204/204 with bhavcopy coverage |
| **Size (Dhan's sizing):** floor(₹3.2L ÷ per-lot margin) | 90% |
| **Stop:** spread value reaches credit + 40 points | 76 intraday stops, none better than −39.9, median −44.7 (fills slip past the trigger); overnight gaps blow through it, worst −201.6 |
| **Target:** buy back when the spread is worth ~10 points — **refuted by the replay below** | 18 of 30 early profitable exits left 6–12 points |
| **Time exit:** next trading day at 15:00; **14:53 from July 2026** | 104/105 |
| **Expiry-day entry:** held to settlement until mid-Sep 2025, then exited at the time exit the same day | 7 settled, all entered on or before 16 Sep 2025 |
| **Stop-and-reverse:** at most one re-entry, on the opposite side, never after 14:00 or after a profitable exit | ≤ 2 trades per day 100%; second trade opposite 94% (29/31) |

### Not pinned down

- **Which stops get a reversal.** After a same-day stop it re-entered 68% of the
  time (28/41), after an overnight position was stopped next day 5/15. No feature
  tested separates them.
- **Delayed first entries** (57% enter 10:15–10:21; the rest later). They lean
  towards days with a small 10:10 candle, consistent with a minimum-candle gate or
  a different price feed (futures / 1-min), but no clean threshold.
- **The 25 flat days with no trade.** 14 were in the Mar–Apr 2026 high-VIX period
  (VIX 17–28), but trades were also taken at VIX up to 25.5, so it is not a
  plain VIX cutoff. 7 of the 10 skips at VIX < 15 were expiry days.
- **The target level** is the least certain rule: 40% of early profitable exits
  don't fit it.
- **Which price feed it reads.** The ~4-point candles where direction disagrees
  suggest NIFTY futures or 1-min bars rather than the 5-min index.

## Independent replay with Black-76 pricing (2026-11-05)

```bash
PY=../intraday-screener/.venv/bin/python
$PY -I scripts/bs_calibrate.py                       # -> out/bs_calibration.json
$PY -I scripts/replay_bs.py --target 0 --tag _t0     # -> out/replay_trades_t0.csv (primary)
$PY -I scripts/replay_bs.py --target 10 --tag _t10   # variant with the ~10-pt target
$PY -I scripts/replay_cost.py --tag _t0              # costs it on 1L next to the record
```

The rebuilt rules decide every entry and exit on their own from 5-min NIFTY bars.
Options are priced with Black-76 (`../hedged-option-selling/hedgelib/blackscholes.py`),
because no intraday option prices are available. Dhan's recorded trades are used
only to calibrate the pricer and, afterwards, to compare.

### The pricer, and how wrong it is

`sigma = prevVIX × atm_mult[DTE bucket] × (1 + slope·z + curve·z²)`, with
`z = ln(K/F) / (atm·√T)`. The forward uses 5% carry. It is fitted by least squares
on the entry legs of trades before 2026-04-01.

| Scored on | Spread-value bias | Mean abs error |
|---|---|---|
| In-sample entries (126) | −0.2 pts | 9.5 pts |
| Out-of-sample entries (82) | −3.2 | 11.5 |
| Exits (201) | −5.1 | 11.6 |
| **Each trade's P&L (201)** | **model +3.8 pts optimistic** | corr 0.95, sign right 96.5% |

Stops are priced ~9.5 pts too cheap, because implied vol rises on adverse moves and a
previous-day VIX can't see it. Expiry-day afternoon exits are ~21 pts too cheap (13
trades). Two mistakes found and fixed along the way:
- a raw-moneyness skew overpriced the 400-pt wing by 14 pts at 4–6 DTE;
- "last closed bar" priced 09:16 exits off the previous day's close, missing the gap.

The data ends 2026-09-17 (VIX), so the replay runs 9 Jul 2025 → 16 Sep 2026.

### Results — same window, fixed 1 lot on ₹1,00,000, same costs as the audit

| Base slippage | Trades | Return | Max DD | Win rate | Profit factor |
|---|---|---|---|---|---|
| Dhan's recorded trades | 200 | +161.8% | 12.1% | 58.5% | 1.59 |
| Replay, no target | 248 | +139.0% | 21.0% | 50.0% | 1.37 |
| Replay, no target, −3.77 pt model haircut | 248 | **+74.4%** | **28.9%** | 49.2% | 1.18 |
| Replay with 10-pt target | 282 | +56.8% | 30.5% | 51.4% | 1.14 |
| Replay with 10-pt target, haircut | 210 | −37.0% | 59.8% | 49.1% | 0.90 |

(`replay_cost.py` prints none/base/high slippage for each.)

- **The ~10-pt target is wrong.** It fires 83 times against Dhan's 28, and cuts trades
  Dhan held to the time exit. Every target variant lost 1,000–1,250 points against the same run without one. With no
  target the replay's raw points (+2,716 over 248 trades) are close to the record's
  (+2,844 over 204).
- **Trade-by-trade agreement is moderate.** On days both traded, the first trade had
  the same direction 68% of the time and the same short strike 70%. Where direction
  matched, P&L correlated 0.85 and the exit reason matched 67%. The replay trades on
  220 days to Dhan's 176, because it doesn't reproduce the skip days.
- **The edge fades in the replay too**, and faster:

  | Opened | Replay pts/trade | Recorded pts/trade |
  |---|---|---|
  | Jul 2025 – Mar 2026 | 13.9 (149) | 12.7 (126) |
  | Apr – Jul 2026 | 7.8 (73) | 17.9 (56) |
  | Aug – mid-Sep 2026 | 2.7 (26) | 10.9 (22) |

  At 1 lot, slippage plus charges cost ~3 pts per trade, before the model haircut.

### What this can and cannot say

- The rules were derived FROM these same trades, so the replay is **in-sample for the
  rules**. No untouched period was left; the data after 17 Sep 2026 doesn't exist here.
- Pricing is a model. Its errors are of the same size as the stop (40 pts), so a stop
  that fires or not in the replay may not match reality on the day.
- Margin per lot is Dhan's monthly median, not modelled.
- An honest range for these rules on ₹1L at 1 lot, base slippage, Jul 2025 – Sep 2026:
  **+74% to +139%, with a 21–29% drawdown**. That is roughly half the record's return
  and twice its drawdown, with recent months near zero.

## Update 2026-11-07

Re-fetched: 2 new signals, nothing earlier changed or removed (diffed against the
2026-11-05 pull). The calendar still reconciles on all 207 days.

| Opened | Closed | Side | Short / long | Credit → exit | 1-lot gross |
|---|---|---|---|---|---|
| 5 Nov 10:16 | 6 Nov 14:03 | PE (bull put) | 24350 / 23950 | 103.45 → 22.70 | +₹5,248.75 |
| 6 Nov 14:06 | 6 Nov 14:55 | CE (bear call) | 24800 / 25200 | 126.60 → 169.85 | −₹2,811.25 |

Both break parts of the rebuilt rules:
- **The first exited at 14:03 with +80.75 pts**, before the 14:53 time exit and with
  22.7 pts still in the spread. That is another early profit exit the "~10 pt" target
  doesn't explain (that rule was already refuted by the replay).
- **The second opened at 14:06, three minutes after a PROFITABLE exit.** The rebuilt
  rules allow a re-entry only after a stop, and never after 14:00. It sold calls into
  a ~450-point two-day rally and was stopped at −43.25 pts 49 minutes later.

Direction couldn't be checked against the 5-min candle: there are no bars after
2026-09-25 (Dhan Free plan).

Audit (fixed 1 lot, base slippage) now: 226 taken, ₹2,53,774, +153.8%, max DD 12.1%,
profit factor 1.46. Net at 1 lot: October −₹7,447; November so far +₹1,356 over 5
trades. That includes the 4 Nov trade, which gapped −159 pts overnight to lose −₹10,757.
