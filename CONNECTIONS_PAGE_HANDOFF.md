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

- Resolve `from.id` on the update to an **application user**, and apply that
  user's existing role. See "Who may issue a command" below — this is settled,
  and it is deliberately NOT a standalone list of ids.
- Nobody is mapped by default. **Commands are off until somebody is**, and a
  Telegram connection with alerts working and no command users is a normal,
  complete state.
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

## The two test buttons, and what the operator should expect

Telegram's detail view gets **Send test message** and **Listen for a test
message**. They prove the two halves separately, which matters because they
fail for completely different reasons. Neither runs on its own — both are
explicit button presses.

### Send test message — proves the outbound half

Calls `sendMessage` to the configured channel. In one press it proves the bot
token, the channel id, and that the bot has permission to post there.

**On screen, immediately:** the button goes busy, then a result that names the
channel it posted to and the time. Not just "Sent" — "Posted to *NSE Swing
Alerts* at 21:58 IST. Check the channel."

**In Telegram, the message must identify itself.** Which installation sent it,
when, and that it is a manual test rather than an alert. Somebody with a
staging copy and a real one needs to tell them apart at a glance, and a bare
"test" tells them nothing:

```
Test message from Dhan Paper Trading
Sent by hand from the Connections page - this is not an alert.
Host: <hostname>  ·  21:58 IST, 18 Sep 2026
If you can read this, alerts will reach this channel.
```

**The failures are distinct and must read distinctly.** A wrong token, a bot
that is not a member of the channel, a bot that is a member but cannot post, a
channel that does not exist, and flood control (`retry_after` is documented on
`ResponseParameters`) are five different problems with five different fixes.
"Failed to send" is not an acceptable message for any of them.

### Listen for a test message — proves the inbound half, and is the setup tool

This is the more valuable of the two and the less obvious. It opens a listening
window — 60 seconds is right — and reports the first update that arrives.

**On screen:** "Listening for 60s. **Send a direct message to @your_bot now.**"
with a live countdown, then the result.

**What it reports, and why this is the point:**

| Field | Why the operator needs it |
|---|---|
| Sender user id | **This is how they discover their own numeric Telegram user id**, which `users.telegram_user_id` needs and which Telegram offers no friendly way to find |
| Sender name / username | So they can confirm it is them and not somebody else |
| Chat id and chat type | Fills in the channel id field without hunting for it |
| The message text | Confirms it is the message they just sent, not a stale one |

Offer both discoveries as one-click suggestions — "map this Telegram user to an
application user", "use this chat as the alert channel" — but **applying them
stays an explicit action**. Discovering an id must never grant it anything.

It should work even when commands are switched OFF, because it is how you set
commands up in the first place.

### What the operator must be told, or this button wastes an hour

**Tell them to DM the bot, not post in the channel.** The Bot API documents
`Message.from` as *"Sender of the message; may be empty for messages sent to
channels"* — **a channel post carries no user**. So posting in the channel
discovers the chat id and teaches them nothing about their user id, which is
the thing they came for. The instruction on screen has to say which to do.

**A bot cannot message a person first.** The operator must have started a
conversation with the bot at least once, or a DM cannot arrive. Say so.

**Privacy mode hides group messages.** In groups a bot sees only commands and
replies unless privacy mode is off. `getMe` returns
`can_read_all_group_messages`, so the card can report this as a fact rather
than leaving the operator guessing — surface it beside the bot name.

**A webhook makes this impossible.** The docs are explicit: `getUpdates` *"will
not work if an outgoing webhook is set up"*. If one is, say that, rather than
timing out and blaming the operator.

**Nothing arriving is a RESULT, not an error.** After 60 quiet seconds, say
"Nothing arrived" and list the reasons in the order they are likely: you posted
in the channel instead of messaging the bot directly, you have never started a
chat with the bot, privacy mode, a webhook is set. A spinner that gives up
silently is the worst version of this button.

**UNVERIFIED — check this before designing around it.** Telegram is widely
reported to allow only ONE `getUpdates` consumer per bot, returning HTTP 409
("terminated by other getUpdates request") to a second concurrent caller. **I
could not find this in the official documentation** — it is not on the
`getUpdates` page — so treat it as likely but unconfirmed. It matters because a
background command poller and a listen-test window would be exactly two
concurrent consumers. The safe design either way is for the listen test to
**borrow the running poller's stream** rather than open its own; if the poller
is not running, the test can poll directly. Confirm the behaviour against a
real bot before relying on either.

---

## What gets alerted

The owner asked for errors, system health, and the rotation's buys and sells
with P&L. Everything below hangs off a place that ALREADY knows the fact --
none of it needs new measurement, and none of it goes near the tick path.

### Trades — the two the owner asked for by name

**`PositionService.apply_fill` is the one place a fill moves a position and its
realised P&L.** Emit from there, not from the order service and not from the
strategy: chart trading, MCX crude and the rotation all converge on it, so one
hook covers every way a share can change hands and none of them can be added
later without inheriting the alert.

