# Handoff — arm the Swing Momentum rotation and let it trade live

Read `CLAUDE.md`, `backend/CLAUDE.md` and `frontend/CLAUDE.md` first. They cover
the no-real-orders constraint, the one-connection invariant, the tick-path
performance contract, the strategy/capability/portfolio model, the two switches
(enabled and armed), the module layering, the theme and the UI honesty rules.
`README.md`'s **NSE Swing Momentum** section covers everything the strategy
already does, and `backend/src/swing/README.md` covers the package. Do not
re-derive any of that.

**The strategy specification is**
`~/Workarea/local/pullback/backend/intrday_test_strategy/research2/SWING_MOMENTUM_HANDOFF.md`
— the rules, the parameters P1–P19, the indicator formulas, the cost model and
the backtested results. Its §13 is computed on a panel with a phantom trading
session; `README.md` explains that and it is already handled in code.

Phases 1–10 are **done, verified against the live Dhan token, and pushed**
(`9479b4e`, `1945bdb`). This document is about the next thing: making it
actually trade.

---

## The requirement

> Create a portfolio dedicated to this strategy with ₹10,00,000 and start
> running **live trades** with that money — not just decisions. Entry and exit
> for any trade must happen **in live market hours**, never off-market, so it
> replicates a real market scenario with paper money.

Two thirds of that already exist. The third is the work, and there is a
decision to put to Akshay before any of it matters.

---

## Baseline — do NOT spend time re-running these

```bash
cd backend
.venv/bin/python -m pytest tests/ -q          # 800 passed, 1 skipped, ~5m30s
CONFIG_PATH=conf .venv/bin/alembic heads      # d4a17e6b2c88 (head), 11 migrations
cd ../frontend && npm run build               # passes, ~2s
```

Measured on 2026-09-18, on the commit this document ships with:

| Check | Result |
|---|---|
| Full suite | **800 passed, 1 skipped** in 333 s |
| Opt-in §13 reproduction | **passes** in 216 s (`SWING_SECTION_13=1 .venv/bin/python -m pytest tests/test_swing_ranking.py -k section_13`) |
| Migration up / down / up on SQLite | clean |
| `alembic revision --autogenerate` | empty `upgrade()` — no drift |
| `npm run build` | passes |
| MySQL | **DDL-compile-verified only, never executed against a live server.** Say so; do not imply otherwise. |

The one skipped test is the §13 reproduction, which is opt-in because it
imports 1.1 M rows. It was run separately and passed.

**Never run two `pytest` processes at once.** The temp database and log
directory are named after the process id now, but overlapping runs still cost
an hour on 2026-09-18 by producing a fatal SQLAlchemy traceback that read
exactly like a broken baseline.

**`conftest` enables every strategy and DISARMS every automated one** for each
test (`strategy_state_baseline`), restoring both afterwards. A test that needs
to place orders arms what it needs; a test whose subject IS a shipped default
reads `enabled_by_default` / `automation.armed_by_default` off the definition.

---

## State of the live installation, 2026-09-18 13:45 IST

A server is running on `:8000` from `backend/` with `CONFIG_PATH=conf`, on the
current code, against the real `.env` and `data/paper_trading.db`. Backups at
`data/paper_trading.db.backup-20260918-130607` and `…-082434`.

| | |
|---|---|
| `instruments` | 1,739 — 499 NSE equities + 1,240 MCX crude contracts, all active |
| `instruments.fno_eligible` | 210 of the 499, derived from the master's own FUTSTK rows |
| `daily_bars` | 1,111,241 rows, 501 symbols, 2015-07-01 → **2026-09-17** |
| Portfolios | `Main` (id 1, crude, ₹10,03,881.56) and **`Swing Momentum` (id 2, ₹10,00,000.00, attached to `nse-swing-momentum` only)** |
| `swing_sessions` | 3 — a staleness refusal, a rebalance, a nightly, all for 2026-09-17 |
| `swing_stops` | 0 — nothing has ever been held |
| `orders` | 4, all pre-existing crude orders. **The rotation has never placed one.** |
| Feature toggles | `STRATEGY/nse-swing-momentum = enabled`. **No `AUTOMATION` row: it is NOT ARMED.** |
| Feed | live Dhan, CONNECTED, not synthetic |

