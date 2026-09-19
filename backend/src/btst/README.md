# `src/btst/` — NSE BTST Overnight

The strategy-specific half of `conf/strategies/nse-btst-overnight.yaml`.
Everything generic — orders, positions, portfolios, charges, the feed, the
daily bars — lives where it always did; this package holds only what is true of
*this* rule.

Read the specification first. It is the single source of truth for the rules,
the parameters (B1–B16), the indicator formulas, the cost model and the
backtested results:

```
~/Workarea/local/pullback/backend/intrday_test_strategy/research2/BTST_OVERNIGHT_HANDOFF.md
```

**Buy today, sell tomorrow.** At ~15:20 buy Nifty 500 names making a fresh
55-day high, closing in the top fifth of the day's range, on twice their
average volume, that are also six-month momentum leaders above their 200-day
SMA. Sell every one of them at the next morning's open. No stop.

---

## The one thing that makes this different from the rotation

**Swing decides at 18:15 on finished daily bars. BTST decides at 15:20 on live
intraday state.** Almost everything below follows from that sentence.

| | Swing Momentum | BTST Overnight |
|---|---|---|
| Decides | 18:15, on finished bars | **15:20, mid-session** |
| Needs live prices to decide | no | **yes — ~289 symbols at once** |
| Enters | next day 09:16 | **same pass, ~15:20** |
| Exits | trailing stop / rotation / regime | **next open, unconditional** |
| Stop | chandelier, 3.5×ATR | **none, and none is possible** |
| Holds | weeks | **~18 hours** |
| A missed run costs | a session's decision, reported | **the trade thesis** |
| Regime gate | load-bearing | 259 of 3,016 signals (§11) |

**Nothing here accumulates ticks.** Root `CLAUDE.md` forbids building bars out
of the feed, and this strategy does not need to: Dhan's Quote/Full packet
already carries the session's running high, low and cumulative volume per
instrument. `hi_sofar`, `lo_sofar` and `vol_sofar` are **reads from
`MarketBook`**. If you find yourself keeping a running maximum in this package,
stop.

## What is here

| Module | Role |
|---|---|
| `services/btst_parameters.py` | B1–B16 read from the strategy YAML; the only place that reads them |
| `services/scan_service.py` | The filter funnel and the ranking, as plain values. Decides; places nothing |
| `services/execution_service.py` | The two runs: the afternoon scan, and the morning exit. The only module here that places an order |
| `services/journal_service.py` | Turns a decision into rows. Decides nothing |
| `services/btst_policy.py` | Whether a RULE is enforced |
| `services/btst_schedule.py` | WHEN it wakes up, and the two times it refuses |
| `services/module_hooks.py` | The seam `src/strategies/` dispatches through: its two run kinds, and its rule hooks |
| `services/btst_service.py` | Read models for the page. Decides nothing |
| `services/btst_health_service.py` | Is this strategy healthy, and DID THE EXIT RUN. Reads only, admin-only |

There is deliberately **no `stop_service` and no `stop_monitor`**. See B15.

## The exit is the strategy

Specification §10.1, and it is the reason this module is shaped the way it is:

| Exit | Mean | Win rate | Net of 0.30% |
|---|---|---|---|
| **Next OPEN** | +0.617% | **71.4%** | +0.317% |
| Next CLOSE | +0.428% | **49.0%** | +0.128% |

Identical signals; only the exit differs. So:

- **The exit runs unconditionally.** No rank is consulted, no stop exists, and
  there is no condition under which a position is kept.
- **It is bounded only at the bottom.** The rotation's rebalance has an upper
  bound so a restart at 19:00 cannot consume the next session's only chance to
  trade. The exit has no such luxury: a position not sold at 09:16 is still
  held, and the right response at 11:00 is to sell it and record that it was
  late. **This is the single most important asymmetry in the module.**
- **A sale after the window is `EXITED_LATE`**, a status of its own carrying how
  many minutes late it was — not a footnote on a normal exit.
- **A position that could not be sold stays OPEN.** `FAILED` counts as open in
  `open_for()`, so the next pass tries again and the alarm keeps firing. A
  failure that marked the row finished would leave a position held indefinitely
  with nothing saying so.
