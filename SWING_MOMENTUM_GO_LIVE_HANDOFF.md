# Handoff — make the Swing Momentum rotation trade live

Read `CLAUDE.md`, `backend/CLAUDE.md` and `frontend/CLAUDE.md` first. They cover
the no-real-orders constraint, the one-connection invariant, the tick-path
performance contract, the strategy/capability/portfolio model, the two switches
(enabled and armed), the module layering, the theme and the UI honesty rules.
`README.md`'s **NSE Swing Momentum** section covers everything the strategy
already does, and `backend/src/swing/README.md` covers the package. Do not
re-derive any of that.

**The strategy specification is**
`~/Workarea/local/pullback/backend/intrday_test_strategy/research2/SWING_MOMENTUM_HANDOFF.md`
— the rules, the parameters P1–P19, the indicator formulas, the cost model and
the backtested results.

Phases 1–10 are **done, verified against the live Dhan token, and pushed**
(`9479b4e`, `1945bdb`). This document is the next piece of work: making it
actually place orders, on Akshay's explicit terms.

---

## Baseline — do NOT spend time re-running these

```bash
cd backend
.venv/bin/python -m pytest tests/ -q          # 800 passed, 1 skipped, ~5m30s
CONFIG_PATH=conf .venv/bin/alembic heads      # d4a17e6b2c88 (head), 11 migrations
cd ../frontend && npm run build               # passes, ~2s
```

Measured on 2026-09-18, on the commit this document ships with:

| Check | Result |
|---|---|
| Full suite | **800 passed, 1 skipped** in 333 s |
| Opt-in §13 reproduction | **passes** in 216 s (`SWING_SECTION_13=1 .venv/bin/python -m pytest tests/test_swing_ranking.py -k section_13`) |
| Migration up / down / up on SQLite | clean |
| `alembic revision --autogenerate` | empty `upgrade()` — no drift |
| `npm run build` | passes |
| MySQL | **DDL-compile-verified only, never executed against a live server.** Say so; do not imply otherwise. |

The one skipped test is the §13 reproduction, which is opt-in because it
imports 1.1 M rows. It was run separately and passed.

**Never run two `pytest` processes at once.** The temp database and log
directory are named after the process id now, but overlapping runs still cost
an hour on 2026-09-18 by producing a fatal SQLAlchemy traceback that read
exactly like a broken baseline.

**`conftest` enables every strategy and DISARMS every automated one** for each
test (`strategy_state_baseline`), restoring both afterwards. A test that needs
to place orders arms what it needs; a test whose subject IS a shipped default
reads `enabled_by_default` / `automation.armed_by_default` off the definition.

---

## State of the live installation, 2026-09-18

A server is running on `:8000` from `backend/` with `CONFIG_PATH=conf`, on the
current code, against the real `.env` and `data/paper_trading.db`. Backups at
`data/paper_trading.db.backup-20260918-130607` and `…-082434`.

| | |
|---|---|
| `instruments` | 1,739 — 499 NSE equities + 1,240 MCX crude contracts, all active |
| `instruments.fno_eligible` | 210 of the 499, derived from the master's own FUTSTK rows |
| `daily_bars` | 1,111,241 rows, 501 symbols, 2015-07-01 → **2026-09-17** |
| Portfolios | `Main` (id 1, crude, ₹10,03,881.56) and **`Swing Momentum` (id 2, ₹10,00,000.00, attached to `nse-swing-momentum` only)** |
| `swing_sessions` | 3 — a staleness refusal, a rebalance, a nightly, all for 2026-09-17 |
| `swing_stops` | 0 — nothing has ever been held |
| `orders` | 4, all pre-existing crude orders. **The rotation has never placed one.** |
| Feature toggles | `STRATEGY/nse-swing-momentum = enabled`. **No `AUTOMATION` row: it is NOT ARMED.** |
| Feed | live Dhan, CONNECTED, not synthetic |

**The dedicated portfolio already exists** — id 2, ₹10,00,000, created during
the phase 6–10 verification so the equity curve is comparable with the
backtest's starting capital. **Do not create a second one.**

---

## Decisions Akshay has made — do not re-litigate these

Settled on 2026-09-18, in his own words plus two clarifying answers.

