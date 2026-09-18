# CLAUDE.md — backend

Backend conventions. Read the root `CLAUDE.md` first; §1 there (never place a
real order) governs everything here.

---

## 1. Module layering

Each feature package follows the same shape. Keep it — the consistency is the
point.

```
src/<feature>/
  routes/        FastAPI router. Auth dependency, query params, docstrings.
  controllers/   Orchestration. Turns service results/exceptions into HTTP.
  services/      Business logic. No FastAPI imports, no HTTPException.
  api_schemas/   Pydantic request/response models, camelCase aliases.
  database/
    db_models/       SQLAlchemy models
    db_operations/   repositories
  __init__.py    exports `<feature>_main_router`
```

Rules:

- **Services raise domain exceptions** (`OrderValidationError`,
  `NoteValidationError`, `ChargesConfigError`). **Controllers** translate them
  into `HTTPException`. A service that imports FastAPI is a bug.
- **Repositories own queries.** No `select()` in a service or controller.
- **API payloads are camelCase** via Pydantic aliases; Python stays snake_case.
- Register new routers in `main.py` under `/api`.
- Config is read through `src.config_utils.get_property_value*`, never
  `os.environ` directly (the YAML does `${VAR}` substitution).

Cross-package imports that would be circular (orders ↔ positions) are done
**inside the function**, not at module scope. There is already one such import
in `positions/controllers/position_controller.py`; follow that pattern.

---

## 2. Async SQLAlchemy gotchas that have already bitten this codebase

These are real bugs that were hit and fixed here. Do not reintroduce them.

**Reading an attribute off an expired instance raises `MissingGreenlet`.**
The session uses `expire_on_commit=False`. After `session.expire_all()`, touching
`order.id` triggers a lazy refresh outside an awaited context and blows up.
Capture the id *before* expiring:

```python
order_id = order.id          # BEFORE
await self.session.commit()
self.session.expire_all()
refreshed = await self.repository.get_by_id(order_id)
```

**Relationships are stale after you add children.** With
`expire_on_commit=False`, re-fetching returns the identity-mapped instance with
its `events` / `fills` collections as first loaded. If you added an event, you
must `expire_all()` (see above) or the response will omit the transition that
just happened.

**Background tasks open their own sessions.** Anything using `session_scope()`
(the feed manager, the order matcher, the greeks poller) cannot see rows the
current request has not committed. If a request needs a background component to
observe its writes, **commit first** — there is already a `session.commit()`
before the feed resync in `instruments/controllers/instrument_controller.py` for
exactly this reason.

---

## 3. Schema and migrations

Every migration must run clean on **both** SQLite and MySQL.

- **`VARCHAR` always has a length** (MySQL requires it on indexed columns).
- **Money is the `Money` type** (`NUMERIC(18,4)`), which returns exact
  `Decimal`s on both backends. Never `Float` for money.
- **Order/fill/event timestamps use `PreciseDateTime`**, which is
  `DATETIME(6)` on MySQL. Plain MySQL `DATETIME` truncates to whole seconds and
  would destroy the millisecond-precision order history.
- **No server defaults.** `created_at` / `updated_at` are set in Python, because
  the SQLite and MySQL spellings of `CURRENT_TIMESTAMP` defaults differ and
  autogenerate emits the SQLite one.
- Timestamps are **naive UTC** in the database (`src.core.time_utils.utc_now`).
  Convert to IST only at the edges (`to_ist`).
- New models must be imported in `src/database/models.py` or autogenerate will
  miss them.
- After changing models, check for drift:
  `alembic revision --autogenerate -m "drift check"` should produce an empty
  `upgrade()`. Delete the file afterwards.

MySQL is **DDL-compile-verified only** — it has never been executed against a
live server here. If you touch the schema, say so rather than implying otherwise.

---

## 4. Fill simulation — do not make it more generous

`src/orders/services/fill_simulator.py` is deliberately pessimistic. A paper
simulator that flatters the trader is worse than none. Preserve all of this:

- Market orders pay the **opposite side of the touch**, never the LTP.
- Large orders **walk the book**; each level supplies only its displayed
  quantity and the average degrades.
- Depth is finite — an order bigger than the visible five levels **partially
  fills**. Never invent liquidity.
- `trading.slippage_ticks` is applied **adversely** (a buy pays more, a sell
  receives less).
- **A resting limit order fills only when the market trades THROUGH its price**,
  not when it touches it. Touching means joining the back of a queue; assuming a
  fill there is the single most flattering error possible. `requires_cross=False`
  exists but is not the default.
- A resting order fills **at its own limit**, not at the better price the market
  traded through — price improvement would need queue priority the feed does not
  publish.

Several tests assert that a fill does *not* occur. They are load-bearing.

---

## 5. Charges

- **Every rate lives in a rate card**, `conf/charges/<card>.yaml`, with a
  primary source URL, an as-of date and a confidence marker. Nothing is
  hardcoded in Python. A strategy names the card it is charged under; the
  engine is generic, the rates are not.
