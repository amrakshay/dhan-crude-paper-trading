# CLAUDE.md — frontend

Frontend conventions. Read the root `CLAUDE.md` first.

React 18 + Vite + **MUI v6**, plain JavaScript (no TypeScript).

---

## 1. Theme — it is a port, not a fresh design

`src/theme/tokens.js` holds palette, typography, spacing and shape values
extracted from the **Privacera SaaS portal**'s MUI v4 theme
(`privacera-saas-portal/.../site/theme.js` and `theme_dark.js`). The dark values,
including their WCAG contrast ratios, come from that portal's documented
`DARK_COLORS` block.

- **Never hardcode a colour in a component.** Use `theme.palette.*` or
  `theme.market.*`. If a token is missing, add it to `tokens.js`.
- `theme.market` (`up`, `down`, `upSoft`, `downSoft`, `atm`, `itm`) carries the
  market semantics the portal has no equivalent for. It is attached to the theme
  in `theme/index.js` so components need not import tokens directly.
- The portal's identity is **flat surfaces with borders, not shadows**, 8 px
  radius everywhere, Inter, and indigo `#354bbb` as the single accent. Keep it.
  `MuiPaper` deliberately sets `boxShadow: none`.
- Both light and dark must work. Test both when changing anything visual.
- Do not upgrade toward MUI v4 APIs (`overrides`, `createMuiTheme`) — the portal
  uses them because it is on an EOL version; this project uses
  `components.styleOverrides`.

Prices and quantities get `className="numeric"`, which applies tabular figures
so digits do not jitter as they tick.

**`MuiLink` is wired to `dark.link`, not to the accent.** The indigo `#354bbb`
is a brand colour chosen against a WHITE surface; a link inside a sentence
rendered in it is close to unreadable on the dark one. The `link` token already
existed and was already used by the text and outlined buttons -- `MuiLink` was
simply never overridden, which is why an inline link needs no `sx` now. Use
MUI's `Link` (with `component={RouterLink}` for an in-app route) rather than a
bare `<RouterLink>`, or it renders in the browser's default blue.

**`MuiChip` sets a background on every chip in this theme.** That defeats
`color="primary"`, whose text turns white and lands on the theme's grey -- once
shipped as an unreadable capability chip. Set `bgcolor` and `color` explicitly
with `sx` when a chip needs to stand out.

---

## 2. One WebSocket, and how re-rendering works

`src/market/MarketFeedContext.jsx` opens **one** socket for the whole app,
mounted around the authenticated shell so navigation never tears it down. Do not
open another.

The performance contract, which is easy to break accidentally:

- **Rows live in a `useRef` Map**, not in state. An incoming batch costs a
  `Map.set` per changed row.
- A single `version` counter bump triggers re-render. The server already
  coalesces to one message per 100 ms, so this is ~10 renders/sec regardless of
  tick rate.
- `applyRows` **merges** (`{...existing, ...row}`) rather than replacing, so a
  payload without `depth` cannot erase depth an earlier one delivered.
- Because unchanged rows keep their object identity, `React.memo` on a row
  component skips them. `OptionChainPage`'s `ChainRow` depends on this — an
  80-strike chain re-renders only the handful of rows that ticked. Do not
  destructure rows into new objects in the parent's render.

Use `useMarketRow(securityId)` for one instrument and `useFeedHealth()` for
connection state. Clients may narrow what they receive:

```js
send({ action: 'configure', securityIds: ['565899'], includeDepth: false })
```

**Do not poll the API for tick data.** Positions poll on a 2 s cadence only
because they need server-computed P&L; prices come from the socket.

---

## 2a. The active portfolio

`src/portfolios/ActivePortfolioContext.jsx` holds which portfolio the portal is
pointing at. It sits **beside** `MarketFeedContext`, never inside it: the feed
context has the performance contract in section 2, and a portfolio change or a
balance tick must not re-render every subscriber to it.