| # | Decision |
|---|---|
| 1 | **Trade even while the NIFTY is below its 200-day SMA.** The regime gate stops gating. It is still computed and still recorded — it just no longer stops anything. |
| 2 | **Relax P9 as well as P8.** Confirmed explicitly. The 63-day return filter is a separate block on new entries and is currently −3.71%; relaxing only P8 would still produce no trades. Both go. |
| 3 | **Record the regime state on every trade** so trades taken with the gate off can be filtered out later. This recording is the entire reason decision 1 is acceptable. |
| 4 | **Arm it — let it trade now.** Flip the `AUTOMATION` toggle on the running instance. **Leave `automation.armed_by_default: false` in the YAML** so a fresh checkout still starts unarmed; he chose that reading explicitly over shipping it armed. |
| 5 | **Analysis may run at any hour; ORDERS may not.** Ranking, the nightly decision, the stop ratchet and the journal can run off-market. Every buy and every sell — entry and exit — must be placed inside live continuous trading, so it replicates a real market with paper money. |
| 6 | **Automated trades must appear on the Positions page** like any other position. |
| 7 | **The Live tab must show what the strategy is doing right now.** "I don't know what the strategy is doing at that point in time" is the problem to solve. |

### What decision 1 costs, stated plainly — put this in the reply, not just the code

This is a deliberate divergence from the specification and from Akshay's own
research, and the next session should say so once, without moralising:

- P8 and P17 are the specification's hard kill switch. §9 attributes the
  strategy's entire drawdown profile to the off-switches rather than to the
  stock selection.
- §11 tested thirteen ways of trading while the gate is off. **Not one beat
  holding cash.** The best of them, V3b, has genuinely good individual trades —
  38 over 11 years, 61% win rate, mean +7.81% — and still ends LOWER than the
  baseline, because capital committed to a bear rally is not available at the
  regime flip, which is exactly when the best trades fire.
- Expect this configuration to underperform the 19.9% headline by more than
  the 12–18% the specification already predicts for live trading.

None of that makes it the wrong call here: the point is to exercise the whole
machine against a live market with paper money, and a strategy that sits in
cash for months exercises nothing. **Say it once, record it per trade, and
move on.**

---

## The work

### 1. The regime override, and recording it per trade

**Where the switch goes.** `conf/strategies/nse-swing-momentum.yaml`, in the
existing `regime:` block — not a new top-level block, and not a constant in
Python (root `CLAUDE.md` §7):

```yaml
regime:
  index_role: "regime_index"
  sma_sessions: 200
  entry_return_sessions: 63
  entry_return_minimum: 0.0
  # NEW. enforce = the specification's rule: gate off means sell everything and
  # buy nothing. observe = the gate is computed and RECORDED on every session
  # and every trade, and gates nothing.
  enforcement: "observe"
```

`enforce` must remain the default in code, so a strategy module that does not
declare `enforcement` behaves exactly as the specification says. `observe` is
the opt-in.

**Where it is resolved.** `effective_gate()` in
`backend/src/swing/services/rebalance_planner.py` — the single place that
already reconciles the baseline against the V3b variant, read by the nightly
runner, the rebalance and the page. Add a third variant there and nothing else
changes:

| | `liquidates` | `entries_allowed` | `slots` | `momentum_floor` |
|---|---|---|---|---|
| baseline, gate on | false | P9 | breadth ramp | P6 |
| baseline, gate off | **true** | false | 0 | P6 |
| V3b, gate off | false | configurable | flat `off_gate.slots` | `off_gate.momentum_floor` |
| **observe, gate off** | **false** | **true** | **breadth ramp** | **P6** |