- **The component list IS the breakdown.** `ChargeBreakdown.components` is the
  source of truth and what gets persisted; `.ctt`, `.gst` and friends are
  conveniences that read out of it. `order_charges` keeps `turnover`,
  `total_charges` and `rates_version` as columns because they are queried and
  aggregated -- the per-tax columns were dropped on 2026-09-18, because which
  taxes exist is a property of the card and a column per tax makes adding one a
  migration. Read a stored breakdown through
  `src/charges/services/charge_persistence.py`, never by parsing the JSON again
  somewhere else.
- **A component carries both its rounded amount and the raw figure.** The total
  is the rounded SUM OF THE RAW components, not the sum of the rounded ones, so
  the two can differ by a paisa. That is a property of rounding and was true
  before the refactor; the raw figure is kept so it can be traced rather than
  argued about.
- All arithmetic is `Decimal`. Rounding is a configured policy
  (`rounding.mode`), not an accident.
- **Brokerage is per executed ORDER**, not per fill. When an order fills in
  several parts, charges are *recomputed* on the cumulative executed quantity
  (`compute_order_charges_for_quantity`) rather than accumulated, so brokerage is
  counted once. The position accrues only the *delta* against what was already
  charged.
- `compute_order_charges_for_quantity` exists because a partial fill is a number
  of barrels, not a whole number of lots.
- Orders persist the `rates_version` they were charged under so old orders stay
  explainable after rates change.

---

## 5a. Strategies and portfolios

`src/strategies/` and `src/portfolios/` are the two packages the rest of this
application now hangs off. The root `CLAUDE.md` section 3a has the rules; these
are the ones that only matter once you are editing the code.

- **`src.strategies` must stay import-light.** Every low-level module reads the
  registry, so the strategies package cannot import orders, positions,
  chart-trading or portfolios at module scope -- the app will not start. Those
  imports go inside the functions that need them, the same way `auth` <->
  `users` is handled (section 10).
- **`feature_toggles` has four scopes**, not three: `STRATEGY`, `CAPABILITY`,
  `AUTOMATION` and, since 2026-09-18, `POLICY` (`toggle_key` is
  `<strategy_key>/<policy>`). The registry stores a policy as an OVERRIDE map
  and deliberately does not learn what any of them mean -- `policy_override`
  returns `None` when nobody has touched a switch, which is not `False`, and the
  strategy's own module resolves it against the YAML default
  (`src/swing/services/gate_policy.py`). A row naming an unknown strategy, an
  unknown policy, or a module with no `automation` block is ignored on load, the
  same property `MANAGED_KEYS` gives the settings table.
- **`strategy_settings` is the sibling table for runtime VALUES.** Toggles are
  booleans; these are not. The registry caches them as raw strings and does not
  learn what they mean, the same way it holds a policy override without
  learning what a regime gate is -- the owning module parses and validates
  (`src/swing/services/schedule_settings.py`). `setting_override` returns
  `None` when nobody has set one, which is not `""` and not the default.
  Clearing an override DELETES the row rather than storing a sentinel.