- **The selection is sent EXPLICITLY** as `portfolioId` on the requests that
  need it. The server never infers it from the session, because a trade landing
  in the wrong book is exactly what the picker exists to prevent.
- It is remembered in `localStorage` per browser, and every read and write is
  wrapped in try/catch -- a private window throws. A remembered id that has been
  archived falls back to the first active portfolio rather than naming a
  portfolio that cannot trade.
- **The header shows AVAILABLE, not cash.** The difference between them is money
  blocked against open shorts, and showing cash would promise money that cannot
  be spent.
- Anything that moves money calls `refresh()` so the header figure does not lag
  behind the trade.

---

## 3. Honesty in the UI

These are product requirements, not styling choices:

- **`SyntheticBanner` is not dismissible.** When prices are locally generated the
  user must know on every screen.
- **`FeedStatusIndicator` always shows the last-tick age**, not just a connected
  dot. A stale book that looks live is the worst failure this tool can have.
- **Unknown is not zero.** When a position has no live mark, show "no mark", not
  a P&L of 0.00 — the API returns `null` deliberately.
- Order entry shows **estimated charges and the net debit/credit before** the
  confirm button is enabled, and warns when an order would only partially fill
  or would rest instead of filling.
- **The same rule applies to money.** The ticket shows the debit beside the
  active portfolio's available balance and disables Confirm when it does not
  fit. The server refuses it anyway; finding that out after clicking is a worse
  way to learn it.
- **Four figures, never one.** Cash, blocked margin, available and equity are
  shown separately wherever a portfolio's money appears. Collapsing them into a
  single "balance" is what makes a short position's effect invisible.
- **Blocked margin always says "estimate".** It is a configured approximation,
  not what a broker would hold.
- **An equity figure that cannot be computed says "no mark"** and names the
  strategies responsible, rather than valuing an unmarked position at zero.
- **A position whose strategy is switched off is labelled.** Its mark is blank
  rather than stale, because nothing is updating it.

---

## 4. Settings and Connections

**They are two pages and the split is the point.** `/connections` owns anything
this application authenticates to and calls over a network — the Dhan
credentials, the Telegram bot. `/settings` keeps this application's own
configuration, which today is the synthetic-feed switch: a MODE of this
application rather than a credential, and it would sit oddly on a card
describing a connection to somebody else.

Both pages are admin-only, and `/connections` is deliberately NOT strategy-gated
— it is how you fix the credentials the strategies run on.

### Rules that span both

- A secret field is **write-only**. The API returns a mask, never the secret, so
  an empty field means "keep what is stored" — not "clear it". That applies to
  the Dhan access token and the Telegram bot token alike.
- Show where each value came from (`saved here` / `from .env`). Silently
  preferring one source over the other is how configuration becomes a mystery.
- **Live mode is still refused without credentials**, even though the switch and
  the credentials are now on different pages. The Settings page disables Save
  and names Connections, because a form that offers an edit the server will
  refuse is worse than one that does not (§5).

### The Connections page

- **The status pill is LAST KNOWN, and every card carries the AGE of its
  check.** Opening the page costs no network call. The age ticks locally off the
  absolute timestamp (the Settings countdown trick), so a card that has not been
  re-checked visibly ages rather than reading "0s ago" until the next load.
- **Null is not zero here either.** `lastCheckedAt: null` renders as "never",
  not as a fresh timestamp, and `lastCheckOk: null` is a THIRD state — a
  connection nobody has checked gets `Never checked`, not green and not red.
  Six pill states, because two would force "not set up yet" and "set up and
  broken" into the same red.
- **A capability chip means the connection ACTUALLY has it.** Telegram with
  commands switched off carries `ALERTS` and not `COMMANDS`. A chip claiming a
  capability nobody enabled is the page lying about what is switched on.
- **Five distinguishable failures get five messages.** The server sends
  `failureKind` and a sentence naming the fix; the page renders it rather than
  composing "Failed to send".
