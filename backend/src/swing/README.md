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