- **`get_strategy_registry()` is synchronous and cached.** It is read from code
  that cannot await (the feed's target resolution, `expected_task_names`), which
  is why enabled state is held in memory and refreshed by
  `StrategyStateService`, not read from the database per call.
- **A toggle applies through `resync()`, not `reconfigure()`.** Nothing about
  the credentials changed, so tearing down the feed client would drop the
  upstream connection and every browser's prices with it. `resync()` diffs the
  target set. The one subtlety: an EMPTY target set means two different things
  -- "the instrument master has not been ingested", where the live subscription
  must be left alone, and "nothing is enabled", where everything must be
  unsubscribed. Telling them apart is what makes "off" free anything.
- **A page belongs to whatever can actually fill it.** `/live` shows ONE
  strategy's front contract -- depth, chart, ticket -- and was granted by
  `STRATEGY_LIVE_PAGES` whenever ANY strategy was live. Switching MCX crude off
  and leaving the NSE rotation on therefore left "Crude Oil" in the sidebar
  showing a contract no running strategy had; the rotation holds a basket of
  equities and has no front contract. It is now the `live-price` CAPABILITY,
  declared by `mcx-crude-options` and by nothing else, which is the mechanism
  that already existed for exactly this. `STRATEGY_LIVE_PAGES` is now empty and
  is kept rather than deleted: the distinction it draws -- a page that needs
  something live but is not specific to one strategy's instruments -- is still
  real, it just has no members.
- **`BalanceService` owns cash, blocked margin, available and equity.** Four
  numbers, one place. A second implementation anywhere is a bug waiting for a
  disagreement.
- **The cash ledger is append-only.** There is deliberately no update and no
  delete on `CashLedgerRepository`. A correction is a new entry.
- **Charges are posted to the ledger as a DELTA.** They are recomputed on
  cumulative executed quantity (section 5), so posting the full figure on a
  second fill would charge the portfolio twice.

---

## 6. P&L reporting

Realised P&L is **derived, not stored**: `reports/services/pnl_service.py`
replays every fill through the same weighted-average rules the live position
book uses.

**The two must agree.** They apply identical rules, so they must quantise
identically — prices at 4 dp (`_qp`), money at 2 dp (`_q`). Rounding the replay's
running average to 2 dp skewed a multi-fill round trip by ₹0.20 before it was
fixed. `test_report_agrees_with_the_position_book_on_realised_pnl` guards this.

Replaying (rather than storing a running total) is what makes the by-day /
by-expiry / by-strike slices and the equity curve possible at all.

---

## 7. Settings and secrets

`src/settings/` stores runtime configuration in the `app_settings` table, and it
**takes priority over `.env`**.

- `SettingsService.apply_to_config()` mutates the in-memory config dict from
  `config_utils`. That is deliberate: it means every existing caller picks up the
  UI value without touching a single call site. It runs in the lifespan
  **before the feed starts** (`main.py::_apply_stored_settings`), and again
  after each save.

  **Do not move or drop the startup call.** It was missing until 2026-09-17 --
  `apply_to_config()` was reached only from `save()` -- so every restart
  silently reverted the running configuration to `.env` while the Settings page
  went on showing the stored values. Nothing failed; the feed just used the
  wrong credentials. The system health page is what caught it, and
  `tests/test_startup_applies_stored_settings.py` now pins both the call and
  its position relative to `get_feed_manager().start()`. The startup call is
  also what registers a database-only token with `log_redaction`, via
  `load_stored()`.
- Only keys in `MANAGED_KEYS` are ever read back out, so a stray row cannot start
  influencing configuration.
- **Secrets go in `encrypted_value`, never `value`** — the repository enforces
  that a value lives in exactly one column.
- `crypto_service` refuses to encrypt when no stable secret is configured, rather
  than using a per-process random key that could never be read back. Decryption
  failures return `None` (log and ask the operator to re-enter) instead of
  raising, so a rotated secret cannot block startup.
- **Never return a token to the browser.** Return `crypto_service.mask()` plus
  the metadata from `inspect_token()`.
- `inspect_token()` decodes the JWT **without verifying the signature** — Dhan
  signed it with a key we do not hold, and we are reading metadata, not trusting
  it. Do not "fix" this by verifying.
- **`dhan_token_client.py` is the third Dhan client and the only one that is
  not market data.** One endpoint, `/RenewToken`, named in that file and
  nowhere else (asserted). It reads the token from the live configuration
  rather than taking one as an argument, so no caller has to hold a token to
  use it. Its failure modes are two types, not one: `TokenExpiredError` means
  stop and tell a person, `TokenRenewalError` means try again later.
- **A renewal handles two secrets at once**, which nothing else here does — the
  old token goes out and a new one comes back. Neither is ever logged, and an
  httpx exception's MESSAGE is deliberately not interpolated into the log or
  into `last_error`, because Dhan's own docs put tokens in query strings on
  other endpoints and an error can carry the request. Only the exception type
  is reported. Both are asserted in `tests/test_no_secrets_in_logs.py`.
- Changing credentials or the synthetic toggle requires
  `FeedManager.reconfigure()`. It rebuilds the feed client, book and greeks
  poller but deliberately **keeps the broadcaster**, so connected browser tabs
  are not stranded on a dead one. Do not replace the FeedManager singleton to
  apply settings.

## 8. Logging

`src/logging_config.py` is the only public API: `get_logger("<feature>.<area>")`
and `get_access_logger()`. Never call `logging.getLogger` directly and never add
a handler at a call site.

- Config is `conf/logging-config.ini` (or `local-logging-config.ini` beside it),
  loaded with `fileConfig` and found via `CONFIG_PATH`. Levels from
  `logging.level` / `logging.access_log_level` / `LOG_LEVEL` are applied on top.
- **Bootstrap order matters.** `configure_logging()` is one-shot. Anything that
  builds a logger at module scope before `load_config_properties()` runs pins
  logging to its defaults and silently ignores `logging.log_dir`. `main.py` and
  `alembic/env.py` therefore call `load_config_properties()` *above* their other
  `src.*` imports. Keep it that way.
- **Never log on the tick path** (`feed_protocol`, `MarketBook.apply_packet`).
  Tick volume is reported as aggregate counters from `Broadcaster._log_summary`
  on the broadcaster's own interval. `market_book.py` deliberately has no log
  calls at all.
- **Never log a secret.** `tests/test_no_secrets_in_logs.py` fails the build on
  one, both at runtime and by AST-scanning every `logger.*()` call for
  secret-named arguments. Wrap a secret in `crypto_service.mask()`, `bool()` or
  `len()` if it must appear at all. `src/log_redaction.register_secret()` adds a
  value to the scrubber for anything discovered at runtime (the Dhan token from
  the Settings page is registered this way).
- **Correlate.** Order lines carry the `client_order_id`; the fill simulator
  takes a `context=` tag for the same purpose. Request lines carry the request
  id from `LogRequestsMiddleware`.
- DEBUG must stay cheap enough to leave on: measured at 0.27 MB/hour with the
  whole stack running. Log decisions and inputs, not loops.

---

## 9. Serving the frontend (single-port mode)

`src/static_serving.py` lets the backend serve `frontend/dist` so one port gives
both UI and API. It is additive — two-port dev (Vite on :5173 proxying to :8000)
is unchanged and is still the development default.

- **`mount_spa(app)` must be the last thing `main.py` does.** It registers a
  `/{path:path}` catch-all, and FastAPI matches in registration order, so
  anything added after it is unreachable.
- **The catch-all refuses `/api/*` and `/ws/*`** and returns the JSON 404
  itself. Without that, a typo'd endpoint returns 200 and an HTML page, which
  is a miserable way to debug a frontend. `tests/test_single_port_mode.py`
  asserts this directly.
- **There is no `@app.get("/")`.** It would win over the catch-all, so `/` could
  never serve the SPA shell. When the frontend is not built, `main.py`
  registers `service_identity` at `/` instead to preserve the old JSON response.
- The global `exception_handler(404)` now passes through an endpoint's own
  `detail`. It used to flatten every 404 to "Path not found", so a real answer
  ("Refresh the instrument master") reached the UI as a routing error.
- Hashed assets get `immutable` for a year; `index.html` gets `no-store`, or a
  rebuild is masked by the cached shell.
- A missing `dist/` logs how to build it and serves the API only. Do not make
  it fatal — that is what a fresh checkout looks like.
- `_safe_join` refuses any path escaping the dist root. `/{path:path}` will
  otherwise happily deliver `../../.env`.

---

## 10. Users, auth and roles

`src/users/` owns the users table; `src/auth/` owns sessions. The two depend on
each other, so **the `auth` package never imports `users` at module scope** --
every such import is inside a function or under `TYPE_CHECKING`, the same way
orders <-> positions is handled (section 1). Importing `src.users.anything`
runs `src/users/__init__.py`, which imports the router, which imports
`src.auth.dependencies`; a module-scope import in the other direction closes
the loop and the app will not start.

- **`require_session` returns a `SessionPrincipal`, not a username**, and
  re-reads the user from the database on every request. That is what makes
  deactivation, deletion and demotion take effect on the *next request* rather
  than at token expiry. Do not "optimise" it by trusting the role in the JWT.
- **`require_admin` is the gate, not the sidebar.** `conf/role-pages.json`
  decides what the UI offers; the route dependencies decide what the API
  allows. Every restricted endpoint needs its own dependency, and
  `tests/test_users_api.py` asserts the negative by calling as a ROLE_USER.
- **`require_session` vs `require_session_allow_password_change`.** The strict
  one 403s a user who still owes a password change. Only `/auth/me`,
  `/auth/logout`, `GET /users/me` and the change-password call use the
  permissive one -- a user must be able to reach the things needed to *stop*
  owing a password change.
- **Passwords are bcrypt-hashed, never encrypted.** `password_service` caps
  them at 72 bytes because bcrypt silently ignores the rest, which would let
  two different long passwords authenticate each other. Never add a code path
  that recovers a password.
- **No response schema has a password_hash field.** `to_response()` builds the
  payload field by field rather than from the ORM object, so a new column
  cannot leak by being added.
- **The seeded admin is protected by the `is_seed_user` column**, not by
  comparing the email, so the guard rails are enforced on data. It cannot be
  deleted, demoted or deactivated -- all three, because any one of them alone
  leaves a lockout hole.
- **Email is never updatable.** An identical value is accepted (so a
  whole-object PUT works); a different one is a 400.
- The seed user is created both by the migration and by the app's lifespan,
  because tests build the schema with `create_tables()` rather than by
  migrating. `ensure_seed_user` is idempotent and never overwrites an existing
  row -- resetting its password from the environment on every boot would undo
  a change made in the UI.

---

## 10a. Candle history (the price chart)

`src/market/services/dhan_charts_client.py` is a second Dhan client, and the
same rules apply to it as to `dhan_option_chain_client.py`: MARKET DATA ONLY, a
closed set of endpoints checked in `_post`, and no relative path that could
resolve against Dhan's base URL onto a trading surface. It may reach exactly
`/charts/historical` and `/charts/intraday`, whose full URLs are the two entries
added to `ALLOWED_DHAN_URLS`.

`src/market/services/candle_service.py` owns everything above the wire:

- **`TIMEFRAMES` is the single source of truth**, and the UI reads it from
  `GET /market/timeframes` rather than hardcoding buttons. A timeframe the
  backend would reject can therefore never appear as a button. Dhan serves
  1/5/15/25/60-minute and daily bars; `3m`, `30m`, `4h`, `1W` and `1M` are
  aggregated here, and `native` on each entry says which is which.
- **Intraday buckets are anchored to each IST day's first bar**, not to midnight
  and not to a bar count. Midnight anchoring puts a 4h boundary at 08:00, an
  hour before MCX opens; count anchoring silently mis-groups everything after a
  gap in the data. Both mistakes produce a chart that is wrong and plausible,
  which is why `tests/test_candles.py` asserts each one directly.
- **Responses are cached per (security id, timeframe, mode)** for 15 s intraday
  and 5 min daily, and `FeedManager.reconfigure()` invalidates the cache for the
  same reason it clears the book: a synthetic bar must not survive into live
  mode.
- **`synthetic_candles.py` is gated by the same flag as `synthetic_feed.py`.**
  Bars are deterministic (seeded from the security id and the bar's own epoch,
  so a refresh does not reshuffle history), anchored so the newest close equals
  the live price, and generated only on the configured session grid. The
  response carries `synthetic: true` and the chart labels itself. Nothing falls
  back to it: no credentials and no synthetic flag raises `CandlesUnavailable`,
  which the controller turns into a 503.
- The instrument's Dhan `instrument` enum (`FUTCOM` / `OPTFUT`) is read from the
  book's registered contract metadata, never guessed — sending the wrong one is
  a silently empty chart.

---

## 10b. Chart trading

`src/chart_trading/` turns a click on the FUTURE's chart into an OPTION
position. Everything in it exists to keep that translation honest.

- **Buy buys the ATM call, Sell buys the ATM put, and neither writes one.** The
  service always submits `OrderSide.BUY`. A code path that sells to open would
  turn a capped premium into unlimited risk; there is a test asserting every
  order the feature places is a BUY.
- **The two buttons net**, so at most one chart trade is open per underlying and
  `get_open_for_underlying` can assume it. The opposite click closes rather than
  opening a second leg.
- **The ATM strike is resolved against the FUTURE's live price**, not the option
  chain's own underlying figure, because the future is what the user is reading
  levels off.
- **Bracket levels are prices of the FUTURE**, stored on the chart trade, and
  they start null -- a trade opens unarmed. `validate_levels` refuses a level on
  the wrong side of the market: armed there it would fire on the tick that
  armed it, which looks like the chart closing the trade by itself.
- **`bracket_monitor` is server-side and off the tick path**, its own 250 ms
  task with its own session, modelled on `OrderMatcher`. A stop that lived in
  the browser would die with the tab. It never acts on a `None` price.
- **Exits go through `submit_paper_order` like everything else**, so they cross
  the spread and can partially fill. Do not give the chart a privileged fill
  path; see section 4.
- The rupee figures attached to a level are **estimates from the option's
  current delta**, not limits. Keep them labelled as estimates wherever they
  surface.

---

## 10c. System health

`src/health/` aggregates what this process already knows about itself. It owns
no tables, ships no migration, and has a `database/` folder holding exactly one
read-only repository (the `alembic_version` row) because repositories own
queries even when a feature owns no schema.

- **Almost nothing here is new measurement.** Nine components already exposed a
  `status()` or `stats()` dict and only `FeedManager.status()` was reachable
  from the API. Prefer joining an existing counter over adding one, and reach
  singletons (`get_feed_manager`, `get_order_matcher`, `get_bracket_monitor`,
  `get_candle_service`) **inside** the function, per section 1.
- **`task_inspector` is the answer to "threads".** There is no thread pool; the
  concurrency is the eight named asyncio tasks. Membership of
  `TASK_DESCRIPTIONS` is what identifies an application task — not a name
  shape. Starlette names its per-request tasks after the coroutine, so a
  `Task-N` filter let framework plumbing into the table as an "unexpected"
  application task. Anything not in the set is counted as transient.
- **Every task row is judged against what should be running**
  (`expected_task_names`), because a task that died silently is the failure
  this table exists to catch. Adding a named task means adding it to
  `TASK_DESCRIPTIONS` *and* to `expected_task_names`, or the page will report
  it as unexpected forever.
- **Counter epochs are a claim about code, and they are pinned by a test.**
  `reconfigure()` replaces the feed client (its counters reset) but does not
  replace the greeks poller — constructed once in `FeedManager.__init__`, only
  stopped and restarted — nor the broadcaster. Verified against a live
  reconfigure;
  `test_the_counter_epochs_match_what_reconfigure_actually_rebuilds` asserts it.
- **`Broadcaster.clients()` lives on the broadcaster**, not in the health
  package. Reaching into `_clients` from outside would make the queue part of
  the public surface. `ClientConnection` uses `__slots__` — adding a field
  means adding the slot.
- **The synthetic feed gets a different card, not a fake one.** In synthetic
  mode there is no upstream socket, so `hasUpstreamConnection` is false and the
  inactivity headroom is `None` rather than a comfortable-looking number
  against a cliff that does not apply. Same honesty rule as the chart.
- **Secrets: see the root `CLAUDE.md`.** Add a field, add its assertion in
  `tests/test_no_secrets_in_logs.py` in the same change.

`src/log_buffer.py` is the recent-warnings buffer. It is installed from
`configure_logging()` and nowhere else (section 8), holds records **already
formatted through `RedactingFormatter`** because an HTTP endpoint reads them,
counts every record it has seen (not just the ones still held) so a wrapped
buffer cannot under-report, and never raises out of `emit`.

---

## 10d. Daily bars

`src/daily_bars/` owns one table and two jobs: a bootstrap import and a nightly
refresh. Root `CLAUDE.md` section 4 explains why a stored daily bar is not the
tick accumulation that rule forbids; read that before touching this.

- **Nothing here reads the feed.** Every bar is fetched whole from
  `/charts/historical` through `DhanChartsClient` -- the same market-data client
  the chart uses, with the same closed endpoint set. If you find yourself
  writing `daily_bars` rows from `MarketBook`, stop.
- **The series is keyed on `(exchange_segment, symbol, bar_date)`, not on the
  security id.** Dhan's ids move -- the universe file's own ids for HEG and
  HFCL no longer match the master -- and keying a price series on one would
  silently start a second copy of a symbol's history the day its id changed.
  The id is stored as data, because it is what the next fetch is made with.
- **An existing bar is UPDATED, not skipped.** Dhan restates a series after a
  corporate action; a split that failed to propagate would leave a price
  history that no longer describes the instrument.
- **The refresh paces itself.** `DhanChartsClient` throttles per security id,
  which throttles a loop over 500 different ids not at all.
  `daily_bars.request_delay_seconds` is the real limiter, and 0.6 s is the
  interval the research project measured as safe. Lowering it chooses a ban
  over a slow overnight job.
- **The refresh reports progress, and a watcher cannot kill it.**
  `refresh_strategy(on_progress=...)` is called before each symbol, so the name
  on screen is the one being fetched rather than the one just finished -- which
  is what makes a stalled run point at the symbol that stalled it. The callback
  is wrapped in a try/except that swallows everything: a stalled progress bar
  is a nuisance, a refresh that died because something watching it raised would
  leave the rotation deciding on stale bars. The scheduler holds the result in
  `self.progress` and clears it on `_begin` and `_idle`, so a finished job never
  leaves a bar on the screen and a new job never inherits the last one's.
- **One symbol failing does not end the run.** 499 refreshed and one error is a
  reportable state; an exception that abandons the other 498 is not.
- **No credentials means no bars.** There is no synthetic fallback on this path
  at all -- unlike the chart, where a fabricated bar is labelled and looked at.
  Here it would go straight into a trading decision.
- **The trading calendar is the regime index's own bar dates.** A date NSE
  published a bar for is a date NSE traded, which is why there is no holiday
  list to maintain and no second source to go stale.
- **The importer AND the refresh commit per symbol.** The ten-year panel is
  1.09 million rows; held in one session's identity map that is enough pending
  ORM objects to take the process down. It also means a failure half-way
  through leaves the symbols already done. The refresh has a third reason,
  found on 2026-09-18 during the first full live run: on SQLite one
  twelve-minute transaction blocks every other writer, and the swing stop
  monitor -- which polls every second -- logged "database is locked" once a
  second for the whole run. Committing per symbol turns that into five hundred
  short locks nothing notices.
- **A flat zero-volume bar is a session that never happened, and is dropped.**
  `is_phantom_bar`: all four prices identical with volume exactly 0. NSE never
  produces that -- a suspended stock has no bar, a circuit-locked one still has
  volume -- but a vendor filling a calendar will, and the research project's
  spliced panel does for 395 equities on an NSE holiday. It matters far more
  than the row count suggests because **every lookback in the strategy is
  positional**: `shift(5)`, `shift(126)`, the ATR EWM and the ADV20 window all
  count rows rather than days, so one phantom row shifts every one of them.
  The guard is on both the import and the refresh paths. A MISSING volume is
  not a zero and is not the artefact.

---

## 10e. The swing rotation

`src/swing/` is the strategy-specific half of
`conf/strategies/nse-swing-momentum.yaml`. Its own `README.md` has the full
list; these are the ones that will bite a future change.

- **`StrategyDefinition.module_config` is how a strategy's rules stay
  configuration.** Blocks of a strategy YAML the framework does not interpret
  travel to the module unchanged and are validated there. P1-P19 are numbers
  that change what is traded, so root `CLAUDE.md` section 7 applies, but
  teaching the registry what a momentum lookback is would make the framework
  specific to one module.
- **`SwingParameters` has no defaults.** A missing key names the file and the
  key. A value that quietly fell back to something reasonable is a silently
  different strategy, and the whole point of the YAML is that its numbers can
  be checked against the specification's own table.
- **Indicators are float, money is `Decimal`,** and the conversion happens at
  the boundary in `ranking_service`. Do not "improve" the indicators to
  `Decimal`: the parity test compares them against numbers pandas produced.
- **The breadth ramp divides by a configured SPAN.** `0.65 - 0.35` is
  `0.30000000000000004` and the backtest divides by the literal `0.30`. The
  difference is a whole slot at reachable breadth values.
- **A symbol with no bar on the session is skipped, never forward-filled.**
  The session is the regime index's own date. Without this a delisted name
  stays rankable on its last close for ever -- JBCHEPHARM did exactly that in a
  first draft, two months after it stopped trading.
- **Undefined is not zero.** Indicators return `None` where undefined,
  `slots_for_breadth(None)` is `None`, and a gate that cannot be evaluated
  reports that rather than defaulting either way.
- **`tests/test_swing_parity.py` is the guarantee that not adding pandas was
  safe.** Golden values come from the backtest's own expressions. Two pandas
  behaviours are load-bearing and asserted first: `max(axis=1)` skips the NaNs
  in the first true range, and `ewm(adjust=False)` seeds on the first
  observation.

- **One decision, one place.** `rebalance_planner.effective_gate()` resolves
  the baseline-vs-V3b disagreement (does P17 liquidate? how many slots? which
  momentum floor?) and the nightly runner, the rebalance and the page all read
  it. `SwingRunner._decide` runs the rebalance's OWN `plan_sells`, so tonight's
  record and tomorrow's orders cannot disagree about which holdings are due out.
  The buy half is deliberately not shared: at 18:15 there is no live price to
  size against.

- **`session_snapshot()`, not `snapshot()`, is what the strategy acts on.** V3b
  applies its own momentum floor while the gate is off, and a floor is applied
  during RANKING rather than afterwards -- a name filtered out by P6 never gets
  a rank, so filtering the finished list would leave every rank wrong.

- **The rebalance COMMITS at defined points**, which is unusual here and
  deliberate. Sells have to be visible to the balance before the buys are
  sized, and `FeedManager.resync()` opens its own session -- which on SQLite
  blocks against an outer transaction still holding uncommitted writes. A
  rebalance is a sequence of durable steps, not one atomic act: an order that
  filled has filled.

---

## 10f. The rotation's background work

Three things run on their own tasks, and all three follow `OrderMatcher`'s
shape: own task, own session, off the tick path, registered in
`TASK_DESCRIPTIONS` **and** `expected_task_names`, with a `status()` dict the
health page reads.

- **`swing_stop_monitor`** is a SIBLING of `bracket_monitor`, not a second
  shape bolted onto it. That one watches a FUTURE and closes an OPTION from a
  `chart_trades` row; this one watches a stock and closes the same stock from a
  `swing_stops` row. It only acts while the market is open (`market_clock`),
  never on a `None` price, and never inside the Closing Auction Session for an
  F&O-eligible name -- there it records the trigger and the exit waits for the
  next open.
- **`swing_scheduler`** is the only clock in this application. Idempotence is
  the JOURNAL's (`sessions_completed_on`), not a flag's; the in-memory attempt
  clock exists only so a failing job does not report one problem two thousand
  times before midnight.
- **`src/strategies/services/market_clock.py`** answers three different
  questions from one strategy's `market_hours`: is the market open (the stop
  monitor), is ANY enabled market open (the feed watchdog), and could an order
  sent now fill CONTINUOUSLY (the auction rule). There is deliberately no
  holiday list -- the session calendar is the regime index's own bar dates, and
  being wrong on a holiday costs one idle poll rather than a wrong decision.

