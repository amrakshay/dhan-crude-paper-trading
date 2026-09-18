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
.venv/bin/python -m pytest tests/ -q                      # full suite (796 tests)
.venv/bin/python -m pytest tests/test_no_real_orders.py -q # safety suite alone
.venv/bin/python -m pytest tests/test_no_secrets_in_logs.py -q  # no-secrets-in-logs suite
LOG_LEVEL=DEBUG CONFIG_PATH=conf .venv/bin/python server.py # verbose run; logs/ is gitignored
CONFIG_PATH=conf .venv/bin/alembic upgrade head            # migrate
CONFIG_PATH=conf .venv/bin/python server.py                # serve on :8000

# daily bars (the swing rotation's inputs)
CONFIG_PATH=conf .venv/bin/python scripts/import_daily_bars.py \
    --strategy nse-swing-momentum --directory <the 10-year panel>   # bootstrap, once
CONFIG_PATH=conf .venv/bin/python scripts/refresh_daily_bars.py \
    --strategy nse-swing-momentum [--as-of YYYY-MM-DD]              # top up from Dhan

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

## 3a. Strategies, capabilities and portfolios

Three ideas, kept apart on purpose.

**A strategy module** is everything specific to trading one thing: underlying,
exchange segment, instrument types, contract specs, market hours, subscription
policy, charge rate card, margin model, and which capabilities it supports. One
YAML per strategy in `conf/strategies/`. `mcx-crude-options.yaml` is the only
one that exists; the framework is designed against the harder case (NSE index
options, then equity F&O) because that is what is expected next.

**A capability** is a generic feature of THIS codebase -- the option chain, the
price chart, chart trading, greeks, trade notes, P&L reports. A strategy
declares which it supports; a capability can also be switched off globally. The
effective state is the intersection, so a capability cannot be switched on for a
strategy that cannot support it.

**A portfolio** is one book of paper money. Every order, position and chart
trade belongs to exactly one, the same strategy can run in several, and their
books must not mix.

Rules that are not negotiable:

- **The YAML says what a strategy IS; the database says whether it is ON.**
  Enabled state is a `feature_toggles` row, applied at startup and after every
  toggle. Nothing about a strategy's rates, specs or margin model is editable
  from the UI.
- **Whether a RULE is enforced is runtime state; the rule itself is not.**
  Added 2026-09-18, and it fits inside the line above rather than bending it.
  A strategy may declare policies -- `regime.enforce`, `regime.enforce_entry_return`,
  `off_gate.enabled` -- whose DEFAULT is in its YAML and whose live value is a
  `feature_toggles` row under the `POLICY` scope, flipped from the Strategies
  page. What that changes is whether the application OBEYS a rule, which is the
  same kind of fact as enabled and armed. What it must never change is a
  PARAMETER of one: P1-P19 -- momentum floor, ATR multiple, breadth ramp, rank
  cut-off, every lookback -- stay in the YAML and are editable from no page, so
  the file stays greppable against the specification's own table. If a
  parameter ever needs to be editable that is its own piece of work with its own
  decision. Resolved in `src/swing/services/gate_policy.py`, read ONCE when a
  run starts, and refused outright when two switches contradict each other.
- **A policy change applies to NEW decisions only.** A position keeps the
  policy it was opened under -- recorded on its own `swing_stops` row -- so
  re-enforcing the regime gate stops new entries and does NOT liquidate a book
  opened while it was relaxed. A position whose policy cannot be established is
  treated as having been opened under enforcement, so the exemption fails
  towards the specification.
- **Off means off.** No instruments on the feed, no greeks poll, no chart
  fetches, no background work, no live pages. And **history never moves**: past
  orders, positions and P&L totals are identical across a toggle, Reports and
  Trade Notes stay reachable with every strategy off, and an open position can
  still be closed.
- **`strategy_key` and `portfolio_id` are STORED on trade rows**, resolved once
  at placement. Deriving either at read time from the instrument would break the
  moment a contract expired and was deactivated.
- **The two open-row lookups are keyed per portfolio.**
  `PositionRepository.get_open_for_security` and
  `ChartTradeRepository.get_open_for_underlying` both take a `portfolio_id`.
  Dropping it merges two books silently -- no error, just wrong numbers.
- **A portfolio's balance is never stored.** Cash is the sum of the append-only
  `cash_ledger`. `src/portfolios/services/balance_service.py` is the only place
  that computes cash, blocked margin, available and equity; do not recompute
  them anywhere else.
- **Blocked margin is an estimate and must always be labelled one.** Equity is
  withheld -- not zeroed -- when any open position has no mark.
- **Funds are checked at placement AND at the fill.** A resting order that
  became unaffordable is rejected, never partially filled to fit.
