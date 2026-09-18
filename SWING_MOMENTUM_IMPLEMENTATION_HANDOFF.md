# Handoff — automate Swing Momentum end to end in this platform

Read `CLAUDE.md`, `backend/CLAUDE.md` and `frontend/CLAUDE.md` first. They cover
the no-real-orders constraint, the one-connection invariant, the tick-path
performance contract, the strategy/capability/portfolio model, the module
layering, the theme and the UI honesty rules. `README.md` covers how to run it.
Do not re-derive any of that.

**The strategy specification is `~/Workarea/local/pullback/backend/intrday_test_strategy/research2/SWING_MOMENTUM_HANDOFF.md`.**
It is the single source of truth for the rules, the parameters (P1–P19), the
indicator formulas, the cost model and the backtested results. Read it in full
before writing anything. This document does not restate the strategy; it says
how to build it *here*.

**Baseline before you start:**

```bash
cd backend
.venv/bin/python -m pytest tests/ -q          # 591 passing
CONFIG_PATH=conf .venv/bin/alembic heads      # 2966a4a8ed93 (head), 8 migrations
CONFIG_PATH=conf .venv/bin/python server.py   # :8000 — serves UI + API on one port
cd ../frontend && npm run build               # must pass
```

Login: `trader@abc.com` / `APP_ADMIN_PASSWORD` from `.env`.

**Non-negotiable:** `backend/tests/test_no_real_orders.py` and
`backend/tests/test_no_secrets_in_logs.py` must keep passing.

> **This is several sessions.** See **Phasing**. Each phase leaves the suite
> green and the existing MCX crude module working exactly as it does today.

---

## The goal

Turn the researched Swing Momentum rotation into a **second strategy module**
that runs itself: selects stocks, sizes positions, places and exits trades,
maintains trailing stops, and records the reason for every decision — all
against paper money in its own portfolio, so its performance can be measured
separately from anything else in the platform.

The platform already has the framework for this (strategy modules, capabilities,
portfolios, a cash ledger, funds enforcement). What it does not have is
**automation**: everything today is triggered by a human clicking. This strategy
must run on a schedule and decide on its own.

Five things the finished system must do:

1. **Decide on a schedule, unattended.** A nightly job after the close and a
   rebalance job at the open, both surviving restarts and both skipping
   correctly when the market is shut.
2. **Record every decision, with its inputs.** Not just the trades — the regime
   gate state, the breadth figure, the slot count, the full ranking, why a
   candidate was skipped, why a position was sold. A human must be able to open
   any past session and see exactly what the system saw and why it acted.
3. **Enter and exit automatically, with a system-written reason on every
   order.** "Rotation: rank 19 > 15", "Trail stop hit at 2,431.50", "Regime
   exit: NIFTY below 200-SMA".
4. **Cost it properly.** NSE delivery charges, which this platform cannot
   currently compute (see **Blocker 3**).
5. **Report on itself.** Positions and history at any time, plus the metrics the
   backtest reports — CAGR, max drawdown, MAR, win rate, profit factor, average
   hold, exit mix.

---

## Decisions already made — do not re-litigate these

Akshay settled these on 2026-09-18. They are answers, not suggestions.

