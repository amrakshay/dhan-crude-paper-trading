# Handoff — a Health tab on the Swing Momentum page

Read `CLAUDE.md`, `backend/CLAUDE.md` and `frontend/CLAUDE.md` first. They cover
the no-real-orders constraint, the one-connection invariant, the tick-path
performance contract, the strategy/capability/portfolio model, the two switches
(enabled and auto trade), the module layering, the theme and the UI honesty
rules. `README.md`'s **NSE Swing Momentum** and **System health** sections cover
what already exists, and `backend/src/swing/README.md` covers the package. Do
not re-derive any of that.

The existing system health page is `frontend/src/pages/SystemHealthPage.jsx`
over `GET /api/healthcheck/system` (`backend/src/health/`). **This is not a
change to that page.** It stays exactly as it is.

---

## What this is for

The Swing Momentum page has three tabs — Live, Configuration, How it works.
This adds a fourth: **Health**.

System Health answers *"what is this process doing"*. It is process-wide, and
it is the right shape for that: one feed connection, one book, one set of
background tasks. What it cannot answer is the question an operator actually has
about an automated strategy that trades unattended:

> **Is this strategy healthy, is anything about to stop it working, and what has
> it actually been doing?**

Today that answer is scattered: some of it is on the Live tab, some on System
Health, some only in the log, and some nowhere at all. This tab gathers it, for
ONE strategy, in plain words.

**Plain words are a requirement, not a preference.** The owner asked for this
explicitly, and the last two pieces of work already moved the page's vocabulary:
the screen says *auto trade*, *analysis of stocks* and *order placement* while
the code and the database still say `armed`, `NIGHTLY` and `REBALANCE`. Keep
that split — the translation belongs at the edge (`RUN_LABELS` in
`SwingMomentumPage.jsx`), because renaming stored values rewrites history.

---

## Baseline — do NOT spend time re-running these

```bash
cd backend
.venv/bin/python -m pytest tests/ -q          # 848 passed, 1 skipped, ~6m
CONFIG_PATH=conf .venv/bin/alembic heads      # c9e42a7b51d8 (head), 13 migrations
cd ../frontend && npm run build               # passes, ~2s
```

**Never run two `pytest` processes at once.** The temp database is named after
the process id, but overlapping runs still produce a fatal SQLAlchemy traceback
that reads exactly like a broken baseline.

State of the running installation on 2026-09-18: NSE Swing Momentum enabled and
ARMED, the regime gate and the entry-return filter both NOT enforced, the
off-gate variant disabled, both timings on their shipped values (18:15 / 09:16),
portfolio 2 (`Swing Momentum`, ₹10,00,000), **zero positions and zero orders
ever placed by the rotation**. MCX crude is off. First automated trade expected
Monday 2026-09-21 at 09:16 IST.

---

## What already exists, verified by reading the code

Everything in this table is **already computed and reachable**. Most of the work
is assembling it, not measuring anything new. Nothing below is on the tick path
and nothing below may be put there.

