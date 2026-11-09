# Hedged option selling — NIFTY index options

Research into defined-risk short premium on NSE index options: credit spreads,
iron condors and short strangles with bought wings, measured against real option
prices, real charges and a sourced margin model.

Self-contained under `experiments/`, imported by nothing, and the application's
database is opened read-only.

---

## Findings at a glance

Over **2016-01-01 → 2026-09-17**, 2,643 NIFTY sessions, real traded option
prices, real charges, and a sourced margin model.

### 1. The conventional structure loses money

A 16-delta iron condor with 5-delta wings at ~30 DTE, closed at 50% of maximum
profit:

| | hedged condor | unhedged strangle |
|---|---|---|
| trades | 205 | 220 |
| **net P&L** | **−₹95,632** | +₹134,865 |
| **return on capital at risk** | **−25.9% a year** | unbounded risk |
| charges | −₹37,461 (**64% of gross**) | −₹21,368 (14% of gross) |
| **worst single trade** | **−₹43,086** | **−₹135,030** |
| worst month | −₹62,801 (2020-03) | −₹170,167 (2020-03) |
| max loss ÷ margin | −667% | −286% |
| skew of trade P&L | −2.75 | −5.88 |
| win rate | 83.9% | 85.0% |

Risk-free over the same span: **+5.64% a year**. Block bootstrap over 129 whole
months: **P(total P&L < 0) = 75.0%**. The loss is the central case, not bad luck.

*A note on CAGR, because the dashboard shows one.* At one lot on ₹20 lakh the
book is ~97% idle, so a CAGR computed on the full capital measures how much cash
was left alone. Crediting the un-deployed balance at the repo rate gives **+5.43%
against the risk-free +5.64%**. **Return on capital at risk is the scale-free
figure** and is the one quoted above.

### 2. But the condor is a good trade stapled to a bad one

| structure | total P&L | **return on capital at risk /yr** |
|---|---|---|
| **bull put spread** | **+₹98,180** | **+26.0%** |
| iron condor | −₹95,632 | −25.9% |
| **bear call spread** | **−₹253,967** | **−88.1%** |

**This is the one large, stable, explicable result.** On an equity index the
volatility smile is skewed: the put sold sits on a steeper part of it than the
put bought, so a put spread is short the richer option and long the cheaper one
*in volatility terms* — and a call spread is the reverse. Measured on this data
(NIFTY 2025-08-01, 27 DTE): **16-delta put at 12.8% implied volatility against
the 16-delta call's 10.6%**.

The mechanism was stated before it was tested, and the prediction it makes — that
the *call* side should be badly negative — is confirmed. An iron condor averages
the two, which is where it lands. It was also the only result that survived a
significant bug fix unchanged (see §4).

**`BULL_PUT.md` then tries to break the positive half.** It survives both halves
of the sample (+21.7% and +29.6%), the removal of 2020, and **eight times** the
assumed slippage; 18 of 19 parameter variants are positive.

**But the current regime is negative.** Since SEBI's index-derivatives framework
took effect on 2024-11-20 the same rules return **−29.1% a year** on 48 trades,
against +43.3% before it. That window contains every structural change at once —
tripled lot sizes, one weekly expiry per exchange, +2% expiry-day ELM, and the
April 2026 STT rise. 48 trades is far too few to conclude from, and it is the
period that describes trading this today.

**Verdict: "index put skew was worth something after costs on this sample" — not
"here is a strategy", and specifically not one for the market as it is now. Do
not trade it.** See `FINAL_LOGIC.md`.

### 3. Three things that matter more than any of the above

1. **Charges are the strategy.** They consume **64% of the condor's gross P&L**.
   The seller's STT rose **~9×** inside the sample (0.017% → 0.15% of premium),
   twice structurally: October 2024 and April 2026.

2. **The win rate is worthless as evidence.** In the delta sweep the only
   positive cell has the *second-worst* win rate (72.6%), while the 89.7%-win
   cell loses money. Visible in one column of `SWEEP.md` §3.

3. **Margin is the wrong thing to size on, and the margin model is what shows
   it.** Margin runs at a **median of 26% of the structure's own maximum loss**,
   because the SPAN scan reaches only ±3σ (floored at 5%) while a 5-delta wing
   sits 8–12% away. The worst trade lost **667% of what it was blocking**. Size
   on maximum loss. `TAILS.md` §4.