| # | Decision |
|---|---|
| 1 | **Paper money only, against live prices.** The system decides and trades automatically in real time, and every order is a row in this database. Nothing reaches a broker, ever. Root `CLAUDE.md` §1 is absolute and applies unchanged: no order-placement path, not behind a flag, not as a stub, not "for later". |
| 2 | **The regime gate is obeyed by default, and V3b is a switch.** With the gate OFF the system holds 100% cash — and says so, every session, with the numbers. Additionally implement the **V3b off-gate variant** (3 slots, no 63-day filter) as a configuration option, **defaulting to off**. Turning it on is an informed choice against the owner's own finding that V3b ends *lower* than baseline because capital is committed when the regime flips. The UI must say that where the switch lives. |
| 3 | **Fills use this platform's own simulator**, not the backtest's model. A market order at the open crosses the spread, walks the live 5-level book and partially fills when depth runs out. Fills will differ from the 19.9% backtest — that difference is the point, and §14.3 of the spec asks for exactly this measurement. Do not add a flattering execution path to make the numbers agree. |
| 4 | **Bootstrap bar history from the existing 10-year panel, then append nightly.** Import `research2/data_daily_10y/` (502 files, 49 MB, 2015-07-01 → 2026-07-14) once, then top up from Dhan each night. The panel ends 2026-07-14, so the first run backfills the gap. |
| 5 | **Indicators are pure Python, with a parity test.** Do not add pandas or numpy to this backend — it deliberately carries no heavyweight data dependency, and there is no latency pressure here (the spec measures the whole computation at 1.44 s in pandas; seconds are fine). The guarantee is a **parity test against golden values taken from the backtest**, which is what actually protects the numbers whichever library computes them. |
| 6 | **The strategy gets its own portfolio, opening balance ₹10,00,000**, matching the backtest's starting capital so the equity curve is comparable. |
| 7 | **The universe is a config file, not a scrape.** Copy `universe_nifty500.csv` into `backend/conf/universes/`. No new outbound URL, no new host in any allowlist. Document the quarterly manual refresh from NSE's archive URL in the README. |
| 8 | **Rebalance cadence is configuration, defaulting to daily** — `swing_live.py` is daily and the owner chose that deliberately (§9.2: 23.4% CAGR / −22.2% DD vs weekly 19.9% / −18.3%). Weekly must work by changing one config value and nothing else. |

---

## What already exists — reuse it, do not rebuild it

This platform was generalised for exactly this on 2026-09-18. Trust this list,
but verify anything you are about to change.

| Area | What you get |
|---|---|
| `src/strategies/` | Strategy modules as YAML, a registry, capabilities, enable/disable applied live, and the Strategies & Features page that shows what each module is costing |
| `src/portfolios/` | Portfolios, an **append-only** `cash_ledger`, and `BalanceService` — the one place that computes cash, blocked margin, available and equity |
| `src/orders/` | `submit_paper_order`, the pessimistic fill simulator, the background matcher for resting orders, and `order_events` — **every state transition is already a timestamped row with a message** |
| `src/positions/` | Weighted-average position book, realised P&L, per-portfolio lookups |
| `src/charges/` | Rate cards per strategy, and a breakdown that persists as **named line items** rather than fixed columns |
| `src/market/` | One upstream connection, subscribe by `(exchange_segment, security_id)`, `max_instruments_per_connection: 5000` |
| `dhan_charts_client.fetch_daily()` | Already takes `exchange_segment` and `instrument`. **`NSE_EQ` + `EQUITY` needs no change to `ALLOWED_DHAN_URLS`** — verify this yourself and keep it that way |
| `src/reports/` | Realised P&L by replay, equity curve, CSV export, all portfolio-scoped |
| `src/health/` | Task inspector that judges every named background task against what *should* be running |
| Funds enforcement | Checked at placement **and again at the fill**, with rejection rather than a partial fill |

**Two existing precedents to follow rather than invent:**

- **`order_matcher` and `bracket_monitor`** are the shape a background task takes
  here: its own asyncio task, its own database session, off the tick path,
  registered in `TASK_DESCRIPTIONS` *and* `expected_task_names`, with a
  `status()` dict the health page reads.
- **`src/chart_trading/`** is the precedent for "a strategy pattern built on top
  of the generic machinery" — it translates a gesture into orders through
  `submit_paper_order` like everything else, and never gets a privileged fill
  path. This strategy is the same idea with a scheduler instead of a click.

---

## Blockers — the things that genuinely do not work yet

These were verified against the live instrument master and the current code on
2026-09-18. They are the real work.

### Blocker 1 — a strategy owns ONE underlying

`StrategyDefinition.owns_instrument(exchange_segment, underlying_symbol)`
matches a single symbol, and `InstrumentMasterService.parse()` builds a
`{(exchange_id, underlying_symbol): strategy}` claims dict from it. This
strategy owns **500 symbols** out of the **9,884 NSE EQUITY rows** in the
master.

