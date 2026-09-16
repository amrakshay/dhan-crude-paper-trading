# CLAUDE.md — dhan-crude-paper-trading

Instructions for Claude Code working in this repository. Read this before
changing anything. `README.md` explains how to *run* the project; this file
explains how to *work on it* and what must not be broken.

---

## 1. The one rule that overrides everything

**This system must never be able to place a real order.**

It is a paper-trading tool. The Dhan credentials it holds are for MARKET DATA
ONLY: the WebSocket feed, the option chain endpoint, and the instrument master
CSV. Do not add, import, wrap, or scaffold a code path that reaches any broker
order-placement, order-modify, order-cancel, funds or holdings endpoint — not
even behind a flag, a comment, or a "for later" stub.

This is enforced by `backend/tests/test_no_real_orders.py`, which parses every
Python file's AST and fails the build on:

- any Dhan URL outside the market-data allowlist;
- any broker trading endpoint (`/orders`, `/funds`, `/holdings`, …) inside a
  Dhan client module;
- any broker order operation name anywhere (`place_order`, `cancel_order`,
  `get_fund_limits`, `convert_position`, …);
- any import of the `dhanhq` SDK, or its appearance in `requirements.txt`.

Comments and docstrings are exempt — documentation may name the endpoints it is
refusing to call. Everything else is in scope.

The URL allowlist has grown exactly once, on 2026-09-16, when the price chart
added `https://api.dhan.co/v2/charts/historical` and
`https://api.dhan.co/v2/charts/intraday`. That is the sanctioned way to extend
it: add the exact read-only market-data URL, keep the constants in a single
client module, and leave the matching logic alone. **Never loosen the pattern,
the endpoint list or the scanner** — a regex that accepts a family of URLs is
not the same guarantee as a set of five you can read in one glance.

**Consequences for naming.** Local order operations are deliberately called
`submit_paper_order` and `cancel_paper_order`, never `place_order` /
`cancel_order`. Those names are banned outright so a genuine broker call can
never be introduced under a name that blends into the local code. If the safety
test fails on a name you just wrote, rename yours — do not weaken the test.

**The `dhanhq` SDK is deliberately not a dependency.** It bundles order
placement. The market-data client here is written by hand from the wire
protocol, which makes the guarantee structural rather than merely tested. Do not
add it, even for convenience.

---

## 2. This is a personal repo — org rules do not apply

Despite the Privacera/Trust3 org-level `CLAUDE.md`, this project is personal
(`~/Workarea/local/`, GitHub `amrakshay/dhan-crude-paper-trading`). For work
here:

- **No JIRA key prefix** on commits or branches. There is no JIRA project.
- **GitHub, not GitLab.** No `glab`, no merge requests, no Bugbot comment.
- **No ADRs, no `ADR_REGISTRY.md`.** Do not create one.
- Commits use `amrakshay@gmail.com`, set in this repo's local git config. Do not
  change it to the Privacera work address.
- The remote is SSH (`git@github.com:amrakshay/...`) because HTTPS has no stored
  credential in this environment.

---

## 3. Commands

Always run backend commands from `backend/` with `CONFIG_PATH=conf`.

```bash
# backend
cd backend
.venv/bin/python -m pytest tests/ -q                      # full suite (448 tests)
.venv/bin/python -m pytest tests/test_no_real_orders.py -q # safety suite alone
.venv/bin/python -m pytest tests/test_no_secrets_in_logs.py -q  # no-secrets-in-logs suite
LOG_LEVEL=DEBUG CONFIG_PATH=conf .venv/bin/python server.py # verbose run; logs/ is gitignored
CONFIG_PATH=conf .venv/bin/alembic upgrade head            # migrate
CONFIG_PATH=conf .venv/bin/python server.py                # serve on :8000

# frontend
cd frontend
npm run dev                                                # Vite on :5173
npm run build
```

Login is `trader@abc.com` / `APP_ADMIN_PASSWORD`. `APP_USERNAME` /
`APP_PASSWORD` were removed and authenticate nobody — see `backend/CLAUDE.md`
§10 for the users module, its role gates and the seeded-admin guard rails.

Two-port dev (Vite :5173 + backend :8000) is the default. `./run-single-port.sh`
from the project root builds the frontend and serves UI + API from :8000 alone;
see `backend/CLAUDE.md` §9 for the route-ordering rules that make that safe.

`server.py` forces `workers=1`. Do not "fix" that — see §4.

Run the full suite before committing. The safety suite must pass.

---

## 4. Invariants that must not be broken

**One upstream feed connection per process.** Dhan allows 5 concurrent
connections per user. Exactly one `DhanFeedClient` exists process-wide
(`get_feed_manager()`), fanned out to browsers over the app's own WebSocket.
Never open a connection per browser client, and never run more than one uvicorn
worker — multiple workers mean multiple upstream connections and a
desynchronised book.