### 4. The parameter rankings are not robust — demonstrated, not asserted

A bug fix that changed **no strategy rule** — it only stopped the engine skipping
sessions when the nearest expiry was illiquid (`EXPIRY_MIX.md`) — reversed most
of the sweep's "best cells":

| dimension | best before | best after |
|---|---|---|
| management | PT50 + stop 2× (+₹33,550) | that cell is now **−₹44,583** |
| short delta | 0.40d (+₹29,723) | that cell is now **−₹71,898** |

`SWEEP.md` §5 carries the retraction of a claim I made from one of those cells.
What survived: the structure table, and the fact that most cells lose.

### What the sample contained

| | |
|---|---|
| sessions | 2,642 |
| single-day falls over 3% | 20 |
| **single-day falls over 5%** | **7** |
| worst single session | **−12.98%** (2020-03-23) |
| worst 20-session stretch | **−37.01%** (to 2020-03-23) |

Seven 5% down days in a decade. Three trades out of 205 are the difference
between the condor losing ₹96k and making ₹23k.

---

## Read in this order

| | |
|---|---|
| [`FEASIBILITY.md`](FEASIBILITY.md) | The gate: does option history, a margin model and a charge card exist at all? All three, and the price history at the best tier — **real traded prices, no synthesis**. |
| [`RESEARCH.md`](RESEARCH.md) | The structures, the variance risk premium, strike and expiry selection, management rules, and the Indian specifics that are the subject rather than a footnote. |
| [`METHOD.md`](METHOD.md) | How it was measured and what was done to stop it lying. |
| [`DATA_QUIRKS.md`](DATA_QUIRKS.md) | Verified facts about the bhavcopy. **Two of these silently changed the answer**, and one produced a perfect equity curve. |
| [`CHARGES.md`](CHARGES.md) | The rate card, five STT regimes, and one divergence recorded rather than tuned. |
| [`MARGIN.md`](MARGIN.md) | The margin model, its calibration, and a sweep of everything in it that is uncertain. |
| [`SWEEP.md`](SWEEP.md) | Parameter trade-offs, one dimension at a time — including the put/call asymmetry. |
| [`BULL_PUT.md`](BULL_PUT.md) | The one positive structure, interrogated: is it real or a cell from a grid? |
| [`EXPIRY_MIX.md`](EXPIRY_MIX.md) | Weekly or monthly? Both — and finding that out exposed a bug that moved every number. |
| [`TAILS.md`](TAILS.md) | The left tail, tail dominance, and ruin-risk position sizing. |
| [`WHY_BIG_LOSSES.md`](WHY_BIG_LOSSES.md) | Why a −2% stop loses 40%: the loss zone is narrower than one day's move. |
| [`BACKSPREADS.md`](BACKSPREADS.md) | **Buy 2 wings per short.** Same return, 27% smaller worst trade, crashes turn profitable. |
| [`WEEKLY_TARGET.md`](WEEKLY_TARGET.md) | "+2% a week or −2% and out", tested. The stop never once delivered −2%. |
| [`CALENDARS.md`](CALENDARS.md) | Sell near, buy expiry+3 further OTM — the assessment before the engine existed. |
| [`DIAGONALS.md`](DIAGONALS.md) | The same structure **built and backtested**. The cushion is real; the slippage assumption decides it. |
| [`FINAL_LOGIC.md`](FINAL_LOGIC.md) | The decided rules, stated precisely enough to implement — and the verdict on whether to. |
| `out/option_selling_dashboard.html` | The dashboard. |

---

## The two bugs that produced a perfect strategy

Recorded here rather than buried, because both are the kind that survive into a
published result and both were caught by controls rather than by inspection.

**1. Expiry settled every leg at the same price.** On expiry day the bhavcopy's
`SttlmPric` is not the option's settlement price — it is the *underlying's*
settlement value, written identically into every row. A four-legged structure
marked at one price per leg cancels to exactly zero cost, so every trade booked
its full credit.

The backtest reported a **100% win rate** and a worst trade of **+₹499** —
through March 2026, a month in which NIFTY fell 11.3%. After the fix that trade
loses essentially its entire maximum loss (−₹51,824 against a maximum of
₹51,650). Verified across all 434 expiry sessions. See `DATA_QUIRKS.md` §1.

