# Order-block playbook: does it make money?

Tests the method taught on **order-block.com/blog** (five posts, 2–24 Sep 2026,
read 2026-10-03) on 9.5 years of NSE 5-minute bars, to decide whether it is
worth automating.

Self-contained research, same shape as `experiments/intraday-screener/`, whose
cleaned data, cost card and statistics it reuses **read-only** (nothing in that
directory is changed). No order path, no Dhan contact: the bars are already on
disk (2017-04-03 → 2026-09-25, 210 F&O names).

## What the blog claims

1. Read the market from ~09:20: count bullish vs bearish F&O stocks, note the
   leading and lagging sectors.
2. Trade the side the market leans to.
3. Shortlist from a board of the 20 strongest stocks per side, ranked by an
   undisclosed measure of clean, one-way intraday strength. The top 3 per side (≤ 2 per sector, no
   event-risk names) are the "picks".
4. Mark the **order block** (last opposite candle before a forceful move that
   broke structure, judged on 5-minute closes). Do not chase: wait for price to
   come back to the zone **Z**. The **first retest** is the cleanest.
5. Risk is Z → S1 (just beyond the block's far side). R:R is Z → the second
   liquidity level (R2) over that risk; a floor such as 1 : 1.5 is suggested.
   Size so that one trade risks ≤ 1% of capital.
6. A "four-point check" before acting: in the top 20, sector agrees, breadth
   agrees, right side of VWAP. 4/4 full attention, 3/4 workable, ≤ 2 pass.

None of the posts gives a backtest, a win rate or a definition of the
strength ranking. The arithmetic in them (R:R, break-even win rates, sizing)
was checked and is correct.

## Decisions (2026-10-03)

- **Sectors:** from NSE Indices' published constituent lists, downloaded once
  (`data/nifty500_constituents.csv` → `Industry`), with "Financial Services"
  split into **Banks** (Nifty Bank ∪ Nifty PSU Bank, or "Bank" in the name),
  **Capital Markets** (Nifty Capital Markets) and **Financials ex-Bank**. All
  210 names matched; 20 sectors. `data/sectors.csv`. Today's classification,
  not point-in-time.
- **What decides "worth automating":** the full playbook on the **top-20
  board**. The top-3 **picks** are reported as a sub-result, not judged
  separately.

## The rules as coded (fixed before any result was seen)

Long side described; the short side mirrors every rule. All times are bar
closes, IST; bar *b* is the window starting 09:15 + 5*b* min (as in the
screener's `islib/panel.py`). Nothing is decided before the close of bar 2
(09:30), the screener's standing rule.

**Universe (per day):** every F&O name with a kept day, a previous close, and
no corporate-action gap flag. All 210, not the screener's liquid top 100,
because the blog ranks every F&O stock. Results are also split by liquidity
rank (top 100 vs the rest), since slippage is not measured for the rest.

**Day move** `m = close / previous close − 1`. **Breadth** at bar *k*: the
number of universe names with `m > 0` against `m < 0`.

**Board** (the blog restarts strength at 09:20, the close of bar 0):
- `r = close_k / close_0 − 1`;
- efficiency `ER = |close_k − close_0| / Σ |close_j − close_{j−1}|`, j = 1..k
  (1 = a straight line; a stock that rallied and gave it all back scores
  low);
- `score = r × ER`. Bullish side: `r > 0`, ranked by score; **top 20** form the
  board.
- **Event risk:** a name whose day move has been ≤ −5% at any bar so far gets
  no setup on either side (the blog withholds levels after an unusually
  large fall).
- **Picks:** board order, skipping event-risk names, at most 2 per sector,
  first 3.

**Four-point check at bar *k*:** in the board's top 20 (required) + sector
(equal-weight mean `m` of the *other* names in its sector > 0) + breadth
(bullish count > bearish) + VWAP (close > VWAP). **≥ 3 of 4** = condition
`C_long[k]`.

**Structure and the order block:**
- Structural levels: the opening-range high (bars 0–2, active from the close of
  bar 2) and every confirmed intraday swing high (bar *i* whose high beats the
  two bars before it and is not exceeded by the two after it; confirmed at the
  close of *i* + 2). A level is used up once a bar closes above it.
- **Break of structure** at bar *k* ≥ 3: the close is above an active level.
- **Impulse** (the blog excludes slow drifts): bar *k*'s true range ≥ 1.5 ×
  ATR5, **or** a fair-value gap in the leg (some bar's low above the high two
  bars earlier, between the block and *k*).
- **Order block:** the last bearish bar (close < open) among the 10 bars before
  *k*. None → no setup.
- **Z** = the block's high. **S1** = the block's low − 0.1 × ATR5. Requires
  `close_k > Z`.
- **Liquidity levels above Z**, as of bar *k*, each only if not yet traded
  through today: today's high so far, today's unswept confirmed swing highs,
  the previous day's high, the prior-5-session high. Sorted upward: **R1**,
  **R2**. No R2 → no R:R → no setup.
- **R:R** = (R2 − Z) / (Z − S1) **≥ 1.5**.
- A newer break of structure on the same side **replaces** the setup (the blog's
  terminal moves on to the newest intact block).

**The order** (a limit buy at Z, bracketed):
- Live in bar *j* only if `C_long[j−1]` held, and 3 ≤ *j* ≤ 57 (the
  screener's "no new decisions after 13:55").
- **First retest only:** the first bar after marking whose low reaches Z ends
  the setup: it fills if live, otherwise the setup is gone.
- Cancelled if price reaches R2 before the retest, or on replacement.
- **One trade per name per day.**

**Fills** (pessimistic, matching the screener engine's spirit):

| Event | Fill |
|---|---|
| Limit entry | Only if the low trades **through** Z (touching is not enough: queue). At Z, or at the open if the bar opened below Z. No slippage. |
| Stop in the fill bar | If the fill bar's low also reaches S1, stopped in that bar. The target is never credited in the fill bar. |
| Stop | At S1, or at the open if gapped through, minus slippage (2 / **5** / 10 bps) |
| Target R2 | Limit; at R2 or a better open; no slippage |
| Bar touches both | Stop |
| Time | Close of bar 71 (15:10–15:15), minus slippage |

**Sizing and the main metric:** a ₹5,00,000 account risking 1% (₹5,000) per
trade, the blog's own example. Quantity = ₹5,000 / (Z − S1), notional capped
at 5× capital (₹25 L, the MIS limit). Charges from
`../intraday-screener/conf/intraday_costs.yaml` at the actual notional.
**Net R** = rupee P&L after charges and slippage ÷ ₹5,000. Also reported: bps
of notional at a fixed ₹1 L and ₹5 L, to compare with the screener's H1–H4.

## Controls (each replaces ONE ingredient, keeps the same risk and reward)

Geometry is kept as fractions: depth `(close_k − Z)/close_k`, risk `(Z −
S1)/Z`, reward `(R2 − Z)/Z`. The order is live in exactly the bars the real
one was (same gating, same replacement times), with the same first-touch and
cancel rules.

| Control | Replaces | Question |
|---|---|---|
| `random_side` | direction → coin flip (zone mirrored to the other side of price) | Does the direction carry information? |
| `random_depth` | the order-block level → a random depth, uniform on [0, 2×] the real one | Is the block's level special, or is any pullback limit as good? |
| `random_name` | the stock → a random other universe name, same moment | Does the selection carry information? |

## Pass bar (same four tests the screener used, fixed before looking)

1. Mean **net R per trade > 0** at 5 bps slippage, day-block-bootstrap 95% CI
   clear of zero, on the **development period 2017-04 → 2022-12**.
2. Beats **each** control: the bootstrap CI of (strategy − control) clear of 0.
3. Positive in a majority of calendar years (≥ 4 of 6).
4. On the **holdout 2023-01 → 2026-09**, run **once**, still positive after
   costs. **Run only if 1–3 pass, and only after asking Akshay.**

## Diagnostics (exploratory, NOT the pass bar)

Each tests one of the blog's specific claims, on the development period only:

- **First retest vs later retests** (retest 1, 2, 3+).
- **Wait vs chase:** market entry at the next open after the break, same S1 and
  R2.
- **Four-point check:** 4/4 vs 3/4 vs ignoring it.
- **Exits:** R2 target vs R1 vs time-only.
- **Picks only** (top 3 per side).
- **R:R floor** on vs off.
- **Rank checked at marking only** vs at every bar before the fill (a stock
  that pulls back to its zone tends to drop down the board).
- **Stop width:** results by Z − S1 bucket (charges are a fixed slice of
  notional, so a tight stop pays a large slice of R).
- A **portfolio run** with the blog's own risk rules: 1% per trade, a daily stop
  at −2% or two losses in a row, 5× gross exposure cap.

Diagnostics are many looks at the same data. Only a pass-bar result counts as
evidence.

## Status

| Step | State |
|---|---|
| Rules written down (this file) | Done, 2026-10-03, before any run |
| Implementation + synthetic tests | Done. 36 tests: fill rules, no lookahead, random-walk null, planted edge. One real trade checked by hand (below). |
| Development run vs pass bar | **Done, 2026-10-03: FAILS** (below) |
| Diagnostics | Done, development only |
| Holdout | **Not run, and not needed.** It's only run if tests 1–3 pass. The 2023–2026 data stays unseen by this idea. |

## Result: the playbook loses money (development 2017-04 → 2022-12)

**Verdict: not worth automating.** Net of costs it loses money in every year.
The order-block structure does carry real information (it beats all three
controls), but not enough to pay for trading it.

At 5 bps slippage, ₹5 L account, 1% (₹5,000) risk per trade, 210 F&O names:

| | Value |
|---|---|
| Trades | 21,496 (15.4 a day) |
| Gross, after slippage | **−0.045 R** per trade |
| Charges | 0.132 R per trade |
| **Net** | **−0.177 R per trade**, 95% CI [−0.209, −0.144] |
| Years positive | **0 of 6** (−0.13 to −0.23 R each year) |
| Win rate | 24% |
| Exits | stop 58%, stop in fill bar 14%, time 17%, target 10% |
| Net at 2 / 10 bps slippage | −0.084 / −0.332 R |

Pass bar:

1. Net positive with the CI clear of 0: **fail**.
2. Beats every control: **pass**. Strategy minus control is +0.070 R for
   random side, +0.068 R for random depth and +0.074 R for random name, each
   with the CI clear of 0.
3. Majority of years positive: **fail** (0 of 6).

### Why it fails: the stops are too tight for the costs

The blog's stop sits just beyond one 5-minute candle, so the **median stop is
0.28%** of price. Sizing 1% risk on that means ₹17 L of notional on a ₹5 L
account (30% of trades hit the 5× cap). Charges scale with notional, so they
come to **0.13 R per trade whatever the account size**. A strategy whose gross
edge is about zero can't carry that.

By stop-width quintile (net R):

| Stop width | Net R | Charges R |
|---|---|---|
| 0.09–0.16% | −0.30 | 0.19 |
| 0.16–0.24% | −0.24 | 0.18 |
| 0.24–0.33% | −0.11 | 0.14 |
| 0.33–0.50% | −0.13 | 0.10 |
| 0.50–6.7% | −0.10 | 0.06 |

### The blog's specific claims (exploratory)

Net R per trade, development period, 95% CI:

| Claim | Test | Result |
|---|---|---|
| The first retest is the cleanest | Retest 1 / 2 / 3+ | −0.18 / −0.15 / −0.09 (n = 21,327 / 1,307 / 250). **No support**: later retests are no worse. |
| Don't chase, wait for the zone | Market entry right after the break | Chase −0.165, wait −0.177. Waiting improves gross (−0.045 vs −0.110) but its tighter stops pay more in charges; **both lose**. |
| The four-point check | 4/4, ≥ 3/4, top 20 only, none | −0.160, −0.177, −0.188, −0.198. **A filter worth ~0.03 R**, far short of the 0.18 R gap. |
| Start with the picks | Top 3 per side | **−0.089 [−0.179, +0.011]**, gross +0.032, 1 of 6 years. The best cut, still negative. |
| R:R ≥ 1.5 | Floor off | −0.173: no effect. |
| R2 target | R1 / time only | −0.204 / −0.164: none positive. |
| (Rank must hold at entry) | Rank checked only when the block is marked | −0.196: worse. |
| (Fill-bar pessimism) | Stop in the fill bar ignored, the most generous reading | −0.154 (picks: −0.071). **The verdict doesn't hinge on it.** |

**With the blog's own risk rules**, on the baseline trades (1% risk, stop the
day at −2% or two losses, 5× exposure cap): 2.6 trades a day, **−₹4.5 L a
year on a ₹5 L account**. The account would be gone inside the first year.

### What would have to be true for this to work

The order-block level, the direction and the selection are each worth about
+0.07 R against a random version. That's a real but small piece of
information. To pay the costs, the gross edge needs to beat 0.13–0.19 R per
trade **on stops this tight**. Wider stops cut charges per R but, here,
didn't raise gross. Nothing in the eleven variants found that combination.
**Don't re-tune these rules on this data to find one**: that is H3c's road
(screener README, +27 bps on development, +6 on the holdout).

### Caveats, stated rather than fixed

- **Undisclosed parts were defined here.** The blog doesn't define its
  strength score, its liquidity levels, "event risk" or "forceful". The
  definitions above are reasonable readings, fixed before any run. Their
  proprietary board may differ. The conclusion held across every variant
  tried, which makes a definition fix an unlikely rescue.
- **Survivorship:** today's F&O list. **Sectors:** today's classification.
- **5-minute bars can't order events inside a bar.** Resolved against the
  trade, and bounded by the sensitivity row above.
- **Slippage isn't measured** for names outside the top 100 by turnover (42%
  of trades). Those trades did no better and no worse.

### One trade checked by hand

ICICIBANK long, 2018-04-05:

- **Break:** the 12:05 bar closes at 275.65, above the 275.30 swing high
  confirmed at 11:55.
- **Impulse:** the breaking bar's range is 0.85, at least 1.5 × the 5-minute
  ATR.
- **Order block:** the 11:50 bar, the last red one. Z = 275.25 (its high),
  S1 = 274.80 (its low 274.85 minus 0.1 ATR).
- **Levels:** R1 276.40 and R2 277.35, unswept swing highs from 10:55 and
  10:30. The 5-day high of 287.30 is third. R:R 4.69.
- **Fill:** the 12:10 bar's low touches Z exactly, which isn't a fill. The
  12:15 bar trades through to 275.05 and fills at 275.25.
- **Exit:** target 277.35 at 13:25.
- **Size and result:** 9,083 shares (the 25 L cap binds), charges ₹940,
  +3.63 R net.

All of it matches the raw bars.

## Layout

```
data/sectors.csv              symbol → sector (built from the files beside it)
data/*.csv                    NSE Indices constituent lists, as downloaded
obl/                          board + conditions, setup detection, simulator, controls
scripts/run_ob.py             one run vs the pass bar
scripts/diagnostics.py        the exploratory cuts
tests/                        no-lookahead, fills, synthetic null + planted edge
out/                          results
.venv/                        numpy, pandas, numba
```