Note what `observe` does NOT change: the breadth ramp still sizes the book
(4 slots at today's 47.9%), the momentum floor still applies, the rotation exit
still fires at rank > 15, and the chandelier stop is untouched. Only P8, P17
and P9 stop gating.

**Refuse the contradictory configuration.** `off_gate.enabled: true` together
with `enforcement: observe` is two different overrides of the same thing.
`SwingParameters.from_definition` should raise and name the file and both keys,
the way it already does for a missing key — a silent precedence rule between
two owner-chosen switches is exactly the kind of thing nobody remembers.

**Recording it per trade.** The goal is decision 3: filter, later, on "was the
gate off when this trade was opened".

What already exists: `swing_sessions` stores `gate_on`, `index_close`,
`index_sma`, `index_return_over_window` and `entries_allowed` per session, and
`swing_decisions.order_id` ties a decision to the order it produced. So
`orders → swing_decisions → swing_sessions` already answers the question, and
the journal is append-only so that join cannot go stale. The case for
denormalising is **queryability, not correctness** — say that rather than
implying the data is missing today.

Recommended shape:

- Add the regime columns to **`swing_decisions`**, set when the row is written:
  `regime_gate_on`, `regime_index_close`, `regime_index_sma`,
  `regime_index_return`. `swing_decisions` is the row that is ALWAYS written —
  for every action and every non-action — which makes it the reliable carrier.
- Consider mirroring `entry_gate_on` onto **`swing_stops`**, which is one row
  per position and already carries `entry_session`, `entry_price` and
  `entry_order_id`, and which the performance metrics and the exit mix already
  read. **But it is not sufficient on its own**: a position entered when ATR14
  was unavailable gets NO `swing_stops` row (see
  `StopService.open_for_entry`), so a filter built only on that table would
  silently lose those trades.
- Migration rules apply (`backend/CLAUDE.md` §3): `VARCHAR` needs a length,
  money is the `Money` type, no server defaults, must apply **and** roll back,
  and `--autogenerate` must be empty afterwards.

**Make the filter visible.** The payoff of recording it is being able to split
the numbers. `GET /api/swing/performance` should be able to report the metrics
**split by regime at entry** — trades taken with the gate on against trades
taken with it off — because that comparison is the whole reason Akshay asked
for the recording. Without it the columns are just data nobody looks at.

**Tests, in this repo's negative style:**

- with `enforcement: observe` and the gate OFF, the rebalance **buys** and the
  decision records the gate state, the index close and the SMA;
- with `enforcement: enforce` (the default) and the gate OFF, it still buys
  **nothing** and still liquidates — the specification's behaviour is not
  disturbed by the new key existing;
- `observe` does not change the slot count away from the breadth ramp, and does
  not change the momentum floor;
- `off_gate.enabled` plus `enforcement: observe` is refused at load, naming the
  file and both keys;
- the performance split reports the two buckets separately, and reports
  `null` rather than 0 for a bucket with no trades.

### 2. Orders only inside live market hours — THIS IS THE REAL GAP

Decision 5. The code currently satisfies half of it.

**What already holds the line:**

- `swing-stop-monitor` will not evaluate a stop unless
  `market_clock.is_market_open(definition)` — `stop_monitor.py:160`.
- It will not send an exit unless `market_clock.can_execute_continuously(...)`
  — `stop_monitor.py:254` — which also covers the Closing Auction Session for
  F&O-eligible names (continuous trading ends 15:15 since 3 Aug 2026).
- The scheduler only fires the rebalance at `schedule.rebalance_at` (09:16
  IST), inside the session, on a trading weekday.

**What does not:**

- **`SwingExecutionService.run_rebalance` has NO market-hours check at all.**
  `execution_service.py` does not import `market_clock`. It checks enabled, bar
  staleness and idempotence, then places orders.
- `POST /api/swing/runs/rebalance` therefore places real paper orders against a
  stale book at any hour an admin clicks the button. It has not bitten yet only
  because the gate was off and there was nothing to buy — **decision 1 removes
  that accident.**
- `OrderService.submit_paper_order` has no market-hours check either, for any
  strategy. Chart trading has the same property today.

**What to build:**

- Check `market_clock.can_execute_continuously(definition, fno_eligible)`
  **per instrument, immediately before each order** — not once for the run.
  F&O eligibility changes the answer after 15:15, and a run that starts at
  15:14 can cross the boundary mid-list.
- A rebalance triggered outside hours must still **decide and journal** — the
  decision is valid, it is the execution that is not — recording "not placed:
  outside continuous trading" per intent, with the time. This is the same
  pattern an unarmed run already uses: the identical journal, no orders.
- **Do not queue the intents for the next open.** The next scheduled rebalance
  re-decides from fresh bars and a fresh book; replaying yesterday's intent is
  how you trade a decision nobody would take today. **This deliberately differs
  from the stop monitor, which DOES defer** — a triggered stop is a fact about
  a position that has already happened, not a fresh opinion, so it waits and
  fires at the next open.
- Decide, and write down, whether the guard belongs in `submit_paper_order`
  instead. A generic guard is harder to bypass, but it changes MCX crude and
  the chart's one-click entry, neither of which asked for it. **If you make it
  generic it must be a per-strategy policy read from the YAML, and the MCX
  module's behaviour must not change** — `tests/test_swing_does_not_disturb_crude.py`
  pins exactly that.

**Tests:** an order is NOT placed at 22:00; an order IS placed at 11:30; an
F&O-eligible name's order is NOT placed at 15:20 while a non-F&O one IS; the
journal carries the refusal and its reason; the nightly run still decides and
journals at 18:15 with no order placed, because that is analysis.

### 3. Show what the strategy is doing, on the Live tab

Decision 7. Today the Live tab shows what the strategy *decided*; it does not
show what it is *doing now*, and the difference is what Akshay is missing.

Everything needed is nearly there. `get_swing_scheduler().status()` already
returns `nowIst`, `schedules` (with `nightlyAtIst`, `rebalanceAtIst`, `cadence`,
`marketOpen`, `enabled`, `armed`), `recent` (the last 20 job runs with their
detail), `missedRuns` and `runs`. `get_swing_stop_monitor().status()` returns
`runs`, `triggered`, `exitsPlaced`, `deferredToAuction` and `error`.

**What to add to the payload:**

- **The next run and when.** The scheduler knows the times but never computes
  the next occurrence. Add `nextNightlyAtIst` / `nextRebalanceAtIst` as
  absolute timestamps so the page can count down locally rather than
  re-fetching every second (the Settings-page countdown trick,
  `frontend/CLAUDE.md` §4).
- **What it is doing right now**, as a state rather than a log: idle / warming
  the book / rebalancing / running the nightly / refreshing bars. The scheduler
  is the only thing that knows, and a `currentActivity` string it sets and
  clears around each job is enough. A long bar refresh (746 s measured) is
  exactly the case where a page that says nothing looks broken.
- **How many stops are being watched**, and how close the nearest one is to
  firing. The `/swing/book` payload already carries each position's
  `distancePercent`; the minimum across the book is the number that matters.

**What to build on the page** (`SwingMomentumPage.jsx`, Live tab):

- A status strip at the top: market open/closed with the IST clock, what the
  scheduler is doing now, what it will do next and in how long, and when it
  last did something. Ticking locally so a stalled poll is visible — that is
  the whole point of the Settings countdown pattern.
- A compact activity log from `scheduler.recent` — kind, time, ok/failed,
  detail. Twenty rows, newest first. It is a status dict, not a second journal;
  the real record is `swing_sessions` below it.
- The stop monitor's line: N stops watched, last pass, nearest position X%
  above its stop, anything deferred to the auction.
- **Honesty rules apply as hard here as anywhere** (`frontend/CLAUDE.md` §3):
  a countdown that cannot be computed says so rather than showing 00:00; "no
  stops watched" and "the monitor is not running" are different states and must
  read differently; and a strategy that is enabled but NOT armed must say, in
  the status strip, that it will decide and place nothing.
- Check light **and** dark.

### 4. Confirm automated trades reach the Positions page

Decision 6. **Expect no work here — verify, do not build.**

`PositionsPage.jsx` is scoped to the ACTIVE portfolio
(`useActivePortfolio().activeId`) and does not filter by strategy; the
controller takes an optional `strategy_key` that the page does not pass. An
automated swing trade in portfolio 2 will therefore appear as soon as
**"Swing Momentum" is selected in the header picker** — and will not appear
while "Main" is selected, which is correct and is what the picker is for.

Verify once a real trade exists: the row appears, the mark is live (swing
positions are subscribed — `subscription.kind: positions`), the unrealised P&L
moves, and closing it by hand still works. If anything is missing, say so
rather than adding a second position list to the swing page.

### 5. Arm it

Decision 4, and it goes **last** — after 1 and 2 are done and tested.

- Arm through **Strategies & Features** (the switch, the confirmation dialog
  and the server's warnings are all built) or `PUT /api/strategies/{key}/armed`.
  It writes a `feature_toggles` row under the `AUTOMATION` scope.
- **Do NOT set `automation.armed_by_default: true` in the YAML.** Akshay chose
  that explicitly: that key is only the state a brand-new installation starts
  in, and flipping it would arm a fresh checkout with no deliberate act.
- Arming restarts and resyncs nothing; the rebalance and the stop monitor read
  `registry.is_armed()` at the moment they would place an order.

### 6. Watch the first real trade closely

Nothing in this system has ever placed an order for this strategy against live
NSE quotes.

- Check the fill against the touch and the depth. The backtest assumes
  `open × (1 ± 0.05%)` per side; this crosses the spread, walks five levels and
  pays `trading.slippage_ticks` adversely on top. §14.3 of the specification
  asks for exactly this comparison. **Do not tune the simulator to agree** —
  report the gap.
- Check the chandelier stop was set at entry (`entry − 3.5 × ATR14`) and that
  `swing_stops` carries the ATR it used.
- Check the order's `PLACED` event carries the strategy's reason, and that the
  decision row carries the regime state from item 1.
- Check the cash ledger moved by the right amount including charges, and that
  `BalanceService` is still the only thing computing equity.

---

## Keeping it alive

The nightly at 18:15 IST keeps `daily_bars` current, and the rebalance
**refuses to trade on bars more than `swing.max_bar_staleness_days` (5) old**.
A laptop that sleeps through 18:15 for a week stops the rotation trading and
says why.

- Missed runs are detected against the regime index's own bar dates and
  reported at startup, on the Swing Momentum page and on the health page. They
  are never silently re-decided.
- To recover by hand:
  `CONFIG_PATH=conf .venv/bin/python scripts/refresh_daily_bars.py --strategy nse-swing-momentum`
  — and pass `--as-of YYYY-MM-DD` if you run it **during** a session, or Dhan
  hands back today's forming bar and it is stored as a daily bar.
- Worth asking Akshay whether this should run under `launchd` so it survives a
  reboot. Out of scope unless he says yes.

---

## Ground truth you must not rediscover the hard way

All verified on 2026-09-18 against the live instrument master, a live Dhan
token and a live feed.

- **Dhan's security ids are unique per SEGMENT, not globally.** Id 13 is NIFTY
  in `IDX_I` and ABB in `NSE_EQ`, and ABB is a Nifty 500 constituent. The
  regime index is a reference instrument and is deliberately never a row in
  `instruments`.
- **The universe is effectively 499.** `JBCHEPHARM` has no NSE row and stopped
  trading 2026-07-16; it is reported as unresolved on every refresh, never
  dropped silently. `HEG` and `HFCL` have universe-file ids that disagree with
  the master — the master wins, and whether the research panel was fetched with
  the wrong ids is **unverified**.
- **210 of the 499 are F&O-eligible**, matching the specification's own figure,
  derived from the master's FUTSTK rows on every refresh.
- **A full live bar refresh is 746 s** for 499 symbols at 0.6 s each. Do not
  parallelise it into a ban. It commits per symbol — a single twelve-minute
  transaction made the stop monitor log "database is locked" once a second for
  the whole run.
- **The bootstrap import is 1,088,241 rows in 211 s.** Already done; do not
  re-run it.
- **`resync()` leaves a rebalance's pins subscribed** while the book is
  otherwise empty, because an empty target set is ambiguous ("master not
  ingested" vs "nothing enabled") and unsubscribing everything would be wrong.
  About ten subscriptions out of five thousand until the next resync that
  resolves something. Do not weaken that guard.
- **0.65 − 0.35 is not 0.30**, and the breadth span is configured for that
  reason.
- **A symbol with no bar on the session is skipped, never forward-filled.**
- **The rebalance commits at defined points** because `resync()` opens its own
  session and SQLite blocks on an outer transaction. Do not wrap it in one
  transaction.

### Where the market stood when these decisions were made

| | 2026-09-17 session |
|---|---|
| NIFTY 50 close | 23,270.60 |
| NIFTY 200-session SMA | 24,501.66 |
| Shortfall to reclaim | 5.29% |
| 63-session return (P9) | −3.71% |
| Breadth | 47.9% (215 of 449) → breadth ramp allows **4 slots** |
| Effective slots under the CURRENT code | 0, because the gate is enforced |

Under `enforcement: observe` the same session allows 4 slots and 198 ranked
candidates, so the first rebalance after arming should open roughly four
positions. If it opens none, something in item 1 is wrong — check
`effective_gate()` before anything else.

---

## Traps

- **Rule #1.** No broker order path, not behind a flag, not as a stub. Local
  operations are `submit_paper_order` / `cancel_paper_order`. If
  `tests/test_no_real_orders.py` fails on a name you wrote, rename yours.
- **`tests/test_no_real_orders.py` and `tests/test_no_secrets_in_logs.py` must
  keep passing**, and every new endpoint needs its no-secrets assertion in the
  same change.
- **Do not make fills more generous.** Several tests assert a fill does *not*
  happen; they are load-bearing.
- **Do not tighten the stop.** §10 of the specification measures every tighter
  variant as worse. Decision 1 relaxes the REGIME gate; it does not touch the
  stop, the rotation exit, the breadth ramp or the momentum floor.
- **Do not tune constants to match the backtest.** It is survivorship-biased
  and in-sample, and this configuration diverges from it deliberately.
- **Undefined is not zero**, anywhere — a `None` breadth, a `None` equity, a
  `None` slot count, a countdown that cannot be computed.
- **Money is `Decimal`; indicators are float.** Convert at the boundary.
- **Committing is not arming.** Pushing code does not start trading; only the
  `AUTOMATION` toggle does. Arm last, deliberately, and say in the reply that
  you did.

---

## Definition of done

- `enforcement: observe` trades with the gate off, and `enforce` (the default)
  still does not — both pinned by tests.
- Every decision row carries the regime state it was taken under, and
  `GET /api/swing/performance` can split its numbers by regime at entry.
- No order can be placed by the swing path outside continuous trading, for
  either the rebalance or a stop exit, with the refusal journalled and tested —
  including the F&O 15:15 case. Analysis still runs at any hour.
- The Live tab says what the strategy is doing now, what it will do next and
  when, and what it last did — checked in light and dark.
- An automated trade appears on the Positions page under the Swing Momentum
  portfolio, with a live mark. Verified, not assumed.
- The strategy is ARMED on the running instance, and
  `automation.armed_by_default` is still `false` in the YAML.
- The MCX crude module is unaffected: charges, fills and P&L byte-identical,
  and the ₹0.01 Zerodha divergence still exactly ₹0.01.
- Full suite green, including both safety suites. Migrations apply and roll
  back on SQLite; `--autogenerate` empty afterwards. `npm run build` passes.
- The reply states, once and without moralising, that trading through the
  regime gate is a deliberate divergence from the specification and from
  Akshay's own §11 finding, and that it is recorded per trade.
- `README.md` and the relevant `CLAUDE.md` files updated, and anything you
  could not verify in the README's "Known gaps".
- Committed with `amrakshay@gmail.com` and pushed to `origin/main` over SSH.
  No JIRA prefix, no GitLab MR, no ADR — personal repo.

---

## Reference

| Thing | Where |
|---|---|
| Strategy spec | `~/Workarea/local/pullback/backend/intrday_test_strategy/research2/SWING_MOMENTUM_HANDOFF.md` |
| Phases 1–5 handoff | `SWING_MOMENTUM_IMPLEMENTATION_HANDOFF.md` |
| Phases 6–10 handoff | `SWING_MOMENTUM_PHASE_6_10_HANDOFF.md` |
| The package | `backend/src/swing/README.md` |
| The gate to change | `backend/src/swing/services/rebalance_planner.py` → `effective_gate()` |
| The hours gap to close | `backend/src/swing/services/execution_service.py` |
| The pattern to copy | `backend/src/swing/services/stop_monitor.py` |
| Market-hours helpers | `backend/src/strategies/services/market_clock.py` |
| The journal to extend | `backend/src/swing/database/db_models/swing_session_model.py` |
| The page to extend | `frontend/src/pages/SwingMomentumPage.jsx` (Live tab) |
| Login | `trader@abc.com` / `APP_ADMIN_PASSWORD` from `.env` |
| The page | `http://127.0.0.1:8000/swing` (and `?tab=how-it-works`) |