| Signal | Where it already lives |
|---|---|
| What the scheduler is doing now, next run times, last 20 runs, missed runs | `get_swing_scheduler().status()` — already per strategy in `schedules[]`, and already on `GET /api/swing/status` |
| Stop watcher: passes, triggers, exits placed, deferred to auction, watching, unprotected, nearest distance | `get_swing_stop_monitor().status()` |
| Instruments this strategy has on the shared feed | `get_feed_manager().strategy_state[key]` → `instrument_count`, and `status()["lastResyncMs"]` |
| Are its two background tasks actually alive | `src/health/services/task_inspector.py` — knows `swing-scheduler` and `swing-stop-monitor` and whether each is expected |
| Daily bar coverage: symbols, rows, first and last date | `DailyBarRepository.coverage(exchange_segment)` |
| Per-symbol bar staleness | `DailyBarRepository.latest_dates(...)` |
| The trading calendar | `DailyBarRepository.trading_dates(index symbol, segment)` — the regime index's own bar dates |
| Instrument master: active rows, F&O-eligible count, last refresh | `InstrumentRepository.count_all()` / `last_refreshed_at()`, `Instrument.fno_eligible` |
| Decision journal: counts, latest per kind, gaps | `SwingSessionRepository.count_for / latest / list_recent / decided_session_dates` |
| Stops: active, triggered, closed, and the exit mix | `SwingStopRepository.list_active / list_triggered / history` |
| Money: cash, blocked, available, equity | `BalanceService.balance_for(portfolio_id)` — the ONLY place that computes these |
| Which portfolios run it | `PortfolioRepository.portfolios_running(key)` |
| Charge rate card and version | `ChargesEngine.for_strategy(definition).version` |
| Effective configuration | `describe_policies()` and `describe_settings()` in `src/swing/services/` |
| Who last changed a switch, and when | `feature_toggles.updated_by_user_id` / `updated_at`, `strategy_settings.updated_by_user_id` / `updated_at` |
| Recent warnings and errors | `src/log_buffer.py` — each record carries its `logger` name, so `dcpt.swing.*` is filterable |

### What does NOT exist, and what it would cost

Be honest about these on the page rather than implying otherwise. Each is a
separate decision for the owner — **do not silently build all of them**.

1. **There is no audit HISTORY.** `feature_toggles` and `strategy_settings`
   carry only the CURRENT value with who set it and when. "The gate was relaxed
   at 15:19 by trader@abc.com and re-enforced at 16:40" cannot be answered. A
   real trail needs a new append-only table (`strategy_audit`: strategy key,
   what changed, from, to, who, when) written by `StrategyStateService`. It is
   maybe 120 lines plus a migration. **Ask before building it.**
2. **Bar refresh history is not persisted.** The nightly's refresh result —
   refreshed, failed, unresolved, inserted, duration — is turned into a string
   on the scheduler's in-memory `JobRun.detail` and dies with the process.
   Either persist a small row per refresh or say plainly that the page shows
   only what this process has done since it started.
3. **The stop watcher's counters are process-wide, not per strategy.**
   `SwingStopMonitor` keeps one set of counters across every strategy. With one
   automated module they are the same number; with two they would silently not
   be. Either key them by strategy or LABEL them as process-wide. Do not show a
   process-wide number under a strategy heading without saying so.
4. **There is no per-strategy log endpoint.** `GET /api/healthcheck/problems`
   is admin-only and unfiltered. Filtering by logger prefix is easy; the access
   question below is the hard part.

---

## Decisions to make before building

### 1. Who may see it — this one is load-bearing

`/swing` is deliberately open to `ROLE_USER` (`backend/conf/role-pages.json`
says so, and the comment explains why: a journal is history and must not be
hidden). `/health` and every `/api/healthcheck/*` endpoint are **admin-only**.

So a Health tab on `/swing` crosses two access policies. Pick one and implement
it in the API, not only in the UI:

- **Recommended:** the tab is visible to everyone, and it is split. Operational
  facts about the strategy — schedule, runs, bars, instruments, stops, money —
  are readable by any signed-in user, exactly like the rest of the page. **Log
  records are admin-only** and the section renders "sign in as an administrator
  to see recent problems" for everyone else. Log lines can carry anything a
  developer ever put in a warning; that is why `/problems` is admin-only today.
- Alternative: the whole tab is admin-only and hidden for a `ROLE_USER`. Simpler
  to reason about, and it takes visibility away from the person most likely to
  be watching the strategy.

### 2. Where the endpoint lives

**Recommended:** a new `GET /api/swing/health?strategyKey=…&portfolioId=…` in
`src/swing/`, alongside `status` and `book`. `src/health/` answers "what is this
process doing" and is genuinely process-wide; per-strategy health is a property
of the strategy, and putting it in `src/health/` would make that package import
the swing package for its own payload. Reuse `task_inspector` from
`src/health/` by importing it — that is a read-only helper and importing it is
fine.

