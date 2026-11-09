# FEASIBILITY — Step 0d: intraday option prices

**Answer as of 2026-10-03: the path exists on paper, but it is UNVERIFIED by
fetching.** The probe was refused because the application's stored Dhan token
expired on 2026-10-02 at 15:49 UTC. The option tracks (B3, B4) stay untested.
Nothing is synthesised in the meantime.

## 1. Dhan expired-options data: found in the docs

Read 2026-10-03 at https://dhanhq.co/docs/v2/expired-options-data/.

| Item | Per the documentation |
|---|---|
| URL | `POST https://api.dhan.co/v2/charts/rollingoption` |
| Read-only? | Yes. It is a chart/history endpoint like `/charts/intraday`. The body describes a contract; nothing in it can reach an order, funds or holdings path. |
| Addressing | `securityId` of the underlying (NIFTY = 13), `instrument: OPTIDX`, `expiryFlag: WEEK/MONTH`, `expiryCode`, `strike: ATM, ATM±1 … ATM±10` (index; ±3 for stocks), `drvOptionType: CALL/PUT` |
| Fields | open, high, low, close, volume, **iv**, **oi**, strike, spot, timestamp |
| Interval | 1, 5, 15, 25, 60 minutes |
| History | "last 5 years". If true, that is about 2021-10 onward. |
| Per call | up to 30 days; `toDate` is exclusive |

Not stated in the docs, and needing the probe:
- whether ATM is re-resolved per candle (a *rolling* ATM series, as the name
  suggests) or fixed per request;
- what `expiryCode` 1 and 2 mean;
- the rate limits;
- whether weekly series exist across the 2024-11-20 break.

## 2. The probe (`scripts/probe_expired_options.py`)

It is written, and it was run once on 2026-10-03. It covers 8 contracts:
- NIFTY weeklies in 2019-08, 2021-06, 2021-11, 2023-03 and 2025-03;
- ATM−2 and `expiryCode` 2 for 2025-03;
- a BANKNIFTY monthly in 2022-06.

**All 8 returned `401 DH-901`** ("Client ID or user generated access token is
invalid or expired"). The stored token's own `exp` claim says it expired
2026-10-02 15:49 UTC. The probe now stops at the first 401, so it no longer
sends the rest with a token Dhan has just refused. No cached data was kept.

The application's token renewer (`token_refresh_service`) can extend a live
token but cannot mint one once it has lapsed (`CLAUDE.md` §1). A fresh token
has to be saved by hand on the Settings page.

## 3. The URL and the allowlist

`https://api.dhan.co/v2/charts/rollingoption` is **not** in the backend's
`ALLOWED_DHAN_URLS`. It is named in exactly one module here,
`scripts/probe_expired_options.py`, which lives outside `backend/`. The
backend's safety scanner doesn't scan `experiments/`, so nothing failed.
**Whether to add it to the backend allowlist is the operator's call.** It
would follow the 2026-09-16 precedent: one exact read-only market-data URL.

## 4. The other two paths

- **Live contracts via `/charts/intraday`:** these only work for contracts in
  today's instrument master. Useful for a forward shadow journal, but no help
  with history.
- **Synthesis** (Black-Scholes off the 5-minute underlying, IV anchored to the
  bhavcopy settle, skew interpolated): **declined.** It assumes the intraday IV
  path, and in a selloff that path is half of what a put buyer is paid for. If
  it were ever used, every result would carry a SYNTHETIC label.

## 5. Even if the probe succeeds, the development sample is thin

Five years back from 2026-10 leaves **2021-10 → 2022-12** as the only
development data, and it holds **one** regime-B episode (2021-11 → 2022-07).
Every other bear episode with option data (2023-01, 2024-10 → 2025-04,
2025-07, 2026) is in the holdout. An option result couldn't meet "positive in
a majority of development episodes" on its own data.

There are two honest options:
- accept that the put track can only be **descriptive** on development data;
- or re-split the option track's period before seeing any result. That is an
  operator decision, to be taken before the download, not after.

The cash and futures results (`INDEX_SHORT.md`) already argue against the put:
the window it would be held in drifts up on bear days.

## 6. Real order flow: only forward, and only with a token (proposal, not built)

History of order-book depth, trade-by-trade aggressor side and delta isn't
available from any source used here. Dhan's chart endpoints return OHLCV only,
and historical tick data is sold by NSE as a paid product (price and terms UNVERIFIED). The free route is to
**record it going forward**:

- **What:** an experiment-side recorder (under `experiments/`, not `backend/`),
  subscribing the day's 5 shortlisted names plus NIFTY futures in **Full**
  mode. It stores LTP, last traded quantity, cumulative volume and 5-level depth
  once a second, 09:15–15:30, to a parquet file per day. Roughly 6 hours × 6
  instruments.
- **Where it may run:** `intraday-screener/scripts/sample_spreads.py` already
  did this for 30 seconds with its own connection. Dhan allows 5 connections per
  user and the app holds 1 (`CLAUDE.md` §4). It must never write into the app's
  database or touch `MarketBook`. The app rule against accumulating ticks is
  about the application's hot path and DB, and a file-based research recorder
  stays outside both.
- **How long:** at least 2–3 months, and long enough to contain bear-regime
  days, before any order-flow rule can be pre-registered and tested.
  Development and holdout would both have to come from this forward data.
- **Blocked on:** a fresh Dhan token, and the operator's go-ahead.
