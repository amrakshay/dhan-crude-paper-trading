# Handoff — finish the Swing Momentum automation (phases 6–10)

Read `CLAUDE.md`, `backend/CLAUDE.md` and `frontend/CLAUDE.md` first. They cover
the no-real-orders constraint, the one-connection invariant, the tick-path
performance contract, the strategy/capability/portfolio model, the module
layering, the theme and the UI honesty rules. `README.md` covers how to run it
and has a long **NSE Swing Momentum** section describing everything below.
Do not re-derive any of that.

**The strategy specification is**
`~/Workarea/local/pullback/backend/intrday_test_strategy/research2/SWING_MOMENTUM_HANDOFF.md`.
It is the source of truth for the rules, the parameters (P1–P19), the indicator
formulas, the cost model and the backtested results. Read it in full. **But see
"The specification's §13 is computed on flawed data" below before trusting its
numbers.**

The original implementation handoff is `SWING_MOMENTUM_IMPLEMENTATION_HANDOFF.md`
in this repository. Phases 1–5 of it are done. This document covers 6–10.

---

## Baseline

```bash
cd backend
.venv/bin/python -m pytest tests/ -q          # 725 passed, 1 skipped
CONFIG_PATH=conf .venv/bin/alembic heads      # c3e81d7a5f92 (head), 10 migrations
CONFIG_PATH=conf .venv/bin/python server.py   # :8000 — serves UI + API on one port
cd ../frontend && npm run build               # must pass
```

The 1 skipped test is `test_section_13_of_the_specification_is_reproduced`, which
is opt-in because it imports 1.1 M rows and adds ~4 minutes:

```bash
SWING_SECTION_13=1 .venv/bin/python -m pytest tests/test_swing_ranking.py -k section_13
```

Login: `trader@abc.com` / `APP_ADMIN_PASSWORD` from `.env`.

**Non-negotiable:** `backend/tests/test_no_real_orders.py` and
`backend/tests/test_no_secrets_in_logs.py` must keep passing.

**Two testing conventions that are new, and that you will trip over otherwise:**

- **Never run two `pytest` processes at once.** They used to share one temp
  SQLite file; that is fixed (per-process names), but the habit cost an hour on
  2026-09-18 when overlapping runs produced a fatal SQLAlchemy traceback that
  read exactly like a broken baseline.
- **`conftest` enables every strategy for every test** (`strategy_state_baseline`)
  and restores afterwards. `submit_paper_order` refuses an order for a
  switched-off strategy, so a test that trades must not depend on which module
  ships enabled. A test whose subject IS a shipped default reads
  `enabled_by_default` off the definition, not runtime state.

---

## What exists now

| | |
|---|---|
| `src/strategies/` | Strategies own a **universe** (a CSV in `conf/universes/`), declare `instrument_sets` with per-set segment / series / lot-size source, and carry uninterpreted blocks to their own module via `module_config` |
| `src/daily_bars/` | The `daily_bars` table, a bootstrap importer, and a nightly Dhan refresh. **Verified against the live token: 500 symbols, 0 failures, 23,000 bars, 747 s** |
| `src/swing/services/indicators.py` | SMA, Wilder ATR, ADV20, momentum, score — pure Python, pinned by a parity test against the backtest's pandas |
| `src/swing/services/swing_parameters.py` | P1–P19 read from the YAML. No defaults: a missing key names the file and the key |
| `src/swing/services/ranking_service.py` | Regime (P8/P9), breadth (P10), slots (P11), the full ranking, and a per-symbol skip reason |
| `src/swing/services/journal_service.py` | The decision journal — append-only, **enforced** (the repositories raise on `update`/`delete`) |
| `src/swing/services/swing_runner.py` | `run_nightly`: decides and records. **Places no orders.** Idempotent per session |
| `conf/charges/nse-equity-delivery.yaml` | STT on both legs, a flat DP charge, NSE's post-1-Mar-2026 transaction charge. The engine iterates card-declared **levies** |

**Current defaults: `nse-swing-momentum` ON, `mcx-crude-options` OFF**, and the
swing module is **not armed** (`automation.armed_by_default: false`).

---

## Decisions already made — do not re-litigate

Everything in `SWING_MOMENTUM_IMPLEMENTATION_HANDOFF.md`'s "Decisions already
made" table still stands. In addition, settled on 2026-09-18:

| # | Decision |
|---|---|
| 1 | **Enabled and armed are two switches.** Enabling the strategy makes it compute, decide and journal. Arming is what lets it submit an order. `automation.armed_by_default` is false and stays false; arming must be a deliberate act with its own UI control. |
| 2 | **Dhan's published rates** for the commercial (non-statutory) charges: ₹0 delivery brokerage, ₹12.50 + GST DP per sell. |
| 3 | **The decision journal is append-only and enforced**, not merely documented. Do not add an `update` path to it. |
| 4 | **Execution is its own run kind** (`REBALANCE`), separate from `NIGHTLY`, so that deciding and trading on the decision are separately recorded and separately gated. |

---

## Ground truth you must not rediscover the hard way

All verified on 2026-09-18 against the live instrument master and a live Dhan
token. Re-verify before relying on any of it, but do not re-derive it.

### Dhan's security ids are unique per SEGMENT, not globally

Id `13` is NIFTY in `IDX_I` and **ABB** in `NSE_EQ`; there are 44 such collisions
between the INDEX and EQUITY row sets, and ABB is a Nifty 500 constituent.
`instruments.security_id` carries a global UNIQUE constraint and `MarketBook`
keys its rows by id alone. Hence:

- the regime index is a **reference instrument**, never ingested into
  `instruments` — the charts client takes `(security_id, exchange_segment,
  instrument)` as arguments and needs no row;
- the instrument-master parser **refuses** an id seen in two segments;
- `daily_bars` is keyed on `(exchange_segment, symbol, bar_date)`, never the id.

### The specification's §13 is computed on flawed data

`research2/gate_run/data_ext/` carries a bar for **395 of 499 equities on
2026-09-14**, a Monday NSE was shut. Each is `open = high = low = close` =
the previous close, `volume = 0` — yfinance's flat-bar-on-a-holiday artefact
from the splice past 2026-07-14. Live Dhan has **zero** such bars in 1,108,462
rows.

Every lookback in this strategy is **positional** (`shift(5)`, `shift(126)`, the
ATR EWM, the ADV20 window count rows, not days), so one phantom row shifts all
of them by a session for those symbols while the NIFTY series stays aligned.

- Affected: §9.3's reproduction, the thirteen §11 gate experiments, §13's table.
- **Not** affected: the headline §9.1 result, which used the pure-Dhan panel.
- `is_phantom_bar` drops the shape on both ingest paths. The §13 reproduction
  test imports with the guard **disabled**, deliberately, to stay like-for-like.
- **Open question for Akshay, not for you:** whether §11's conclusions survive
  the correction. Do not silently assume they do.

### The universe has three known defects

- `JBCHEPHARM` has no NSE row in the master and stopped trading 2026-07-16. The
  universe is effectively 499.
- `HEG` (file 1336, master 7368) and `HFCL` (file 21951, master 21954) — the
  universe file's ids disagree with the master, and **HFCL is rank 2** in §13.
  Whether the research panel was fetched with the wrong ids is **unverified**.
- Both are reported as warnings on every master refresh, never dropped silently.

### 0.65 − 0.35 is not 0.30

The backtest's slot ramp divides by the literal `0.30`. The YAML configures the
breadth **span** for that reason; a derived span gives 2 slots where the engine
gives 3, at a breadth of 0.425.

### A symbol with no bar on the session is skipped, never forward-filled

The backtest's mechanic 1. Forward-filled, JBCHEPHARM ranked as a buy candidate
two months after it stopped trading, on a price that no longer existed.

---

## State of the live installation

**Neither of these is done, and both are prerequisites for anything in phase 6+
producing a real result.** They are substantial writes against Akshay's own
database; ask before running them.

1. **The instrument master holds crude's 1,240 contracts and no NSE equities.**
   A refresh ingests the 499 equity rows; crude is disabled so it contributes no
   filter and its existing rows are left active rather than deactivated.
2. **`daily_bars` is empty.** The bootstrap is
   `scripts/import_daily_bars.py --strategy nse-swing-momentum --directory <the
   10-year panel>` (~197 s, 1,088,241 rows), then a live refresh to close the
   gap from 2026-07-14 (~13 minutes, measured).

Everything verified so far ran on an isolated instance, not on `data/paper_trading.db`.

---

## Phase 6 — execution

The rebalance: sell list, then buy list, through `submit_paper_order`.

- **Sells fill before buys.** The backtest processes pending exits before
  pending entries and sizing is cash-constrained. Get this wrong and the funds
  check rejects buys the backtest funded from that morning's sales — and it will
  look like a bug in the funds code.
- **Sizing is `equity / position_size_divisor`, whole shares, floored**, and
  `position_size_divisor` (10) is deliberately separate from `max_positions`, so
  a narrow market holds cash rather than concentrating.
