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

The URL allowlist has grown twice. On 2026-09-16 the price chart added
`https://api.dhan.co/v2/charts/historical` and `.../charts/intraday`. That is
the sanctioned way to extend it: add the exact read-only market-data URL, keep
the constants in a single client module, and leave the matching logic alone.
**Never loosen the pattern, the endpoint list or the scanner** — a regex that
accepts a family of URLs is not the same guarantee as a set you can read in one
glance.

On 2026-09-18 it gained `https://api.dhan.co/v2/RenewToken` (a **GET** — the
documented `curl` has no `--request` and no `--data`, and sending a POST returns
400), which is **not market data** and is the only entry that is not. It exchanges the access token
this application already holds for a fresh 24-hour one. It was an explicit
decision, because the alternative is a token pasted in by hand every day and an
expired token silently stops the feed, the chart and the overnight bar refresh.
What makes it acceptable is that it sends nothing the application did not
already have: it can extend a session the operator started, never start one.

**`auth.dhan.co` is deliberately absent and must stay absent.** That host turns
a client id, a six-digit PIN and a TOTP into a token —
`/app/generateAccessToken` — and `/app/generate-consent` begins an OAuth login.
Either would let this application authenticate AS the operator and would mean
storing their PIN. Considered the same day and declined; the cost is that a
token allowed to lapse entirely must be replaced by hand.

The scanner's host pattern was widened in the same change. It used to name
`api.dhan.co`, `api-feed.dhan.co` and `images.dhan.co`, so a call to
`auth.dhan.co` would have passed without a word — a closed set of hosts is only
a guarantee if it is closed against the hosts nobody thought of. It now matches
`dhan.co` and every subdomain, so a new Dhan host fails the build until someone
allowlists it on purpose.

On 2026-09-18 the application gained its **first OUTBOUND host**,
`api.telegram.org`, and with it the first inbound CONTROL path. Everything
before it was inbound market data: the app fetched prices and sent nothing
anywhere. Telegram is not a broker and must not become one -- there is no broker
surface for a command to reach, because none exists -- and
`test_no_real_orders.py` is deliberately UNCHANGED by it. Widening that file
would blur what it is for. The sibling guard is
`tests/test_outbound_hosts.py`: every non-Dhan external host must be named in
exactly ONE module and listed there, or the build fails. Same containment
`dhan_token_client.py` gives `/RenewToken`, applied to the general case.

On 2026-09-19 a SECOND outbound host arrived with the IPO dashboard:
`webnodejs.investorgain.com`, the JSON endpoint behind investorgain.com's IPO
GMP page, named in `src/ipo/services/ipo_source_client.py` and nowhere else.
It is INBOUND data like Dhan's -- the application fetches and sends nothing --
but it is not Dhan, so `test_outbound_hosts.py` owns it and
`test_no_real_orders.py` is again UNCHANGED. Note that the host a human reads
(`www.investorgain.com`) is a different one and is deliberately absent from
Python: the backend never fetches it, the display link is built in the browser,
and a host on that list which is never contacted would weaken what the list
says.

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
.venv/bin/python -m pytest tests/ -q                      # full suite (1,177 tests, ~1.7 min)
.venv/bin/python -m pytest tests/ -q -n0                  # the same, serially (~6.5 min)
.venv/bin/python -m pytest tests/test_no_real_orders.py -q # safety suite alone
.venv/bin/python -m pytest tests/test_no_secrets_in_logs.py -q  # no-secrets-in-logs suite
LOG_LEVEL=DEBUG CONFIG_PATH=conf .venv/bin/python server.py # verbose run; logs/ is gitignored
CONFIG_PATH=conf .venv/bin/alembic upgrade head            # migrate
CONFIG_PATH=conf .venv/bin/python server.py                # serve on :24601

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

