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

## What is not here yet

The decision journal, the rebalance, the trailing stop, the scheduler, the UI
and the performance metrics. See the handoff's phasing. Until those exist this
package computes what the strategy *would* do and nothing else acts on it.
