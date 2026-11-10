# VCP integration plan — `nse-vcp-swing`

**Status:** phases 0-4 built 2026-11-09 (uncommitted); phase 5 (portfolio, enable, arm) waits for the operator and for Dhan data.
**Goal:** run the VCP rule set from `experiments/pattern-detection/FINAL_LOGIC.md`
as a third automated strategy, paper only, in its own ₹10,00,000 portfolio.
**Blocked on:** the Dhan Data APIs plan (DH-902). The build can proceed; live
running cannot.

---

## 1. What runs, in one paragraph

Every morning after the bar refresh, scan the Nifty 500 on finished daily bars
for VCP bases in the `forming` state (3+ contractions, Trend Template ≥ 6/7,
score ≥ 0.65, stop ≥ 0.5 ATR14 below entry). Buy new signals at the open,
₹1,50,000 each, for as many as available cash allows. Each position carries
two exits in sequence: a **static stop at the final contraction's low**, watched
live during the session, and, once price has closed above the pivot, an
**exit on the first daily close below the 50 EMA**, with the contraction low
kept as a hard floor. A 250-session holding cap is the backstop. No regime
gate: it was tested and made results worse (`VCP_REGIME.md`).

## 2. The one thing that differs from the backtest — measure it first

The backtest **buys at the close of the signal bar** and **exits the trail at
the close of the bar that broke the EMA**. Live, neither is possible: the
nightly runs at 08:35 (deliberate, Dhan's EOD bars arrive late), so both
fills happen at the **next session's open**.

**Phase 0 (before any backend code):** re-run `scripts/vcp_final.py` with
next-open fills for entries and trail exits, and with real NSE delivery
charges in place of the flat 0.3%. If ₹1.5L / fixed notional drops well below
the current 20.4% CAGR / −14.5% drawdown, we decide before building, not after.
About one hour.

## 3. What is reused, and what is new

| Need | Reuse | Notes |
|---|---|---|
| Daily bars for the Nifty 500 | `daily_bars` + `DailyBarRefreshService.refresh_strategy` | Same universe file as Swing and BTST. Symbols already current are skipped, so a third caller adds almost no Dhan requests. VCP must not depend on Swing being enabled: it calls the refresh itself. |
| Universe, instrument sets, charges, margin, market hours, closing auction | Copied YAML blocks from `nse-btst-overnight.yaml` | `conf/strategies/nse-vcp-swing.yaml`; rate card `nse-equity-delivery`. |
| The clock | `SwingScheduler` + `strategy_modules.MODULE_HOOKS` | Register `src.vcp.services.module_hooks` with `tick_strategy` / `detect_missed` and the six policy/setting hooks, the way BTST does. No new clock. |
| "Done today" and missed-run reporting | `_succeeded` + a `ran_on_day`-style lookup on VCP's own session table | Same guard pattern as Swing (CLAUDE.md §4). |
| Placing paper orders | `submit_paper_order(strategy_key="nse-vcp-swing", portfolio_id=…)` | A Nifty 500 name now belongs to three strategies, so the key is always passed. Closes resolve from the position. |
| "May this order fill now?" | `market_clock.can_execute_continuously`, per instrument | Refused orders are journalled, not queued (CLAUDE.md §4). |
| Portfolio and cash | `portfolios`, `cash_ledger`, `balance_service` | New portfolio "VCP Swing", ₹10,00,000 opening deposit, linked to the strategy. Sizing reads `available` from `balance_service`, never recomputes it. |
| Enable / arm switches, Strategies page | `feature_toggles`, existing page | Appears automatically once the YAML has an `automation` block. |
| Live prices for held names | Feed manager, `subscription.kind: positions` | Same as Swing: subscribe only what is held. |
| ATR14, Wilder smoothing, SMA | `src/swing/services/indicators.py` | Add `ema()` there; nothing else needed. |
| Alerts (fills, missed runs, refused renewal) | `alert_service` → `alert-dispatcher` → Telegram | Rows first, as everywhere else. |
| Positions, Order History, Reports, Notes | Existing pages | Already filter by strategy and portfolio. |
| Frontend page shell | `BtstOvernightPage.jsx` tab layout | New `VcpSwingPage.jsx`. |

**New code:**

1. **The detector, ported into the backend.** `backend/src/vcp/services/detector/`:
   `vcp.py`, `pivots.py`, `trend.py`, plus the small parts of `bars.py`,
   `base.py` and `indicators.py` it needs (about 600 lines). The backend must not
   import from `experiments/`. **A golden test** loads a fixed set of stored
   bars and asserts that the port finds exactly the signals, stops and pivots the
   experiment finds. That test is the guarantee that we are paper-trading the
   strategy we backtested.
2. **Tables (one Alembic migration):**
   - `vcp_sessions`: one row per nightly run, with the session decided and when it ran. This is the idempotence key.
   - `vcp_signals`: every `forming` detection, with its stop, pivot, ATR, score and contraction count, plus what happened to it (`taken`, `skipped_no_cash`, `skipped_tight_stop`, `refused_market_closed`, …). This is the journal.
   - `vcp_stops`: one row per open position: contraction low, pivot, phase (`base` / `broken_out`), breakout date, entry date, session count. Same role as `swing_stops`, but the rule is different.
3. **`vcp_stop_monitor`**: a small live watcher for the static stop, modelled on `SwingStopMonitor`. It is a separate monitor, not that one with a flag, because Swing's stop ratchets and this one never moves. It handles the closing auction the same way: a stop triggered after 15:15 on an F&O name exits at the next open.
4. **`execution_service` (nightly decide, morning act):**
   - **Nightly (after the refresh):** mark breakouts, flag positions whose close is below the 50 EMA after breakout, flag positions at the 250-session cap, detect new signals, size them, and journal everything.
   - **Morning (09:16):** sell flagged positions, then buy new signals while `available ≥ ₹1,50,000`. When there are more signals than cash, go in symbol order and journal the rest as `skipped_no_cash`. Ranking was tested and nothing beat random (`VCP_RANKING.md`).
5. **`vcp_health_service` + Health tab**: is this strategy healthy. Admin-only, same split as the Swing and BTST health tabs.
6. **Page tabs:** Today (signals and what was done), Positions (stop, phase, distance to stop and to EMA), Journal, Health.

**Not touched:** `test_no_real_orders.py` and `test_outbound_hosts.py`. VCP adds no URL and no host. Every price comes from endpoints already on the allowlist.

## 4. Build order

| Phase | What | Done when |
|---|---|---|
| 0 | Re-run the backtest with next-open fills and real charges | You have decided whether to continue |
| 1 | YAML, module registration, migration, detector port + golden test | Golden test passes; strategy shows on the Strategies page, disabled |
| 2 | Nightly decide + journal (no orders) | A run on stored bars (data up to 15 Oct) writes sensible `vcp_signals` |
| 3 | Morning execution, sizing, `vcp_stop_monitor`, EMA exit | Tests: buy, stop-out, breakout→EMA exit, cap exit, no-cash skip, market-closed refusal, closing-auction deferral |
| 4 | Frontend page + Health tab | Checked in the running app on :24601 |
| 5 | Create portfolio "VCP Swing" (₹10L), enable, arm | Full test suite and safety suite pass; Swing/BTST behaviour unchanged |

Phases 1–4 can be built and tested on the bars already stored. Phase 5 is only useful once Dhan data is back.

## 5. Decisions needed (recommendation first)

1. **Trade size:** ₹1,50,000 fixed per position *(recommended: best MAR in the backtest)*, or ₹1,00,000 for more, smaller positions.
2. **Run Phase 0 first:** yes *(recommended: an hour now against weeks of paper trading the wrong numbers)*, or skip and accept the fill-timing gap.
3. **At launch:** enabled and armed *(recommended: it is paper money, and an unarmed run only journals)*, or enabled but unarmed for a week to check the signals by eye.
4. **Strategy key and name:** `nse-vcp-swing` / "NSE VCP Swing" *(recommended)*, or your own.

## 6. Risks to keep in view

- **Slow evidence.** About 44 trades a year, 78% losers, with the profit in about one trade in twenty. A year of paper results will not settle whether it works.
- **Overlap with Swing Momentum.** Same universe and the same "strong stocks" tilt, so the two books will likely fall together.
- **Survivorship bias** in the backtest is still unmeasured. Paper trading is free of it, which is one of the reasons to run it.
- **Three strategies on one universe.** A name held in Swing and VCP is two positions in two portfolios. Correct by design (keyed per portfolio), but it is the first time this will happen in practice. Phase 3 includes a test for it.
