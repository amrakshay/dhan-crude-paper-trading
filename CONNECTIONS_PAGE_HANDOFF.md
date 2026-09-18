# Handoff — a Connections page, with Dhan and Telegram on it

Read `CLAUDE.md`, `backend/CLAUDE.md` and `frontend/CLAUDE.md` first. They cover
the no-real-orders constraint and its URL allowlist, the secrets rules
(`encrypted_value`, `log_redaction`, `test_no_secrets_in_logs.py`), the module
layering, the strategy/capability/page model, the theme and the UI honesty
rules. `README.md`'s **Settings page** and **The access token renews itself**
sections cover what exists today. Do not re-derive any of that.

---

## What this is for

Two things talk to a third party today and they are in different places, neither
of which is called a connection: the Dhan credentials sit on **Settings** beside
a switch that has nothing to do with credentials, and there is nowhere at all to
put a second provider.

This adds `/connections`: one page that owns **anything this application
authenticates to and calls over the network**. Two cards to start with — Dhan
and Telegram — laid out as the reference screenshot's grid.

**Telegram is a genuinely new kind of thing in this codebase and must be
treated as one.** Every external call today is INBOUND market data: the app
fetches prices and never sends anything anywhere. Telegram is the first
outbound path, and per the decision below it is also the first INBOUND CONTROL
path. Read "The command surface" before writing any of it.

---

## Baseline — do NOT spend time re-running these

```bash
cd backend
.venv/bin/python -m pytest tests/ -q     # 894 passed, 1 skipped, ~60s (parallel)
.venv/bin/python -m pytest tests/ -q -n0 # the same, serially, ~6.5 min
CONFIG_PATH=conf .venv/bin/alembic heads # c9e42a7b51d8 (head), 13 migrations
cd ../frontend && npm run build          # passes, ~2s
```

The suite runs across 12 workers by default (`pytest.ini`). `-n0` disables it
for a breakpoint. **Never run two `pytest` processes at once** — see
`backend/CLAUDE.md` §11.

State of the running installation on 2026-09-18: NSE Swing Momentum enabled and
ARMED, the regime gate and entry-return filter NOT enforced, portfolio 2
(`Swing Momentum`, ₹10,00,000) the only portfolio, zero orders and zero
positions. MCX crude is off, and the `live-price` capability now gates the
Crude Oil page with it. Live Dhan feed, token auto-renewing.

---

## Decisions already made by the owner

Do not re-open these. They are the shape of the work.

1. **Telegram is alerts OUT and commands IN.** Not one-way. The bot posts to a
   channel AND accepts commands. This is the decision that makes the rest of
   the handoff careful rather than routine.
2. **Dhan moves to Connections. Settings keeps app config.** Connections owns
   anything that talks to a third party: the client id, the token, validation,
   the auto-renew state. The **synthetic-feed switch stays on Settings** — it is
   a mode of THIS application, not a credential, and it would sit oddly on a
   card describing a connection to somebody else.
3. **The status pill is LAST KNOWN, checked in the background.** Opening the
   page costs no network call. A card shows the most recent check WITH ITS AGE.
   A check that is stale says so rather than looking live — same rule the
   feed status indicator already follows (`frontend/CLAUDE.md` §3).

---

## What already exists, verified by reading the code

