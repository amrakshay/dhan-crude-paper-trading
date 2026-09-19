# Handoff — integrating the BTST Overnight strategy

Read `CLAUDE.md`, `backend/CLAUDE.md` and `frontend/CLAUDE.md` first, then
`backend/src/swing/README.md`. The swing rotation is the worked example of
everything this asks for: a strategy module, its YAML, its journal, its page,
its policies and settings, its background jobs. Do not re-derive any of it.

**The strategy specification is the source of truth for every number:**
`~/Workarea/local/pullback/backend/intrday_test_strategy/research2/BTST_OVERNIGHT_HANDOFF.md`
(read 2026-09-19). Parameters are cited below as **B1–B16** and sections as
**§n** from that document. Where this handoff and the specification disagree
about a number, the specification wins.

---

## What this is

**BTST = Buy Today, Sell Tomorrow.** At ~15:20 IST buy Nifty 500 names making a
fresh 55-day high, closing in the top 20% of the day's range, on ≥2× average
volume, that are also 6-month momentum leaders above their 200-day SMA. Sell
every position at the next morning's open. **No stop** — an overnight gap
cannot be stopped.

The edge is the overnight gap and nothing else: +0.617% gross at a 71.4% win
rate held to the next OPEN, against +0.428% at a **49.0%** win rate held to the
next close (§10.1). Exit timing is not a preference, it is the strategy.

**The author's recommendation is: do not fund, paper-trade 8–12 weeks first
(§16).** That is exactly what this application is. Integrating it here IS the
recommendation being followed, which is worth saying out loud because it sets
the bar: the job is to produce an honest paper record, not to make the numbers
look good.

---

## The one thing that makes this different from Swing Momentum

**Swing decides overnight on completed daily bars. BTST decides at 15:20 on
live intraday state.** Almost every design consequence follows from that one
sentence:

| | Swing Momentum | BTST Overnight |
|---|---|---|
| Decides | 18:15, on finished bars | **15:20, mid-session** |
| Needs live prices to decide | no | **yes — for ~289 symbols at once** |
| Enters | next day 09:16 | **same day, ~15:20** |
| Exits | trailing stop / rotation / regime | **next open, unconditional** |
| Stop | chandelier, 3.5×ATR | **none, and none is possible** |
| Holds | weeks | **~18 hours** |
| A missed run costs | a session's decision, reported | **the entire trade thesis** — see Traps |
| Regime gate | load-bearing | near-optional (259 of 3,016 signals, §11) |

**The good news, verified in this codebase:** the feed's Quote/Full packet
already carries the session's **high, low, open and cumulative volume** per
instrument (`feed_protocol.py`, fields 13/14 and 8). So `hi_sofar`, `lo_sofar`
and `vol_sofar` are **reads from `MarketBook`**, not something to accumulate.
That matters because the root `CLAUDE.md` forbids accumulating ticks into bars —
and this strategy does not need to. **Do not build a tick accumulator.** Read
the book.

---

## Baseline — do NOT spend time re-running these

```bash
cd backend
.venv/bin/python -m pytest tests/ -q     # 1001 passed, 1 skipped, ~80s (parallel)
.venv/bin/python -m pytest tests/ -q -n0 # the same, serially, ~6.5 min
CONFIG_PATH=conf .venv/bin/alembic heads
cd ../frontend && npm run build
```

State of the running installation on 2026-09-19: NSE Swing Momentum enabled and
ARMED on portfolio 2 (`Swing Momentum`, ₹10,00,000), regime gate and
entry-return filter NOT enforced, zero orders placed. MCX crude off. Live Dhan
feed. Instrument master holds **210 F&O-eligible and 289 non-F&O** active
NSE_EQ rows — which matches the specification's 210/290 split almost exactly.

---

## Decisions already made by the owner

1. **Its own strategy module**, integrated the same way as Swing Momentum.
2. **Its own page with FIVE tabs** (swing has four).
3. **Its own portfolio, ₹10,00,000**, and it trades only that book.
4. **The same settings treatment as swing** — the Configuration tab: rule
   switches as runtime policies, timings as runtime settings, and every
   strategy PARAMETER staying in the YAML and editable from nowhere.

---

## What is directly reusable, verified by reading the code