**Arming is framework state, not a swing concept.** `StrategyDefinition.
automation` is parsed from the YAML's `automation` block (a FRAMEWORK key since
2026-09-18, so `module_section("automation")` returns nothing), the live flag
is a `feature_toggles` row under the `AUTOMATION` scope, and
`registry.is_armed()` is armed AND enabled. A module that declares no
`automation` block is discretionary: `set_armed` raises for it, a stored row
for it is ignored on load, and no arming control is offered.

**`automation.max_lots_per_order` exists because a "lot" means two things.**
One MCX CRUDEOIL lot is 100 barrels; an NSE delivery "lot" is one share. A
single global `trading.max_lots_per_order` cannot be right for both, and
routing the share count through `quantity_override` to dodge the check would
leave `order.lots` disagreeing with `order.quantity / order.lot_size` on every
equity order.

**`submit_paper_order(reason=...)` writes WHY onto the PLACED event**, not onto
a column. An order placed by a person has no reason beyond their clicking; one
placed by a strategy has a specific one, and the journal carries the same
sentence at full length tied to the order through `SwingDecision.order_id`. A
`strategy_reason` column on `orders` would be a third copy, null for every
human order.

---

## 10f-bis. The rotation's own health tab

`src/swing/services/swing_health_service.py` assembles `GET /api/swing/health`.
It is the per-strategy sibling of `src/health/`, and the split is the point:
that package answers "what is this process doing" and is genuinely
process-wide; this one answers "is this strategy healthy". Putting it in
`src/health/` would make that package import `src/swing/` for its own payload.

