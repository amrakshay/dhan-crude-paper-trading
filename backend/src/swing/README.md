# `src/swing/` — the NSE Swing Momentum rotation

The strategy-specific half of `conf/strategies/nse-swing-momentum.yaml`.
Everything generic — orders, positions, portfolios, charges, the feed — lives
where it always did; this package holds only what is true of *this* rule.

Read the specification first. It is the single source of truth for the rules,
the parameters (P1–P19), the indicator formulas, the cost model and the
backtested results:

```
~/Workarea/local/pullback/backend/intrday_test_strategy/research2/SWING_MOMENTUM_HANDOFF.md
```

## What is here

| Module | Role |
|---|---|
| `services/indicators.py` | SMA, Wilder ATR, ADV20, momentum, score — pure Python, no pandas |
| `services/swing_parameters.py` | P1–P19 read from the strategy YAML; the only place that reads them |
| `services/ranking_service.py` | Regime (P8/P9), breadth (P10), slots (P11) and the full ranking |
| `services/rebalance_planner.py` | The sell list and the buy list, worked out as plain values. Decides; places nothing |
| `services/execution_service.py` | The rebalance: sells, then buys, through `submit_paper_order`. The only module here that places an order |
| `services/stop_service.py` | The chandelier stop (P14/P15): set at entry, ratcheted on the close, never down |
| `services/stop_monitor.py` | The `swing-stop-monitor` task: watches the stops, exits the ones that are hit |
| `services/scheduler.py` | The `swing-scheduler` task: the nightly job, the rebalance, and missed-run detection |
| `services/swing_runner.py` | One nightly decision, journalled. Places no orders |
| `services/swing_service.py` | Read models for the page. Decides nothing |

## The two switches

**Enabled** makes the strategy compute, decide and write a decision record
every session. **Armed** is what lets it submit an order. They are separate
because this is the first thing in the application that trades with nobody
watching, and watching it decide for a while before it can spend anything is
the cheapest possible safeguard.

Both are runtime state in `feature_toggles` — `STRATEGY` scope and `AUTOMATION`
scope — overlaid on the YAML's `enabled_by_default` and
`automation.armed_by_default` at startup. `automation` is a FRAMEWORK key, not
a `module_config` block: whether a module trades unattended is something the
Strategies page, the health page and the scheduler all have to know without
understanding momentum. A module that declares no `automation` block at all is
discretionary, offers no arming control, and a stored arming row for it is
ignored.

An **unarmed run journals the identical decision** and places nothing. The
decision rows are the same down to the reason sentence; `order_id` being null
is the only difference, so an armed and an unarmed run can be diffed and only
the orders differ.

## The rebalance

Sells are planned AND EXECUTED before the buys are planned. The backtest
processes pending exits before pending entries (specification §6, mechanic 4)
and sizing is cash-constrained, so a buy funded out of that morning's sale must
see the money. That is why there is no single `plan()`: `plan_sells` and
`plan_buys` are separate calls with the execution of the sells in between, and
getting it wrong looks like a bug in the funds code rather than in the
ordering.

Three refusals the planner makes rather than guessing:

- **Equity that could not be computed blocks sizing entirely.** P13 sizes from
  total equity; `BalanceService` withholds equity when any open position has no
  mark; the honest response is to buy nothing and say why.
- **A buy that does not fit is skipped, not shrunk.** The backtest resizes
  (`size = min(equity / 10, cash)`), which is a different trade from the one
  the rule asked for. This is a deliberate divergence and is in the README's
  known gaps.
- **A candidate with no live price is skipped.** The rule buys at the next
  session's open, and what that costs is a live price or nothing at all — never
  yesterday's close.

Before placing anything the rebalance PINS the names it might trade
(`FeedManager.pin_instruments`) and resyncs, because the fill simulator needs a
depth book for a name before the order rather than after it. About thirty
instruments, never five hundred; the scheduler warms them
`swing.warmup_minutes` ahead of the open and the pins are given back afterwards.

## The chandelier stop

`swing_stops` holds what specification §14.4 says has to survive a restart: the
position, the ATR at entry, the highest close since entry and the current stop.
None of it is recomputable after the fact — the highest close depends on the
entry date and the stop is a running maximum, so a bar restated after a
corporate action would silently move a stop that has already been acted on.

- **The ratchet is one-directional**, and that is the whole point: ATR widens
  after a violent day, so `highest_close − 3.5 × ATR_today` can be LOWER than
  yesterday's stop. `max()` is what stops the exit sliding away from a position
  that has just become more dangerous.
- **Do not tighten it.** Specification §10 measures every tighter variant as
  worse — an 8% initial cap takes CAGR from 19.9% to 15.7%, a breakeven ratchet
  to 14.4%, a 2.5× trail to 15.3%, all three to 10.6%.
- **The Closing Auction Session**, live since 3 August 2026: for F&O-eligible
  names continuous cash trading ends at 15:15 and a call auction runs to 15:35.
  A stop that fires in that window is RECORDED as triggered and its exit waits
  for the next session's open, because this simulator has no model of a call
  auction and filling against the last continuous book would flatter it. The
  F&O set is `instruments.fno_eligible`, derived from the master's own FUTSTK
  rows on every refresh — 210 of the 499 universe names on 2026-09-18.