### 3. Audit history — build it or not?

See gap 1 above. Without it the tab can say *"the regime gate is not enforced,
set by trader@abc.com at 15:19 today"* and nothing more. That is already a large
improvement on today and may be enough.

---

## What the tab should show

Groups in this order, each a `SectionCard`-style panel. **Every heading below is
the wording to put on screen.** Keep it.

### "Is it working right now?"
One row of plain verdicts, each green/amber/red with a sentence:

- **Switched on** / off
- **Auto trade** on / off — and when off, "it will decide and place nothing"
- **Market** open / closed, with the IST clock
- **Its two background jobs** — the clock and the stop watcher — *running* or
  *not running*, from `task_inspector`. A strategy that is on and armed with a
  dead scheduler is the failure this whole tab exists to make visible.
- **Prices arriving** — instruments this strategy has on the shared feed, and
  when the subscription was last rebuilt

### "What it will do next, and what it last did"
Move the countdowns here from the Live tab's status strip? **No — keep them on
Live and repeat the schedule here in full.** Live answers "what now"; Health
answers "is the clock sound". Show next analysis, next order placement, the last
run of each kind with its outcome, and the **missed runs** list — which should
MOVE off the Live tab (see below).

### "The data it decides on"
The most likely thing to quietly break, because it depends on an overnight job
and an external vendor.

- Daily bars: symbols, rows, oldest and newest session
- **How stale the newest session is**, in sessions and in days, against
  `swing.max_bar_staleness_days` (5) — with the sentence the rebalance itself
  would refuse with, if it would refuse
- Symbols whose newest bar is behind the rest — these are the ones that silently
  drop out of the ranking
- The universe: how many symbols configured, how many resolved in the instrument
  master, how many unresolved (`JBCHEPHARM` is permanently one), how many are
  F&O-eligible
- Instrument master last refreshed at

### "What it holds, and what it is watching"
- Open positions, and how many have no live mark
- Stops: active, how many have no level yet (entered with no ATR14), triggered
  and waiting for the next open, nearest distance to a stop
- Money for its portfolio: cash, blocked, available, equity — **four figures,
  never one**, and equity says "no mark" rather than a number when it is withheld

### "The rules it is running under"
A read-only mirror of the Configuration tab: each of the three rule switches and
both timings, with **what the strategy ships with** beside **what is in force**,
and who changed it and when. Not editable here — link to Configuration.

This is the section the owner most wanted: one place that says "this is what is
actually in force", without having to remember which switches were moved.

### "Recent problems"
Warnings and errors from this strategy's own components, newest first, filtered
to the `dcpt.swing.*` loggers. Admin-only (decision 1). It **must** say that the
list is in memory and starts empty after a restart, and that `logs/app.log` is
the durable record — the existing health page already says exactly this and the
wording can be reused.

### "What changed, and who changed it"
Either the audit history (decision 3) or, without it, the `updated_at` /
`updated_by` of each switch and setting.

---

## What to MOVE off the Live tab

The Live tab has grown and some of it is health, not activity. Move:

- **the missed-runs warning and its date list** — a gap in the journal is a
  health fact, and it is currently the loudest thing on the Live tab despite
  usually being historical. Leave a one-line summary on Live ("20 missed runs —
  see Health") so it is not hidden.
- **"Recent scheduled runs"** (the in-memory activity log) — it is diagnostics.
  Live keeps "what it is doing now" and "what it last did"; the twenty-row table
  belongs on Health.
- **the closing-auction note** under the schedule figures — reference material.
- **the stop watcher's internals** (passes, deferred to auction, last pass) —
  keep "N stops watched, nearest X%" on Live, move the rest.

Leave on Live: the status strip, the gate card, the run buttons, the open book,
the ranking, performance, and the decision history. Those are what the strategy
is doing and deciding.

---

## Traps

- **Nothing here may be measured on the tick path.** Every figure is either
  existing component state, a database read or an `asyncio.all_tasks()` walk.
  The same rule the system health page follows.
- **Do not add a second poll loop.** The Swing page already polls at 10 s. Fetch
  the health payload on the same cadence (or slower — the system health page
  uses 5 s and explains why it is slower than Positions' 2 s). A page that
  reports on load must not be a load source.
- **Undefined is not zero.** A breadth that could not be measured, a nearest
  stop with no marks, an equity figure `BalanceService` withheld, a countdown
  that cannot be computed — each says so. This rule has been broken twice on
  this page already and caught both times in review.
- **The tab must work when the strategy is OFF**, and when it has never traded.
  Every number on it today is zero or absent, and the page has to read as
  "nothing has happened yet" rather than as "everything is broken".
- **No secrets.** The Dhan token, the database URL and anything from the config
  tree stay out of this payload. **The new endpoint needs its own assertion in
  `backend/tests/test_no_secrets_in_logs.py` in the same change** — that is a
  rule in the root `CLAUDE.md`, and the last two pieces of work each added one.
- **Do not duplicate System Health.** CPU, memory, uptime, the upstream socket,
  browser sockets, credentials and the schema revision are process-wide and stay
  there. If a number is not about this strategy, link to `/health` instead of
  copying it.
- **`tests/test_no_real_orders.py` and `tests/test_no_secrets_in_logs.py` must
  keep passing.** This tab reads; it must place nothing and change nothing.
- **Check light and dark.**

---

## Definition of done

- A fourth tab, **Health**, at `/swing?tab=health`, in the same plain vocabulary
  as the rest of the page (auto trade, analysis of stocks, order placement).
- One new read-only endpoint, with its no-secrets assertion in the same change,
  and admin-only gating on whatever decision 1 settles.
- It answers, without reading a log: is it on, may it trade, are its jobs alive,
  are its prices fresh, what does it hold, what is it watching, which rules are
  in force and who changed them.
- The Live tab is lighter by the four items above, with a pointer where each
  went.
- Every absent figure says what is absent rather than showing zero, and the page
  reads correctly for a strategy that is off and has never traded.
- MCX crude is unaffected — it is discretionary and has no Health tab.
- Full suite green, `npm run build` passes, and any migration applies and rolls
  back on SQLite with `--autogenerate` empty afterwards.
- `README.md` and the relevant `CLAUDE.md` files updated, and anything that could
  not be verified added to the README's "Known gaps".
- Committed with `amrakshay@gmail.com` and pushed to `origin/main` over SSH. No
  JIRA prefix, no GitLab MR, no ADR — personal repo.

---

## Reference

| Thing | Where |
|---|---|
| The page to extend | `frontend/src/pages/SwingMomentumPage.jsx` |
| The page to imitate, not change | `frontend/src/pages/SystemHealthPage.jsx` |
| Process-wide health | `backend/src/health/services/health_service.py` |
| Are the tasks alive | `backend/src/health/services/task_inspector.py` |
| Recent warnings, with logger names | `backend/src/log_buffer.py` |
| The clock | `backend/src/swing/services/scheduler.py` |
| The stop watcher | `backend/src/swing/services/stop_monitor.py` |
| Bars and coverage | `backend/src/daily_bars/database/db_operations/daily_bar_repository.py` |
| The journal | `backend/src/swing/database/db_operations/swing_session_repository.py` |
| Effective rules and timings | `backend/src/swing/services/gate_policy.py`, `schedule_settings.py` |
| Money, and the only place it is computed | `backend/src/portfolios/services/balance_service.py` |
| Who may see which page | `backend/conf/role-pages.json` |
| Login | `trader@abc.com` / `APP_ADMIN_PASSWORD` from `.env` |
| The page | `http://127.0.0.1:8000/swing` |