| Thing | Where |
|---|---|
| Encrypted-at-rest settings, one row per key | `app_settings` table; `value` XOR `encrypted_value`, enforced by the repository |
| Which keys are readable back | `MANAGED_KEYS` / `SECRET_KEYS` in `settings_service.py` — a stray row cannot influence config |
| Encrypt / decrypt / mask | `src/settings/services/crypto_service.py`. Refuses to encrypt without a stable secret rather than using a per-process key |
| Runtime secret scrubbing | `src/log_redaction.register_secret()` — anything registered is scrubbed from every log line and from the health page's buffer |
| Dhan credential validation | `SettingsService.validate_credentials()` — issues the option-chain expiry list, the lightest authenticated market-data call |
| Dhan token expiry, read locally | `inspect_token()` decodes the JWT's `exp` without verifying the signature |
| Dhan token auto-renewal + its counters | `src/settings/services/token_refresh_service.py`, `dhan-token-refresh` task, `status()` dict |
| Feed connection state | `FeedManager.status()["feed"]["state"]`, and `task_inspector.feed_flags()` |
| Settings overlay onto running config | `SettingsService.apply_to_config()`, called in the lifespan **before** the feed starts |
| Page → role gating | `conf/role-pages.json` INTERSECTED with `StrategyRegistry.feature_pages()`; `pages_for_role()` is the one computation |
| A background task's shape | `OrderMatcher`, `bracket_monitor`, `swing_stop_monitor`, `dhan-token-refresh`: own task, own session, off the tick path, in `TASK_DESCRIPTIONS` **and** `expected_task_names`, with a `status()` |
| The card/panel look | `SwingHealth.jsx`'s `Panel`, `SystemHealthPage.jsx`'s `SectionCard`, `theme/tokens.js` |

### What does NOT exist

- **No notion of a "connection".** Nothing in the schema, no model, no page.
- **No outbound HTTP to anywhere but Dhan.** The allowlist and the scanner in
  `test_no_real_orders.py` only know Dhan hosts; there is no general outbound
  policy.
- **No inbound control path of any kind.** Every mutation today arrives through
  the authenticated API with a role check.
- **No notification/alert concept.** Nothing decides what is worth telling
  somebody about.

---

## The command surface — read this before writing any Telegram code

The owner chose commands-in. That is a real inbound control path into an
application that, when ARMED, spends money on its own schedule. Build it, but
build it like this.

**The root rule is untouched and untouchable.** `CLAUDE.md` §1: this system must
never be able to place a real order. Telegram changes nothing about that. No
command may reach a broker surface, because no such surface exists.

**A channel id is not authentication.** It identifies a destination, not a
person. Anyone who learns the channel id and can post in it would otherwise be
issuing commands. So:

- Store an explicit **allowlist of Telegram user ids** (`from.id` on the
  update) that may issue commands. A message from anyone else is ignored and
  logged as refused.
- The allowlist is empty by default. **Commands are off until somebody is on
  it**, and a Telegram connection with alerts configured and no command users
  is a normal, complete state.
- Match on the numeric user id, never on `@username` — usernames are
  reassignable.

**Split the commands in two, and make the second half opt-in.**

- **Read-only** (`/status`, `/book`, `/pnl`, `/health`): safe, and most of the
  value. These read the same services the pages read.
- **Control** (`/arm`, `/disarm`, `/run`): these change what the software does
  with money. Put them behind their own switch, default OFF, and make a
  refused-because-disabled reply say so. The repo already treats arming as
  special — it is admin-only in the API and has its own confirmation dialog
  with the server's warnings — so a Telegram message that arms a strategy
  cannot be a thinner path than the button.
- If you only get half of this built, build the read-only half. It is the half
  with the value and none of the risk.

**Recommendation: `getUpdates` long-polling, not a webhook.** A webhook needs
this app reachable from the internet, which it is not and should not need to
be. Long-polling is one more background task in the shape the codebase already
uses four times.

**Every command that changes anything must be journalled** the same way a UI
action is — who (Telegram user id), what, when, and the outcome. An action with
no record is the thing this codebase most consistently refuses.

---

## Traps — these are specific and each has cost me or the repo real time

**The Telegram bot token goes in the URL PATH.** Every call is
`https://api.telegram.org/bot<TOKEN>/METHOD`. And **httpx logs the full URL at
INFO** — this is happening in `logs/app.log` right now for Dhan:

```
[INFO] _client.py:_send_single_request:1740 [httpx] : HTTP Request: POST https://api.dhan.co/v2/charts/historical "HTTP/1.1 200 "
```