- **Almost nothing here is new measurement**, the same rule `src/health/`
  follows. Every figure is an existing `status()` dict, a repository read or an
  `asyncio.all_tasks()` walk, and nothing is measured on the tick path. Prefer
  joining an existing counter to adding one.
- **It is the one ADMIN-ONLY read on the swing router.** Every other endpoint
  there is open to a `ROLE_USER`, because the page is a journal and a journal
  is history. This one serves live machinery state and WARNING+ log records,
  which can carry anything a developer interpolated -- the same exposure
  `/api/healthcheck/problems` is gated for. `require_admin` on the route is
  what refuses it; hiding the tab is presentation (section 10).
- **It reuses the rule rather than restating it.**
  `execution_service.staleness_reason()` was lifted to module scope so the tab
  shows the operator the SAME sentence the rebalance would refuse with;
  `DailyBarRefreshService.targets_for` is what decides which universe symbols
  are "resolved"; `BalanceService` is the only place money is computed;
  `describe_policies` / `describe_settings` already report the default beside
  what is in force. A second description of any of these would drift.
- **`task_inspector.feed_flags()` exists so the two health surfaces cannot
  disagree** about whether a task should be alive. `health_service` derives
  `feed_running` from it too.
- **It must render with the strategy OFF and with nothing ever traded.**
  Nothing here computes a ranking, resolves a subscription or requires a
  portfolio; every block degrades to an absence WITH A REASON. `undefined is
  not zero` applies to every count, and `tests/test_swing_health.py` asserts
  the specific ones that have been got wrong elsewhere on this page.
