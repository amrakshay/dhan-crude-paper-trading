# Bear-market leaders pot — research backtest

## Findings at a glance (added after the run)

**Verdict: FAIL.** The primary configuration (25% pot, 5 slots) fails all four
pass-bar criteria on the full period, and fails all four again on the run cut off
before the 2026 window. The 15% and 35% sensitivity runs fail too (15% passes
the drawdown criterion only). No forward-run plan is written, because the task
asked for one only if the test passed.

| | V0 baseline | Combined, 25% pot |
|---|---|---|
| ₹10,00,000 → | ₹70,93,012 | ₹70,23,180 |
| CAGR | 19.09% | 18.98% |
| Max DD | −17.85% | −19.87% |
| MAR | 1.070 | 0.955 |
| Pot net P&L (230 trades, after costs) | — | **−₹2,08,023** |

- **The pot loses money. The loss is not a cost-of-capital effect.** Its 230
  trades average −0.02% (median +0.19%, win 51%). The spec's §11 off-gate trades
  averaged +7.81%. That figure does not carry over to a pot that must sell at
  the flip: 21 of those 38 trades were held *through* a flip and averaged
  +9.99%. They collected the post-flip rally, which a forced-out pot cannot.
  (Checked against `gate_run/out/V3b_trades.csv`.)
- **One window decides it: Jan–Apr 2025.** NIFTY was nearly flat (−0.6% end to
  end, −8.0% at the trough), but the leaders crashed. The pot lost 28.6% of
  its allotment (−₹5.13 lakh), and 2025 ends −5.1% for the combined book vs
  +1.4% for the baseline. 2022 lost 11.4% of the allotment.
  In the 2020 COVID crash the pot was about flat (−0.7%).
- **The pot made money in short whipsaw episodes and lost it in real bears.**
  Episodes of 30 sessions or fewer: +₹3.46 lakh. The five longer than 30
  sessions: −₹5.54 lakh. This split uses each episode's length *after the fact*,
  which nobody knows at entry, so it is not a rule anyone could trade.
- **2026 was barely positive:** +2.6% on the allotment (+₹46k). WELCORP made
  +69.6% (+₹2.33 lakh), but the pot only entered it on 2026-06-15. Earlier
  "leaders" (SHRIRAMFIN, SBIN, HINDCOPPER, FEDERALBNK, ASHOKLEY) were stopped out
  in March. Excluding 2026 makes the pot worse (−₹2.54 lakh), not better.
- **A small lookahead in the baseline engine** (not caused by this work, and
  kept so V0 reproduces). At the open of t+1, the main book's entry cap reads
  the gate at the *close* of t+1. Reading the signal close instead gives V0
  18.98% / −17.84% / MAR 1.064, against 19.09% / −17.85% / 1.070. The effect is
  small but real.
- **Survivorship is large in this universe.** An equal-weight index of the 485
  surviving names compounds at 23.1%/yr against 11.9%/yr for the real Nifty 500
  (^CRSLDX), 2016-07-20 → 2026-09-17. Equal weight vs cap weight is a confound,
  so treat this as an upper bound. The bias flatters a rule that buys winners,
  so it makes the pot's FAIL *more* robust.
- **Status: DESCRIPTIVE.** Every window has been seen before (spec §11). This
  could have shown promise but not proof. It did not show promise.

The pre-registration below is reproduced word for word. It is also in
`out/README_preregistration_snapshot.md`, with its sha256 and UTC timestamp in
`out/preregistration.sha256`, written before any pot result existed.

---

Self-contained research experiment. Imported by nothing in the app. Places no
orders and touches no broker endpoint; it reads CSV price panels and simulates.

> RESULTS SECTION IS EMPTY UNTIL THE RUN. Everything above the line
> "PRE-REGISTRATION ENDS HERE" was written and saved before any pot result was
> computed. The only numbers seen before writing it were: the V0 baseline
> reproduction (step 1, below) and the list of gate-off episodes recomputed from
> the NIFTY 50 series (a property of the index, not of the pot).

---

## Pre-registration (written 2026-10-03, before any pot result)

### Status of this test

**DESCRIPTIVE, NOT VALIDATION.** Every gate-off window in this data has already
been looked at in the spec's §11 (and the 2026 window again in the discussion
that produced this idea). No window here is out-of-sample. Whatever this shows,
validation can only come from a forward paper run.

**Survivorship bias.** The universe is today's Nifty 500 run backwards. A rule
that buys names that went up is flattered by a universe made of names that
survived and grew into the index. The pot buys relative-strength leaders, so it
is exactly the kind of rule this bias favours.