The framework anticipated this ("eventually cash-segment instruments with no
expiry and no strike") but nothing implements it. A strategy needs to declare a
**universe** — a named list of symbols — and ownership must resolve against it.
Keep the single-underlying form working: the crude module must not need
rewriting.

### Blocker 2 — the instrument master overrides equity lot sizes

`parse()` treats `LOT_SIZE <= 1` as Dhan's known MCX defect and substitutes
`contract_specs.<symbol>.lot_size`, raising `InstrumentMasterError` when there
is none. For NSE equity **`LOT_SIZE = 1.0` is correct** — delivery trades one
share — and requiring a `contract_specs` entry would mean 500 of them.

A strategy must be able to say "the master's lot size is trustworthy for me".
Verified row shape:

```
EXCH_ID=NSE  SEGMENT=E  INSTRUMENT=EQUITY  UNDERLYING_SYMBOL=BHEL
SYMBOL_NAME=BHEL  SECURITY_ID=438  LOT_SIZE=1.0  TICK_SIZE=5.0000
```

**Correction to an existing comment:** `conf/strategies/mcx-crude-options.yaml`
claims an NSE strategy "will *not* need the divisor". That is wrong. NSE
publishes tick size in paise too — the distinct values across NSE equities are
`1.0, 5.0, 10.0, 50.0, 100.0`, i.e. ₹0.01 to ₹1.00 — so `tick_size_divisor: 100`
applies. Fix that comment when you touch the file.

### Blocker 3 — the charges engine computes a fixed set of six

`ChargesEngine._compute()` calls exactly `compute_brokerage`, `compute_ctt`,
`compute_exchange_transaction_charge`, `compute_sebi_turnover_fee`,
`compute_stamp_duty` and `compute_gst`. The *persistence* is generic — components
are named line items in `breakdown_json` — but the *computation* is not.

NSE delivery needs two charges that do not exist:

- **STT at 0.10% on BOTH legs** (commodity CTT is sell-side only, so `compute_ctt`
  is not it under another name)
- **A flat ₹16 DP charge per sell** — the engine has no concept of a per-leg flat
  fee that is not brokerage

The right fix is to make the card declare its levies and the engine iterate them,
rather than adding two more hardcoded methods. Whatever you choose:
**the MCX crude numbers must not move by a paisa.** `tests/test_charges_rate_cards.py`
pins them and `tests/test_charges_worked_examples.py` checks them against a
published broker calculator, including the documented ₹0.01 round-trip
divergence. If a crude number changes, you broke something.

The exact rates are in §5 of the strategy spec. Every one needs a primary source
URL, an as-of date and a confidence marker in the card, like every other rate
here. **Do not copy the backtest's constants without sourcing them** — the
backtest's own comment says brokerage is assumed ₹0 for a discount broker, which
is a commercial assumption and not a statutory rate.

### Blocker 4 — nothing stores daily bars

`candle_service` caches candles in memory for 15 s (intraday) or 5 min (daily)
and nothing is persisted. This strategy needs 260+ sessions for ~500 symbols
available instantly at 09:15, and re-fetching that is a ~5-minute job at Dhan's
rate limits.

Root `CLAUDE.md` says *"Price history is fetched, never accumulated"*. **Read
that rule carefully before deciding you are breaking it.** It forbids
accumulating **ticks** to back a chart — writing on the tick path, which would
violate the performance contract. A nightly table of daily OHLCV fetched from
`/charts/historical` is a different thing entirely: no tick ever touches it, and
it is written once per symbol per day by a background job.

Build it, and **add a paragraph to root `CLAUDE.md` §4 saying exactly why this is
not the thing that rule prohibits**, so the next reader does not have to
re-litigate it.

### Blocker 5 — there is no scheduler

Nothing in this platform runs on a clock. You need two jobs, both IST-aware,
both restart-safe:

- **Nightly** (after the close, config-driven, spec suggests post-18:00 IST):
  refresh the universe's daily bars, recompute indicators, recompute the regime
  and breadth, update trailing stops, write the session's decision record.
- **Rebalance** (at the open): emit and execute the sell list, then the buy list.

Restart-safe means **a missed run is detected and reported, not silently
skipped** — if the process was down at 18:00, the next start must notice and say
so rather than carrying yesterday's stops forward as if nothing happened.

### Blocker 6 — trailing stops are chart-trade shaped

`bracket_monitor` watches a **future** and closes an **option**, driven by rows
in `chart_trades`. This strategy needs a chandelier stop on the stock itself:
set at entry (P14), ratcheting up on each daily close (P15), never down, and
triggered intraday.

Do not bend `bracket_monitor` into both shapes. Either generalise it deliberately
or write a sibling that follows the same rules (own task, own session, never acts
on a `None` price, exits through `submit_paper_order` like everything else).

### Blocker 7 — no strategy-level performance metrics

`src/reports/` gives realised P&L, slices and an equity curve. The backtest
reports CAGR, max drawdown, MAR, win rate, profit factor, average hold and the
exit mix (trail vs rotation), plus return concentration — which the spec calls
"the single most important statistic on this page". None of that exists.

---

## The decision journal — the heart of this

Akshay's requirement: *"every decision taken by system should be recorded"*.
Order events already record what happened to an order. This is about what the
**strategy** decided, and why, including the decisions that produced no trade.

Design it so that any past session can be reconstructed:

- **One record per session run**, carrying the inputs the decision was made from:
  NIFTY close and its 200-SMA, the 63-day return, the breadth fraction and its
  numerator/denominator, the slot count, how many names passed each filter
  (P2/P3/P4/P6), and which config was in force.
- **The ranking as it stood**, at least the top N and every held name's rank —
  because a rotation exit is justified by a rank the operator must be able to see.
- **One record per action and per non-action**: bought, sold, skipped and why.
  "Skipped: already held", "Skipped: insufficient cash", "Skipped: slots full",
  "Not entered: 63-day filter blocks new entries".
- **Every order gets a system-written reason**, stored on the order and visible
  in Order History. The existing `order_events.message` is the natural place;
  check whether a strategy-level reason needs its own column rather than being
  buried in a transition message.

Two rules, both borrowed from what this codebase already does well:

- **Append-only.** A decision record is never edited, the same as `order_events`
  and `cash_ledger`. If the system reconsiders, that is a new record.
- **Store the inputs, not just the conclusion.** "Gate OFF" is useless in six
  months. "NIFTY 23,270.6 vs SMA200 24,501.7 → OFF, needs +5.3%" is auditable.

---

## What to configure, and where

Akshay asked for the levers to be configuration. Split them the way the platform
already splits things — generic behaviour in `default-config.yaml`, everything
strategy-specific in the strategy's own YAML.

Belonging in `conf/strategies/nse-swing-momentum.yaml` (name it what you like,
but the key must be stable — it is stored on every order):

- The universe name, and NSE market hours (09:15–15:30)
- Every parameter P1–P19 from the spec, each named so it is greppable against the
  spec table: liquidity floor, price floor, momentum lookback and skip, momentum
  floor, ATR multiple, rank-exit threshold, max positions, breadth bounds
- The breadth→slots mapping (35%→0, 65%→10) as numbers, not code
- The regime gates: which index, which SMA, the 63-day entry filter
- Rebalance cadence (`daily` | `weekly`) and the schedule times
- **The V3b off-gate block, defaulting to disabled**, with its own slot count and
  momentum floor, and a comment recording that the owner's research found it ends
  lower than baseline
- The charge rate card name and the margin model

A hard rule from this codebase: **no number that affects a trade may be
hardcoded in Python.** If the spec has a number, the YAML has that number, and
the code reads it.

---

## Traps

- **Rule #1.** No broker order path. The banned names include `place_order`,
  `cancel_order`, `get_fund_limits`, `convert_position`. Local operations are
  `submit_paper_order` / `cancel_paper_order`. If the safety test fails on a name
  you wrote, rename yours.
- **One upstream connection.** Do not open a second feed client. And do **not**
  subscribe all 500 symbols: the rotation runs on daily bars from REST, so the
  live feed is only needed for **held positions (≤10) plus the NIFTY index** —
  about 11 instruments. Subscribing 500 would burn the connection's budget for
  nothing.
- **The tick path stays cheap.** No strategy lookup, no stop evaluation, no
  database write inside `apply_packet`. The stop monitor polls the book on its
  own cadence, exactly as `bracket_monitor` does.
- **Sells fill before buys.** The backtest processes pending exits before pending
  entries (§6 mechanics, item 4) and sizing is cash-constrained. Get this wrong
  and the funds check will reject buys that the backtest funded from that
  morning's sales — a discrepancy that will look like a bug in the funds code.
- **Equity can be unknown.** `BalanceService` withholds equity when any position
  has no mark. P13 sizes from total equity, so the pipeline must **refuse to size
  and say why** rather than falling back to a guess. This is the honesty rule
  (`frontend/CLAUDE.md` §3) applied to an automated decision.
- **Money is `Decimal`.** The backtest is float throughout. Convert at the
  boundary, deliberately, and keep indicator maths and money maths apart —
  indicators are ratios and can be float; every rupee is `Decimal`.
- **ATR is Wilder-smoothed** (`ewm(alpha=1/14, adjust=False)`), not a rolling
  mean. The spec warns that small differences here move the stop and therefore
  every result. This is the first thing your parity test must pin.
- **The charts client has never been run against a live Dhan token.** Its
  docstring says so plainly — it was written from documentation when this project
  had no credentials. Akshay's token arrives today, so **this strategy is the
  first thing ever to exercise it.** Expect the response schema to differ from the
  docs and budget for it. Verify against one symbol before pulling 500.
- **Rate limits.** `REQUEST_DELAY = 0.6 s/symbol` in the research script; a
  500-symbol pull is ~5 minutes. Do not parallelise it into a ban.
- **Migrations** (`backend/CLAUDE.md` §3): `VARCHAR` needs a length, no server
  defaults, must apply *and* roll back, and `--autogenerate` must produce an empty
  `upgrade()` afterwards.
- **The Closing Auction Session** (spec §14.5, live since 3 Aug 2026): continuous
  trading for the 210 F&O names ends at 15:15. A stop triggered between 15:15 and
  15:30 on those names cannot fill in continuous trading. The backtest predates
  this entirely. Model it or record that you have not — do not let it silently
  flatter the fills.
- **Do not tune constants to make results match the backtest.** The backtest is
  survivorship-biased and in-sample; the spec itself expects 12–18% live against a
  19.9% headline. If the live numbers diverge, report the gap.

---

## Phasing

Each phase leaves the suite green and the MCX crude module working exactly as
before.

1. **Universe-owning strategies.** Blockers 1 and 2: a strategy declares a
   universe; ownership, the instrument-master filter and lot-size handling resolve
   against it. Ship the NSE equity strategy YAML and the universe file. Nothing
   trades yet. This is the phase that proves the seam the framework was built for.
2. **Equity charges.** Blocker 3: card-declared levies, an `nse-equity-delivery`
   rate card with sourced rates, STT on both legs and the flat DP charge. Crude's
   numbers unchanged, pinned by the existing tests.
3. **Daily bars.** Blocker 4: the table, the bootstrap importer for the 10-year
   panel, the nightly refresh, and the `CLAUDE.md` paragraph explaining why this is
   not tick accumulation.
4. **Indicators + parity.** SMA200, ATR14 (Wilder), ADV20, mom126, score, breadth,
   regime — in pure Python, with the parity test against golden values from the
   backtest. No trading yet: a read-only endpoint that reproduces §13 of the spec
   (gate state, breadth, slots, top-15 ranking) is the deliverable, and it should
   match the spec's snapshot table when run on the same data.
5. **The decision journal**, and the session-run record. Still no trading: the
   nightly job runs, decides, and records that it decided to do nothing.
6. **Execution.** The rebalance job: sell list then buy list through
   `submit_paper_order`, sized from portfolio equity, with reasons on every order.
7. **Trailing stops.** Blocker 6, and the CAS caveat.
8. **The scheduler** proper: both jobs, restart-safe, missed-run detection, health
   page integration.
9. **The UI.** A strategy page showing the current decision, the ranking, the open
   book with each position's stop and distance to it, and the decision history.
10. **Performance metrics.** Blocker 7: CAGR, max DD, MAR, win rate, profit
    factor, average hold, exit mix, return concentration — from the portfolio's own
    realised history, not from the backtest.

Phases 1–4 are independently shippable and useful on their own: at the end of
phase 4 the platform can tell you, live, what the strategy *would* do.

---

## Definition of done

- Full suite green, including `test_no_real_orders.py` and
  `test_no_secrets_in_logs.py`, with **new no-secrets assertions covering every
  new endpoint**.
- **A parity test** that reproduces the backtest's indicator values and at least
  one full session's ranking from a fixed data fixture, to the precision the spec
  demands.
- **A test that the MCX crude module is unaffected**: charges, fills and P&L
  byte-identical, and the existing ₹0.01 Zerodha divergence still exactly ₹0.01.
- New tests asserting the negatives, in this repo's style:
  - with the gate OFF, a nightly run **buys nothing** and records *why*, with the
    NIFTY and SMA values that produced the decision;
  - with the gate ON and breadth below 35%, slots resolve to 0 and nothing is
    bought;
  - a rotation exit fires on rank > 15 and the order carries that reason;
  - a trailing stop **never ratchets down**;
  - a buy is skipped, with a recorded reason, when cash is short — not silently
    resized;
  - equity being unknown **blocks sizing** rather than guessing;
  - V3b enabled changes slot allocation with the gate off, and disabled (the
    default) does not.
- Migrations apply **and** roll back on SQLite; DDL-compile-verified for MySQL
  (never executed against a live server here — say so rather than implying
  otherwise). `--autogenerate` produces an empty `upgrade()` afterwards.
- `npm run build` passes.
- **Verified by actually running it** against the live Dhan token: pull real daily
  bars for the universe, reproduce §13's gate/breadth/slots/top-15 against the
  spec's snapshot, run a full nightly and a full rebalance, and show the decision
  record for both. A server may already be running on `:8000` with Akshay's real
  `.env` — start an isolated instance on a spare port with its own `CONFIG_PATH`,
  `DATABASE_URL` and `LOG_DIR`, and clean it up afterwards. Check light and dark.
- `README.md` and the relevant `CLAUDE.md` files updated — the automation model,
  the decision journal, the daily-bar exception to the fetch-never-accumulate
  rule, the universe refresh procedure — and everything you could **not** verify
  in the README's "Known gaps", including that this strategy has no live track
  record and that its universe is survivorship-biased.
- Committed with the local personal email (`amrakshay@gmail.com`) and pushed to
  `origin/main` over SSH. No JIRA prefix, no GitLab MR, no ADR — personal repo.

---

## Reference

| Thing | Where |
|---|---|
| Strategy spec | `~/Workarea/local/pullback/backend/intrday_test_strategy/research2/SWING_MOMENTUM_HANDOFF.md` |
| Backtest engine (the production one) | `research2/swing_backtest_v2.py::run_variant`, config `C4 graded+r15` |
| Indicator formulas | spec §4, and `research2/swing_backtest.py` |
| Cost model | spec §5 |
| Live runner (daily cadence) | `research2/live/swing_live.py` |
| 10-year daily panel | `research2/data_daily_10y/` — 502 files, 49 MB, to 2026-07-14 |
| Universe | `backend/intrday_test_strategy/data/universe_nifty500.csv` — 500 rows: symbol, security_id, name, exchange_segment |
| Golden trade ledger | `research2/out/swingv2_C4_trades_full.csv` — 672 trades |
| Golden equity curve | `research2/out/swingv2_C4_equity.csv` |
| Expected §13 snapshot | spec §13 — gate OFF, breadth 48.0%, 4 slots, top-15 table, as of 2026-09-17 |