Two-port dev (Vite :5173 + backend :24601) is the default. `./run-single-port.sh`
from the project root builds the frontend and serves UI + API from :24601 alone;
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
- **WHEN a strategy wakes up is runtime state too; what it decides is not.**
  A strategy may declare SETTINGS -- runtime VALUES, as against policies, which
  are runtime booleans -- whose default is in its YAML and whose live value is a
  `strategy_settings` row. Two exist: `schedule.nightly_at` and
  `schedule.rebalance_at`. A second table rather than a fifth toggle scope,
  because every toggle is a boolean and these are values. Resolved in
  `src/swing/services/schedule_settings.py`, validated there before anything is
  stored, and REFUSED rather than warned about when the value would break
  something: an analysis time inside the session stores a forming bar as a
  finished one, and an order time outside it configures a strategy that never
  trades. The rebalance CADENCE is P18 and stays in the YAML.
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
- **AN INSTRUMENT NO LONGER DETERMINES A STRATEGY.** Added 2026-09-19, when a
  second NSE equity module arrived: the swing rotation and BTST Overnight both
  trade the Nifty 500, so SUNTV belongs to both. Two consequences, and both are
  the same rule the portfolio picker already follows.

  `submit_paper_order(strategy_key=...)` -- the caller SAYS which, the way it
  already says which portfolio. An ambiguous instrument with no key is REFUSED,
  never resolved to the alphabetically first: a trade under the wrong strategy
  carries the wrong rate card, the wrong arming switch and the wrong journal,
  and nothing downstream would ever notice. A CLOSE resolves from the POSITION,
  which already carries the strategy it was opened under, so the two halves of
  a round trip cannot land in different books.

  And the instrument master's exclusivity rule is now "one INGESTION RULE per
  symbol", not "one strategy per symbol". Two strategies may claim a name when
  the `instruments` row either would produce is identical -- same series
  filter, same lot-size source, same trading-symbol source. Two that DISAGREE
  are still refused, because then the row genuinely depends on which won.

- **A MISSED RUN DOES NOT COST THE SAME THING IN EVERY STRATEGY.** The rotation
  treats a missed session as reportable and harmless: the next run re-decides.
  BTST's EXIT is the edge -- the same positions held to the next close instead
  of the next open measure a 49.0% win rate against 71.4% -- so its exit job is
  bounded only at the BOTTOM, sells late rather than waiting, records a late
  sale as its own status, leaves a position that could not be sold OPEN, and
  raises an alert. Do not "harmonise" the two jobs; the asymmetry is the point.

- **NOT EVERYTHING WITH A PAGE IS A STRATEGY OR A CAPABILITY.** Added
  2026-09-19 with the IPO dashboard (`/ipo`), which places no orders, touches
  no portfolio and reads no feed -- it fetches a public page, stores it, shows
  it and sends a reminder. It therefore has NO `feature_toggles` row of any
  scope, and the page is gated on none: inventing a fifth meaning for a toggle
  in order to switch a page on is how a vocabulary stops meaning anything. Its
  on/off switch is the plain config property `ipo.scheduler_enabled`, the same
  shape `swing.scheduler_enabled` has, and what it stops is the CLOCK. The
  page, its three tabs and every action stay reachable with it off, for the
  same reason a strategy's journal does: what has been recorded is history,
  and history never moves.

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

**A job that has done its work is DONE, not due for a retry.** `RETRY_AFTER`
bounds how often a FAILED job is tried again; it must never be what decides
whether a successful one runs again. The nightly is deliberately unbounded at
the top end -- recording what the stored data says is valid at any hour -- and
until 2026-09-18 that combination made it re-run every fifteen minutes from
18:15 until midnight, each time pulling five hundred symbols from Dhan and each
time correctly deciding nothing because the journal already held the session.
Roughly 3,500 wasted requests in two hours. Two guards now: `_succeeded`
in memory for a running process, and `SwingSessionRepository.ran_on_day` --
keyed on when the job RAN, not on the session it decided -- for a restart,
because a restart clears the memory and is how the same pull got repeated all
evening. A run whose REFRESH FAILED is not marked done, so credentials fixed at
18:30 still get bars at 18:45.

**THERE ARE TWO CLOCKS, AND THEY ARE SEPARATE ON PURPOSE.** Until 2026-09-19
`src/swing/services/scheduler.py` was the only one; it now drives every
automated STRATEGY, and `src/ipo/services/scheduler.py` drives the IPO
dashboard's daily GMP refresh and its hourly closing-day reminders. They do not
import each other in either direction.