### Step 1 — baseline reproduced before anything was changed

Original engine `pullback/.../research2/gate_run/engine.py`, loader
`run_gates.load`, data `gate_run/data_ext` (485 symbols, NIFTY to 2026-09-17),
imported read-only by `scripts/reproduce_baseline_original.py`:

| | spec §9.3 | reproduced |
|---|---|---|
| Final (from ₹10,00,000) | ₹70,93,012 | ₹70,93,012 |
| CAGR | 19.1% | 19.09% |
| Max DD | −17.8% | −17.85% |
| MAR | 1.07 | 1.070 |
| Trades | 669 · win 50% | 669 · win 49.6% |

Exact reproduction.

### The design

**Main book: V0, unchanged.** Every rule of C4 / §3 applies as before: P8 gate,
P9 63-day entry block, breadth throttle, 10 slots, 3.5×ATR chandelier,
rank-15 rotation, liquidate-all at the open after the gate goes off. The copied
engine with the pot disabled must reproduce ₹70,93,012 to the rupee (regression
test), or nothing below is run.

**One pool of capital, two ledgers.** Combined equity starts at ₹10,00,000.
While the gate is ON the pot has zero capital and the main book owns
everything — so the main book has full capital at every flip.

- **Activation.** At the first gate-OFF close *t* (NIFTY close ≤ its SMA200,
  the same P8 test), the pot is allotted `f × combined_equity(t)`. The
  transfer happens at the open of *t+1*, after the main book's liquidation
  fills, and is capped at the main book's cash at that moment (so no ledger can
  go negative).
- **Pot size f:** **0.25 (primary)**. **0.15 and 0.35 are pre-declared
  sensitivities**, run once each, not tuning. The verdict is decided on 0.25.
- **Slots: 5.** Each pot position is sized `min(pot_equity / 5, pot_cash)`,
  whole shares, floor — P13 applied inside the pot. At f = 0.25 that is 5% of
  total equity per position, half a main-book slot.
- **Ranking day.** Pot ranking runs on the same weekly rebalance day as the main
  book (P18: last session of a calendar week, detected exactly as the engine
  does), and only if the gate is OFF at that close. A pot that activates
  mid-week waits for the weekly ranking. No extra ranking on the flip day.
- **Leader definition (eligibility), all at close *t*:**
  1. P2 liquidity: ADV20 turnover ≥ ₹10 cr
  2. P3 price: close ≥ ₹50
  3. P4 trend: close > own SMA200
  4. P6 momentum floor: `mom126 > +10%` (`close[t−5]/close[t−126] − 1`)
  5. **RS-126: `mom126_stock > mom126_NIFTY`**, NIFTY's computed with the same
     formula on the same dates.
  6. **RS-63: `ret63_stock > ret63_NIFTY`**, `ret63 = close[t]/close[t−63] − 1`
     (the same 63-session return P9 uses for NIFTY). This is the condition that
     makes it a *bear-market* leader: it must have beaten the index over the
     last quarter, not merely carry momentum from before the decline.
- **Rank:** P7 score `mom126 / (ATR14 / close)`, descending.
- **Entries:** the top 5 eligible names not already in the pot are queued; at
  the next open they fill until the pot holds 5. **No breadth throttle (P11) and
  no P9 63-day block in the pot** — both exist to keep the main book out of
  bears, which is the opposite of the pot's job.
- **Exits (pot):** 3.5×ATR14 initial stop and chandelier trail exactly as
  P14/P15 (intrabar, gap-through fills at the open); rotation exit when the
  last computed pot rank > 15 or the name is no longer eligible (next open);
  **forced liquidation of every pot position at the open after the first
  gate-ON close**, before any main-book entry fills that same open. The pot's
  cash (allotment ± P&L) then rejoins the main book's cash.
- **Independence.** The pot and the main book are separate ledgers (separate
  portfolios in an app). The same symbol may be sold by the main book and
  bought by the pot at the same open; both legs are charged. Counted and
  reported.
- **Costs:** spec §5 exactly — `buy_cost`, `sell_cost` (STT 0.1% both sides,
  exchange txn 0.00297%, SEBI, stamp 0.015% buy, GST on txn, ₹16 DP per sell),
  slippage 0.05% per side, ₹0 brokerage.
- **Idle cash:** earns 0 in both the baseline and the combined run. No liquid
  fund yield is simulated.