- **A position with no stop is a real state, not a missing field.** No ATR at
  entry means no stop and a loud log line; the next nightly ratchet sets one.

## The scheduler

Two IST jobs, both restart-safe. The nightly (18:15) refreshes the bars,
decides, ratchets every stop and journals; the rebalance (09:16) trades. Times
come from the strategy YAML because a different strategy would want different
ones; how often the clock is checked is `swing.scheduler_interval_seconds`.

**Idempotence is the journal's, not a flag's.** `sessions_completed_on` is what
stops a restart at 18:20 re-deciding what was decided at 18:15. The in-memory
attempt clock only stops a FAILING job retrying every thirty seconds.

**A missed run is detected and reported, never silently re-decided.** The
trading calendar is the regime index's own bar dates compared against
`decided_session_dates` — no holiday list, no second source to go stale. A
decision recorded days late, on bars that may since have been restated, would
be a record of a decision nobody took.

## Rules that are not negotiable

- **No number that affects a trade is in Python.** Every parameter is a key in
  the strategy YAML under the name the specification gives it, so the two can
  be grepped against each other. `SwingParameters` has no defaults: a missing
  key names the file and the key rather than falling back to something
  reasonable, because a silent fallback is a silently different strategy.

- **Indicators are float; money is `Decimal`.** Indicators are ratios and
  averages and are compared against pandas; rupees are `Decimal` everywhere,
  and the conversion happens at the boundary in `ranking_service`. Do not mix
  them, and do not "improve" the indicators to `Decimal` — the parity test
  compares against numbers pandas produced.

- **The parity test is the guarantee, not the library.** `tests/
  test_swing_parity.py` pins every indicator against golden values computed by
  the backtest's own expressions. Two pandas behaviours are load-bearing and
  are asserted before anything else: `max(axis=1)` skips the NaNs in the first
  true range (so TR[0] is `high - low`), and `ewm(adjust=False)` seeds on the
  first observation rather than on a mean of the first fourteen.

- **The breadth ramp divides by a configured SPAN, not by `upper - lower`.**
  `0.65 - 0.35` is `0.30000000000000004` in IEEE-754 and the backtest divides
  by the literal `0.30`. The difference is a whole slot at reachable breadth
  values — 0.425 gives 3 against the engine and 2 against a derived span.

- **Undefined is not zero.** Every indicator returns `None` where it is not yet
  defined, `slots_for_breadth(None)` is `None` rather than 0, and the regime
  gate reports "could not be evaluated" rather than defaulting either way. "We
  could not measure breadth" and "breadth says buy nothing" are different
  answers and only one of them is a reason to hold cash.

- **The universe filter runs before every other filter.** A symbol with fewer
  than `minimum_sessions` bars is excluded entirely, exactly as the backtest's
  loader excludes it. That single line is the whole difference between the
  specification's 480-symbol and 485-symbol reproductions (§9.3).

- **Ranks cover every candidate, not the top ten.** A rotation exit is
  justified by a rank of 16 or of 40, and an operator has to be able to see
  which one.

## The decision journal

`swing_sessions` and `swing_decisions`, written by `journal_service.py` from
whatever `swing_runner.py` decided. Two rules:

- **Append-only, and enforced rather than documented.** `BaseRepository` hands
  every subclass an `update` and a `delete`, so `CashLedgerRepository`'s
  "append-only" has only ever been a convention. The journal's repositories
  override both to raise. A decision record that can be edited is not a record
  of what was decided, and this is the table someone will open in six months to
  find out why the system sold something.
- **Store the inputs, not just the conclusion.** Every number the gate, the
  breadth and the slot count were computed from is a column. "Gate OFF" cannot
  be checked later; "NIFTY 23,270.6 against SMA200 24,501.7, breadth 215/448,
  4 slots" can. The ranking and the configuration in force are stored whole.

Other things it does deliberately:

- `session_date` is the **session decided** -- the regime index's own bar date
  -- not the wall-clock date the job ran.
- A closed market records a `SKIPPED` row. "The job did not run" and "the job
  ran and there was no session" are different facts, and the missed-run
  detector has to tell them apart.
- A run is **idempotent per session**: a restart at 18:20 does not re-decide
  what was decided at 18:15. `force=True` appends a new record rather than
  editing the old one.
- **Every held name's rank is stored**, even when it has fallen out of the top
  of the list -- that is precisely the case a rotation exit has to be explained
  by.
- A non-action is a record too: "Skipped: slots full (4 allowed by a breadth of
  48.0%)", "Not entered: the regime gate is OFF".

## What is not here yet

Execution (the rebalance through `submit_paper_order`), the chandelier trailing
stop, the scheduler, the UI and the performance metrics.

**Carried to the final phase:** teaching the feed's inactivity watchdog about
market hours. It reconnects after 40 s without a data frame, which is right
during a session and wrong across the close — a book subscribed overnight
reconnects every 45 seconds until the next open. The zero-subscription case is
fixed; the idle-market case needs each strategy's `market_hours`, which is the
knowledge the scheduler brings. See `dhan_feed_client._watchdog`. See the handoff's
phasing. `SwingRunner.run_nightly` decides and records; **it places no orders**,
and execution is a separate run kind so that deciding and trading on the
decision are separately recorded and separately gated.