- **Timestamps go through `to_ist()` on the way out.** Stored values are naive
  UTC (section 3), and a naive ISO string is parsed by the browser as LOCAL --
  so the tab reported a switch as moved five and a half hours before it was,
  beside an IST clock reading correctly. Caught on the rendered page on
  2026-09-18 and pinned by
  `test_every_timestamp_carries_its_offset`.
- **Three things it cannot know are STATED on the payload, not implied away:**
  there is no audit history (only the current value with its last author), the
  scheduler's run list is process-scoped, and `SwingStopMonitor`'s counters are
  kept once per process across every strategy. Each carries its own note
  constant, and each note is asserted.
- **Secrets: see the root `CLAUDE.md`.** This endpoint serves log records, so
  it has its own assertion in `tests/test_no_secrets_in_logs.py` rather than a
  line in the swing list -- the record path is the new exposure and is
  exercised with a registered runtime secret.

---

## 10g. Performance metrics

`src/reports/services/metrics_service.py` is generic and sits beside
`pnl_service`, which it does not duplicate: realised P&L is still replayed from
fills by that module, and the two agree by construction (section 6).

- **A trade is a closed `positions` ROW, not a fill.** A position row is one
  round trip with its own entry time, exit time and charges. Counting fills
  would turn a three-part exit into three trades and make the win rate, the
  average hold and the concentration all wrong.