- **Timing:** signals at close *t*, fills at open *t+1*, stops intrabar on *t+1*
  onwards — identical to the engine. Nothing reads a bar after the decision
  date.
- **End of data (2026-09-17):** open positions closed at the final close
  (`eod_final`), as the engine does. The 2026 window is still open at the end of
  the data.

### Gate-off windows (recomputed from the NIFTY series)

39 gate-off episodes after the 260-session warm-up (22.7% of sessions). The
five longer than 30 sessions — the spec §11 definition — are:

| Window | Sessions |
|---|---|
| 2019-07-30 → 2019-09-19 | 34 |
| 2020-02-26 → 2020-07-16 | 95 |
| 2022-04-29 → 2022-07-28 | 64 |
| 2025-01-06 → 2025-04-17 | 70 |
| 2026-02-27 → 2026-09-17 (open) | 136+ |

23 of the 39 episodes last under 5 sessions. The pot has no hysteresis
filter; whipsaw cost in short episodes is part of the result and is reported.

**Per-window return** is measured on the combined and on the baseline equity
curve from the close of the last gate-ON session before the episode to the close
of the session in which the pot's liquidation fills (for 2026, to the last close
in the data). Pot trades are attributed to the episode in which they were
entered.

### PASS BAR — fixed, never moved

Evaluated on the primary configuration (f = 0.25, 5 slots).

1. **Return.** Combined CAGR > V0 CAGR **and** combined-minus-baseline window
   return > 0 in a majority (≥ 3 of 5) of the gate-off windows longer than 30
   sessions.
