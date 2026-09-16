# MCX Crude Oil Options — Paper Trading

A single-user paper-trading platform for MCX CRUDEOIL options, built on live
DhanHQ v2 market data.

**This tool trades paper rupees only.** It contains no order-placement code path
and never contacts a broker's trading, funds or holdings endpoints. The Dhan
credentials it uses are for market data only: the WebSocket feed, the option
chain endpoint and the instrument master CSV. This is enforced by
`backend/tests/test_no_real_orders.py`, which fails the build if a trading
endpoint, a broker order operation, or the `dhanhq` SDK ever appears in the
source.

---

## What it does

| | |
|---|---|
| **Live price** | CRUDEOIL near-month future — LTP, OHLC, volume, OI, 5-level depth, with connection health and last-tick age always on screen |
| **Option chain** | Full CE/PE ladder in the conventional Indian broker layout, with IV and greeks, OI change, ATM highlighting and click-to-trade |
| **Order entry** | Market and limit, from the chain or a standalone ticket, with estimated charges and net debit/credit shown **before** confirmation |
| **Fill simulation** | Orders cross the spread, walk the book level by level, and partially fill when depth runs out |
| **Positions** | Live MTM, per-position and aggregate P&L, full or partial close |
| **Order history** | Every state transition timestamped to the millisecond, with fills and a complete charges breakdown |
| **P&L reports** | Realised and unrealised, by day / expiry / strike, gross vs net, charges by component, equity curve, CSV export |
| **Trade notes** | Free-text notes on completed trades — searchable, editable, visible from history and reports |
| **Settings** | Dhan credentials and feed mode editable in the UI, with a token validator and a live expiry countdown |

---

## Setup

Requirements: Python 3.11+, Node 18+.

```bash
git clone <this repo> && cd dhan-crude-paper-trading
cp .env.example .env          # then edit it — see the table below
```

**Backend**

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
CONFIG_PATH=conf .venv/bin/alembic upgrade head
CONFIG_PATH=conf .venv/bin/python server.py        # http://127.0.0.1:8000
```

**Frontend** (separate terminal)

```bash
cd frontend
npm install
npm run dev                                         # http://localhost:5173
```

Open http://localhost:5173 and sign in with the `APP_USERNAME` / `APP_PASSWORD`
you set in `.env`. Then click **Refresh instruments** on the Live Price page to
pull the instrument master (~35 MB, cached for 12 hours).

The Vite dev server proxies `/api` and `/ws` to the backend, so the browser sees
one origin and the session cookie works on the WebSocket handshake.

---

## Environment variables

All live in `.env` at the project root. `conf/default-config.yaml` reads them via
`${VAR}` substitution — put secrets here, not in the YAML.

**The three `DHAN_*` variables are only a fallback.** Anything saved on the
Settings page is stored in the database and takes priority — see *Settings* below.

| Variable | Default | Purpose |
|---|---|---|
| `APP_USERNAME` | `trader` | The single login username. There is no user table. |
| `APP_PASSWORD` | *(none)* | The single login password. **Login is rejected until this is set.** |
| `APP_JWT_SECRET` | *(none)* | Signs the session cookie. If blank, a random key is generated per process and **every restart logs you out**. Generate one with `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. |
| `APP_ENCRYPTION_KEY` | *(none)* | Encrypts the Dhan access token at rest. Falls back to `APP_JWT_SECRET`; if both are blank the app refuses to store a token. Prefer setting it, so rotating the session secret does not destroy the stored token. |
| `DHAN_CLIENT_ID` | *(none)* | Dhan client id — market data only. |
| `DHAN_ACCESS_TOKEN` | *(none)* | Dhan access token (JWT) — market data only. |
| `DHAN_SYNTHETIC_FEED` | `true` | **`true` generates fake prices locally.** Set to `false` for real MCX data. See below. |
| `DATABASE_URL` | SQLite file | Connection URL. See *Switching to MySQL*. |
| `LOG_LEVEL` | `INFO` | Logging level for both the app and access loggers. Overrides `conf/logging-config.ini`. |
| `LOG_DIR` | `./logs` | Where `app.log` and `access.log` are written (relative to `backend/`). |