- **Cash flows are not returns.** CAGR, maximum drawdown and MAR are WITHHELD
  when money moved into or out of the book after the first trade, with the
  reason attached -- a deposit is not a gain and a withdrawal is not a
  drawdown. Same rule `BalanceService` applies to an unmarked position.
- **Undefined is not zero or infinity.** Profit factor with no losses is
  `None`, not a very large number; concentration on a negative summed return is
  `None`, because a share of a loss is not a figure anyone can read.
- The **exit mix** is the one swing-specific piece and lives in
  `swing_service.exit_mix`, counted from `swing_stops.exit_kind` rather than by
  reading English out of a reason string.

---

## 11. Tests

- `pytest.ini` sets `asyncio_mode = auto` — async tests need no decorator.
- `tests/conftest.py` sets env vars **before** anything imports the app, and
  points `DATABASE_URL` at a temp SQLite file. Fixtures: `db_session`,
  `api_client`, `auth_client`, `sample_master_csv`.
- **The strategy registry is reset to "everything enabled" for every test**, by
  the autouse `strategy_state_baseline` fixture, and restored afterwards. It
  is a process-wide singleton, so a test that flips a toggle otherwise leaks it
  into every test after — and, less obviously, `submit_paper_order` refuses a
  new order for a switched-off strategy, so every test that trades a contract
  used to depend on that contract's module happening to ship enabled. When the
  defaults changed on 2026-09-18, 71 tests failed for a reason that had nothing
  to do with what they were testing. A test whose subject IS a shipped default
  reads `enabled_by_default` off the definition; a test whose subject is what
  switching a strategy off *does* sets the state it wants.