The swing clock decides, arms and places (paper) orders on a live book. The IPO
clock fetches a public page and sends a message; it reaches no strategy, no
portfolio and no order path. Merging them would mean editing the clock that
trades in order to ship a page that does not -- a change nothing about the IPO
dashboard justifies asking of it. Duplicating a thirty-line tick loop is the
accepted cost, and the lesson that actually matters is duplicated with it: a
job that has done its work is DONE, guarded both in memory and against a
persisted record of the SLOT it ran for.

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

**There are TWO health surfaces and they answer different questions.**
`src/health/` and `/health` answer "what is this PROCESS doing" -- one feed
connection, one book, one CPU, one set of tasks -- and are admin-only.
`src/swing/services/swing_health_service.py` and `/swing?tab=health` answer "is
THIS STRATEGY healthy, and what has it been doing", and are admin-only too:
they serve live machinery state and log records, which is the exposure the
`/api/healthcheck/*` routes are gated for. Neither copies the other. A figure
that is not about one strategy stays on the process page and the strategy tab
LINKS to it; the strategy tab imports `task_inspector` (a read-only helper) and
`src/health/` imports nothing from `src/swing/` for its own payload, which is
why the per-strategy service lives in the swing package.

**The health page must not distort what it monitors.** Nothing it reports is
measured on the tick path — every number is either already-existing component
state or an `asyncio.all_tasks()` walk. It polls at 5 s, and both its endpoints
are in `LogRequestsMiddleware.IGNORED_PATHS` so the polling does not fill the
access log the page reports on. Do not add timing or sampling inside
`apply_packet` to feed it.

**An alert is a ROW first and an HTTP call second.** `PositionService.
apply_fill` writes the fact; `alert-dispatcher` delivers it. A fill must never
block on somebody else's HTTP, an alert raised during a crash still has to
arrive after the restart, and "what did it tell me, and did it arrive" has to be
answerable -- a log line saying `sendMessage returned 200` answers none of the
three. A fact with nothing configured to carry it is recorded SUPPRESSED with
the reason, never dropped.

**De-duplication is the alert feature, not a nicety.** This codebase has already
produced the flood: "database is locked" once a second for twelve minutes, and
the nightly re-running every fifteen minutes for two hours. Unthrottled, either
would have sent hundreds of messages and met flood control -- which stops the
ONE message that mattered from arriving. Collapse on the logger name plus a
NORMALISED message (raw text fails, because the body carries its own counter),
and **exclude the alerting logger from its own sink** or a delivery failure
alerts about itself in a loop. Both are asserted.

**A command is authorised by resolving its sender to a USER.**
`users.telegram_user_id` -> the user -> that user's existing role. Never a
standalone allowlist of Telegram ids: that would be a second authorisation
model, and the one this codebase has already gets deactivation, demotion, the
seeded-admin guard rails and the owes-a-password-change refusal right. Mapping
inherits all of them for free; a parallel list inherits none, and the first time
somebody was deactivated they could still arm a strategy from their phone.
Nobody is mapped by default, matching is on the NUMERIC id (a username is
reassignable), and control commands need `ROLE_ACCOUNT_ADMIN` AND their own
default-off switch -- a Telegram message that arms a strategy must not be a
thinner path than the button.

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

**The access token renews itself, and cannot mint itself.**
`src/settings/services/token_refresh_service.py` watches the token's OWN expiry
— `inspect_token()` reads the `exp` claim locally — and renews when under six
hours remain, rather than running on a clock it could get wrong. The renewed
token is saved through `SettingsService.save()` so it lands encrypted, is
registered with the log redactor and is overlaid onto the running config, and
the feed is then `reconfigure()`d so the process is not left holding a fresh
token while using the old one. Three outcomes are kept apart: renewed, could
not renew yet (retry), and **refused** — and that last distinction is
load-bearing rather than tidy. On 2026-09-19 a 400 was classified as transient,
so a refusal was retried nineteen times across the six hours of validity the
operator could have acted in, and the only alert arrived after the token was
dead and unrenewable by anyone. Any 4xx except 429 is now a `TokenRenewalRefused`:
it stops the retries, carries **Dhan's own error message** rather than a bare
status code, and reports `needsHuman` so the alert goes out while there is still
time to act. The monitor remembers which token was refused and resumes the
moment a different one is stored.

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

**Unverified, flagged in code, and now LOAD-BEARING:** the Quote/Full packet
maps its four price fields as open, close, high, low per the SDK. Never checked
against a live feed.