- **Nothing arriving from the listen test is a RESULT, not an error**, and the
  reasons come from the server in the order they are likely. The page also says,
  *while the window is open*, to DM the bot rather than post in the channel — a
  channel post carries no user, so posting discovers the chat id and teaches the
  operator nothing about their own user id, which is what they came for.
- **Discovering an id grants it nothing.** The listen result offers "use this
  chat" and "map this user" as one-click SUGGESTIONS; applying either is an
  explicit action, and mapping a user still requires choosing which account.
- The token expiry countdown ticks locally off the absolute `expiresAt`, rather
  than re-fetching every second.

## 4a. The Alerts tab

`src/components/AlertsPanel.jsx` is rendered by BOTH the System Health page and
the Swing Momentum page, the same way `JobProgress.jsx` is shared by two tabs
and for the same reason: they are the same list filtered, and two components
would drift into describing the same rule differently.

- **Read-only, and it says so.** No edit, no delete. What gets alerted is a
  property of the build. The panel states that rather than leaving somebody
  hunting for a switch that does not exist.
- **Never fired is not zero.** A rule with no stored row renders "never", not a
  timestamp and not a count. The age ticks locally off the absolute timestamp
  (the Settings countdown trick) so a rule that has not fired again visibly
  ages.
- **Recorded is not delivered.** The banner reports them separately, because a
  panel that conflated them would look healthy while every message was being
  dropped.
- **Scope decides the page.** A strategy's tab shows only rules about that
  strategy; process-wide ones stay on System Health. Do not repeat one onto
  both -- the server filters on `alerts.strategy_key` and the two pages would
  disagree the moment one changed.
- Both tabs are **admin-only** and fetch on their page's EXISTING poll rather
  than adding one. Their failure is kept off the page's `error` state, so a
  hiccup on an admin-only read cannot blank the tab beside it.

---

## 5. Roles in the UI

- **The sidebar is not the whole route table.** `/profile` is a real route
  with a real `RoleRoute` gate and is deliberately NOT in `navigationItems`:
  a page about the signed-in person does not belong in a rail of pages about
  trading. It lives in the header's account menu, which also carries Sign out
  -- one menu rather than a menu item and a stray icon doing the same thing.
  `pagesOutsideTheSidebar` in `navigation.js` records the exception so the next
  reader does not "fix" the omission. The menu item is still gated off the
  server's `pages` list, like everything else.
- **The landing route is `/positions`, not `/live`.** `/live` is the Crude Oil
  screen and is gated behind the MCX strategy, so it is not somewhere to land
  when that strategy is off. Positions is ungated and is never withdrawn by a
  toggle, which is what makes it a safe default.
- **The sidebar and the routes are driven by the server.** `/auth/me` returns
  the pages the role may see -- `backend/conf/role-pages.json` INTERSECTED with
  the pages the enabled strategies and capabilities grant;
  `visibleNavigationItems(pages)` filters `navigation.js` and `RoleRoute` gates
  each route. Do not hardcode a role check in a component, and do not add a
  second check for a disabled strategy: `pages` already reflects it.
- **After a toggle, re-read the session.** The page list has just changed, and
  leaving a dead nav item behind is how a user finds a page whose every request
  refuses.
- **This is presentation, not access control.** The API refuses what the role
  may not do regardless of what the UI shows. Never treat a hidden nav item as
  a security boundary, and never skip the server-side check because the button
  is hidden.
- `ChangePasswordGate` renders **instead of** the shell when the session says a
  password change is owed, so there is nowhere to navigate around it. The
  backend enforces the same rule with a 403 on every other endpoint.
- The email field is disabled everywhere it appears, and the Users and Profile
  pages never send `email` on an update — the server rejects a changed one, and
  a form that offers an edit it will refuse is worse than one that does not.

---

## 5a. The price chart

`src/components/PriceChart.jsx` uses TradingView Lightweight Charts, pinned to
**5.2.1** in `package.json`. Pin it, and write against the version installed:
v5 replaced `chart.addCandlestickSeries(...)` with
`chart.addSeries(CandlestickSeries)`, and almost every example online is still
v4.