## Settings page

Dhan credentials and the feed mode can be set in the UI at **Settings**, which
avoids editing `.env` and restarting for a token that expires daily.

**Precedence: database (UI) over `.env`.** Stored settings are overlaid onto the
in-memory config at startup and after each save, so every existing config reader
picks them up. The page shows where each value came from (`saved here` /
`from .env` / `not set`).

- **Client ID** — stored as plain text.
- **Access token** — encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256,
  random IV), the key derived by HKDF-SHA256 from `APP_ENCRYPTION_KEY` (or
  `APP_JWT_SECRET`). It is **never returned to the browser** — responses carry
  only a mask and decoded metadata. Leave the field blank to keep the stored
  token when changing something else.
- **Synthetic feed** — the toggle. While it is on, both credentials are
  optional; turning it off requires them.
- **Validate token** — issues one real market-data request (the option chain
  expiry list, already on the market-data allowlist) to confirm the credentials
  work, without saving them. Expired tokens and a client ID that disagrees with
  the token's own `dhanClientId` claim are caught locally first, with no network
  call.
- **Expiry countdown** — Dhan access tokens are JWTs, so the real `exp` claim is
  decoded and counted down live, with warnings under two hours and on expiry.
  No guessing "24 hours from whenever it was pasted".

Saving restarts the market feed in place so changes take effect immediately;
connected browser tabs keep their WebSocket.

### The synthetic feed

`DHAN_SYNTHETIC_FEED=true` is the default so the stack runs with no credentials
and outside MCX hours (09:00–23:30 IST). It opens **no upstream connection** and
generates prices from a random walk, pricing the chain with Black-76 so puts and
calls stay consistent with each other and with the future.

It is impossible to mistake for real data: the connection state reads
`SYNTHETIC`, and every screen carries a permanent banner. Nothing silently falls
back to it — if credentials are missing and the flag is `false`, the feed
reports an error rather than inventing prices.

To go live: set `DHAN_CLIENT_ID` and `DHAN_ACCESS_TOKEN`, set
`DHAN_SYNTHETIC_FEED=false`, and restart.

---

## Logging

Two loggers, three handlers: `app.log` and `access.log` under `LOG_DIR`
(default `backend/logs/`, gitignored), plus stdout. Both files rotate at 10 MB
with 3 backups.

```
2026-09-16 19:00:36 [INFO] order_service.py:_apply_fills:353 [dcpt.orders.service] : Order a45372… filled 100 at average 999.4580 across 2 level(s): 42@999.4000(L1), 58@999.5000(L2)
```

The `filename:funcName:lineno` field is the point of the format — a log line is
a jump target. Access lines go to `access.log` only:

```
2026-09-16 19:00:36 - [2f6d4bdc8744] "POST /api/orders" 201 40.695 ms
```

**Configuration.** `backend/conf/logging-config.ini`, loaded with
`logging.config.fileConfig` and found via `CONFIG_PATH` like the YAML. Drop a
`local-logging-config.ini` beside it to replace it wholesale (ini files are not
deep-mergeable the way the YAML is). Levels from `logging.level` /
`logging.access_log_level` in `default-config.yaml`, or `LOG_LEVEL`, are
applied on top of whatever the ini sets.

**Correlation.** Every request gets an id, returned as `X-Request-Id` and
stamped on its access line; an inbound `X-Request-Id` is honoured. Every
order-related line carries the `client_order_id`, so `grep <id> app.log`
reconstructs a fill end to end — the levels walked, the quantity taken at each,
the slippage, the charges and every status transition.

