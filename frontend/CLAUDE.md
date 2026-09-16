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

## 5. Conventions

- API access goes through `src/api/` (`client.js` wraps fetch and raises
  `ApiError` with a status; a 401 drops the user to login). No bare `fetch` in a
  component.
- Formatting goes through `src/utils/format.js` (`formatPrice`, `formatQty`,
  `formatCompact`, `formatAge`). Do not inline `toFixed` in JSX.
- The Vite dev server proxies `/api` and `/ws` to `:8000` so the browser sees one
  origin — that is what makes the session cookie work on the WebSocket
  handshake. Keep relative URLs.
- `npm run build` must pass before committing.