- **Candles never enter React state.** History goes into the series once with
  `setData`; after that only `series.update()` is called, which is an imperative
  call rather than a render. Putting candle arrays in state would re-render the
  page on every tick and break section 2's contract. The raw bars are kept in a
  `useRef` so the volume pane's per-point colours can be rebuilt on a theme
  toggle without refetching.
- **The volume pane is chart pane 1** (`chart.addSeries(HistogramSeries, opts, 1)`),
  coloured per bar from `theme.market.upVolume` / `downVolume` — the `*Soft`
  tokens are row tints and vanish when drawn as a thin bar. The feed publishes
  the session's cumulative volume, so the forming bar's volume is the growth in
  that counter since the bar opened, rebased on rollover and whenever the
  counter goes backwards (a new session).
- **The chart pauses rather than inventing, except where pausing is the bug.**
  A stale feed stops the forming bar entirely. A closed exchange stops it too
  *only when the prices are real* — the SYNTHETIC feed exists to exercise the
  stack outside MCX hours (see `synthetic_feed.py`), so freezing it there
  freezes the chart exactly when it is most useful. A chip in the chart header
  says which of the two paused it, because a chart that stops silently looks
  broken. Rollover is what bounds a bar's volume: block it and the last bar of
  the session grows forever.
- **`<LiveCandle>` renders `null`.** It is the only part of the chart that
  subscribes to the feed, so the chart chrome does not re-render at ~10/sec.
  Keep the subscription there.
- **No second WebSocket, no polling.** History is one request per (instrument,
  timeframe); the forming bar comes from the shared feed context.
- **Timestamps go through `src/utils/chartTime.js`.** The library renders every
  time in UTC and has no timezone option, so IST bars are shifted by a fixed
  +05:30 on the way in. IST has no daylight saving, which is what makes the
  shift exact. Do the arithmetic nowhere else.
- **Attribution is mandatory.** The library is Apache-2.0 with an attribution
  clause: `attributionLogo` stays enabled and the "Charts by TradingView" link
  stays under the chart. See `frontend/NOTICE`.
- **Honesty applies to the chart too** (section 3). Synthetic bars are labelled
  on the chart itself, not only by the page banner — a chart is exactly the
  thing that gets screenshotted away from its banner. An aggregated timeframe
  says it was aggregated, a series with no volume says so, and a bar with no
  reported volume is left out of the pane rather than drawn as zero.

---

## 5b. Trading from the chart

`ChartTradeHud.jsx` is the buttons and the position strip, `BracketLines.jsx`
the draggable SL/TP lines, `useChartTrading.js` the state.

- **The cost is shown before the click, because there is no confirm step.**
  One-click entry does not exempt this from section 3's rule -- it moves it.
  The contract, premium, estimated charges and net debit for each button are on
  screen continuously, from `/chart-trading/preview`.
- **`useChartTrading` polls at 2 s, the same as Positions and for the same
  reason**: charges and realised P&L are server-computed. Prices still come from
  the socket. The unrealised leg is recomputed in the HUD from the live option
  mark so the number moves with the feed, using the server's own inputs and the
  same formula -- the server's figure replaces it on the next poll.
- **lightweight-charts has no draggable price line.** The line is a native
  price line; the handle is an HTML chip positioned with `priceToCoordinate()`
  and dragged back through `coordinateToPrice()`. Handles are repositioned by
  mutating `style.top` on an animation frame -- through React state that would
  be a 60 fps re-render of the chart chrome.
- **Nothing is armed mid-drag.** The server is told the new level on pointer-up.
  A poll that lands during a drag must not yank the line out of the hand, which
  is what the `draggingRef` guard is for.
- **A level that scrolls out of the visible range pins to the edge of the price
  pane**, rather than drifting down over the volume pane.
- **`+ SL` places, the handle moves.** A button that says "Move" but jumps the
  line to a default distance is a lie; when a level exists the button reads
  "Reset".