**`LOG_LEVEL=DEBUG` is meant to be left on.** Measured at **0.27 MB/hour** with
the synthetic feed, broadcaster, greeks poller and order matcher all running —
about 37 hours per 10 MB file, 150 hours across the rotation. Nothing logs on
the tick path (`feed_protocol` → `MarketBook.apply_packet`); tick volume is
reported instead as aggregate counters on the broadcaster's own 10-second
interval, alongside an edge-triggered warning when the book goes stale.

**Secrets never reach the log.** Enforced three ways:
`tests/test_no_secrets_in_logs.py` runs the real login, settings and order
flows with sentinel credentials and greps the captured output; the same file
AST-scans every `logger.*()` call in `src/` for secret-named arguments (with
known-bad and known-good controls, so it cannot pass vacuously); and
`src/log_redaction.py` scrubs registered secrets out of the formatted line —
including out of formatted tracebacks — as a last resort.

Migrations log through the same configuration, so `alembic upgrade head` and
Alembic's own `Running upgrade …` lines land in `app.log`.

---

## Switching SQLite → MySQL

Change one line in `.env`:

```bash
# DATABASE_URL=sqlite+aiosqlite:///./data/paper_trading.db
DATABASE_URL=mysql+aiomysql://user:password@127.0.0.1:3306/crude_paper?charset=utf8mb4
```

Then create the database and run the migrations against it:

```bash
mysql -u root -p -e "CREATE DATABASE crude_paper CHARACTER SET utf8mb4;"
cd backend && CONFIG_PATH=conf .venv/bin/alembic upgrade head
```

No code changes are needed — every query goes through SQLAlchemy, and the schema
avoids SQLite-only constructs:

* every `VARCHAR` has an explicit length (MySQL requires it for indexed columns);
* money is `NUMERIC(18,4)` everywhere, via a `Money` type that hands Python exact
  `Decimal`s on both backends;
* order/fill/event timestamps use `DATETIME(6)` on MySQL, because plain MySQL
  `DATETIME` truncates to whole seconds and would destroy the millisecond
  precision order history depends on;
* `created_at` / `updated_at` are set in Python rather than by server defaults,
  since the SQLite and MySQL spellings of `CURRENT_TIMESTAMP` defaults differ;
* `alembic/env.py` enables batch mode on SQLite so future `ALTER`s work there too.

**Verification status:** the migration has been run and rolled back cleanly on
SQLite, and the full schema DDL compiles without error against the MySQL dialect
(7 tables, 42 indexes). It has **not** been executed against a live MySQL server
— no MySQL server was available in the build environment. Run
`alembic upgrade head` against your instance before trusting it.

---

## Updating charge rates

Every rate lives in **`backend/conf/charges.yaml`**. No rate is hardcoded in
Python. Each one carries a primary source URL, an as-of date and a confidence
marker in a comment beside it.

Edit the file, then either restart or reload at runtime:

```bash
curl -b cookies.txt -X POST http://127.0.0.1:8000/api/charges/rates/reload
```

Orders record the `rates_version` they were charged under, so an old order can
still be explained after rates change.

### Current rates (researched 2026-09-16)

| Charge | Rate | Base | Side | Confidence |
|---|---|---|---|---|
| CTT | 0.05% | option premium | SELL | CONFIRMED |
| CTT on exercise | 0.0001% | settlement price | BUYER | CONFIRMED |
| MCX exchange txn | 0.0418% | premium turnover | both | CONFIRMED |
| SEBI turnover fee | ₹10/crore | premium turnover | both | rate CONFIRMED |
| Stamp duty | 0.003% | premium turnover | BUY | CONFIRMED |
| GST | 18% | brokerage + exchange + SEBI | both | **UNCONFIRMED** vs primary tax law |
| Brokerage | ₹20/order (flat) | — | both | broker commercial rate |

Notes worth knowing before you trust a number:

* **Commodity options attract CTT, not STT.** The 0.125% figure in the same
  statutory table belongs to *options in goods*, a different taxable transaction,
  and must never be applied to CRUDEOIL OPTFUT.