**2. Eight and a half years were silently skipped.** The legacy bhavcopy carries
no lot size, so `build()` refused every chain before 2024-07 — including COVID —
and reported 47 trades without mentioning it had discarded 80% of the sample.
The lot size is now *derived* from the exchange's own turnover, which for options
is notional on **strike plus premium** (median error 0.018%, exact on every index
contract checked). A date-keyed table would not have worked: NIFTY's lot changed
four times in the sample and a change applies only to newly listed expiries, so
two lot sizes are live at once. See `DATA_QUIRKS.md` §2.

Both were found because the negative controls and bounds checks existed, not
because anything looked wrong. The first result — smooth, 100% win rate, no
disaster — is exactly what the handoff predicted this kind of backtest produces
by default.

---

## Running it

```bash
cd experiments/hedged-option-selling
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt

.venv/bin/python scripts/run_all.py --fetch     # first time (~50 min of fetching)
.venv/bin/python scripts/run_all.py             # everything else
```

Individual steps:

```bash
.venv/bin/python scripts/validate_bhavcopy.py      # the legacy/UDiFF splice
.venv/bin/python scripts/validate_charges.py       # the charge card
.venv/bin/python scripts/run_negative_controls.py  # ground truth, before any result
.venv/bin/python scripts/run_backtest.py           # the real-data run
.venv/bin/python scripts/sweep.py                  # parameter trade-offs
.venv/bin/python scripts/tail_report.py            # the left tail and ruin risk
.venv/bin/python scripts/dashboard.py              # out/option_selling_dashboard.html
```

The data cache is ~2 GB of SQLite under `data/cache/` and is rebuilt from public
NSE archives; nothing in it is private and nothing is committed.

---

## This is research, and it stays outside the application

`CLAUDE.md` states, as a non-negotiable rule of this codebase:

> A "Sell" is a long put, never a short call, so the worst case stays the premium
> paid. **Do not add a code path that sells an option to open.**

**This research is about selling options to open.** That is fine here: this
directory is outside `backend/`, outside the safety scanners, and places nothing
— there is no broker surface for any of it to reach.

**It is not fine for any of it to leak into `backend/src/`, and it is not this
research's call to relax that rule.** Graduating any of this into the
application would be a deliberate decision by the operator, taken explicitly,
with the rule in `CLAUDE.md` rewritten in the same change.

On this sample there is nothing to graduate. Everything else the repository
forbids still applies and is untouched: no broker order endpoint, no `dhanhq`,
no `place_order`-family names. The backend safety suites pass unchanged.

---

## Known gaps

Kept honest and current, in the repo's style.

- **Daily data only.** 0DTE, intraday stop-losses and the whole "defend the
  tested side" school cannot be tested here. No claim is made about them.
- **The margin model is an approximation.** NSE's historical SPAN parameter files
  are not archived, so no backtest by anyone can use the exchange's real number
  for a past day. Whether ELM nets a long against a short at a different strike
  is not stated in the rules; it was calibrated against reported broker figures,
  it moves the answer by a factor of about 2.5, and it is the assumption here
  most likely to be wrong.
- **Bid-ask is modelled, not observed.** The bhavcopy has no quotes. The bought
  wing is two to three orders of magnitude thinner than the leg it protects
  (NIFTY 2025-08-01: near-money put 40,466 executed trades, the 5-delta wing
  **101**), so this is the assumption most able to flip the answer. Swept.
- **No executed-trade count before July 2024**, so the liquidity filter is weaker
  over the older two-thirds of the sample.
- **Single-stock options are excluded** — physically settled, so a short leg in
  the money at expiry is a delivery obligation rather than a cash difference.
- **BANKNIFTY is collected but not the primary.** SEBI left one weekly expiry per
  exchange from 2024-11-20 and NSE kept NIFTY's, so a continuous weekly study of
  anything else is impossible across that date.
- **The pre-October-2024 transaction charge is irreducibly uncertain** at the
  0.001-percentage-point level, because before SEBI's "True to Label" rule what a
  broker charged a client and what the exchange charged the broker were different
  objects by design.
- **Seven 5% down days** is a thin basis for any statement about the tail.