---

## 5c. The system health page

`src/pages/SystemHealthPage.jsx`, admin-only (`/health` in
`backend/conf/role-pages.json`, and `require_admin` on the routes -- the
sidebar is presentation, section 5).

- **It polls at 5 s, slower than Positions' 2 s.** Nothing on it changes
  meaningfully faster, and a health page that is itself a load source is
  reporting on a system it distorted. It shows an "as of" age that ticks
  locally once a second (the Settings countdown trick) so a stalled poll is
  visible, plus a manual Refresh and a switch to stop polling entirely.
- **It uses no market data and opens no socket.** Everything comes from
  `GET /api/healthcheck/system`. Do not wire it to `MarketFeedContext` -- a
  page that reports on the feed must not depend on the feed being healthy.
- **Section 3's honesty rules apply hardest here.** In synthetic mode the
  upstream card says "no upstream connection" instead of rendering a healthy
  socket; the Credentials card shows the *effective* configuration and warns
  when saved settings are not in effect; "measuring..." is shown for a CPU
  reading that has no previous sample rather than 0%; and a resource figure
  that cannot be read says so rather than showing a dash that reads as zero.
- The last-message age is rendered as **headroom against Dhan's 40 s drop**,
  with a bar, because the raw integer means nothing without the threshold.
- Numbers go through `src/utils/format.js` (`formatAge`, `formatBytes`,
  `formatDuration`, `formatCompact`) and colours through `theme.market.*` /
  `theme.palette.*`. `formatDuration` is uptime (days and hours);
  `formatCountdown` counts down to a deadline. They are not interchangeable.

---

## 5d. The Swing Momentum page

`src/pages/SwingMomentumPage.jsx`, and the arming control on
`StrategiesPage.jsx`. This is the decision journal of the only strategy that
trades with nobody watching, so section 3's honesty rules apply harder here
than anywhere except the health page.

- **It polls at 10 s and opens no socket.** The ranking changes once a session
  and the stops are recomputed once a night; nothing on this page moves at tick
  speed, and a page that is a RECORD must not depend on the feed being healthy
  to show what was decided. Marks come from the server's own
  `BalanceService.mark_for`, not from `MarketFeedContext`.
- **Null is rendered as "not measured", never as 0.** A breadth the server
  could not measure, a slot count of null, a position with no mark, an equity
  figure `BalanceService` withheld -- each says so. `breadth: null` and
  `breadth: 0` are different answers and only one of them is a reason to hold
  cash.
- **ENABLED and ARMED are two chips, not one.** A module with no `automation`
  block gets NO arming control at all -- there is nothing to arm, because every
  order in it comes from a person. Arming gets its own confirmation dialog
  carrying the server's warnings, the same treatment switching a strategy off
  gets and for the opposite reason.
- **"No live track record" is on the page, above the numbers.** The
  specification's 19.9% is a survivorship-biased, in-sample backtest of a rule
  that has never traded a rupee, and showing this book's CAGR beside it without
  saying so invites exactly the wrong comparison. The server puts the sentence
  on every payload; the page does not compose its own.
- **A withheld CAGR says why.** When money moved into or out of the portfolio
  after the first trade, CAGR, drawdown and MAR come back null with a note -- a
  deposit is not a gain and a withdrawal is not a drawdown.
- **The "How it works" tab restates no number.** `SwingExplainer.jsx` renders
  `GET /api/swing/explain`, which reads every threshold, multiple and lookback
  out of the strategy's own YAML. Hardcoding "3.5 × ATR" in JSX would be a
  second source of truth (root `CLAUDE.md` §7) and it would go on saying 3.5
  for as long as it took someone to notice the configuration had changed. The
  prose explains WHY a rule exists; the values always come from the payload.