| Thing | Reuse |
|---|---|
| `src/daily_bars/` | **Whole package, unchanged.** Same universe, same segment, same nightly refresh. BTST adds no new data source |
| `swing/services/indicators.py` | `sma`, `average_daily_value`, `momentum`, `percent_change` all apply. B8's `close[t-5]/close[t-126]-1` is exactly `momentum(skip=5, lookback=126)` |
| `Instrument.fno_eligible` | **Already derived from the master's own FUTSTK rows.** The §5a "non-F&O only" recommendation is a filter this codebase can already apply, for free |
| `conf/charges/nse-equity-delivery.yaml` | Swing already uses it. STT on both legs is what BTST needs (§5) — **verify the card against §5's cost model** |
| `market_clock` | `is_market_open`, `can_execute_continuously`, the CAS/closing-auction knowledge |
| `submit_paper_order`, `fill_simulator` | Unchanged. The simulator is deliberately pessimistic, which is the honest test of §10.2 |
| `BalanceService`, portfolios, cash ledger | Unchanged |
| `feature_toggles` POLICY scope, `strategy_settings` | The framework for "is this rule enforced" and "when does it wake up" is generic and already built |
| `automation:` block, arming, the registry | Generic. Declaring the block is what makes a module automated |
| `task_inspector`, the health-tab shape, `JobProgress` | Patterns to follow |
| `SwingExplainer` / `GET /explain` | The pattern: the page renders the YAML, never restates a number |

**Two small additions to `indicators.py`**, both trivial and both belonging
there rather than in a new module: **average SHARE volume** (B5's `advq` —
note §4's warning that `advq` and `adv20` are different columns with different
roles, do not conflate them) and **CLV** (B6).

---

## What is swing-SHAPED and should not be bent

- **`swing_sessions` is not a generic journal.** Its columns are
  `gate_on, breadth_above, breadth_liquid, slots, index_sma, ranking_json` —
  a breadth-ramped rotation's record. BTST wants candidates, `vol_ratio`,
  `clv`, `hi55`, the K picked and the realised overnight gap. **Give BTST its
  own journal tables.** Bending swing's would make both harder to read and put
  a migration on a live, armed strategy.
- **`stop_service` and `stop_monitor` do not apply at all.** BTST has no stop
  (B15) and cannot have one. Do not wire them in.
- **`rebalance_planner` does not apply.** Different selection entirely.
- **`scheduler.py` is "the only clock in this application" and must stay the
  only one.** Do not start a second. It already loops `registry.automated()`;
  what has to change is that the run kinds become per-module rather than the
  hardcoded `NIGHTLY` / `REBALANCE`. **That is a change to a live component
  running an armed strategy — do it carefully, and do not refactor swing's job
  logic at the same time as adding BTST's.**

**Recommendation on how far to generalise: not very, yet.** Share what is
provably identical (the list above) and let BTST have its own journal, its own
parameters class, its own planner. A shared "automated strategy core" is the
right end state and the wrong thing to attempt while the first one is armed and
trading. Extract it when a third module makes the duplication real.

---

## The five tabs

Swing has four: Live, Configuration, Health, How it works. BTST gets those plus
one, and the extra one should be the thing swing does not need:

1. **Live** — what it holds overnight, last night's gap, the decision history.
2. **Signals** — **the new one.** Today's scan, watchable during the session:
   the filter funnel (B2→B8) as it stands right now, which names are currently
   eligible, their `vol_ratio` / `clv` / distance above `hi55`, and a countdown
   to the scan. Swing has no equivalent because its decision happens overnight
   in one shot; BTST's forms over the afternoon and is the most interesting
   thing on the screen between 14:30 and 15:20.
3. **Configuration** — the same form as swing's: rule switches and timings,
   nothing takes effect until Save, every pending change shown with the
   server's warnings first.
4. **Health** — the same six panels, plus the two that matter here: **is the
   universe subscribed to the feed right now**, and **did the exit run**.
5. **How it works** — rendered from `GET /explain`, every number read from the
   YAML. Must carry §16's recommendation and the decay table from §9.4, not
   just the rules.

---

## Traps — specific to this strategy, and sharp