- **The suite runs in PARALLEL by default** (`addopts = -n auto --dist
  loadfile` in `pytest.ini`): 6m29s serially, under a minute across 12 workers.
  The cost was never one slow test -- the 25 slowest accounted for 40s of 389s
  -- it was per-test setup repeated 875 times, which is what parallelism fixes
  and micro-optimising a fixture would not.

  **It is safe because of the PID naming below, which already existed.** Each
  xdist worker is its own process, so it gets its own temp database, its own
  log directory and its own copies of the process-wide singletons.

  **`loadfile`, not `load`.** loadfile gives a whole FILE to one worker and so
  preserves the order tests run in within it; `load` scatters individual tests
  across workers. This suite has at least one test that depends on process
  state a previous test in the same file left behind, and `load` measured 53.8s
  against loadfile's 56.9s. Five percent does not buy back a class of failure
  that is intermittent and reads like a real bug. Pass `-n0` to run serially
  when debugging or using a breakpoint.
- **The temp database and log directory are named after the process id**, and
  `pytest_sessionfinish` removes them. They used to have fixed names, so two
  concurrent runs shared one SQLite file — the second run's schema setup tears
  down tables the first is mid-query on. That does not merely produce wrong
  results: it twice took the interpreter down with a fatal error whose
  traceback points into SQLAlchemy and says nothing about the real cause, and
  it reads exactly like a broken baseline. If you ever see a suite "failure"
  that disappears on a second run, check whether something else was running
  pytest at the same time.
- Name tests for the behaviour, not the method
  (`test_a_resting_buy_does_not_fill_when_the_ask_merely_touches_it`).
- When a test fails, check whether the *expectation* is wrong before changing
  the code — that happened twice here, both times the test was wrong.