- **ENABLED and ARMED are two switches.** Enabling a strategy makes it compute,
  decide and write a decision record; ARMING is what lets it submit an order of
  its own accord. Only a module that declares an `automation` block in its YAML
  can be armed at all -- `mcx-crude-options` declares none and never will, so
  every order in it comes from a person. An unarmed run journals the identical
  decision and places nothing, which is what makes arming a safeguard rather
  than a mode.

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

**`daily_bars` is not that, and here is why.** The rule above forbids
accumulating **ticks**: database I/O on the hot path, which breaks the
performance contract, and rebuilding from a stream what the vendor already
serves correctly. The `daily_bars` table, added 2026-09-18 for the NSE swing
momentum rotation, is a different object with a different provenance. Every row
is *fetched whole* from `/charts/historical` — the same read-only market-data
endpoint the chart uses — by a background job, once per symbol per day, after
the close. No tick reaches it, nothing in it is derived from the feed,
`apply_packet` does not know it exists, and if it were deleted it would be
refetched rather than lost.

It exists because a rotation needs 260+ sessions for ~500 symbols available
*instantly* at 09:15, and re-fetching that is a five-minute job at Dhan's rate
limits — comfortable overnight, impossible between waking up and the open. The
chart's 15-second/5-minute in-memory cache in `candle_service` is the right
answer for one instrument a human is looking at, and no answer at all for five
hundred a scheduler is about to trade.

The distinction to keep is **provenance, not durability**: fetched bars may be
stored; ticks may not be accumulated into bars. If you find yourself writing
`daily_bars` rows from `MarketBook`, you are on the wrong side of it.

**The rotation decides on a clock, and a missed run is reported.** The nightly
job (18:15 IST) refreshes the bars, decides, ratchets every trailing stop and
journals; the rebalance (09:16) trades. Idempotence comes from the JOURNAL
(`sessions_completed_on`), not from a flag, so a restart at 18:20 does not
re-decide 18:15. A session with no record is detected against the regime
index's own bar dates and REPORTED -- never silently re-decided days later on
bars that may since have been restated.

**The chandelier stop ratchets up and never down.** Set at entry to
`entry - 3.5 x ATR14`, raised on each daily close to
`max(stop, highest_close_since_entry - 3.5 x ATR14_today)`. ATR widens after a
violent day, so the naive formula can LOWER the stop on exactly the session the
position became more dangerous; the `max()` is the rule. Do not tighten the
multiple -- the specification measures every tighter variant as worse.

**Analysis may run at any hour; ORDERS may not.** Ranking, the nightly
decision, the stop ratchet and the journal run off-market quite happily. Every
buy and every sell is checked against
`market_clock.can_execute_continuously` **per instrument, immediately before the
order** -- per instrument because F&O eligibility moves the close from 15:30 to
15:15. A refused order is decided and journalled with its reason and the time,
and is NOT queued for the next open: the next rebalance re-decides from fresh
bars, and replaying yesterday's intent is how you trade a decision nobody would
take today. The stop monitor deliberately differs and DOES defer -- a triggered
stop is a fact that has already happened, not a fresh opinion. The guard is in
`src/swing/`, not in `submit_paper_order`; making it generic would change MCX
crude and chart trading, which did not ask for it, and could not journal the
refusal.

**A stop that cannot fill honestly does not fill.** NSE's Closing Auction
Session (live 3 Aug 2026) ends continuous cash trading at 15:15 for
F&O-eligible names. A stop triggered after that is recorded as triggered and
its exit waits for the next session's open, because this simulator has no model
of a call auction. The F&O set is derived from the instrument master's own
FUTSTK rows, never configured.

**A chart click never writes an option.** `src/chart_trading/` translates a
click on the FUTURE's chart into a long ATM option: Buy buys the call, Sell buys
the put. A "Sell" is a long put, never a short call, so the worst case stays the
premium paid. Do not add a code path that sells an option to open.

**Never invent prices.** If credentials are missing and
`DHAN_SYNTHETIC_FEED` is false, the feed reports an error. It must never
silently fall back to generated data. The synthetic feed is opt-in, reports its
state as `SYNTHETIC`, and the UI shows a permanent banner.

**Fill simulation stays pessimistic.** See `backend/CLAUDE.md` §4. Do not make
fills more generous without being asked.

**The health page displays internals, so it must never display a secret.**
`src/health/` exists to answer "what is this process doing", which makes it the
likeliest place in the codebase to leak one. The Dhan token is a
`crypto_service.mask()` plus `inspect_token()` metadata, the database URL goes
through `database.connection.redact_database_url` (the same redactor the log
uses — do not write a second one), and log lines are served from
`src/log_buffer.py`, which stores records already formatted through
`RedactingFormatter`. Every one of these has an assertion in
`tests/test_no_secrets_in_logs.py` pointed at the endpoint itself. If you add a
field to the health payload, add its no-secrets assertion in the same change.