**The portfolio the requirement asks for already exists.** It was created
during the phase 6–10 verification, with exactly ₹10,00,000 so the equity curve
is comparable with the backtest's starting capital (decision 6 of the phase 1–5
handoff). Do not create a second one.

---

## The decision to put to Akshay FIRST — do not start coding around it

**Arming the strategy today will produce zero trades, and that is correct
behaviour.**

The regime gate (P8) has been OFF since 2026-02-27. As of the 2026-09-17
session, live:

| | |
|---|---|
| NIFTY 50 close | 23,270.60 |
| NIFTY 200-session SMA | 24,501.66 |
| Shortfall to reclaim | **5.29%** |
| 63-session return (P9) | −3.71% — new entries blocked even if P8 flips |
| Breadth | 47.9% (215 of 449) → the ramp allows 4 slots |
| **Effective slots** | **0**, because the gate is off |

So: arm it, and every session it will record "the regime gate is OFF, the book
holds 100% cash by design" and place nothing. That is the strategy working, not
the strategy broken. The gate is off about 23% of all sessions and the current
episode is 136+ sessions long.

There are exactly three honest ways to get live trades, and **only Akshay can
pick**:

| Option | What happens | The cost |
|---|---|---|
| **A. Arm it and wait** | Nothing trades until the NIFTY reclaims its 200-day SMA — 5.29% away — and its 63-day return turns positive. Could be weeks or months. | Nothing to build. The system is already right; it is the market that is not. |
| **B. Enable the V3b off-gate variant** | `off_gate.enabled: true` in the strategy YAML. Up to 3 positions while the gate is off, at a 10% momentum floor, with the 63-day filter dropped. It would trade **this week**. | Akshay's own research tested 13 variants and **not one beat holding cash**. V3b's individual trades are good — 38 trades over 11 years, 61% win, mean +7.81% — and its total is still LOWER than baseline, because capital committed to a bear rally is not available at the regime flip. Enabling it is an informed choice against his own finding. It is already implemented, tested and switched off. |
| **C. Paper-trade a second, looser module** | A separate strategy YAML with a gate that is currently on, so the machinery gets exercised without corrupting this strategy's record. | A new module to specify, and a second book to reason about. Nothing in the framework prevents it. |

**Do not silently pick one.** Ask, with those numbers in front of him. If he
picks B, the UI already warns about it where the switch lives and the
explainer page says so — but say it again in the reply.

---

## The actual work

### 1. Orders must not be placed outside live market hours — THIS IS THE GAP

The requirement is explicit and the code only half satisfies it.

**What already holds the line:**

- `swing-stop-monitor` will not evaluate a stop unless
  `market_clock.is_market_open(definition)` — verified at
  `stop_monitor.py:160`.
- It will not send an exit unless `market_clock.can_execute_continuously(...)`
  — `stop_monitor.py:254` — which also covers the Closing Auction Session for
  F&O-eligible names (continuous trading ends 15:15 since 3 Aug 2026).
- The scheduler only fires the rebalance at `schedule.rebalance_at` (09:16
  IST), inside the session, and only on a trading weekday.

**What does not:**

- **`SwingExecutionService.run_rebalance` has NO market-hours check at all.**
  Grep it: `execution_service.py` does not import `market_clock`. It checks
  enabled, bar staleness and idempotence, and then places orders.
- `POST /api/swing/runs/rebalance` therefore places real paper orders against a
  stale book at any hour an admin clicks the button. During the phase 6–10
  verification this did not bite only because the gate was off and there was
  nothing to buy.
- `OrderService.submit_paper_order` has no market-hours check either, for any
  strategy. Chart trading has the same property today.

**What to build.** Refuse to place an order outside continuous trading, on the
swing execution path, and record why rather than throwing away the decision —
the same shape the stop monitor already uses:

