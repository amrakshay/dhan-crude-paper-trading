# Bear-market leaders pot — research backtest

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

## Results

(not yet run)