A naive implementation therefore writes the bot token into `app.log` on every
single call. Fixes, and you want both: `log_redaction.register_secret()` the
bot token the moment it is loaded or saved (the Dhan token already does this
via `load_stored()`), and do not rely on that alone — an httpx exception can
carry the request too, which is why `dhan_token_client.py` logs the exception
TYPE and never its message. Add the assertion to
`tests/test_no_secrets_in_logs.py` in the same change, pointed at the endpoint.

**Validation has three outcomes, not two.** `getMe` validates the bot token.
`getChat` validates the channel — but it fails when the bot is not a member or
admin of that channel, which is the single most common setup mistake. "Bad
token", "good token, bot is not in that channel" and "that channel does not
exist" must read differently, because the fix is different for each. Do not
collapse them into "invalid".

**Do not validate by sending a message** unless the operator asked. A "Send a
test message" button is good; a page that posts to the channel every time
somebody opens it is not.

**`api.telegram.org` is a new outbound host and nothing guards it.** The
scanner in `test_no_real_orders.py` guards Dhan hosts only — and was widened on
2026-09-18 to `dhan.co` and every subdomain after `auth.dhan.co` was found to
slip through. Consider whether outbound hosts in general now deserve an
allowlist; at minimum, the Telegram base URL should be named in exactly one
module the way `dhan_token_client.py` names `/RenewToken`, with a test
asserting nothing else names it.

**The status pill must not lie about freshness.** Decision 3 is last-known.
That means every card carries the age of its check, a check that has never run
says so rather than showing a green pill, and a provider that has gone quiet
shows its last result greying out rather than a stale success. `undefined is
not zero` and its siblings apply here exactly as they do on the Health tab.

**Do not break the startup overlay.** `apply_to_config()` runs in the lifespan
BEFORE the feed starts, and `tests/test_startup_applies_stored_settings.py`
pins both the call and its position. If Connections introduces its own loading
path, it must not move or duplicate that.

**Moving Dhan off Settings must not orphan anything.** `PUT /api/settings`
currently takes client id, token and the synthetic switch together, and
`save()` refuses live mode without credentials. If credentials move to
Connections and the switch stays on Settings, that validation has to keep
working across two pages — decide deliberately whether the switch still refuses
to go live without credentials, and where the operator is told.

**`/connections` is a new page and pages are gated.** Add it to
`conf/role-pages.json` (admin-only, like `/settings` and `/strategies`), to
`navigationItems`, and to `App.jsx` behind a `RoleRoute`. It is NOT a
strategy-gated page, so keep it out of `gated_pages()`. `require_admin` on
every route is what actually refuses; the sidebar is presentation.

**Check light and dark.** The reference design is a light screenshot.

---

## What the page should show

`/connections`, admin-only. A responsive grid of cards, three across on a wide
screen, following the reference screenshot.

**One card per connection:**

| Screenshot | Here |
|---|---|
| Icon tile | Provider glyph |
| Title (`azure-eastus-087`) | `Dhan` / `Telegram` |
| Subtitle (`AKS · D2P`) | `Market data · Live feed, chart, option chain` / `Alerts · Commands` |
| Status pill (`Running` / `Stopped`) | See below — **more than two states** |
| Metric row left (`Apps 1`) | Dhan: instruments on the feed. Telegram: channel name |
| Metric row right (`1d ago`) | **Age of the last check** (decision 3) |
| Tag chip (`AI_ASSETS_COLLECTOR`) | `MARKET_DATA` / `ALERTS` / `COMMANDS` — one per capability the connection actually has |

**The pill needs more than Running/Stopped.** At minimum: `Connected`,
`Not configured`, `Expiring soon` (Dhan, under the renewal threshold),
`Error` with the reason on the card, and `Never checked`. Two states would
force "not set up yet" and "set up and broken" into the same red, and those
are the two an operator most needs to tell apart.

**Clicking a card opens its detail** — the form, the validate button, and
whatever that provider needs. Dhan's is essentially today's Settings card minus
the feed switch. Telegram's is: bot token (write-only, masked, same rules as the
Dhan token), channel id, a Validate button that reports the three outcomes
above, a Send test message button, the command-user allowlist, and the
control-commands switch.