**Bought** — symbol, quantity, fill price, total value, the strategy and
portfolio, and **the reason already on the PLACED event**
(`submit_paper_order(reason=...)`), which for the rotation is its rank and
score. Add cash remaining, because the next question is always "what is left".

**Sold** — symbol, quantity, exit price, **realised P&L in rupees and percent**,
how long it was held, charges, and **why it left**. That last one is a stored
fact, not a guess: `swing_stops.exit_kind` is `TRAIL_STOP`, `ROTATION`,
`REGIME` or `MANUAL`. A sale with no exit-kind row is a manual close and should
say so rather than inventing a category.

**Take the P&L from the position row, never recompute it.** `realized_pnl` is
maintained by `apply_fill`, and `reports/services/pnl_service.py` replays fills
to the same number by construction (`backend/CLAUDE.md` §6). A third
calculation in an alert would be a third thing that can disagree, and the one
that disagrees in a message on your phone is the worst place to find out.

### Errors

**Hook the existing buffer, not every call site.** `src/log_buffer.py` is
already a logging handler that sees every WARNING+ record, already stores text
formatted through `RedactingFormatter`, and already never raises out of
`emit()`. An alert sink is a second handler with the same properties. Alert on
**ERROR and above**; WARNING is too chatty to put on a phone.

**Rate limiting and de-duplication are not optional, they are the feature.**
This codebase has produced exactly the flood this would have forwarded, twice
in one evening:

- the swing stop monitor logged *"database is locked"* **once a second** for
  twelve minutes during the first full bar refresh;
- the nightly re-ran every fifteen minutes until it was fixed on 2026-09-18.

Unthrottled, either would have sent hundreds of messages and hit Telegram's
flood control. So: collapse identical errors, send the first immediately, then
a count ("this has now happened 47 times"), with a floor between messages.
Key the de-duplication on the logger name plus a normalised message, not the
raw text, or a counter in the message defeats it.

**The alert path must never alert about its own failure.** A Telegram delivery
error that logs an ERROR that sends an alert that fails is a loop. Exclude the
alerting logger from its own sink, explicitly, with a test.

### System health — what I would actually send, in priority order

The owner left the list to me. These are the ones where not knowing costs
something real:

1. **A background task that should be running is not.** `task_inspector`
   already computes `missing` against `expected_task_names`. A dead
   `swing-scheduler` on an ARMED strategy means nothing decides and nothing
   trades, silently, and it is the failure the whole Health tab was built to
   surface. This is the single most valuable alert here.
2. **Dhan token renewal failed, or the token has lapsed.** Renewal is automatic
   now, so its FAILURE is the event. Distinguish "will retry" from "cannot
   renew, a human must paste a new token" -- only the second needs waking
   somebody up.
3. **The feed is stale or disconnected while a market is open.** Stale prices
   that look live are described in `frontend/CLAUDE.md` §3 as the worst failure
   this tool can have.
4. **A scheduled session was missed.** The detector already exists and already
   reports; this just forwards it.
5. **Daily bars too stale to trade on.** The rebalance refuses past
   `swing.max_bar_staleness_days`, and `staleness_reason()` is the exact
   sentence. Send it when the nightly notices, not at 09:16 when it is too late
   to fix.
6. **An order was refused because the market was shut**, and **a stop that
   triggered but deferred to the next open**. Both are decisions the strategy
   took that a human would want to know about the same evening.
7. **The application started.** Low volume, high signal: a restart you did not
   perform is worth a message.

Do not alert on: anything WARNING-level and routine, the synthetic feed being
on (the UI already says so permanently), or a strategy being switched off by
somebody who is looking at the screen at the time.

### How it is delivered

**An `alerts` outbox table, not a direct call.** Write the alert row where the
fact happens, and let a delivery task drain it. Three reasons, all of which
have bitten this codebase in other forms: a fill must never block on somebody
else's HTTP; an alert raised during a crash still needs to arrive after the
restart; and "what did it tell me, and did it arrive" should be answerable.
The row carries the status (`PENDING` / `SENT` / `FAILED` / `SUPPRESSED`), the
attempt count and the reason it was suppressed, so de-duplication is visible
rather than silent.

---

## The connections table

The owner asked for a real table rather than more `app_settings` keys. Three
tables, and the split matters.

```
connections
  id                   INTEGER PK
  provider             VARCHAR(32)  NOT NULL   -- 'dhan' | 'telegram'
  name                 VARCHAR(80)  NOT NULL   -- the operator's label, shown on the card
  enabled              BOOLEAN      NOT NULL
  last_checked_at      DATETIME     NULL       -- decision 3: last known, with its age
  last_check_ok        BOOLEAN      NULL       -- NULL = never checked, which is not false
  last_check_detail    VARCHAR(500) NULL
  updated_by_user_id   INTEGER      NULL FK users.id ON DELETE SET NULL
  created_at / updated_at
  UNIQUE (provider, name)

connection_settings
  id, connection_id FK ON DELETE CASCADE
  key                  VARCHAR(64)  NOT NULL
  value                TEXT NULL          -- plaintext
  encrypted_value      TEXT NULL          -- Fernet ciphertext
  is_encrypted         BOOLEAN NOT NULL
  UNIQUE (connection_id, key)

alerts                                    -- the outbox, see above
  id, connection_id FK, kind, severity, title, body,
  status, attempts, last_error, dedupe_key, created_at, sent_at
```