2. **Drawdown.** Combined max drawdown no worse than V0's by more than 2.0
   percentage points (i.e. ≥ −19.85% given V0's −17.85%).
3. **Risk-adjusted.** Combined MAR ≥ V0 MAR (1.070).
4. **Pot edge.** Sum of pot trade net P&L (after costs) > 0, **and** still > 0
   after removing the single gate-off episode with the largest pot P&L
   (leave-one-window-out).
5. **Not carried by 2026.** Criteria 1–4 must also hold when both the baseline
   and the combined run are truncated at 2026-02-26 (the last session before
   the 2026 window), with "≥ 3 of 4" long windows for criterion 1. The 2026
   window has been looked at, so a pass that depends on it is not a pass.

**Verdict = PASS only if all five hold.** The 0.15 and 0.35 runs are reported
next to it; if they disagree with the 0.25 verdict that is stated, not used to
change it.

### Survivorship measurement (if feasible)

As spec §12 did for smallcaps: compare an equal-weight index of the 485
surviving names against the real Nifty 500 index (`^CRSLDX`, via yfinance) over
the full period and inside the gate-off windows. Caveat fixed in advance: equal
weight vs cap weight is a confound, so the gap is an upper-bound indicator of
the bias, not a clean measurement. If the fetch fails, the bias is stated as
unmeasured.

### Variant ledger

Every run is counted in the results, including reproductions and regressions.

**PRE-REGISTRATION ENDS HERE**

---

## Results (run 2026-10-03)

All numbers come from `scripts/run_pot.py` (log: `out/run_pot.log`, data:
`out/results.json`, `out/pot*_windows.csv`, `out/pot*_pot_trades.csv`). The
test suite (7 tests) passed before and after the run (`out/tests.log`).

### Mechanics verified (tests/test_pot.py)

| Claim | How it is proven |
|---|---|
| Pot off == V0 | Copied engine, pot_frac 0: ₹70,93,012 and 669 trades, the same trade set as the original engine (symbol, entry and exit dates identical; net P&L differences 0.0) |
| Pot only active while the gate is OFF | Every day the pot holds a position or is active follows a gate-OFF close. After 2+ gate-ON closes the pot holds ₹0 |
| Fully liquidated at the open after the flip | Each liquidation follows a gate-ON close. That day the pot has 0 positions, ₹0 cash, is inactive, and has 0 stragglers. Every `flip` exit fills on a liquidation day |
| Main book capital at the flip = baseline + pot P&L | First episode, exact. Before activation, combined equity equals baseline equity. While the pot is live, the difference equals pot P&L every day. At the flip open, main cash equals baseline main cash plus the episode's pot P&L. At every liquidation, main cash after = main cash before + pot cash, and the episode's pot P&L equals the sum of its trades' net |
| No lookahead | All bars after 2022-06-15 (inside an active pot window) are scrambled, prices ×0.3–3, for every symbol and NIFTY. Equity, pot holdings and every entry up to that date are unchanged. Every pot entry fills the session after a gate-OFF weekly close |

### Pass bar

Primary configuration: f = 0.25, 5 slots.

| # | Criterion | Full period (to 2026-09-17) | Truncated (to 2026-02-26) |
|---|---|---|---|
| 1 | CAGR > V0 **and** ≥ 3 long windows improved | 18.98% vs 19.09%; 2 of 5 windows → **FAIL** | 20.13% vs 20.31%; 1 of 4 windows → **FAIL** |
| 2 | Max DD no worse than V0 by > 2pp | −19.87% vs bar −19.85% → **FAIL** (by 0.02pp) | −19.87% vs bar −19.85% → **FAIL** |
| 3 | MAR ≥ V0 | 0.955 vs 1.070 → **FAIL** | 1.013 vs 1.138 → **FAIL** |
| 4 | Pot P&L > 0, and > 0 without its best window | −₹2,08,023; best window removed −₹2,94,082 → **FAIL** | −₹2,54,201; best window removed −₹3,40,260 → **FAIL** |
| 5 | Criteria 1–4 also hold without 2026 | — | **FAIL** (all four) |

Criterion 2 misses by 0.02pp, which is inside any reasonable noise. The bar is
fixed, and criteria 1, 3 and 4 fail by wide margins anyway, so this does not
affect the verdict. The "best window" removed in criterion 4 is episode 32
(2024-11-14 → 11-21, 4 sessions, +₹86,059). The largest pot gain came from a
four-day dip, not from a bear market.

Pre-declared sensitivities (reported, not used to change the verdict):

| Pot | Final | CAGR | Max DD | MAR | Long windows improved | Pot P&L | Without best window | Pass |
|---|---|---|---|---|---|---|---|---|
| V0 (none) | ₹70,93,012 | 19.09% | −17.85% | 1.070 | — | — | — | — |
| 15% | ₹70,57,613 | 19.04% | −18.11% | 1.051 | 2/5 | −₹1,18,727 | −₹1,69,716 | C2 only |
| **25%** | **₹70,23,180** | **18.98%** | **−19.87%** | **0.955** | **2/5** | **−₹2,08,023** | **−₹2,94,082** | **none** |
| 35% | ₹69,34,272 | 18.85% | −22.04% | 0.855 | 2/5 | −₹3,12,077 | −₹4,32,790 | none |
| *Truncated to 2026-02-26:* | | | | | | | | |
| V0 | ₹71,79,322 | 20.31% | −17.85% | 1.138 | — | — | — | — |
| 15% | ₹71,13,602 | 20.21% | −18.11% | 1.116 | 1/4 | −₹1,47,857 | −₹1,98,847 | C2 only |
| 25% | ₹70,61,279 | 20.13% | −19.87% | 1.013 | 1/4 | −₹2,54,201 | −₹3,40,260 | none |
| 35% | ₹69,54,392 | 19.96% | −22.04% | 0.906 | 1/4 | −₹3,74,772 | −₹4,95,485 | none |

The picture is monotonic: a bigger pot gives a worse result. All three sizes
trade the same names (230 pot trades); only the size changes.

### Per window (25% pot, full period)

Window return runs from the last gate-ON close before the episode to the close
of the day the pot's liquidation fills. For 2026, it runs to the last close in
the data. "Pot return" is the pot's net P&L divided by its allotment.

| Window | Sessions | NIFTY move (trough) | Baseline | Combined | Δ | Pot trades | Pot P&L | Pot return | Pot win |
|---|---|---|---|---|---|---|---|---|---|
| 2019-07-30 → 2019-09-19 | 34 | −4.3% (−4.3%) | −0.02% | +2.11% | **+2.13** | 10 | +₹30,247 | +8.5% | 60% |
| 2020-02-26 → 2020-07-16 | 95 | −9.0% (−35.5%) | −1.29% | −1.47% | −0.17 | 18 | −₹2,892 | −0.7% | 39% |
| 2022-04-29 → 2022-07-28 | 64 | −1.8% (−11.3%) | +1.02% | −1.66% | −2.68 | 17 | −₹1,14,592 | −11.4% | 41% |
| 2025-01-06 → 2025-04-17 | 70 | −0.6% (−8.0%) | 0.00% | −7.15% | **−7.15** | 23 | −₹5,12,690 | −28.6% | 30% |
| 2026-02-27 → open (2026-09-17) | 136+ | −8.7% (−12.4%) | −1.20% | −0.54% | +0.66 | 32 | +₹46,178 | +2.6% | 38% |
| 25 episodes of ≤ 5 sessions | — | — | — | — | — | 70 | +₹2,42,927 | — | — |
| 9 episodes of 6–30 sessions | — | — | — | — | — | 60 | +₹1,02,799 | — | — |
| **All 39 episodes** | | | | | | **230** | **−₹2,08,023** | | |

NIFTY move is measured from the last gate-ON close to the last gate-OFF close.
The spec's §11 used a different base, so its figures differ slightly (2019:
−3.4% there, −4.3% here). The full 39-row table is in `out/pot25_full_windows.csv`.

