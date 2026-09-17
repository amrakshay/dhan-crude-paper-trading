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
| **Price chart** | Candlestick chart of the near-month future with a volume pane, at ten timeframes (1m to 1M), history from Dhan's read-only chart endpoints, newest bar updating live from the existing feed |
| **Chart trading** | One-click Buy/Sell on the futures chart, draggable stop-loss and take-profit lines, live P&L net of charges — trading futures levels while the book holds ATM options |
| **Option chain** | Full CE/PE ladder in the conventional Indian broker layout, with IV and greeks, OI change, ATM highlighting and click-to-trade |
| **Order entry** | Market and limit, from the chain or a standalone ticket, with estimated charges and net debit/credit shown **before** confirmation |
| **Fill simulation** | Orders cross the spread, walk the book level by level, and partially fill when depth runs out |
| **Positions** | Live MTM, per-position and aggregate P&L, full or partial close |
| **Order history** | Every state transition timestamped to the millisecond, with fills and a complete charges breakdown |
| **P&L reports** | Realised and unrealised, by day / expiry / strike, gross vs net, charges by component, equity curve, CSV export |
| **Trade notes** | Free-text notes on completed trades — searchable, editable, visible from history and reports |
| **Settings** | Dhan credentials and feed mode editable in the UI, with a token validator and a live expiry countdown — **account admins only** |
| **System health** | One page answering "is this thing healthy and what is it doing right now?" — uptime, the eight named background tasks, the upstream feed against Dhan's 40 s drop cliff, every connected browser tab, Dhan API usage, token expiry and recent warnings — **account admins only** |
| **Users** | Real user accounts with two roles, add/edit/delete, per-account status, and a forced password change on first login |
| **Profile** | Your own details and password; email is read-only |

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

Open http://localhost:5173 and sign in as **`trader@abc.com`** with the
`APP_ADMIN_PASSWORD` you set in `.env`. That account is seeded by the migration
above. Then click **Refresh instruments** on the Live Price page to pull the
instrument master (~35 MB, cached for 12 hours).

