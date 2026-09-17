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

---

## 4. Settings page

- The access token field is **write-only**. The API returns a mask, never the
  token, so an empty field means "keep what is stored" — not "clear it".
  Clearing is an explicit `clearAccessToken` flag.
- The expiry countdown ticks locally off the absolute `expiresAt` the API
  returns, rather than re-fetching every second.
- Credentials are only required when the synthetic toggle is off; the Save
  button's enabled state encodes that rule, and the server enforces it again.
- Show where each value came from (`saved here` / `from .env`). Silently
  preferring one source over the other is how configuration becomes a mystery.

## 5. Roles in the UI

- **The sidebar and the routes are driven by the server.** `/auth/me` returns
  the pages the role may see (from `backend/conf/role-pages.json`);
  `visibleNavigationItems(pages)` filters `navigation.js` and `RoleRoute` gates
  each route. Do not hardcode a role check in a component.
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