**Pot P&L with and without 2026.** All windows: −₹2,08,023 over 230 trades.
Entries before 2026-02-27: −₹2,54,201 over 198 trades. The 2026 window alone:
+₹46,178 over 32 trades. 2026 is the contaminated window, and it *helps* the
pot. Without it the result is worse.

### Equity and drawdown

| | V0 | Combined 25% |
|---|---|---|
| Max DD | −17.85%, peak 2024-02-07 → trough 2024-03-20 | −19.87%, peak 2024-07-11 → trough 2025-12-18 |
| Worst month | −8.70% | −8.63% |
| Trades (main + pot) | 669 | 670 + 230 |

Yearly return, V0 → combined: 2016 +0.6 → −0.5 · 2017 +50.3 → +53.2 · 2018
−3.9 → −4.3 · 2019 +5.5 → +8.7 · 2020 +29.1 → +28.9 · 2021 +93.9 → +93.6 ·
2022 +21.4 → +19.4 · 2023 +35.7 → +36.7 · 2024 +8.8 → +10.4 · **2025 +1.4 →
−5.1** · 2026 (to 17 Sep) +1.6 → +3.7.

The deeper drawdown is the 2024–25 baseline drawdown with the pot's 2025 loss
added to it.

### Pot trades, exits and concentration (25%)

| Exit | n | Mean | Win | Net |
|---|---|---|---|---|
| `flip` (forced at regime return) | 130 | +2.56% | 68% | +₹6,09,584 |
| `stop` (3.5×ATR trail) | 68 | −6.45% | 22% | −₹9,12,312 |
| `signal` (rank > 15 / ineligible) | 30 | +3.34% | 47% | +₹89,172 |
| `eod_final` | 2 | +0.88% | 50% | +₹5,532 |

Costs paid by the pot are about ₹96,000 (§5 model).

Top 10 trades total +₹7.03 lakh. WELCORP 2026 alone is +₹2,33,377, and without
it the pot is −₹4.41 lakh. The bottom 10 total −₹4.82 lakh: 7 are in 2025 and 2 in
March 2026.

| Best | Window | Return | Net | | Worst | Window | Return | Net |
|---|---|---|---|---|---|---|---|---|
| WELCORP | 2026 | +69.6% | +₹2,33,377 | | PGEL | 2025 | −17.4% | −₹62,388 |
| MCX | 2026 | +29.5% | +₹90,181 | | HINDCOPPER | 2026 | −14.9% | −₹52,270 |
| BSE | Feb–Mar 2022 | +40.4% | +₹74,779 | | WOCKPHARMA | 2025 | −17.1% | −₹48,873 |
| BLS | 2022 | +32.6% | +₹58,112 | | LLOYDSME | 2025 | −15.3% | −₹48,827 |
| VEDL | 2026 | +15.4% | +₹49,788 | | BSE | 2025 | −17.5% | −₹48,434 |

On 6 occasions the main book sold a name and the pot bought the same name at the
same open. Both legs were charged, as pre-registered.

### Why it fails (interpretation, partly verified)

1. **Verified:** the §11 "off-gate trades are good" statistic depends on holding
   through the flip. 21 of V3b's 38 off-gate trades were still open when the gate
   came back on, and they averaged +9.99%. The other 17 averaged +5.12%. A pot
   that is liquidated at the flip, by design, gives up the first group's edge.
2. **Verified for this sample:** in real bear windows, the leaders this rule
   picks get stopped out. 2025 had 23 trades with 30% wins, mostly 3.5×ATR
   stops. Relative-strength leaders were the names that fell hardest in the
   Jan–Mar 2025 small- and mid-cap sell-off, while NIFTY itself ended the window
   nearly flat.
3. **UNVERIFIED as a cause:** V3b's off-gate entries were also limited by the
   breadth throttle (`cap = min(offgate_slots, slots_allowed)` in the original
   engine). The pot, as pre-registered, ignores breadth. Whether a
   breadth-limited pot would do better was **not run**. Running it now would be
   tuning on windows that have already been seen.

