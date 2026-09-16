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

- **Every rate lives in `conf/charges.yaml`** with a primary source URL, an
  as-of date and a confidence marker. Nothing is hardcoded in Python.
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
  UI value without touching a single call site. It runs in the lifespan **before
  the feed starts**, and again after each save.
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

## 10. Tests

- `pytest.ini` sets `asyncio_mode = auto` — async tests need no decorator.
- `tests/conftest.py` sets env vars **before** anything imports the app, and
  points `DATABASE_URL` at a temp SQLite file. Fixtures: `db_session`,
  `api_client`, `auth_client`, `sample_master_csv`.
- Name tests for the behaviour, not the method
  (`test_a_resting_buy_does_not_fill_when_the_ask_merely_touches_it`).
- When a test fails, check whether the *expectation* is wrong before changing
  the code — that happened twice here, both times the test was wrong.