- **Its diagrams are inline SVG in theme tokens**, not a charting dependency
  and not a hardcoded hex — five static pictures do not justify a library, and
  `theme.palette.*` / `theme.market.*` is what makes them legible in both
  modes without a second set of assets. Where the live snapshot is available
  they are drawn with TODAY's numbers: the funnel shows the real filter census
  and the breadth ramp marks where the market actually is. A diagram of the
  rule in the abstract teaches less than the same diagram with this morning's
  market in it.
- **The Live tab says what it is doing NOW, not only what it decided.** The
  status strip carries the market state and the IST clock, what the scheduler is
  doing this second, what it will do next and in how long, what it last did, and
  the stop monitor's line. The countdown ticks LOCALLY off the absolute
  timestamp the server sends (the Settings trick, §4) so a stalled poll shows up
  as a clock running past a run that never happened. §3 applies hard: a
  countdown that cannot be computed says so rather than showing 00:00; "the
  monitor is not running", "idle" and "watching nothing" are three states and
  read differently; and an enabled-but-unarmed strategy says, in the strip, that
  it will decide and place nothing. The activity log under it is the scheduler's
  in-memory status dict, not a second journal -- it says so, because it starts
  empty after a restart while the decision history below does not.
- **A gate that looks ON must never be shown while nothing obeys it.** The three
  enforcement switches are runtime state, so the gate card carries `NOT
  ENFORCED` beside the gate's own boolean, and `SwingExplainer` renders the
  EFFECTIVE policy beside the YAML default rather than the file alone. The
  switches themselves live on `StrategiesPage.jsx` beside enabled and armed,
  are offered only for a module that declares an `automation` block, and each
  gets a confirmation dialog carrying the server's warnings -- including the one
  that says re-enforcing the gate does NOT sell the open book.
- **Three tabs, and the Configuration one draws a line.** `SwingConfiguration.jsx`
  holds everything about ONE strategy an operator may change: the three rule
  switches (moved off Strategies & Features on 2026-09-18, so a strategy's
  settings are in one place) and the two clock times. It also states what is
  NOT editable there and why -- P1-P19 and the rebalance cadence live in the
  YAML, and the "How it works" tab renders them read-only from that same file.
  Auto trade deliberately stays on Strategies & Features: it is the one control
  that lets the software spend money on its own and it belongs with the
  strategy's running state.
- **The Configuration tab is a FORM: nothing takes effect until Save.** The
  controls edit a draft, one sticky Save applies it, and one dialog lists every
  pending change with the server's warnings for each before any of it happens.
  Live switches were the first version and were wrong for this page: these
  settings change what software does with money unattended, several only make
  sense together, and an operator could not see what they were about to do
  before it was already true.
  - **The draft holds ONLY the fields actually changed.** That removes the whole
    bug class: with a 10 s poll, a draft seeded with every field either gets
    overwritten mid-edit or stops tracking the server and offers to "save" a
    revert nobody asked for. Setting a field back to the server's value removes
    it from the draft, so it stops counting as a change and starts tracking
    again.
  - **Changes are applied in an order that avoids an illegal intermediate
    state.** The server refuses the off-gate variant together with a relaxed
    regime gate, so the restrictive move goes first in each direction; without
    that, a valid Save fails halfway with a message about a state nobody asked
    for. `orderPolicyChanges` is that rule.
  - **A half-applied save says which half.** Each change is reported applied or
    not applied with its reason, and the draft is dropped and the page re-read
    afterwards rather than assuming the save did what was asked.
  - **The contradictory pair is caught in the draft** so Save is never offered
    for it. The server refuses it too and that refusal is the authority; this is
    so the operator sees it while still deciding.
- **The dialog shows the server's refusal BEFORE the confirm button, and
  disables it.** A form that offers an edit the server will refuse is worse
  than one that does not (section 5), so each pending change asks
  `policy-warnings` / `setting-warnings` first and renders the reason.
- **The screen says "auto trade", "analysis of stocks" and "order placement";
  the code and the database still say armed, NIGHTLY and REBALANCE.** Renaming
  stored run kinds would rewrite history, so the translation lives at the edge
  (`RUN_LABELS` in the page). Keep the two vocabularies apart rather than
  half-renaming either.