**The tick path stays cheap.** `feed_protocol` → `MarketBook` is the hot path:
`struct.unpack` into plain tuples, merged into dicts keyed the way the browser
wants them. No Pydantic, no ORM, no database I/O, no logging per tick. Anything
slow (persistence, greeks, order matching) belongs in a separate asyncio task.
Current cost: ~2 µs/packet, ~1.1 ms to build a full 165-instrument broadcast.

**Greeks never come from the feed.** The WebSocket carries no greeks. They come
from the option chain REST endpoint (`greeks_poller`, 3 s per expiry, expiries
polled concurrently) and merge via `MarketBook.merge_greeks` — a separate entry
point from `apply_packet` precisely so the two can never be confused.

**Price history is fetched, never accumulated.** Nothing writes ticks to the
database and nothing should start: candle history comes from Dhan's chart
endpoints (`dhan_charts_client` → `candle_service`), and the browser updates only
the newest bar from the WebSocket it already has. If you are tempted to persist
ticks to back a chart, read the "Price chart" section of `README.md` first — that
option was considered and rejected.

**Never invent prices.** If credentials are missing and
`DHAN_SYNTHETIC_FEED` is false, the feed reports an error. It must never
silently fall back to generated data. The synthetic feed is opt-in, reports its
state as `SYNTHETIC`, and the UI shows a permanent banner.

**Fill simulation stays pessimistic.** See `backend/CLAUDE.md` §4. Do not make
fills more generous without being asked.

**Money is `Decimal`, never `float`.** Timestamps are stored naive-UTC.

**Settings from the UI beat `.env`.** `SettingsService.apply_to_config()` overlays
stored settings onto the in-memory config at startup (before the feed starts)
and after every save. Do not read `DHAN_*` from `os.environ` directly — go
through `config_utils`, or you will see the `.env` fallback instead of what the
operator actually configured.

**The Dhan access token is encrypted at rest and never leaves the server.**
Responses carry a mask and decoded JWT metadata only. If you add a settings
field, decide explicitly whether it is a secret; secrets go in
`encrypted_value`, never `value`.

---

## 5. Data quirks that will bite you

These are verified facts about Dhan's data, not assumptions. Changing code that
depends on them without re-verifying will produce silently wrong numbers.

| Thing | Reality |
|---|---|
| Feed `RequestCode` | Differs per mode: Ticker 15, Quote 17, Depth 19, **Full 21**. Unsubscribe is `mode + 1`. Options use Full. |
| `LOT_SIZE` in the master | Reads `1.0` for **every** MCX row. Unusable. CRUDEOIL's 100-barrel lot comes from `contract_specs` in `conf/default-config.yaml`. |
| `TICK_SIZE` in the master | Published in **paise**. Divided by `contract_specs.tick_size_divisor` (100). |
| `SYMBOL_NAME` vs `DISPLAY_NAME` | `SYMBOL_NAME` is just "CRUDEOIL". `DISPLAY_NAME` identifies the contract. Use `DISPLAY_NAME`. |
| Option vs futures expiry | **Different dates.** Sep options expire 2026-09-17, the Sep future 2026-09-21. Map an option expiry to the earliest future expiring on or after it — never by month name. |
| Exchange transaction charge | 0.0418% (MCX circular MCX/F&A/631/2024). The 0.053% figure appears in no MCX circular. |
| CTT vs STT | Commodity options attract **CTT**, on the sell side of the premium. The 0.125% entry in the same statute is for *options in goods* — a different transaction. |

Unverified, flagged in code: the Quote/Full packet maps its four price fields as
open, close, high, low per the SDK. Never checked against a live feed.

---

## 6. Where things live

```
backend/conf/default-config.yaml   app config; ${ENV_VAR} substitution
backend/conf/charges.yaml          every charge rate, with source URL + as-of date
backend/src/<feature>/             routes/ controllers/ services/ api_schemas/ database/
backend/src/market/services/       feed protocol, book, feed client, broadcaster, greeks
backend/src/market/services/dhan_charts_client.py   candle history (market data only)
backend/src/market/services/candle_service.py       timeframes, aggregation, cache
frontend/src/components/PriceChart.jsx              the chart; see frontend/NOTICE
backend/tests/test_no_real_orders.py   the safety suite
frontend/src/theme/tokens.js       palette ported from the Privacera portal
frontend/src/market/               the single shared WebSocket context
```

See `backend/CLAUDE.md` and `frontend/CLAUDE.md` for per-side conventions.

---

## 7. Working style for this repo

- **No rate is hardcoded in Python.** Every charge rate lives in
  `conf/charges.yaml` with a primary source URL, an as-of date and a confidence
  marker. If you change a rate, update its source and date too.
- **Do not tune constants to make tests agree.** The charges engine has a known,
  asserted ₹0.01 divergence from Zerodha on round trips, with the reason
  documented in the test. If a number does not match, report the gap.
- **Say what is verified and what is not.** The README has a "Known gaps"
  section; keep it honest and current rather than quietly dropping items.
- **Tests assert behaviour, not implementation.** Several deliberately assert
  that a fill does *not* happen. Do not delete those to make a change pass.
- Add tests alongside behaviour changes; this suite is the only thing standing
  between a refactor and silently wrong P&L.