- **Equity can be unknown.** `BalanceService` withholds equity when any position
  has no mark. The pipeline must **refuse to size and say why** rather than
  guessing. This is the honesty rule applied to an automated decision.
- **Every order carries a system-written reason**, and the journal already has
  `SwingDecision.order_id` to tie a decision to the order it produced. Decide
  whether `orders` needs its own `strategy_reason` column or whether
  `order_events.message` plus the journal is enough — the original handoff asks
  for this explicitly.
- **Arming gates order submission, not decision-making.** An unarmed run must
  produce the identical journal entry with the orders not placed.
- **The fill simulator needs a depth book for names it is about to buy.** The
  feed's `positions` subscription kind resolves to what is held; `pin_instruments()`
  on `FeedManager` exists for exactly this — pin the buy candidates a few minutes
  before the rebalance, `resync()`, place, then unpin. Roughly 30 instruments,
  not 500.
- Do not give this strategy a privileged fill path. `src/chart_trading/` is the
  precedent: it goes through `submit_paper_order` like everything else.

## Phase 7 — the chandelier trailing stop

- Set at entry (P14: `entry − 3.5 × ATR14`), ratcheted up on each daily close
  (P15: `max(stop, highest_close_since_entry − 3.5 × ATR14_today)`), **never
  down**, triggered intraday.
- Do not bend `bracket_monitor` into two shapes — it watches a FUTURE and closes
  an OPTION from `chart_trades` rows. Write a sibling that follows the same
  rules: own task, own session, off the tick path, never acts on a `None` price,
  exits through `submit_paper_order`.
- Register it in `TASK_DESCRIPTIONS` **and** `expected_task_names`, with a
  `status()` dict the health page reads, or the page reports it as unexpected
  for ever.
- **Do not tighten the stop.** §10 of the specification measures every tighter
  variant as worse.
- **The Closing Auction Session** (live 3 Aug 2026): continuous trading for
  F&O-eligible names ends at 15:15. A stop triggered between 15:15 and 15:30 on
  those names cannot fill in continuous trading. The F&O set is derivable from
  the instrument master — 228 distinct NSE `FUTSTK` underlyings as of
  2026-09-18 — so this needs no new data source. Model it or record that you
  have not; do not let it silently flatter the fills.

## Phase 8 — the scheduler

Two IST-aware jobs, both restart-safe: nightly (post-18:00, config-driven) and
rebalance (at the open). `schedule.nightly_at` / `rebalance_at` are already in
the strategy YAML.

- The journal is already built for this: `sessions_completed_on` gives
  idempotence, `decided_session_dates` gives **missed-run detection**. A missed
  run must be *detected and reported*, not silently skipped — if the process was
  down at 18:00, the next start must notice and say so rather than carrying
  yesterday's stops forward as if nothing happened.
- The trading calendar is the regime index's own bar dates. There is no holiday
  list and there should not be one.
- **Carried here from phase 4:** the feed's inactivity watchdog false-positives
  on a **subscribed but idle** market. It reconnects after 40 s without a data
  frame, and Dhan's protocol pings never reach the message loop. MCX closes at
  23:30 and NSE at 15:30, so a book held across the close reconnects every 45
  seconds until the next open — burning one of Dhan's five connection slots and
  drowning the dead-feed signal in noise. The zero-subscription case is already
  fixed. This one needs the watchdog to consult each strategy's `market_hours`,
  which is the awareness the scheduler introduces, which is why it lands here.
  See `dhan_feed_client._watchdog`.

## Phase 9 — the UI

A strategy page showing the current decision, the ranking, the open book with
each position's stop and its distance to it, and the decision history.

- `frontend/CLAUDE.md` §1 (never hardcode a colour) and §3 (the honesty rules)
  apply. Check light **and** dark.
- Everything the page needs is already stored: `swing_sessions.ranking_json`,
  `filter_counts_json`, `parameters_json`, and `swing_decisions` per session and
  per symbol.
- **Surfaces that must be honest:** a withheld equity figure, a breadth that
  could not be measured (`None`, not 0), the arming state, and the fact that the
  strategy has no live track record.
- New endpoints need **no-secrets assertions in the same change**
  (`tests/test_no_secrets_in_logs.py`).

## Phase 10 — performance metrics

CAGR, max drawdown, MAR, win rate, profit factor, average hold, exit mix (trail
vs rotation), and **return concentration** — which the specification calls the
single most important statistic on its page (top 10 of 672 trades = 55% of the
summed return).