- **Anything still open raises an ALERT** (`btst-exit-incomplete`), because a
  log line saying so is read by nobody at 09:30. `CONDITION` dedupe, not
  `WINDOW`: a stuck position is a STATE, and this codebase has already produced
  the flood twice.
- **The due date is the next TRADING day, not the next calendar day.** A Friday
  entry is sold on the Monday and that is not late. Getting this wrong put a
  LATE status and an alert on every Friday signal; it was caught by
  `test_a_friday_entry_sold_on_the_monday_is_NOT_late`.
- **It sells WHAT IS HELD, never what the row recorded at entry.**
  `btst_holdings.quantity` is what filled at entry and never moves again, so a
  retry after a partial sale, or an exit reaching a position an operator closed
  by hand, would sell shares that are no longer there. Nothing downstream
  refuses that — a closing order skips the funds check by design and the
  position book simply lets the net go negative — so the result is a SHORT in a
  strategy that has no stop and must never sell to open. The sale is bounded by
  both figures: `min(recorded, actually held)`, read from the position book
  through `_open_quantity`, which is the guard
  `SwingStopMonitor._open_quantity` already makes on the rotation's side.
  Nothing held at all is `NOT_HELD` — its own status, because `EXITED` would
  claim this job sold it and `FAILED` counts as open and would keep the alarm
  firing for a position that is not there. Added 2026-09-19; both halves are
  pinned in `tests/test_btst_exit.py`.

## The scan

**It decides AND buys in the same pass**, which is the opposite of the
rotation's arrangement and is forced by what it decides on. The rotation
decides at 18:15 on finished bars and trades at 09:16 the next morning, and the
decision survives the night because the bars do. This decides on a running
high, a running low and a cumulative volume, and none of that survives ten
minutes. A decision that cannot be carried forward has to be acted on where it
is taken.

**Bounded at both ends, tightly.** A scan is a measurement of one moment:
§10.3 validated the 15:20 snapshot specifically, at 83.4% precision against the
closing signal, and a scan an hour later is not covered by that. Worse, a late
scan would journal the session and — because idempotence is the journal's — the
next day's real scan would find it already recorded.

**Two entry points, and the difference is §3's own NOTE.**

- `scan_live()` substitutes the current price for today's unfinished close in
  B4, B6 and B7, and computes `advq`/`adv20` from COMPLETED bars only, because
  today's volume is not a completed number. §3's pseudocode says exactly that:
  `advq = mean(volume[t-20 .. t-1])`.
- `scan_on_bars()` (`include_last_in_averages=True`) is the reconstruction, and
  it exists because §13 lists twelve dated signals and says the same code on the
  same data must produce them. That table came from `scan.py::build`, which
  works on the daily panel where those windows INCLUDE day T. Reproducing it
  needs the panel's convention.

**The two conventions roughly cancel.** Including today's volume in a 20-day
mean raises the denominator on exactly the high-volume days this rule fires on
— about 5% at a 2× day — while measuring `vol_sofar` at 15:20 gives roughly 95%
of the day. §14 item 1 is explicit that the ratio must NOT be scaled up to a
projected full day, and it is not.

## The unverified fact this strategy rests on

Root `CLAUDE.md` §5: the Quote/Full packet's four price fields are mapped
open/close/high/low per the `dhanhq` SDK and have never been checked against a
live feed.

For the price chart that was cosmetic. **Here it is not.** B6 is
`CLV = (price − low) / (high − low) > 0.8`, so a transposition does not make
the filter drift — it **inverts** it, and the strategy buys the weakest closes
in the market while every number it produces looks plausible.

Two things follow:

- `scripts/verify_feed_session_fields.py` is the standing answer. It subscribes
  a handful of names mid-session and compares the packet's session high and low
  against `/charts/intraday` for the same day. It refuses to run outside market
  hours, because outside a session the comparison means nothing.
- `Quote.usable` REFUSES a quote whose high is below its low, or whose last
  trade is outside its own session range. That does not verify the mapping; it
  makes the failure a scan that qualifies nothing and says why, instead of a
  book of inverted trades.

The same script settles the second unverified claim, the one the subscription
window rests on: that the packet carries the session AGGREGATE rather than a
delta since subscription.