- `run_rebalance` should check `market_clock.can_execute_continuously` (per
  instrument, because F&O eligibility changes the answer after 15:15) before
  each order, not once for the run.
- A rebalance triggered outside hours should still DECIDE and JOURNAL — the
  decision is valid, it is the execution that is not — and record
  "not placed: outside continuous trading" per intent. An unarmed run already
  produces the identical journal with no orders; this is the same pattern with
  a different reason.
- Think hard about whether to put the guard in `submit_paper_order` instead.
  Arguments both ways: a generic guard is harder to bypass, but it would change
  MCX crude's behaviour and the chart's one-click entry, neither of which asked
  for it. **If you make it generic, it must be a per-strategy policy read from
  the YAML, and the MCX module's behaviour must not change** — there is a suite
  pinning exactly that (`tests/test_swing_does_not_disturb_crude.py`).
- Tests to write, in this repo's negative style: an order is NOT placed at
  22:00; an order IS placed at 11:30; an F&O-eligible name's order is NOT
  placed at 15:20 while a non-F&O one IS; the journal records the refusal with
  the time and the reason.

### 2. Arm it

Once Akshay has chosen from A/B/C:

- Arming is a `feature_toggles` row under the `AUTOMATION` scope, flipped from
  **Strategies & Features** (the switch, the confirmation dialog and the
  server's warnings are all built) or `PUT /api/strategies/{key}/armed`.
- Do NOT arm it by editing `automation.armed_by_default` in the YAML. That key
  is only the state a brand-new installation starts in, and changing it would
  arm a fresh checkout by default — the opposite of the decision it encodes.
- Arming does not restart or resync anything; the rebalance and the stop
  monitor read `registry.is_armed()` at the moment they would place an order.

### 3. Keep the process alive, or the bars go stale

The nightly at 18:15 IST is what keeps `daily_bars` current, and the rebalance
**refuses to trade on bars more than `swing.max_bar_staleness_days` (5) old**.
A laptop that sleeps through 18:15 for a week will stop the rotation trading
and say why.

- Missed runs are detected against the regime index's own bar dates and
  reported at startup, on the Swing Momentum page and on the health page. They
  are never silently re-decided.
- To recover by hand:
  `CONFIG_PATH=conf .venv/bin/python scripts/refresh_daily_bars.py --strategy nse-swing-momentum`
  — and pass `--as-of YYYY-MM-DD` if you run it **during** a session, or Dhan
  will hand back today's forming bar and it will be stored as a daily bar.
- Worth considering and asking about: whether this should run under `launchd`
  so it survives a reboot. Out of scope unless Akshay wants it.

### 4. Watch the first real trade closely

Nothing in this system has ever placed an order for this strategy against live
NSE quotes. When it does:

- Check the fill against the touch and the depth. The backtest assumes
  `open × (1 ± 0.05%)` per side; this crosses the spread, walks five levels and
  pays `trading.slippage_ticks` adversely on top. §14.3 of the specification
  asks for exactly this comparison. **Do not tune the simulator to agree** —
  report the gap.
- Check the chandelier stop was set at entry (`entry − 3.5 × ATR14`) and that
  `swing_stops` has a row with the ATR it used.
- Check the order's `PLACED` event carries the strategy's reason.
- Check the cash ledger moved by the right amount including charges, and that
  `BalanceService` is still the only thing computing equity.

---

## Ground truth you must not rediscover the hard way

All verified on 2026-09-18 against the live instrument master, a live Dhan
token and a live feed.

- **Dhan's security ids are unique per SEGMENT, not globally.** Id 13 is NIFTY
  in `IDX_I` and ABB in `NSE_EQ`, and ABB is a Nifty 500 constituent. The
  regime index is a reference instrument and is deliberately never a row in
  `instruments`.
- **The universe is effectively 499.** `JBCHEPHARM` has no NSE row and stopped
  trading 2026-07-16; it is reported as unresolved on every refresh, never
  dropped silently. `HEG` and `HFCL` have universe-file ids that disagree with
  the master — the master wins, and whether the research panel was fetched with
  the wrong ids is **unverified**.
- **210 of the 499 are F&O-eligible**, matching the specification's own figure,
  derived from the master's FUTSTK rows on every refresh.
- **A full live bar refresh is 746 s** for 499 symbols at 0.6 s each. Do not
  parallelise it into a ban. It commits per symbol — a single twelve-minute
  transaction made the stop monitor log "database is locked" once a second for
  the whole run.
- **The bootstrap import is 1,088,241 rows in 211 s.** Already done; do not
  re-run it.
- **`resync()` leaves a rebalance's pins subscribed** while the book is
  otherwise empty, because an empty target set is ambiguous ("master not
  ingested" vs "nothing enabled") and unsubscribing everything would be wrong.
  About ten subscriptions out of five thousand until the next resync that
  resolves something. Do not weaken that guard.
- **0.65 − 0.35 is not 0.30**, and the breadth span is configured for that
  reason.
- **A symbol with no bar on the session is skipped, never forward-filled.**

---

## Traps

- **Rule #1.** No broker order path, not behind a flag, not as a stub. Local
  operations are `submit_paper_order` / `cancel_paper_order`. If
  `tests/test_no_real_orders.py` fails on a name you wrote, rename yours.
- **`tests/test_no_real_orders.py` and `tests/test_no_secrets_in_logs.py` must
  keep passing**, and every new endpoint needs its no-secrets assertion in the
  same change.
- **Do not make fills more generous.** Several tests assert a fill does *not*
  happen; they are load-bearing.
- **Do not tighten the stop.** §10 of the specification measures every tighter
  variant as worse.
- **Do not tune constants to match the backtest.** It is survivorship-biased
  and in-sample; its own author expects 12–18% live against the 19.9%
  headline.
- **Undefined is not zero**, anywhere — a `None` breadth, a `None` equity, a
  `None` slot count all mean "could not measure".
- **Money is `Decimal`; indicators are float.** Convert at the boundary.
- **The rebalance commits at defined points** because `resync()` opens its own
  session and SQLite blocks on an outer transaction. Do not wrap it in one
  transaction.
- **Committing is not the same as arming.** Pushing code does not start
  trading; only the `AUTOMATION` toggle does.

---

## Definition of done

- Akshay has picked A, B or C, and the reply says which and why.
- No order can be placed by the swing path outside continuous trading, for
  either the rebalance or a stop exit, with the refusal journalled and tested —
  including the F&O 15:15 case.
- The MCX crude module is still unaffected: charges, fills and P&L
  byte-identical, and the ₹0.01 Zerodha divergence still exactly ₹0.01.
- Full suite green, including both safety suites. Migrations apply and roll
  back on SQLite; `--autogenerate` empty afterwards. `npm run build` passes.
- If the strategy is armed and a trade actually happens: the fill, the stop and
  the ledger entry are all shown, and the difference from the backtest's
  slippage model is **reported, not tuned away**.
- `README.md` and the relevant `CLAUDE.md` files updated, and anything you
  could not verify in the README's "Known gaps".
- Committed with `amrakshay@gmail.com` and pushed to `origin/main` over SSH.
  No JIRA prefix, no GitLab MR, no ADR — personal repo.

---

## Reference

| Thing | Where |
|---|---|
| Strategy spec | `~/Workarea/local/pullback/backend/intrday_test_strategy/research2/SWING_MOMENTUM_HANDOFF.md` |
| Phases 1–5 handoff | `SWING_MOMENTUM_IMPLEMENTATION_HANDOFF.md` |
| Phases 6–10 handoff | `SWING_MOMENTUM_PHASE_6_10_HANDOFF.md` |
| The package | `backend/src/swing/README.md` |
| Market-hours helpers | `backend/src/strategies/services/market_clock.py` |
| The gap to close | `backend/src/swing/services/execution_service.py` |
| The pattern to copy | `backend/src/swing/services/stop_monitor.py` |
| Login | `trader@abc.com` / `APP_ADMIN_PASSWORD` from `.env` |
| The page | `http://127.0.0.1:8000/swing` (and `?tab=how-it-works`) |