* **The exchange charge is 0.0418%, not 0.053%.** MCX circular MCX/F&A/631/2024
  (24 Sep 2024, effective 1 Oct 2024) sets ₹41.80 per lakh of premium turnover.
  No MCX circular between 2018 and Sep 2026 contains 0.053%. The rate is no
  longer slabbed.
* **GST's 18% and its base are corroborated by every broker checked and by a live
  broker calculator, but no primary CBIC notification was obtained.** It is
  marked UNCONFIRMED in the rate card. Treat a formal audit citation as a TODO.
* **CRUDEOIL lot size (100 barrels) comes from config, not from Dhan.** Dhan's
  instrument master reports `LOT_SIZE=1.0` for every MCX row (verified: all 158
  FUTCOM and 15,844 OPTFUT rows), while NSE rows in the same file carry real
  values. If that number is wrong, every turnover and charge is wrong with it.
  It is set in `contract_specs` in `conf/default-config.yaml`.

### Validation

`backend/tests/test_charges_worked_examples.py` reproduces three worked examples
from Zerodha's published brokerage calculator (run live 2026-09-16):

| Example | This engine | Zerodha | Gap |
|---|---|---|---|
| BUY 1 lot @ 100 | ₹28.84 | ₹28.54 | ₹0.30 — Zerodha rounds stamp duty to whole rupees |
| SELL 1 lot @ 100 | ₹33.54 | ₹33.54 | exact match |
| Round trip 100 → 150 | ₹67.36 | ₹67.05 | ₹0.31 — stamp duty, plus a SEBI-fee rounding difference |

Both numbers are asserted in the tests and neither is tuned to force agreement.
Setting `rounding.mode: broker_compatible` in `charges.yaml` reproduces the
broker's rounding exactly on the two single-order examples; the round trip then
still differs by ₹0.01, because Zerodha rounds the SEBI fee once over the
combined turnover while this engine charges **per order** — which is required,
since every order carries its own auditable charges row.

---

## Running tests

```bash
cd backend && .venv/bin/python -m pytest tests/ -q
```

339 tests. Two of them are safety suites rather than feature tests:

`tests/test_no_real_orders.py` parses every Python file's AST (comments and
docstrings exempt, everything else in scope) and fails if any Dhan URL outside
the market-data allowlist, any broker trading endpoint inside a Dhan client
module, any broker order operation, or any `dhanhq` import appears. It includes
a self-test that feeds the scanner known-bad code, so it cannot pass vacuously.

`tests/test_no_secrets_in_logs.py` exercises the real login, settings and
order flows with sentinel credentials and fails if one reaches the log, and
AST-scans every `logger.*()` call for secret-named arguments. It carries the
same style of self-test — seven known-bad snippets it must catch and seven
known-good ones it must not flag.

---

## Architecture notes

**One upstream connection.** Exactly one Dhan WebSocket connection exists
process-wide regardless of how many browser tabs are open; it fans out to
browsers over the app's own WebSocket. Opening one per client would burn Dhan's
5-connection cap and add latency. `server.py` forces `workers=1` for this reason
— more than one worker would mean more than one upstream connection and a
desynchronised book.

**The tick path is kept cheap.** Packets are parsed with `struct.unpack` into
plain tuples and merged into a dict-based in-memory book: no Pydantic, no ORM, no
database I/O on the hot path. Measured on this machine: **~2 µs** per packet
(parse + book update), and **~1.1 ms** to build and serialise a full
165-instrument broadcast. Browser pushes are coalesced on a fixed interval
(`fanout.broadcast_interval_ms`, default 100 ms) carrying only changed rows, so a
contract that ticked 40 times is sent once at its latest state.