**1. THE HIGH/LOW MAPPING IS UNVERIFIED, AND B6 DEPENDS ON IT ENTIRELY.**
Root `CLAUDE.md` §5 ends with: *"Unverified, flagged in code: the Quote/Full
packet maps its four price fields as open, close, high, low per the SDK. Never
checked against a live feed."* B6 is `CLV = (p − lo_sofar) / (hi_sofar −
lo_sofar) > 0.8`. If high and low are transposed, CLV is not merely wrong, it
is inverted, and the strategy buys the weakest closes instead of the strongest.
**Verify this against the live feed before writing the scan** — compare a few
symbols' feed high/low against `/charts/intraday` for the same session. This is
the first task, not a later one.

**2. A MISSED EXIT IS NOT LIKE A MISSED SWING RUN.** Swing treats a missed
session as reportable and harmless — the next run re-decides. **BTST's exit is
the edge.** Holding to the next close instead of the open takes the win rate
from 71.4% to 49.0% and the net edge from +0.317% to +0.128% (§10.1). So:
- the 09:15 exit job is the most defended thing in the module;
- a position still open after the open must raise an **alert**, not a log line
  (the alerting built from `CONNECTIONS_PAGE_HANDOFF.md` is the vehicle);
- the Health tab's first verdict is "did the exit run", not "is the clock
  alive";
- and if the app was down at the open, the exit still happens on the next pass
  and is journalled as LATE with the realised difference, rather than silently
  treated as normal.

**3. The scan time is INSIDE the session, which the settings validator
currently refuses.** Swing's `schedule.nightly_at` is validated as being
OUTSIDE market hours precisely because an in-session analysis would store a
forming bar as a finished one. BTST's scan **must** be in-session — that is the
whole point. The validator is per-module and must say so, rather than being
loosened for both.

**4. CAS makes 15:20 mean two different things (§5a).** For the 210 F&O names
continuous trading ends at **15:15** and an order at 15:20 lands in an auction
that fills at 15:30–15:35. For the 289 non-F&O names nothing changed.
**The specification's own recommendation is to trade non-F&O names only** —
better per-trade edge (+0.396% vs +0.295%), better win rate, lower drawdown,
and CAS removed from the problem entirely. This codebase already knows which
names are which. Make it a POLICY (`fno.exclude`, default ON) so it is a switch
with a recorded reason rather than a hidden assumption.

**5. Subscribing ~289 symbols is a new scale for the feed.** The config allows
it — `market_feed.max_instruments_per_connection` is 5000 and subscribes are
batched at 100 — but swing's YAML explicitly argues AGAINST subscribing the
universe (*"Subscribing all 500 would spend the one process-wide connection's
budget on 490 instruments nobody reads"*). BTST inverts that reasoning and the
inversion must be written into its YAML, not left implicit. A new subscription
`kind: "universe"` is the clean way.
- **Consider subscribing late.** Dhan's packet carries the session's aggregate
  high/low/volume, not a delta, so a subscription opened at ~15:00 should still
  report the full session. **Verify that** — if it holds, the feed carries 289
  extra instruments for twenty minutes a day instead of all session.
- Re-measure the broadcast cost either way. The tick-path contract cites
  ~1.1 ms to build a full 165-instrument broadcast; 289 is not free.

**6. The strategy dies at +0.30% of extra slippage (§10.2).** The fill
simulator here is deliberately pessimistic — market orders pay the far touch,
large orders walk the book, depth is finite. **That is the point, not a
problem.** Expect the paper record to look worse than the backtest's
0.05%/side assumption, and do not tune the simulator to close the gap. Report
the difference; it is the most valuable number this exercise can produce.

**7. Signal frequency is ~1.07/session, and ~0.54 for non-F&O only.** Most days
produce one or two candidates and many produce none. A page that looks broken
when nothing qualifies is wrong — "nothing qualified today" is the normal
state and must read as such.

**8. Verify the reimplementation against §13.** The specification lists twelve
dated signals with their symbols and realised gaps. The same rule on the same
data must reproduce them. That is the acceptance test for the scan, and it is
free.

**9. Do not bring the ₹16 DP charge or the STT-on-both-legs assumption into
Python.** §5's cost model belongs in the rate card under `conf/charges/`, with
its source and as-of date, like every other rate in this project.

---

## The portfolio

A new one, **₹10,00,000**, named for the strategy and mapped to it alone. This
is exactly what portfolios are for — §14 of the specification flags that BTST
deploys ~43% of capital on a daily cycle while the swing book holds for weeks
and *"they compete for the same rupees"*. Separate books means they cannot.

Create it through `PortfolioService.create(name, strategy_keys=[...],
opening_balance=...)` rather than by hand in SQL, so the opening balance lands
as a proper `cash_ledger` entry — a portfolio's cash is **the sum of the
ledger** and is never stored.

---

## Definition of done

- A `conf/strategies/nse-btst-overnight.yaml` carrying **B1–B16**, its market
  hours, its subscription policy, its rate card and its `automation` block with
  `armed_by_default: false`.
- `src/btst/` following the swing package's shape, sharing what the reuse table
  lists and duplicating nothing that is genuinely identical.
- Its own journal tables and migration, applying and rolling back on SQLite
  with `--autogenerate` empty afterwards.
- The scan job at the configured in-session time, and the **exit job at the
  open**, both on the existing scheduler — no second clock.
- The exit defended as described in trap 2, including a LATE exit path and an
  alert for a position still open after the open.
- A new portfolio with ₹10,00,000, mapped to this strategy only.
- `/btst` with five tabs, admin-gated the same way `/swing` is (the journal
  open to any signed-in user, Health admin-only), and correct in light and dark.
- Policies and settings through the existing framework: `regime.enforce`
  (default ON, §11), `fno.exclude` (default ON, §5a), the scan time and the
  exit time. **B1–B16 stay in the YAML and are editable from no page.**
- The scan reproduces §13's twelve dated signals on the same data.
- The high/low feed mapping verified against `/charts/intraday`, and the
  finding written into the root `CLAUDE.md` §5 table — it is currently marked
  unverified there and this is the work that settles it.
- Swing Momentum demonstrably unaffected: it is live and ARMED. Its tests pass
  untouched, and `tests/test_swing_does_not_disturb_crude.py` has the pattern
  for asserting one module does not disturb another.
- Full suite green, `npm run build` passes.
- `README.md` and the relevant `CLAUDE.md` files updated, with everything the
  specification lists in its §15 carried into the README's "Known gaps" — the
  decay, the absent holdout, the +1.6%/13-month honest test, the survivorship
  bias and the slippage cliff. **Do not let those get lost in translation.**
- Committed with `amrakshay@gmail.com` and pushed to `origin/main` over SSH. No
  JIRA prefix, no GitLab MR, no ADR — personal repo.

---

## Decisions still open

1. **K — how many slots?** The specification's author recommends **K=8** if
   funded (13.1% CAGR, −5.3% DD, MAR 2.48, §9.2) but **K=5 or K=3** for the
   non-F&O subset (§5a). K is a YAML parameter either way; the question is what
   ships as the default. Recommendation: **K=5 non-F&O**, the §5a row with the
   best win rate and the smallest drawdown.
2. **Ranking (B10).** §9.3 shows it barely matters — ≤0.9pp of CAGR over
   alphabetical — and `mom126` scored the best MAR. Ship `vol_ratio` as
   specified, or `mom126` as measured? Recommendation: **as specified**, with
   the finding in the explainer, because deviating from the spec on a parameter
   that does not matter buys nothing and costs comparability.
3. **Does the scan run pre-CAS for F&O names at 15:14?** Only relevant if
   `fno.exclude` is ever switched off. Recommendation: **do not build it now.**
   Ship non-F&O only; add the 15:14 path when somebody wants F&O names.
4. **Should the 09:15 exit place market-on-open orders, or market orders once
   the session is open?** §14 says "pre-open or market-on-open"; this simulator
   fills against the depth book and has no pre-open model. Likely answer: a
   market order immediately at the open, and say plainly in the explainer that
   the backtest assumed `open × (1 − 0.05%)` and this does not.

---

## Reference

| Thing | Where |
|---|---|
| The specification | `~/Workarea/local/pullback/backend/intrday_test_strategy/research2/BTST_OVERNIGHT_HANDOFF.md` |
| The worked example to follow | `backend/src/swing/`, and its `README.md` |
| Strategy YAML to copy the shape of | `backend/conf/strategies/nse-swing-momentum.yaml` |
| Indicators to extend | `backend/src/swing/services/indicators.py` |
| Daily bars (reuse whole) | `backend/src/daily_bars/` |
| Session high/low/volume from the feed | `backend/src/market/services/feed_protocol.py`, `market_book.py` |
| F&O eligibility, already derived | `Instrument.fno_eligible` |
| The only clock | `backend/src/swing/services/scheduler.py` |
| Policies and settings framework | `backend/src/strategies/`, `swing/services/gate_policy.py`, `schedule_settings.py` |
| Page and tabs to imitate | `frontend/src/pages/SwingMomentumPage.jsx`, `components/SwingHealth.jsx`, `SwingConfiguration.jsx`, `SwingExplainer.jsx` |
| Rate card | `backend/conf/charges/nse-equity-delivery.yaml` |
| Alerting, for the missed-exit alarm | `CONNECTIONS_PAGE_HANDOFF.md` and what was built from it |
| Login | `trader@abc.com` / `APP_ADMIN_PASSWORD` from `.env` |