**The search box** in the screenshot is for a page with dozens of cards. With
two, leave it out and add it when it earns its place.

---

## Definition of done

- `/connections`, admin-only, in the sidebar and in `role-pages.json`, with a
  card per connection in the reference layout and correct in light and dark.
- Dhan's credentials, validation and renewal state live there. Settings keeps
  the synthetic-feed switch and still works.
- Telegram: bot token stored encrypted and registered with `log_redaction`,
  channel id, validation that distinguishes bad token / bot not in channel /
  no such channel, and a test message on request only.
- Alerts out, on whatever events are agreed — and the list of events is itself
  worth a decision before building.
- Commands in, with a user-id allowlist that is empty by default, read-only
  commands separated from control commands, control commands behind their own
  default-off switch, and every state-changing command journalled with its
  sender.
- The bot token appears in no log line, with an assertion in
  `tests/test_no_secrets_in_logs.py` added in the same change.
- `tests/test_no_real_orders.py` still passes untouched. Telegram is not a
  broker and must not become one.
- Any new background task is in `TASK_DESCRIPTIONS` **and**
  `expected_task_names`, or the health page reports it as unexpected forever.
- Full suite green, `npm run build` passes, and any migration applies and rolls
  back on SQLite with `--autogenerate` empty afterwards.
- `README.md` and the relevant `CLAUDE.md` files updated, and anything that
  could not be verified added to the README's "Known gaps".
- Committed with `amrakshay@gmail.com` and pushed to `origin/main` over SSH. No
  JIRA prefix, no GitLab MR, no ADR — personal repo.

---

## Decisions still open

1. **Which events send an alert?** Orders placed, stops triggered, the nightly
   decision, a missed run, the token about to expire, a failed bar refresh.
   Sending all of them is noise; the useful default is probably "things that
   changed money, and things that broke". Agree the list before building.
2. **Does a connection have its own table, or keep using `app_settings`?**
   `app_settings` is a flat key-value store with `MANAGED_KEYS` as its guard,
   and two connections fit it fine (`telegram.bot_token`,
   `telegram.channel_id`, …). A `connections` table earns its migration when a
   connection needs rows rather than keys — several channels, or several
   accounts of the same provider. Recommendation: **stay with `app_settings`**
   and revisit when a third connection arrives.
3. **Does the Telegram command allowlist belong in the users table?** There is
   already a users module with roles. Mapping a Telegram user id onto an
   application user would make "who did this" answerable in one vocabulary
   rather than two. Probably worth it if commands ever do anything that matters.

---

## Reference

| Thing | Where |
|---|---|
| The page to build | `frontend/src/pages/ConnectionsPage.jsx` (new) |
| What Dhan's card replaces | `frontend/src/pages/SettingsPage.jsx` (credentials card) |
| Card/panel look to follow | `frontend/src/components/SwingHealth.jsx`, `frontend/src/pages/SystemHealthPage.jsx` |
| Encrypted settings + masking | `backend/src/settings/services/settings_service.py`, `crypto_service.py` |
| Runtime secret scrubbing | `backend/src/log_redaction.py` |
| A one-endpoint external client to copy | `backend/src/market/services/dhan_token_client.py` |
| A background task to copy | `backend/src/settings/services/token_refresh_service.py` |
| Task registration | `backend/src/health/services/task_inspector.py` |
| Page → role gating | `backend/conf/role-pages.json`, `backend/src/users/services/role_service.py` |
| The safety scanner and its allowlist | `backend/tests/test_no_real_orders.py` |
| No-secrets assertions | `backend/tests/test_no_secrets_in_logs.py` |
| Telegram Bot API | https://core.telegram.org/bots/api — `getMe`, `getChat`, `sendMessage`, `getUpdates` |
| Login | `trader@abc.com` / `APP_ADMIN_PASSWORD` from `.env` |