### Survivorship (`scripts/survivorship.py`, `out/survivorship.json`)

| 2016-07-20 → 2026-09-17 | Equal-weight of the 485 survivors | Nifty 500 index (^CRSLDX) |
|---|---|---|
| Total | +725% | +214% |
| CAGR | 23.1% | 11.9% |

Inside the long windows (survivors EW vs index): 2019 +5.0% vs +4.1% · 2020
−5.6% vs −7.6% · 2022 −1.0% vs −0.4% · 2025 −6.1% vs −2.7% · 2026 +5.1% vs
−3.4%.

The 11pp/yr gap mixes survivorship with the equal-weight vs cap-weight
difference and the daily-rebalance bonus, so it is an upper-bound indicator, not
a measurement. It points one way: the universe flatters rules that buy winners.
The pot still failed with that tailwind.

### Other caveats

- The data ends 2026-09-17, and the 2026 window is still open. Two pot positions
  are marked at the final close (`eod_final`).
- The `data_ext` panel is Dhan to 2026-07-14, then yfinance (spec §7.2).
- VEDL appears in two 2026 pot trades (+₹49,788 and −₹46,954). Spec §7.3 flags a
  VEDL corporate-action mismatch in the *5-minute* cache and calls the daily
  panel clean. Whether a 2026 VEDL corporate action affects these bars is
  **UNVERIFIED**. The two trades net about +₹2,800, so the result does not
  depend on them.
- Idle cash earns 0 in both runs. A liquid-fund yield would raise the baseline
  and the combined book about equally while the gate is off, and the pot's
  allotment would forgo it. It would not rescue the pot.
- Pre-tax. Pot trades are short holds, so STCG applies.

### Variant ledger (every run counted)

| # | Run | Purpose |
|---|---|---|
| 1 | V0, original pullback engine (read-only import) | Step 1 reproduction: ₹70,93,012 exact |
| 2 | V0, copied engine, pot off, full | Regression, identical to #1 |
| 3 | V0, copied engine, truncated to 2026-02-26 | Comparator for criterion 5 |
| 4–5 | Pot 25%, full + truncated | **Primary, pre-registered** |
| 6–7 | Pot 15%, full + truncated | Pre-declared sensitivity |
| 8–9 | Pot 35%, full + truncated | Pre-declared sensitivity |
| 10 | V0 with the entry cap reading the signal-day gate | Diagnostic of the baseline engine, not a pot variant |

That is **10 evaluation runs over 5 distinct configurations** (V0, pot 25/15/35,
V0-cap diagnostic). Of these, **3 are pot configurations, all pre-registered**.
Additional runs did not test new configurations:

- the test suite re-runs V0 and pot 25%, plus pot 25% on scrambled future data;
- run #2 was repeated twice during development (same config, same result);
- the survivorship script runs no strategy.

**No post-hoc pot variant was run.** That includes a breadth-limited pot,
hysteresis, other slot counts and dropping RS-63.

### Verdict

**FAIL.** As specified, the bear-market leaders pot lowers CAGR, deepens the
drawdown and lowers MAR at every pre-declared size. Its own P&L is negative after
costs, with or without its best window, and with or without 2026. On this data,
the spec's §11 conclusion stands: keep the gate and keep the cash.

**Forward run plan:** not written. The task asked for one only on a pass.

### Reproduce

```bash
cd experiments/leaders-pot
PY=/Users/akshay/Workarea/local/pullback/backend/.venv/bin/python
PYTHONDONTWRITEBYTECODE=1 $PY scripts/reproduce_baseline_original.py   # step 1 (original engine)
PYTHONDONTWRITEBYTECODE=1 $PY -m pytest tests -q -p no:cacheprovider     # mechanics (~30 s)
PYTHONDONTWRITEBYTECODE=1 $PY scripts/run_pot.py                         # all pot runs (~70 s)
PYTHONDONTWRITEBYTECODE=1 $PY scripts/diag_baseline_cap.py               # diagnostic
PYTHONDONTWRITEBYTECODE=1 $PY scripts/survivorship.py                    # fetches ^CRSLDX via yfinance
```

Inputs are read-only: `pullback/.../research2/gate_run/data_ext/*.csv`, plus the
original engine for step 1 only. `PYTHONDONTWRITEBYTECODE=1` keeps `__pycache__`
out of the pullback tree. Nothing here touches the app, its database or any
broker endpoint.