**Keep `value` XOR `encrypted_value`** and enforce it in the repository, exactly
as `AppSettingRepository` does today. That invariant is why a secret has never
landed in a plaintext column here.

**A row naming an unknown provider is IGNORED on load**, the same property
`MANAGED_KEYS` gives `app_settings`. A stray row must never start influencing
configuration.

**`last_check_ok` is nullable on purpose.** Never checked, checked and failed,
and checked and fine are three states. Two booleans' worth of meaning in one
column would put "we have no idea" and "it is broken" in the same red pill.

**The data migration is the risky part and must be reversible.** Dhan's
`client_id` and `access_token` move out of `app_settings` into a `dhan`
connection row. The token is Fernet ciphertext and moves **as-is** -- do not
decrypt and re-encrypt, there is no reason to have it in memory. Write the
`downgrade()` to put the rows back, and test upgrade→downgrade→upgrade **on a
copy of the real database**, because getting this wrong takes the live feed
down. `backend/data/` holds dated backups; make another first.

---

## Who may issue a command — put it on the users table

This one was left to me, and the answer is **`users.telegram_user_id`**,
nullable and unique, rather than a list of ids hanging off the connection.

The reason is not tidiness. A separate allowlist would be **a second
authorisation model** -- and this codebase already has one, with properties
that took work to get right:

- `require_admin` is what refuses a request, not the sidebar
  (`backend/CLAUDE.md` §10);
- `require_session` re-reads the user on every request, so deactivating or
  demoting somebody takes effect on the NEXT request rather than at token
  expiry;
- the seeded admin cannot be deleted, demoted or deactivated, enforced on the
  `is_seed_user` column rather than by comparing emails;
- a user who owes a password change is refused everything except the endpoints
  that let them stop owing one.

Map the Telegram sender to an app user and every one of those holds for
Telegram too, for free. A parallel list inherits none of them, and the first
time somebody is deactivated they would still be able to arm a strategy from
their phone.

So: **a command is authorised by resolving `from.id` to a user, then applying
that user's existing role.** Read-only commands need any active user. Control
commands need `ROLE_ACCOUNT_ADMIN` -- the same gate the arming endpoint uses,
not a thinner one. An unmapped sender is ignored and logged as refused. A
deactivated user is refused. `updated_by_user_id` on whatever the command
changes is then filled in exactly as the UI fills it.

The listen-test button discovers the numeric id; mapping it to a user stays an
explicit action on the Users page or the connection detail, and granting it is
never a side effect of discovering it.

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
above, a Send test message button, the Telegram-user-to-app-user mapping, and the
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
  no such channel.
- **Send test message**, posting a self-identifying message that names the
  installation and the time, with five distinguishable failure messages.
- **Listen for a test message**, with a countdown, reporting the sender's user
  id and the chat id as one-click suggestions that still require an explicit
  action to apply — and telling the operator to DM the bot rather than post in
  the channel, because a channel post carries no user.
- Alerts out for: the rotation's buys and sells with realised P&L and the exit
  kind, ERROR-level log records (de-duplicated and rate limited, with the
  alerting logger excluded from its own sink), and the seven system-health
  events listed above — a dead background task first among them.
- An `alerts` outbox table, so a fill never blocks on Telegram and an alert
  raised during a crash still arrives after the restart.
- Commands in, authorised by resolving the Telegram sender to an application
  user and applying that user's role — nobody mapped by default, read-only
  commands separated from control commands, control commands requiring
  `ROLE_ACCOUNT_ADMIN` and behind their own default-off switch, and every
  state-changing command journalled with its sender's user id.
- The bot token appears in no log line, with an assertion in
  `tests/test_no_secrets_in_logs.py` added in the same change.
- `tests/test_no_real_orders.py` still passes untouched. Telegram is not a
  broker and must not become one.
- Any new background task is in `TASK_DESCRIPTIONS` **and**
  `expected_task_names`, or the health page reports it as unexpected forever.
- `connections`, `connection_settings` and `alerts` tables, with `value` XOR
  `encrypted_value` enforced in the repository and an unknown provider ignored
  on load.
- The Dhan credentials migrated out of `app_settings`, with a `downgrade()`
  that puts them back, tested upgrade→downgrade→upgrade **on a copy of the real
  database** — getting this wrong takes the live feed down.
- `users.telegram_user_id`, nullable and unique, with commands authorised by
  resolving the sender to a user and applying that user's existing role.
- Full suite green, `npm run build` passes, and any migration applies and rolls
  back on SQLite with `--autogenerate` empty afterwards.
- `README.md` and the relevant `CLAUDE.md` files updated, and anything that
  could not be verified added to the README's "Known gaps".
- Committed with `amrakshay@gmail.com` and pushed to `origin/main` over SSH. No
  JIRA prefix, no GitLab MR, no ADR — personal repo.

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
