# Handoff — a deployment name on every alert, and Postgres as the real database

Read `CLAUDE.md`, `backend/CLAUDE.md` and `frontend/CLAUDE.md` first. They cover
the no-real-orders constraint, the secrets rules, module layering, the schema
and migration rules (`backend/CLAUDE.md` §3), and the alert design
(§7a and the root file's §4). `README.md`'s **Alerts** and **Connections page**
sections cover what exists today. Do not re-derive any of that.

Two pieces of work. The first is small and self-contained. The second is not,
and most of this document is about it.

---

## Baseline — do NOT spend time re-running these

```bash
cd backend
.venv/bin/python -m pytest tests/ -q      # 996 passed, 1 skipped, ~80s (parallel)
CONFIG_PATH=conf .venv/bin/alembic heads  # f2a8b1c94d37 (head), 15 migrations
cd ../frontend && npm run build           # passes, ~2s
```

Python 3.11.13. `pytest.ini` runs 12 workers; **never run two `pytest`
processes at once** (`backend/CLAUDE.md` §11).

State of the running installation on 2026-09-19: NSE Swing Momentum enabled and
ARMED, MCX crude off, one portfolio (`Swing Momentum`, ₹10,00,000), zero orders
and zero positions. **The Dhan access token expired at 10:18 IST on 2026-09-19
and automatic renewal has never succeeded** — every attempt returned HTTP 400.
There were uncommitted changes to `dhan_token_client.py` and
`token_refresh_service.py` in the working tree addressing that, written by
somebody else; check `git status` before you start and do not assume they are
yours.

---

## Part 1 — `DEPLOYMENT_NAME` on every alert

### What the owner asked for

> "we need to give our project deployment name … so send this deployment name
> in all alerts, so we know from which env this alert is coming"

`DEPLOYMENT_NAME` is already in `.env` and is currently `local`.

### What exists

- `config_utils` reads YAML with `${VAR}` / `${VAR:default}` substitution
  (`conf/default-config.yaml`). Nothing reads `DEPLOYMENT_NAME` yet.
- `src/connections/services/alert_dispatcher.py::render()` turns one outbox row
  into the text that is sent. **Every delivered alert goes through it**, which
  makes it the one place a deployment name has to be added for "all alerts" to
  be true.
- `src/connections/services/telegram_service.py::test_message_body()` already
  identifies the installation, but by `socket.gethostname()` via
  `installation_name()`. That predates `DEPLOYMENT_NAME` and should now prefer
  it, falling back to the hostname when it is unset.

### How to do it

Add `app.deployment_name: "${DEPLOYMENT_NAME:local}"` to
`conf/default-config.yaml` and read it through `config_utils` — never
`os.environ` (`backend/CLAUDE.md` §1).

Put it in `render()`. Resist adding it at each `record_*` call site: that is
four places that can each forget, and the whole reason the outbox has one
renderer is so the wire format is decided once.

**Make it unmissable but not shouty.** A prefix on the title reads better on a
phone than a trailing line — `[prod] Bought TCS` tells you where you are before
you have read anything else. Agree the exact shape with the owner if you like,
but do not bury it at the bottom.

### Two things worth deciding, not assuming

1. **Should the deployment name also be a COLUMN on `alerts`?** It is not
   needed for the ask, and the ask is about the message. But see Part 2: if
   local and production share one Supabase database, both write to the same
   `alerts` table, and then "which deployment raised this" is a query, not a
   string in a body. The prefix satisfies the ask today; the column is what the
   Alerts tab would need to stay honest on a shared database. Raise it, and let
   the owner choose.
2. **`installation_name()` has two callers' worth of meaning.** The hostname
   answers "which machine"; the deployment name answers "which environment".
   They are not the same thing and a staging copy running on the same laptop
   has the same hostname. Report both in the test message rather than replacing
   one with the other.

### Done when

- Every alert delivered to Telegram carries the deployment name, asserted in
  `tests/test_alerts.py` against `AlertDispatcher.render()`.
- The "Send test message" body carries it too.
- An unset `DEPLOYMENT_NAME` falls back to something honest rather than
  printing an empty bracket.
- `README.md`'s Alerts section says it.

---

## Part 2 — Postgres, for real

### What the owner asked for

> "our platform works well on local sqlite database now but we can't run in
> production with sqlite … make sure our project runs fully properly on
> postgres … since we will be using public schema … make sure to have some
> prefix for our platform tables, so we never collide with other systems …
> The current state we have in our sqlite database, I want the same state in
> the postgres database and from now on for local development we should use
> this postgres database only … test each and everything by making api call,
> all data types are compatible"

`POSTGRES_DB_CONNECTION_STRING` is already in `.env`.

---

### STOP — a blocker verified before this handoff was written

**The configured connection string cannot work from this machine, and it is
not a network problem at your end.**

```
POSTGRES_DB_CONNECTION_STRING → postgresql://…@db.qcdphsuldffpxsjsralk.supabase.co:5432/…
```

That host **does not resolve at all**:

| Host | DNS |
|---|---|
| `qcdphsuldffpxsjsralk.supabase.co` (project API) | resolves — the project ref is valid |
| `db.qcdphsuldffpxsjsralk.supabase.co` (direct database) | **NXDOMAIN** |
| `aws-0-ap-south-1.pooler.supabase.com` | resolves |
| `aws-0-us-east-1.pooler.supabase.com` | resolves |

Supabase stopped giving new projects a directly-resolvable `db.<ref>` host;
connections go through the **pooler**, whose hostname, port and username differ
(`postgres.<project-ref>` rather than `postgres`). **Do not start by debugging
SQLAlchemy.** Ask the owner to copy the connection string from the Supabase
dashboard → *Connect* → and to say which of the two they want:

- **Session mode (port 5432)** — a real Postgres session per connection.
  Everything works, including prepared statements. Best for this application,
  which is one process with a small pool.
- **Transaction mode (port 6543)** — pgbouncer. Cheaper at scale and **breaks
  asyncpg's prepared-statement cache**. If it is used, the engine MUST be built
  with `statement_cache_size=0` and
  `prepared_statement_cache_size=0`, or you get
  `DuplicatePreparedStatementError` intermittently and under load only. This is
  the single most common way this integration fails, and it fails late.

Recommend session mode and say why. Get this settled before writing code.

---

### What already exists, verified by reading the code

| Thing | Where | State |
|---|---|---|
| Async engine, one per process | `src/database/connection.py::get_async_engine` | Branches on `url.startswith("sqlite")`; the else-branch already sets `pool_pre_ping`, `pool_recycle`, `pool_size=10`, `max_overflow=20` |
| URL redaction | `redact_database_url` | Handles `user:pass@host` and `?password=`. The health page and the log share it — do not write a second one |
| Money | `src/database/base.py::Money` | `NUMERIC(18,4)`, with a **SQLite-only** `asdecimal=False` branch. Postgres already takes the correct path and returns `Decimal` |
| Timestamps | `PreciseDateTime` | `DateTime(timezone=False)` with a MySQL fsp=6 variant. On Postgres this is `TIMESTAMP WITHOUT TIME ZONE`, microsecond precision, which is what naive-UTC storage wants. **No change needed** |
| Migrations | `alembic/env.py` | `render_as_batch` is already conditional on `dialect.name == "sqlite"`, and the URL already comes from the app config. Both were written for exactly this |
| Case-insensitive search | `.ilike()` in `trade_note_repository`, `order_repository` | SQLAlchemy renders `ILIKE` on Postgres and `lower() LIKE lower()` on SQLite. **Portable already** |
| Upserts | `daily_bar_repository`, `instrument_repository` | Deliberately select-then-write rather than a dialect-specific upsert. Portable, and the comments say why |
| Schema version | `src/health/database/db_operations/schema_repository.py` | Raw `SELECT version_num FROM alembic_version` |

### What does NOT exist

- **No Postgres driver.** `requirements.txt` has `aiosqlite` and no `asyncpg`.
- **No table prefix.** 22 tables, all unprefixed.
- **Nothing reads `POSTGRES_DB_CONNECTION_STRING`.** `database.url` reads
  `DATABASE_URL`.
- **Postgres has never been executed against.** `backend/CLAUDE.md` §3 says
  MySQL is "DDL-compile-verified only"; Postgres is not even that yet. After
  this work, that sentence needs rewriting — Postgres becomes the backend that
  is actually exercised, and MySQL becomes the one that never was.

---

### The table prefix

22 tables: `alerts`, `app_settings`, `cash_ledger`, `chart_trades`,
`connection_settings`, `connections`, `daily_bars`, `feature_toggles`,
`instruments`, `order_charges`, `order_events`, `order_fills`, `orders`,
`portfolio_strategies`, `portfolios`, `positions`, `strategy_settings`,
`swing_decisions`, `swing_sessions`, `swing_stops`, `trade_notes`, `users`.

**Everything that names a table by string has to move with them**, and the
compiler will not tell you:

- `__tablename__` on 22 models.
- **`ForeignKey("<table>.id")` — 6 distinct targets** (`connections`, `orders`,
  `portfolios`, `positions`, `swing_sessions`, `users`), used in many more
  places. These are strings resolved at mapper-configuration time; a missed one
  fails at import, which is at least loud.
- **`ForeignKeyConstraint([...], ["<table>.id"])` inside migrations** —
  `orders.id`, `portfolios.id`, `swing_sessions.id`.
- **Raw SQL inside migrations.** `sa.text()` naming `app_settings`,
  `connections`, `order_charges`, `orders`, `portfolios`. The Connections
  migration's data move (`e1f4a90c72b6`) is the big one.
- **`alembic_version` itself.** Set `version_table="<prefix>alembic_version"` in
  `alembic/env.py`'s `context.configure(...)`, in BOTH `run_migrations_online`
  and `run_migrations_offline`, or you get an unprefixed table in `public`
  — which is exactly the collision the prefix exists to prevent.
- **`schema_repository.py`'s raw `SELECT … FROM alembic_version`.**

**Suggested shape.** Put the prefix in one place —
`src/database/base.py` — and have models and foreign keys derive from it, so a
future change is one constant and not 22 files:

```python
TABLE_PREFIX = "dcpt_"          # whatever the owner prefers

def table(name: str) -> str:
    return f"{TABLE_PREFIX}{name}"

def fk(name: str, column: str = "id") -> str:
    return f"{table(name)}.{column}"
```

**Then assert it**, because a mechanical change across 22 models is exactly the
kind of thing that is 21/22 done:

```python
def test_every_table_carries_the_prefix():
    for name in Base.metadata.tables:
        assert name.startswith(TABLE_PREFIX), name

def test_every_foreign_key_points_at_a_prefixed_table():
    for table in Base.metadata.tables.values():
        for fk in table.foreign_keys:
            assert fk.target_fullname.startswith(TABLE_PREFIX), fk.target_fullname
```

**Two decisions to put to the owner:**

1. **The prefix itself.** `dcpt_` matches the logger namespace and the session
   cookie (`dcpt_session`) already used throughout. Recommend it.
2. **Rename in place, or create fresh?** The existing SQLite database has to be
   migrated anyway (below), so the simplest honest answer is: the Postgres
   database is created *already prefixed* by running the migrations against it,
   and the SQLite file is left alone as the source to copy FROM. A
   `rename_table` migration is only needed if an unprefixed Postgres database
   already exists — it does not.

**Identifier length is fine, and this was checked.** The longest index today is
`ix_chart_trades_portfolio_underlying_status` at 43 characters; with a 5-character
prefix it is 48, comfortably inside Postgres's 63-byte limit. Postgres
*silently truncates* longer identifiers, which would be a genuinely nasty way
to get two indexes with the same name, so re-check if a longer prefix is chosen.

---

### Moving the data

**Row counts in the live SQLite database (326 MB):**

| Table | Rows |
|---|---|
| `daily_bars` | **1,111,241** |
| `instruments` | 1,739 |
| `alerts` | 107 |
| `swing_decisions` | 12 |
| `connection_settings` | 6 |
| `swing_sessions` | 4 |
| `feature_toggles` | 4 |
| `connections` | 2 |
| `users`, `portfolios`, `portfolio_strategies`, `cash_ledger`, `app_settings` | 1 each |
| `orders`, `order_events`, `order_fills`, `order_charges`, `positions`, `swing_stops`, `trade_notes`, `chart_trades`, `strategy_settings` | 0 |

So it is **one big table and a rounding error**. Everything except `daily_bars`
is trivial. `daily_bars` over the network to Supabase is the whole job: do it
with `COPY` (asyncpg's `copy_records_to_table`) in batches, not 1.1 million
ORM inserts, and expect it to take minutes rather than seconds.

**Good news, verified before this handoff:** every one of the **84
length-bounded string columns** was checked against the live data and **no
stored value exceeds its declared `VARCHAR(n)`**. SQLite ignores those lengths
entirely, so a copy into Postgres is where they would first be enforced — and
there is nothing waiting to be rejected. Re-run that check if data changes
before you migrate.

**The trap that WILL bite: sequences.** Copying rows with explicit `id` values
does not advance Postgres's identity sequence, so the next ordinary insert
tries `id = 1` and fails with a unique violation. After the copy, for every
table:

```sql
SELECT setval(
  pg_get_serial_sequence('dcpt_orders', 'id'),
  COALESCE((SELECT MAX(id) FROM dcpt_orders), 0) + 1,
  false
);
```

Do it for all 22, in the migration script, and **assert it afterwards** by
inserting and rolling back — this failure appears the first time somebody
places an order, not during the copy.

**Booleans.** SQLite stores `0`/`1`; Postgres wants real booleans. If you go
through SQLAlchemy models the conversion is free; if you go through raw `COPY`
you must convert. `daily_bars` has none, which is why raw COPY is safe for the
big table.

**Suggest a script, not a migration.** `scripts/` already holds
`import_daily_bars.py` and `refresh_daily_bars.py`; a
`scripts/migrate_sqlite_to_postgres.py` belongs beside them. It should be
re-runnable, report progress per table (the bar refresh already sets the
pattern), and refuse to run against a non-empty target unless explicitly told
to.

---

### Testing: the decision that needs making first

The owner said *"for local development we should use this postgres database
only"* and *"test each and everything by making api call"*.

`tests/conftest.py` currently pins `DATABASE_URL` to a **per-process temp
SQLite file** and drops/creates the schema for every test. That design is load
bearing (`backend/CLAUDE.md` §11): 996 tests across 12 workers in 80 seconds,
each with its own database.

Pointing that at one shared Supabase database would be wrong three times over:
twelve workers would fight over one schema, every run would be slow, and a test
run would destroy the development data. **Recommend to the owner:**

- **Tests stay on SQLite** for the fast inner loop, AND
- **the suite gains a Postgres mode** — `DATABASE_URL` honoured when set, so
  the same tests can be run against a real Postgres before a release. Local
  Postgres in Docker is the natural target for that; Supabase is the
  development database, not a test fixture.
- **The application (local dev) moves to Postgres**, which is what was asked
  for.

If the owner wants the suite itself on Supabase, say plainly what it costs
(serial execution, minutes not seconds, and the dev data gone) and let them
choose.

**"Test everything by making an API call" is the right instruction and should
be taken literally.** A schema that migrates cleanly proves very little; the
types only prove out when a real payload round-trips. At minimum, exercise
against Postgres, signed in, and check the response bodies rather than just the
status codes:

- `GET /api/healthcheck/system` — the schema version row, the pool stats (which
  on Postgres are real for the first time; the health card currently says
  "SQLite uses a NullPool, nothing to report")
- `GET /api/instruments/...`, a refresh, and the option chain
- `POST /api/orders` → fills → `GET /api/positions` → `GET /api/reports/pnl`.
  **This is the important one**: `Money` is `NUMERIC(18,4)` and the whole P&L
  agreement between the live book and the replayed reports (`backend/CLAUDE.md`
  §6) depends on exact `Decimal` quantisation. A float sneaking in shows up
  here as a paisa, not as an exception.
- `GET /api/portfolios` — cash, blocked margin, available, equity
- `GET /api/swing/status`, `/history`, `/health`, `/performance`
- `GET /api/connections`, `/connections/alerts/catalogue` — `BigInteger`
  (`users.telegram_user_id`), `Text`, and the nullable-boolean
  `connections.last_check_ok`, which must still come back as `None` / `True` /
  `False` and not collapse
- `GET /api/notes?search=...` and `GET /api/orders?search=...` — the two
  `.ilike()` paths
- A daily-bar read for the swing ranking, which is where 1.1M rows and
  `Numeric` meet

---

### Traps, each of which is specific

- **The URL scheme must become `postgresql+asyncpg://`.** What is in `.env` is
  `postgresql://`, which SQLAlchemy resolves to psycopg2 — a *sync* driver —
  and `create_async_engine` will refuse it. Normalise it in
  `get_database_url()` rather than asking the owner to edit the string, and log
  what you normalised it to (redacted).
- **Decide precedence explicitly.** There are now two variables:
  `DATABASE_URL` and `POSTGRES_DB_CONNECTION_STRING`. Pick one rule, write it
  in `conf/default-config.yaml` next to the key, and make the health page show
  which one is in force. Two config sources for one value, with no stated
  precedence, is how the Settings-versus-`.env` bug happened
  (`backend/CLAUDE.md` §7).
- **`pool_size=10, max_overflow=20` is 30 connections.** Supabase's free tier
  allows far fewer, and the pooler has its own limit. This process is
  `workers=1` with a handful of background tasks; size the pool to something
  like 5/5 and say why. A pool that exhausts the server's connection limit
  fails as timeouts under load, not as an obvious error.
- **SSL.** Supabase requires it. asyncpg needs `ssl` in `connect_args` or
  `?sslmode=require` handled appropriately — asyncpg does **not** understand
  libpq's `sslmode` query parameter the way psycopg2 does.
- **Naive UTC must stay naive.** `src/core/time_utils` stores naive UTC and
  converts at the edges. `TIMESTAMP WITHOUT TIME ZONE` preserves that. Do not
  "improve" it to `timestamptz` — every `to_ist()` call site assumes naive, and
  the swing Health tab already shipped a bug from exactly this confusion
  (`backend/CLAUDE.md` §10f-bis).
- **`database is locked` disappears, and some comments become wrong.** Several
  design notes exist *because* SQLite serialises writers — the per-symbol
  commits in the bar refresh (`backend/CLAUDE.md` §10d), the rebalance's
  committing at defined points (§10e). The behaviour stays correct on
  Postgres; the *reasons* partly stop applying. Update the comments you
  invalidate, and do not remove the commits — they have independent reasons
  (durability, and `resync()` opening its own session).
- **`server.py` forces `workers=1` and must keep doing so.** Root `CLAUDE.md`
  §4: more workers means more upstream Dhan feed connections. Postgres removes
  the *database* objection to multiple workers; the feed objection is
  untouched, and it is the binding one.
- **The migration that moved the Dhan credentials (`e1f4a90c72b6`) does raw
  SQL with `INSERT … SELECT id …`.** It runs clean on SQLite. Re-read it
  against Postgres semantics before trusting it on a fresh database, and note
  that on a fresh Postgres it will find no `app_settings` rows to move and
  should no-op cleanly — which is its designed behaviour, but assert it.
- **Keep the SQLite file.** Do not delete `backend/data/paper_trading.db` when
  the cutover works. `backend/data/` already holds dated backups; make another
  before you start.

---

## Definition of done

**Part 1**

- Every alert delivered carries `DEPLOYMENT_NAME`, asserted in the test suite.
- The Telegram test message carries it alongside the hostname.
- An unset value degrades to something honest.

**Part 2**

- `asyncpg` in `requirements.txt`; the URL scheme normalised; SSL working.
- Every table prefixed, including `alembic_version`, with the two metadata
  assertions above so a missed one fails the build.
- `alembic upgrade head` runs clean on a fresh Postgres database, and
  `--autogenerate` is empty afterwards. `downgrade` back to base runs clean
  too — on Postgres this is a real test, where on SQLite batch mode hid a lot.
- Every row from the SQLite database present in Postgres, **with sequences
  reset**, proven by an insert that does not collide.
- The application runs against Postgres and the API surfaces above return
  correct values — money exact to the paisa, timestamps carrying `+05:30`,
  nullable booleans still three-state.
- The full suite green, and a documented way to run it against Postgres.
- `npm run build` passes.
- `README.md` and the `CLAUDE.md` files updated. In particular
  `backend/CLAUDE.md` §3's "MySQL is DDL-compile-verified only" is now
  misleading and must be rewritten: **Postgres is the backend that is actually
  exercised.**
- Anything unverified added to the README's "Known gaps" — including, if it is
  still true, that production has never run on this.
- Committed with `amrakshay@gmail.com` and pushed to `origin/main` over SSH. No
  JIRA prefix, no GitLab MR, no ADR — personal repo.

---

## Reference

| Thing | Where |
|---|---|
| Engine and pooling | `backend/src/database/connection.py` |
| Portable column types | `backend/src/database/base.py` |
| Migration environment | `backend/alembic/env.py` |
| The one migration with raw SQL and a data move | `backend/alembic/versions/e1f4a90c72b6_*.py` |
| Schema version reader | `backend/src/health/database/db_operations/schema_repository.py` |
| Alert rendering (Part 1's one place) | `backend/src/connections/services/alert_dispatcher.py` |
| Test database setup | `backend/tests/conftest.py` |
| Scripts that already do long, progress-reporting jobs | `backend/scripts/refresh_daily_bars.py` |
| Login | `trader@abc.com` / `APP_ADMIN_PASSWORD` from `.env` |