It stopped being cosmetic on 2026-09-19. NSE BTST Overnight's B6 is
`CLV = (price - low) / (high - low) > 0.8` measured on the session so far, so a
transposition does not make that filter drift -- it INVERTS it, and the strategy
buys the weakest closes in the market while every number it produces looks
plausible.

`scripts/verify_feed_session_fields.py` is the standing answer: it subscribes a
few names mid-session and compares the packet's session high and low against
`/charts/intraday` for the same day. **It has not been run yet** -- it must run
during a session with a working token, and it refuses outside market hours
because the comparison would mean nothing. WHEN IT IS RUN, RECORD THE FINDING
IN THE TABLE ABOVE and delete this note.

Until then `scan_service.Quote.usable` refuses a quote whose high is below its
low, or whose last trade is outside its own session range. That does not verify
the mapping; it makes the failure a scan that qualifies nothing and says why,
instead of a book of inverted trades.

The same script settles a second unverified claim, the one BTST's subscription
window rests on: that the packet carries the session AGGREGATE high, low and
volume rather than a delta since subscription. If it is a delta, the fix is to
open the window at 09:15 in the strategy YAML rather than at 14:45.

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
backend/src/btst/                  the NSE overnight hold: the 15:20 scan, the
                                   defended exit, its own journal -- see its README
backend/src/btst/services/scan_service.py   the filter funnel, B2-B8
backend/src/btst/services/execution_service.py  the scan, and THE EXIT
backend/scripts/verify_feed_session_fields.py   settles root CLAUDE.md's
                                   unverified high/low mapping, against a live feed
backend/scripts/replay_btst_scan.py   rebuilds a past session's 15:20 book from
                                   intraday bars and replays the scan read-only,
                                   for the near-misses the journal's row cap drops
backend/src/strategies/services/strategy_modules.py  which package owns a
                                   strategy's own rules, looked up by key
backend/src/strategies/services/scheduling.py   JobRun and MissedRun, so two
                                   modules can return them without importing each other
frontend/src/pages/BtstOvernightPage.jsx       its five tabs
frontend/src/components/BtstSignals.jsx        today's scan, watchable live
backend/src/swing/services/gate_policy.py          whether a RULE is enforced
backend/src/swing/services/schedule_settings.py   WHEN it wakes up, and the two
                                                  times it refuses
backend/src/swing/services/swing_health_service.py  is THIS strategy healthy
frontend/src/components/SwingHealth.jsx           its Health tab, admin-only
backend/src/swing/services/scheduler.py            every STRATEGY's clock
backend/src/ipo/                   the IPO dashboard: the GMP source, the
                                   three tabs, the actions and its own clock
backend/src/ipo/services/ipo_source_client.py   the ONLY module naming
                                   webnodejs.investorgain.com
backend/src/ipo/services/scheduler.py   the SECOND clock; nothing to do with
                                   a strategy, and deliberately separate
backend/src/ipo/services/ipo_health_service.py   is THIS FEATURE healthy,
                                   and did the 14:00 reminder actually go out
frontend/src/pages/IpoDashboardPage.jsx           its four tabs
frontend/src/components/IpoStatus.jsx             the Status tab, admin-only
backend/src/swing/services/stop_monitor.py         the chandelier stop watcher
backend/src/reports/services/metrics_service.py    CAGR, drawdown, MAR, concentration
backend/src/strategies/services/market_clock.py    is the market open, and may an
                                                   order fill continuously
backend/src/strategies/            the registry, the toggles and their page
backend/src/portfolios/            portfolios, the cash ledger, the balance maths
backend/src/connections/           Dhan and Telegram: credentials, alerts, commands
backend/src/connections/services/telegram_client.py  the ONLY module naming
                                                     api.telegram.org
backend/src/connections/services/alert_service.py    writes the outbox rows
backend/src/connections/services/alert_dispatcher.py the only thing that SENDS
backend/src/connections/services/alert_watcher.py    the seven health events
backend/src/alert_sink.py          the ERROR+ logging sink feeding the outbox
frontend/src/pages/ConnectionsPage.jsx            the cards and the two test buttons
backend/tests/test_outbound_hosts.py   one module per external host
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