**Greeks arrive separately.** The WebSocket feed carries no greeks, so IV and
greeks come from the option chain REST endpoint on a 3-second cadence (Dhan's
documented limit is one request per 3 s per underlying+expiry; expiries are
polled concurrently). That poller runs as its own task and merges into the same
book — it never blocks the tick path.

**Subscriptions follow the money.** The near future plus ATM ± 20 strikes across
the nearest two expiries (~165 instruments), re-centred automatically as the
underlying moves.

**Option and futures expiries differ.** CRUDEOIL September options expire
2026-09-17 while the September future expires 2026-09-21 — the chain and the
underlying roll on different dates. Each option expiry is mapped to the earliest
futures contract expiring on or after it, rather than by month name.

**Fill simulation is deliberately pessimistic**, because a simulator that
flatters the trader is worse than none:

* market orders pay the opposite side of the touch, never the LTP;
* large orders walk the book and get a worse average;
* only the quantity actually displayed is available — an order bigger than the
  visible five levels partially fills rather than inventing liquidity;
* `trading.slippage_ticks` (default 1) is applied adversely on top;
* **a resting limit order fills only when the market trades *through* its price,
  not when it merely touches it.** Touching your price means joining the back of
  a queue; assuming a fill there is the single most flattering error a paper
  simulator can make. `trading.limit_fill_requires_cross: false` restores the
  optimistic behaviour if you want it.

**Realised P&L is derived, not stored.** Reports replay every fill through the
same weighted-average rules the live position book uses, so the reports and the
positions screen can never disagree, and each realisation carries a timestamp,
strike and expiry — which is what makes the by-day/expiry/strike slices and the
equity curve possible.

### Layout

```
CLAUDE.md       working notes for Claude Code (safety rules, invariants, gotchas)
backend/
  CLAUDE.md     backend conventions: layering, async SQLAlchemy traps, charges
  conf/         default-config.yaml (app), charges.yaml (rate card),
                logging-config.ini (handlers, rotation, format)
  logs/         app.log + access.log, rotating, gitignored
  alembic/      migrations
  src/
    core/       base repository, pagination, time helpers
    database/   engine, session, Money/PreciseDateTime column types
    auth/ instruments/ market/ charges/ orders/ positions/ reports/ notes/
                each: routes/ controllers/ services/ api_schemas/ database/
  tests/
frontend/
  CLAUDE.md     frontend conventions: theme port, feed context, UI honesty rules
  src/
    theme/      Privacera palette ported to MUI v6 (light + dark)
    market/     one shared WebSocket context
    pages/ components/ api/
```

The three `CLAUDE.md` files are instructions for Claude Code sessions working on
this repo. They cover the no-real-orders constraint, the invariants that must not
be broken (single upstream connection, cheap tick path, pessimistic fills), and
the Dhan data quirks that produce silently wrong numbers if ignored. Worth a read
before making changes by hand too.

The frontend theme is ported from the Privacera SaaS portal's MUI v4 theme —
palette, typography, spacing and shape tokens re-expressed for a current
`createTheme`, with both light and dark. The portal's own MobX/webpack tooling is
not carried over.

---

## Known gaps

* **MySQL is not execution-verified** (see above).
* **The Quote/Full packet OHLC field order is unverified.** The layouts come from
  the official `dhanhq` SDK v2.2.0, which maps those four fields as open, close,
  high, low. That is reproduced faithfully, but has not been checked against a
  live feed, since no market-data credentials were available at build time. If
  highs and lows look transposed, see `FULL_IDX_OPEN`/`CLOSE`/`HIGH`/`LOW` in
  `backend/src/market/services/feed_protocol.py`.
* **Expiry/exercise is modelled in the charges engine but not automated.** The
  exercise-leg CTT is implemented and tested, but nothing automatically settles
  positions at expiry or handles devolvement into futures.
* **Charges assume per-order attribution.** MCXCCL aggregates a client's buy
  trades per contract per day before applying stamp duty, so neither rounding
  mode reproduces a real contract note exactly across a multi-trade day.