- From the portfolio's own realised history, **not** from the backtest.
- `src/reports/` already replays fills for realised P&L and the equity curve;
  extend rather than duplicate. `pnl_service` and the live position book must
  continue to agree — there is a test pinning that.

---

## Definition of done

- Full suite green, including `test_no_real_orders.py` and
  `test_no_secrets_in_logs.py`, with new no-secrets assertions covering every
  new endpoint.
- New tests asserting the negatives, in this repo's style:
  - with the gate OFF, a rebalance **buys nothing** and records why with the
    NIFTY and SMA values (the nightly case is already covered);
  - with the gate ON and breadth below 35%, slots resolve to 0 and nothing is
    bought;
  - a rotation exit fires on rank > 15 and **the order carries that reason**;
  - a trailing stop **never ratchets down**;
  - a buy is skipped, with a recorded reason, when cash is short — not silently
    resized;
  - equity being unknown **blocks sizing** rather than guessing;
  - an **unarmed** run journals the identical decision and places no order;
  - V3b enabled changes slot allocation with the gate off, and disabled (the
    default) does not.
- A test that the MCX crude module is unaffected: charges, fills and P&L
  byte-identical, and the ₹0.01 Zerodha divergence still exactly ₹0.01.
- Migrations apply **and** roll back on SQLite; DDL-compile-verified for MySQL
  (never executed against a live server here — say so rather than implying
  otherwise). `--autogenerate` produces an empty `upgrade()` afterwards.
- `npm run build` passes.
- **Verified by actually running it** against the live Dhan token: a full
  nightly and a full rebalance, with the decision record for both shown. A
  server may be running on `:8000` with the real `.env` — start an isolated
  instance on a spare port with its own `CONFIG_PATH`, `DATABASE_URL` and
  `LOG_DIR`, and clean it up afterwards. Check light and dark.
- `README.md` and the relevant `CLAUDE.md` files updated, and everything you
  could **not** verify in the README's "Known gaps".
- Committed with `amrakshay@gmail.com` and pushed to `origin/main` over SSH. No
  JIRA prefix, no GitLab MR, no ADR — personal repo.

---

## Traps

- **Rule #1.** No broker order path, not behind a flag, not as a stub. Local
  operations are `submit_paper_order` / `cancel_paper_order`. If the safety test
  fails on a name you wrote, rename yours.
- **One upstream connection.** Do not open a second feed client, and do not
  subscribe all 500 symbols — the rotation decides from daily bars over REST.
- **The tick path stays cheap.** No strategy lookup, no stop evaluation, no
  database write inside `apply_packet`. The stop monitor polls the book on its
  own cadence, exactly as `bracket_monitor` does.
- **Money is `Decimal`; indicators are float.** Convert at the boundary,
  deliberately. Do not "improve" the indicators to `Decimal` — the parity test
  compares them against numbers pandas produced.
- **Undefined is not zero**, anywhere: a `None` breadth, a `None` equity, a
  `None` slot count all mean "could not measure" and must not be rendered or
  treated as 0.
- **Do not tune constants to make results match the backtest.** It is
  survivorship-biased and in-sample; the specification itself expects 12–18%
  live against a 19.9% headline. If the live numbers diverge, report the gap.
- **Rate limits.** 0.6 s per symbol; a 500-symbol pull is ~12 minutes measured.
  Do not parallelise it into a ban.
- **Migrations** (`backend/CLAUDE.md` §3): `VARCHAR` needs a length, no server
  defaults, must apply *and* roll back.

---

## Reference

| Thing | Where |
|---|---|
| Strategy spec | `~/Workarea/local/pullback/backend/intrday_test_strategy/research2/SWING_MOMENTUM_HANDOFF.md` |
| Phases 1–5 handoff | `SWING_MOMENTUM_IMPLEMENTATION_HANDOFF.md` (this repo) |
| Backtest engine | `research2/swing_backtest_v2.py::run_variant`, config `C4 graded+r15` |
| Indicator formulas | spec §4, and `research2/swing_backtest.py` |
| Live runner (daily cadence) | `research2/live/swing_live.py` |
| 10-year daily panel (pure Dhan, clean) | `research2/data_daily_10y/` — to 2026-07-14 |
| Extended panel (**has the phantom session**) | `research2/gate_run/data_ext/` — to 2026-09-17, fixture only |
| Golden trade ledger | `research2/out/swingv2_C4_trades_full.csv` — 672 trades |
| Universe | `backend/conf/universes/nifty500.csv`, and its `README.md` for the caveats |