- **The FOURTH tab, Health, is the only ADMIN-ONLY part of this page.**
  `SwingHealth.jsx` renders `GET /api/swing/health`. The rest of `/swing` is
  open to a `ROLE_USER` because a journal is history; this one is live
  machinery state and WARNING+ log records, which is the exposure `/health` is
  gated for. The tab is not offered to a `ROLE_USER` and `?tab=health` falls
  back to Live for one -- and that is presentation: the endpoint refuses them
  with a 403 regardless (section 5).
  - **It adds no second poll.** The page already polls at 10 s and fetches the
    health payload on the same cadence. A page that reports on load must not be
    a load source. Its failure is kept OFF the page's `error` state, so a
    hiccup on an admin-only read cannot blank the Live tab.
  - **"Off" is not "broken".** `Verdict` takes three tones, not a boolean: a
    switched-off strategy is working exactly as configured and colouring it red
    would make the panel cry wolf on the state an operator chose. Red is
    reserved for a job that should be running and is not.
  - **Three states where two would lie.** A subscription that was BUILT and
    matched nothing, one that was never built, and one holding instruments are
    three different answers -- a green "0 instruments" would be the page saying
    "no prices are arriving" in the colour it uses for everything being fine.
    Same for the stop watcher: "not running", "no pass yet" and "watching
    nothing" each read differently.
  - **"What it last did" comes from the JOURNAL, not the run list.** The
    scheduler's in-memory list is empty after a restart; the journal is not,
    and a panel showing only the first would say "nothing has run" about a
    strategy that ran last night. Both are on the tab, labelled.
  - **It says what it cannot know.** No audit history, a process-scoped run
    list, and stop-watcher counters kept once per process across every
    strategy. The server sends each note; the tab renders it rather than
    composing its own.
- **`JobProgress.jsx` is rendered by BOTH tabs from the same field.** A long
  job -- the twelve-minute bar refresh -- needs to be watchable from the strip
  somebody is already looking at AND from the Health tab. One component, so the
  two cannot drift into describing the same run differently. §3 applies: no
  progress renders NOTHING rather than an empty bar (a job that has not started
  and one stuck at 0% are different states), a total of zero says "nothing to
  do" rather than dividing by it, the estimated finish is withheld until there
  is enough of the run to extrapolate from and is labelled an estimate, and the
  elapsed clock ticks locally off the server's `startedAtIst` so a job whose
  updates stopped shows a clock running past a bar that is not moving.
- **Four things MOVED off Live when Health was added**, because they are health
  rather than activity: the missed-runs date list (a one-line summary stays,
  pointing at Health), the twenty-row "Recent scheduled runs" table, the
  closing-auction note, and the stop watcher's internals. Live keeps "N stops
  watched, nearest X%". Do not move them back: Live answers "what is it doing",
  Health answers "is it sound".
- **The tab is in the URL** (`?tab=how-it-works`, `?tab=configuration`,
  `?tab=health`), so "read this page" is a link somebody can send.
- **The page is NOT gated by the strategy toggle.** `/swing` is deliberately
  outside `gated_pages()`: a journal is history, and switching the strategy off
  must not hide the record of what it did. Reading it is open to any signed-in
  user; TRIGGERING a run is admin-only, and the API enforces that regardless of
  what the buttons show (section 5).

---

## 6. Conventions

- API access goes through `src/api/` (`client.js` wraps fetch and raises
  `ApiError` with a status; a 401 drops the user to login). No bare `fetch` in a
  component.
- Formatting goes through `src/utils/format.js` (`formatPrice`, `formatQty`,
  `formatCompact`, `formatAge`). Do not inline `toFixed` in JSX.
- The Vite dev server proxies `/api` and `/ws` to `:8000` so the browser sees one
  origin — that is what makes the session cookie work on the WebSocket
  handshake. Keep relative URLs.
- `npm run build` must pass before committing.