**The health page must not distort what it monitors.** Nothing it reports is
measured on the tick path — every number is either already-existing component
state or an `asyncio.all_tasks()` walk. It polls at 5 s, and both its endpoints
are in `LogRequestsMiddleware.IGNORED_PATHS` so the polling does not fill the
access log the page reports on. Do not add timing or sampling inside
`apply_packet` to feed it.

**Money is `Decimal`, never `float`.** Timestamps are stored naive-UTC. That
includes every ledger amount, margin estimate and equity figure.

**Nothing strategy-aware goes on the tick path.** A packet is not looked up
against a strategy or a portfolio. Membership is resolved when a subscription is
built (the contract metadata carries `strategyKey`) or when a trade is written,
never when a tick arrives. Portfolio equity is computed on request, at the
Positions/Portfolios cadence.

**Settings from the UI beat `.env`.** `SettingsService.apply_to_config()` overlays
stored settings onto the in-memory config at startup (`main.py`'s
`_apply_stored_settings()`, called from the lifespan **before**
`get_feed_manager().start()`) and after every save. Do not read `DHAN_*` from
`os.environ` directly — go through `config_utils`, or you will see the `.env`
fallback instead of what the operator actually configured.

The startup call is load-bearing and its absence is silent: without it the app
boots perfectly well on `.env` while the Settings page shows something else, so
a Dhan token configured in the UI is not the one the feed uses. That was a real
bug until 2026-09-17, found by the system health page. `tests/
test_startup_applies_stored_settings.py` asserts both that the overlay happens
and that it happens before the feed starts — moving the call after the feed
would reintroduce the bug in a form the behavioural test alone would miss.

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
backend/conf/strategies/*.yaml     one strategy module each: underlying, specs,
                                   hours, subscription, rate card, margin
backend/conf/charges/*.yaml        rate cards, with source URL + as-of date
backend/src/<feature>/             routes/ controllers/ services/ api_schemas/ database/
backend/src/market/services/       feed protocol, book, feed client, broadcaster, greeks
backend/src/market/services/dhan_charts_client.py   candle history (market data only)
backend/src/market/services/candle_service.py       timeframes, aggregation, cache
frontend/src/components/PriceChart.jsx              the chart; see frontend/NOTICE
backend/src/chart_trading/                         one-click trading from the chart
backend/src/chart_trading/services/bracket_monitor.py   server-side SL/TP watcher
backend/src/swing/                 the NSE rotation: ranking, planner, execution,
                                   stops, scheduler, journal -- see its README
backend/src/swing/services/gate_policy.py          whether a RULE is enforced
backend/src/swing/services/scheduler.py            the only clock in this app
backend/src/swing/services/stop_monitor.py         the chandelier stop watcher
backend/src/reports/services/metrics_service.py    CAGR, drawdown, MAR, concentration
backend/src/strategies/services/market_clock.py    is the market open, and may an
                                                   order fill continuously
backend/src/strategies/            the registry, the toggles and their page
backend/src/portfolios/            portfolios, the cash ledger, the balance maths
backend/src/health/                the system health page's backend (no tables)
backend/src/log_buffer.py          in-memory ring buffer of recent WARNING+ records
frontend/src/pages/SystemHealthPage.jsx            the system health page
frontend/src/pages/PortfoliosPage.jsx             money, per portfolio
frontend/src/pages/StrategiesPage.jsx             what is on, what it costs, what is armed
frontend/src/pages/SwingMomentumPage.jsx          the rotation's decision journal
frontend/src/portfolios/ActivePortfolioContext.jsx  the header picker's scope
backend/tests/test_no_real_orders.py   the safety suite
frontend/src/theme/tokens.js       palette ported from the Privacera portal
frontend/src/market/               the single shared WebSocket context
```

See `backend/CLAUDE.md` and `frontend/CLAUDE.md` for per-side conventions.

---

## 7. Working style for this repo

- **No rate is hardcoded in Python.** Every charge rate lives in a rate card
  under `conf/charges/` with a primary source URL, an as-of date and a
  confidence marker. If you change a rate, update its source and date too. The
  margin model gets the same treatment, marked `APPROXIMATION`.
- **Do not tune constants to make tests agree.** The charges engine has a known,
  asserted ₹0.01 divergence from Zerodha on round trips, with the reason
  documented in the test. If a number does not match, report the gap.
- **Say what is verified and what is not.** The README has a "Known gaps"
  section; keep it honest and current rather than quietly dropping items.
- **Tests assert behaviour, not implementation.** Several deliberately assert
  that a fill does *not* happen. Do not delete those to make a change pass.
- Add tests alongside behaviour changes; this suite is the only thing standing
  between a refactor and silently wrong P&L.