## The universe subscription

`subscription.kind: "universe"`, and it **inverts the rotation's reasoning on
purpose**. `nse-swing-momentum.yaml` argues against subscribing the universe —
its decisions come from daily bars fetched over REST, so 490 of the 500 would
be instruments nobody reads. Every word of that is true of that strategy.

This one needs the session's own high, low and volume for every candidate at
one moment in the afternoon. §8 measures the alternative: REST-polling ~480
symbols at Dhan's 0.6 s rate limit takes about five minutes and would overrun
the window entirely.

**And it subscribes late.** `window_opens_at` / `window_closes_at` bound the
cost: outside the window this behaves exactly like `positions`, so the feed
carries ~289 extra instruments for forty minutes a day rather than six hours.
That rests on the aggregate claim above; if it is false the fix is to move
`window_opens_at` to the open, which is configuration rather than code.

## Two strategies, one universe

The rotation and this module both trade the Nifty 500, which is the first time
anything here has shared an instrument. Three shared-code changes followed, and
each is asserted in `tests/test_btst_does_not_disturb_swing.py`:

- **The instrument master's exclusivity rule became "one INGESTION RULE per
  symbol"** rather than "one strategy per symbol". The row either module would
  ingest is byte for byte the same row — same series filter, same lot-size
  source, same trading-symbol source — so there is nothing to disambiguate. Two
  strategies that DISAGREED are still refused.
- **`submit_paper_order` takes a `strategy_key`.** An instrument no longer
  determines a strategy, so the caller says which — exactly the way
  `portfolio_id` works, and for the same reason: a trade under the wrong
  strategy carries the wrong rate card, the wrong arming switch and the wrong
  journal, silently. An ambiguous instrument with no key is REFUSED, never
  guessed. A CLOSE resolves from the position, which already carries the
  strategy it was opened under.
- **`registry.for_instrument` returns `None` when several claim a name.**
  `strategies_for_instrument` is the one that lists them.

## Rules that are not negotiable

- **No number that affects a trade is in Python.** Every parameter is a key in
  the strategy YAML under the name the specification gives it, so the two can be
  grepped against each other. `BtstParameters` has no defaults: a missing key
  names the file and the key.
- **Undefined is not zero.** A CLV on a zero-range session is `None`, not 1.0 —
  treating it as 1.0 would buy every untraded name. An unmeasurable regime gate
  blocks entries rather than defaulting either way. An equity figure
  `BalanceService` withheld means buying nothing and saying why.
- **"Nothing qualified" is the ORDINARY outcome.** About 0.54 signals a session
  with F&O names excluded. Every session records the whole funnel, so a working
  scan that found nothing is distinguishable from one that did not run.
- **Analysis at any hour; ORDERS only inside continuous trading.** Checked with
  `market_clock.can_execute_continuously` PER INSTRUMENT immediately before
  each order, because F&O eligibility moves the close from 15:30 to 15:15 and
  the scan is at 15:20.
- **A policy is read ONCE when a run starts** and carried down as a value. That
  matters more here than on the rotation, because `fno.exclude` decides the
  UNIVERSE: a change mid-scan would rank two different populations against each
  other.
- **The journal is append-only and enforced in the repository.**
  `btst_holdings` is the deliberate exception — it holds the open question "did
  the exit run", which is answered by changing it — and it still refuses
  `delete`.

## What is not here yet

- **Idle cash is not modelled.** The backtest assumes 6.5% a year on
  undeployed capital; this application has no liquid-fund instrument at all, so
  this book's cash earns nothing and its returns are lower than the
  specification's by that amount. In the README's known gaps.
- **No pre-CAS scan at 15:14 for F&O names.** Considered on 2026-09-19 and
  deliberately not built: it would make those names trade at 15:20 in the
  auction window, which is what `fno.exclude` exists to avoid. Ship the
  non-F&O half; add the 15:14 path when somebody wants F&O names.
- **The exit places a market order once the session is open**, not a
  market-on-open order. §14 says "pre-open or market-on-open"; this simulator
  fills against the depth book and has no pre-open model. The explainer says so
  plainly, and the backtest's `open × (1 − 0.05%)` assumption is not what this
  book gets.