> **Upgrading an existing checkout?** `APP_USERNAME` / `APP_PASSWORD` no longer
> authenticate anyone. Add `APP_ADMIN_PASSWORD` to your `.env` and run
> `alembic upgrade head`; if the variable is missing, the administrator is not
> created and the log says so on every start.

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
| `APP_ADMIN_PASSWORD` | *(none)* | Password for the seeded administrator `trader@abc.com`. Used **only** when that account does not yet exist; changing it later has no effect (change the password in the UI). **If blank, the account is not created and nobody can log in.** |
| `APP_JWT_SECRET` | *(none)* | Signs the session cookie. If blank, a random key is generated per process and **every restart logs you out**. Generate one with `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. |
| `APP_ENCRYPTION_KEY` | *(none)* | Encrypts the Dhan access token at rest. Falls back to `APP_JWT_SECRET`; if both are blank the app refuses to store a token. Prefer setting it, so rotating the session secret does not destroy the stored token. |
| `DHAN_CLIENT_ID` | *(none)* | Dhan client id — market data only. |
| `DHAN_ACCESS_TOKEN` | *(none)* | Dhan access token (JWT) — market data only. |
| `DHAN_SYNTHETIC_FEED` | `true` | **`true` generates fake prices locally.** Set to `false` for real MCX data. See below. |
| `DATABASE_URL` | SQLite file | Connection URL. See *Switching to MySQL*. |
| `LOG_LEVEL` | `INFO` | Logging level for both the app and access loggers. Overrides `conf/logging-config.ini`. |
| `LOG_DIR` | `./logs` | Where `app.log` and `access.log` are written (relative to `backend/`). |
| `STATIC_DIR` | `../frontend/dist` | Built frontend to serve in single-port mode. Set to `""` to disable. |

## Users and roles

Authentication is against a real `users` table. `APP_USERNAME` / `APP_PASSWORD`
were **removed**, not left as a fallback — two authentication paths is exactly
the kind of thing that rots, and an env-var backdoor outliving the table it was
meant to replace is worse than no backdoor at all.

**Login is by email.** Users have a first name, last name, email, password,
role and status.

| Role | Can |
|---|---|
| `ROLE_ACCOUNT_ADMIN` | Everything, including Settings and adding/editing/deleting users |
| `ROLE_USER` | Everything except Settings. Sees the user list but cannot change it; can edit their own profile and password |

**Passwords are hashed, not encrypted.** bcrypt with a per-user salt. The
requirement as first written said "stored encrypted", but encryption is
reversible: anyone holding the key recovers every password, and people reuse
passwords across systems. A password only ever needs comparing, never
recovering. (Contrast the Dhan access token, which *is* Fernet-encrypted —
correctly, because it has to be replayed to Dhan verbatim.) Passwords are
capped at 72 bytes because bcrypt silently ignores anything beyond that, which
would let two different long passwords authenticate each other.

**Which pages a role sees is a JSON file**, `backend/conf/role-pages.json`,
served at `GET /api/users/role-pages` and used for both the sidebar and the
client-side routes. **Hiding a nav item is not access control** — the API
enforces the same rules independently, and
`backend/tests/test_users_api.py` asserts a `ROLE_USER` calling the settings
endpoints directly gets a 403.

### Guard rails

All enforced server-side, not by disabling a button:

* **The seeded administrator (`trader@abc.com`) cannot be deleted, demoted or
  deactivated.** Blocking only the delete would still allow demoting an
  undeletable row to `ROLE_USER`, leaving the application with no administrator
  and no way back.
* **Nobody can delete their own account**, whatever their role.
* **The last active administrator** cannot be deleted, demoted or deactivated.
* **Email can never be changed.** It is the login identifier and the thing
  every audit line refers to; changing it would silently reassign that history.
  Resending an unchanged email is fine, so a whole-object PUT still works.
* **An inactive user cannot log in**, and an existing session for a user who
  becomes inactive **stops working on their next request**, not at token
  expiry. The same applies to a deleted user and to a demoted one: the session
  dependency re-reads the user from the database on every request rather than
  trusting the role baked into the token.
* **"Must change password on first login"** is a hard gate: such a user gets a
  403 from every endpoint except the ones needed to change it, and the UI shows
  the change-password screen instead of the app.

---

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

## Price chart

The Live Price page carries a candlestick chart of the near-month CRUDEOIL
future, below the stat tiles, with a volume pane beneath the candles.
Timeframes: `1m 3m 5m 15m 30m 1h 4h 1D 1W 1M`.

It opens on the most recent ~180 bars rather than the whole series — scroll or
zoom out for the rest. **The chart pauses when there is nothing to draw**: a
stale feed freezes the forming bar rather than extending it, as does a closed
exchange when the prices are real, and a chip in the chart header says which.
The synthetic feed is deliberately exempt from the market-hours rule — it exists
to exercise the stack outside MCX hours, so the chart keeps moving there.
Volume bars whose volume the server did not report are omitted rather than drawn
as zero.

**Where the bars come from.** This application persists no price history at all
— `MarketBook` holds one current row per instrument in memory and nothing writes
ticks to the database — so history is fetched from Dhan's two read-only chart
endpoints and only the newest bar is updated from the live WebSocket. The chart
opens no second socket and polls nothing.

**Native versus derived timeframes.** Dhan's intraday endpoint serves 1, 5, 15,
25 and 60 minute candles, and daily candles come from a separate endpoint.
Everything else is aggregated by the backend from the nearest finer native
interval, and the chart says so underneath itself rather than presenting every
series as equally direct:

| Timeframe | Source |
|---|---|
| `1m` `5m` `15m` `1h` | Dhan intraday, passed through |
| `1D` | Dhan daily, passed through |
| `3m` | aggregated from 1m |
| `30m` | aggregated from 15m |
| `4h` | aggregated from 1h |
| `1W` `1M` | aggregated from daily |

Dhan's 25-minute interval is deliberately not offered. Intraday buckets are
anchored to each IST trading day's first bar, not to midnight — anchoring to
midnight would put a 4h boundary at 08:00, an hour before MCX opens, and leave
the session's first bucket one hour long. Weekly bars are anchored to Monday and
monthly bars to the 1st, in IST.

**Two entries were added to the safety suite's URL allowlist.**
`backend/tests/test_no_real_orders.py` fails the build on any Dhan URL it does
not recognise, so charting required adding exactly these two:

```
https://api.dhan.co/v2/charts/historical
https://api.dhan.co/v2/charts/intraday
```

Both are read-only market data, the same category as `/optionchain`. The
matching logic, the forbidden-endpoint list and the `dhanhq` ban were **not**
touched — only the explicit allowlist grew. The endpoint constants live in
`backend/src/market/services/dhan_charts_client.py` and nowhere else, and that
module carries the same "MARKET DATA ONLY" docstring as the option chain client.

**Without credentials** the chart follows the same rule as the rest of the app:
it never silently invents prices. With `DHAN_SYNTHETIC_FEED=true` it draws
locally generated bars, labelled `SYNTHETIC — generated locally` on the chart
itself as well as by the page banner; with the synthetic feed off and no
credentials it reports the error instead of drawing anything.

The chart is [TradingView Lightweight Charts](https://github.com/tradingview/lightweight-charts)
(Apache-2.0, pinned to 5.2.1). Its licence requires attribution and a link to
tradingview.com: the built-in `attributionLogo` is left enabled and the notice
is repeated under the chart. See `frontend/NOTICE` — do not remove either.

---

## Trading from the chart

The price chart is also the order ticket. One click on **Buy** or **Sell**, one
lot, no confirmation dialog.

**A Buy buys the nearest ATM call; a Sell buys the nearest ATM put.** Neither
ever *writes* an option, so the worst case is always the premium paid. You are
reading and trading levels on the FUTURE while the book holds OPTIONS — that
translation is the whole feature, and `src/chart_trading/` is where it lives.

**The two buttons net.** A Sell while long a call closes the call rather than
stacking a put on top of it; click Sell again once flat and it buys the put.
So one click can never leave you in a straddle you did not intend, and at most
one chart trade is open per contract.

```
long 1 CE  --Sell-->  flat        (closes the call)
flat       --Sell-->  long 1 PE   (opens the put)
long 1 PE  --Buy -->  flat        (closes the put)
```

**The cost is on screen before the click, not after it.** There is no confirm
step, so the row above each button continuously shows the contract that button
would buy, its premium, the estimated charges and the net debit — priced
through the same fill simulation the order ticket uses, and warning when an
order would only partially fill. That is how one-click entry still satisfies the
rule that the cost of an order is visible before it is sent.

**Stop-loss and take-profit lines are levels of the FUTURE.** Nothing is armed
until you place a line: `+ SL` / `+ TP` drop one at a default distance and you
drag its handle from there. `bracket_monitor` watches the future server-side on
its own 250 ms task and sells the option at market when a level is crossed — a
stop that lived in the browser would die with the tab. A level dropped on the
wrong side of the market is refused rather than armed, because it would fire on
the tick that armed it.

> **A stop does not bound your loss in rupees.** What the option is worth when
> the future reaches your level depends on delta, time decay and implied
> volatility. The rupee figure on each line is a first-order estimate from the
> option's *current* delta and is labelled `est.` — it is not a limit.

Live P&L sits on the chart, net of every charge accrued so far. The exit's own
charges are not in it: they are only known once the exit fills.

Configuration lives under `chart_trading:` in `conf/default-config.yaml`
(`enabled`, `default_lots`, `bracket_interval_ms`).

---

## Running it on one port

By default the app runs on two ports in development: Vite on `:5173` for the UI,
the backend on `:8000` for the API, with Vite proxying `/api` and `/ws` so the
browser sees one origin. **That setup is unchanged and is still the development
default** — it is what gives you hot reload.

The backend can also serve the built frontend itself, so one port is the whole
application and no Node process is left running:

```bash
./run-single-port.sh          # builds the frontend, migrates, then serves
./run-single-port.sh --skip-build   # reuse an existing frontend/dist
```

or by hand:

```bash
cd frontend && npm run build
cd ../backend && CONFIG_PATH=conf .venv/bin/python server.py
```

Then <http://localhost:8000> serves both the UI and the API.

| | Two-port (dev) | Single-port |
|---|---|---|
| UI | Vite `:5173` | FastAPI `:8000` |
| API | `:8000` via the Vite proxy | `:8000` directly |
| Hot reload | yes | no — rebuild to see changes |
| Node running | yes | no |

**How it works.** `server.static_dir` (default `../frontend/dist`, resolved
against `backend/`) is mounted by `src/static_serving.py` *after* every API and
WebSocket router, because FastAPI matches routes in registration order. The
catch-all deliberately refuses to answer for `/api/*` and `/ws/*`, so a typo'd
endpoint still returns a JSON 404 rather than 200 and an HTML page. Hashed
assets under `/assets` are served `immutable` for a year; `index.html` is
`no-store`, or a rebuild would be masked by the shell the browser cached.

**If the frontend is not built**, the app logs how to build it and serves the
API only — it does not fail. Set `server.static_dir: ""` to turn single-port
serving off entirely.

`server.py` still forces `workers=1` in both modes: more than one worker means
more than one upstream Dhan connection.

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

## System health

`/health` (account admins only) answers "is this healthy and what is it doing
right now?" without SSH-ing in to read `app.log`. One endpoint,
`GET /api/healthcheck/system`, aggregates what nine components already knew
about themselves and were never asked.

**There are no threads to monitor.** This is one process, one uvicorn worker,
one asyncio event loop — `server.py` forces `workers=1` because more workers
means more upstream Dhan connections and a desynchronised book. There is no
thread pool. The honest equivalent is the eight **named asyncio tasks**, and
the page lists them with what each one is for, its interval, its progress
counter and its last error:

```
dhan-feed          dhan-feed-watchdog   synthetic-feed     broadcaster
greeks-poller      feed-resync          order-matcher      bracket-monitor
```

Each row is judged against what *should* be running for the current
configuration, because the failure worth catching is a task that died
silently — a dead `greeks-poller` does not announce itself, the greeks just
stop moving and look merely stale. Framework tasks (per-request, per-socket)
are counted, not listed.

**The two kinds of WebSocket are two separate cards**, because they fail
differently and "WebSocket: connected" hides both failures:

* **The upstream Dhan feed** — exactly one, and the scarce resource. Its
  last-message age is shown as *headroom against the 40 s cliff Dhan drops us
  at*, not as a bare integer; a reconnect count that climbs while the state
  reads healthy is flapping; subscription and connection budgets are shown
  against their ceilings. **In synthetic mode the card says "no upstream
  connection" rather than showing a healthy socket that does not exist.**
* **The browser fan-out sockets** — one row per open tab: client id, who opened
  it, how long it has been open, whether it took the whole book or narrowed to
  a few ids, depth on or off, and **how many times it fell behind and had its
  queue reset**. That last number is the point: a slow tab silently dropping
  data was previously visible only in the disconnect log line.

The greeks poller and the charts client are plain HTTPS, so they get their own
card — a connected feed says nothing about whether greeks are arriving.

**Counters are labelled with their epoch, because they do not all reset
together.** `FeedManager.reconfigure()` (which a settings save triggers)
*replaces* the feed client, so `framesReceived`, `packetsApplied` and
`reconnects` reset. It does **not** replace the greeks poller — that is
constructed once and merely stopped and restarted — nor the broadcaster, which
is deliberately kept alive so open tabs are not stranded. Verified against a
live reconfigure; `tests/test_health_api.py` pins it.

**It never returns a secret.** The Dhan access token is a mask plus its decoded
expiry, the database URL goes through the same redactor the log uses, and the
recent-warnings list is read from an in-memory buffer that has already been
through `RedactingFormatter`. `tests/test_no_secrets_in_logs.py` asserts each
of these against the endpoint directly.

**Recent warnings and errors** come from a bounded in-memory ring buffer (the
last 250 WARNING+ records), attached in `configure_logging()`. It is
process-scoped and empty after a restart — the page says so, and `app.log`
remains the durable record.

The page polls every 5 s with a visible "as of" age and a manual Refresh.
`/api/healthcheck/system` and `/api/healthcheck/problems` are both in
`LogRequestsMiddleware.IGNORED_PATHS`, so polling does not fill the access log
the page reports on.

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

448 tests. Two files are safety suites rather than feature tests:

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
run-single-port.sh  build the frontend, then serve UI + API from one port
backend/
  CLAUDE.md     backend conventions: layering, async SQLAlchemy traps, charges
  conf/         default-config.yaml (app), charges.yaml (rate card),
                logging-config.ini (handlers, rotation, format),
                role-pages.json (which pages each role sees)
  logs/         app.log + access.log, rotating, gitignored
  alembic/      migrations
  src/
    core/       base repository, pagination, time helpers
    database/   engine, session, Money/PreciseDateTime column types
    auth/ users/ instruments/ market/ charges/ orders/ positions/ reports/
    notes/      each: routes/ controllers/ services/ api_schemas/ database/
  tests/
frontend/
  CLAUDE.md     frontend conventions: theme port, feed context, UI honesty rules
  NOTICE        TradingView attribution required by the chart library's licence
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

* **The system health page's process metrics need `psutil`.** Memory, CPU, open
  file descriptors and the OS thread count come from it; if it is ever missing
  the card says the figures are unavailable rather than guessing. The CPU
  percentage is averaged over the interval since the previous poll, so the
  first reading after a page load is shown as "measuring…" rather than as 0%.
* **The health page cannot see other processes.** "1 of 5 connections used" is
  scoped to *this* process, and says so. A second instance started against the
  same Dhan credentials takes another of the five slots invisibly.
* **No database pool statistics.** SQLite uses a `NullPool`, so there is
  nothing to report and the page says that rather than implying otherwise. The
  MySQL branch does configure `pool_size=10, max_overflow=20`, but MySQL has
  never been executed against a live server here, so those numbers are reported
  as *configured*, never as observed.
* **The charge rate card's per-rate as-of dates are YAML comments.** The health
  page reports the rate-card `version` but cannot say "this rate is N days
  stale", because the dates are not structured fields. Promoting them to real
  keys in `conf/charges.yaml` would fix it; parsing comments would not.
* **The health page has never been seen against a live Dhan token.** The
  inactivity headroom, the reconnect count, the real feed's `mode`/`requestCode`
  and the option chain client's request counters were all exercised with the
  synthetic feed and with a forced status, not against `wss://api-feed.dhan.co`.
  The arithmetic is unit-tested; the values it reads are not.
* **This application's own API has no request metrics.** No counters, no
  latency histogram, no error rate — the access log is the only record, and the
  health page says so rather than showing an empty chart. The middleware does
  log a warning above 1000 ms.
* **The default administrator's password is never forced to rotate.** The
  seeded `trader@abc.com` account is created with `must_change_password = false`,
  so whatever `APP_ADMIN_PASSWORD` was set to stands until someone changes it in
  the UI. That is a deliberate decision for a local single-user tool, recorded
  here rather than left implicit — a default credential that is never forced to
  rotate is a known risk. Change it on the Profile page.
* **MySQL is not execution-verified** (see above). The `users` table is
  DDL-compile-verified on MySQL like the rest of the schema — every VARCHAR has
  a length, `role` is quoted as a reserved word, timestamps are `DATETIME(6)` —
  but no migration has been run against a live MySQL server.
* **There is no password reset flow.** An administrator can set someone's
  password for them (which forces a change at their next sign-in), but there is
  no self-service "forgot password" — it would need email delivery, which this
  tool has no business having.
* **The book is shared, by design.** Orders, positions and notes are not owned
  by a user: every account sees and trades the same book, and deleting a user
  leaves their trades in place. This is a deliberate decision, not an
  oversight — the tool models one trading account that several people may look
  at, rather than one account each. Per-user books would be a schema change
  across four tables and a different product.
* **Nothing about Dhan's chart endpoints has been verified against the live
  API.** No Dhan token existed when the price chart was built, so the request
  fields, the `YYYY-MM-DD` / `YYYY-MM-DD HH:MM:SS` date formats, the parallel-array
  response shape, the non-inclusive `toDate`, the 90-day intraday cap and the
  native interval set (`1, 5, 15, 25, 60` — note 25, not 30) all come from
  <https://dhanhq.co/docs/v2/historical-data/> read on 2026-09-16 and from
  nothing else. The first run against a real token is the first real test of
  `backend/src/market/services/dhan_charts_client.py`. Dhan publishes no rate
  limit for these endpoints either; the client self-throttles to one request per
  second per series and caches responses, both of which are guesses.
* **Synthetic candles are per-timeframe, not mutually consistent.** In synthetic
  mode each timeframe's bars are generated independently, so the synthetic 1h
  series is not exactly the aggregate of the synthetic 5m series. The synthetic
  feed's tick volume is not calibrated against the synthetic history's volume
  either, so live bars can stand a couple of times taller than historical ones. Bars are
  deterministic per instrument and anchored so the newest close equals the live
  price, but they are fake and labelled as such. Real Dhan data has no such
  problem.
* **The volume pane's units are whatever Dhan returns.** The feed's volume for
  CRUDEOIL is barrels, and the chart endpoints are assumed to agree, but with no
  token that has never been checked — if the pane's numbers disagree with the
  Volume tile by a factor of the lot size, this is why.
* **The forming bar's volume is derived from a cumulative counter.** The feed
  publishes the session's running volume, not a per-bar figure, so the live
  bar's volume is the growth in that counter since the bar opened. It is exact
  while the page stays open and the counter only rises; a counter that goes
  backwards is treated as a new session. Reloading replaces it with the
  server's own figure.
* **A chart exit can partially fill, and then the remainder rests.**
  `reject_market_order_on_insufficient_depth` is false, so a triggered stop
  takes whatever the visible book offers and leaves the rest as an open order —
  the honest simulation, but it means "stopped out" does not always mean
  "flat". The chart trade is marked closed either way; check Positions and
  Order History if the numbers look odd.
* **The chart's live bar reflects the last traded price only.** The forming
  candle is updated from the feed's LTP, so its high and low are the extremes
  this browser has *seen* since the bar opened, not the true extremes of every
  trade in that bucket. Reloading replaces it with the server's own bar. The bar
  stops updating entirely when the feed is stale, rather than painting a flat
  price into new buckets.
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
