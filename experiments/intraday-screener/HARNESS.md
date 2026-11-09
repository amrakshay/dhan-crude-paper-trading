# Step 3: Backtest harness

```
data/clean/ --build_panel--> panel.npz [symbol x day x bar]
  --Features (causal)--> Hypothesis.signals --engine.simulate--> trades
  --stats (day bootstrap) + controls--> pass bar
```

Run it with:

```bash
.venv/bin/python scripts/build_panel.py
.venv/bin/python scripts/run_hypothesis.py H1_orb_volume            # development period
.venv/bin/python scripts/run_hypothesis.py H1_orb_volume --holdout  # ONCE, logged
.venv/bin/python -m pytest tests -q
```

## Time conventions (islib/panel.py)

- **Bar *b*** is the window starting at 09:15 + 5*b* minutes.
- **The first decision** is on the close of bar 2, i.e. 09:30.
- **Orders fill** at the open of the next bar.
- **Everything is flat** at the close of bar 71 (15:10–15:15), for every year,
  so old and new periods are tested under the same closing-auction rule.
- **No new decisions after the 13:55 bar** (bar 56), for every hypothesis.

## Fill rules (islib/engine.py)

Each rule is pessimistic, and each has a test in `tests/test_engine.py`:

| Event | Fill |
|---|---|
| Entry | Next bar's open, plus 5 bps adverse (2 / 5 / 10 all reported) |
| Stop | At the stop, or at the bar's open if it gapped through, then adverse slippage |
| Target | Limit at the target, or a better open; no slippage |
| Bar touches both stop and target | **Stop** |
| Time exit | Close of bar 71, adverse slippage |
| Stop already breached at entry | Not taken |

Costs come from `conf/intraday_costs.yaml` at ₹1,00,000 per trade. The test
asserts ₹82.68 per round trip against the hand calculation.

## Causality (islib/features.py)

A feature at bar *k* may use bars 0..*k* of that day and any earlier day.
`tests/test_features.py` corrupts every later bar (and every later day) and
asserts nothing at *k* moves.

The features are:
- VWAP;
- 5-minute ATR, over the last 14 bars that exist, ignoring overnight gaps;
- cumulative volume against the 20-day average **at the same bar**;
- the opening range;
- return since open, for the stock and the Nifty;
- point-in-time liquidity: the top N by prior-20-day median turnover.

## Controls (islib/hypotheses.py)

Each control replaces **one** ingredient and keeps the **same stop distance**:

| Control | Replaces | Question it answers |
|---|---|---|
| `random_side` | Direction → coin flip | Did the direction carry information? |
| `random_time` | Entry bar → random bar **at or after** the signal | Is the edge in the trigger moment, or does it survive a late entry? |
| `random_name` | Stock → random other liquid stock, same moment | Did the selection carry information? |

The first versions of two controls were wrong. The end-to-end tests caught
both, and neither would have been visible in real results:

1. **The stop rule smuggled the direction back in.** `random_side` first
   re-applied H1's own stop rule (the far side of the opening range). A coin
   that flipped a long into a short put its stop where price already was, so
   the engine skipped it. The control only ever took the trades where the coin
   agreed with H1, and on planted-edge data it earned exactly what H1 did.
   Controls now keep the stop *distance*, and a test asserts they take more
   than 90% of their trades with a 50/50 side split.
2. **The random time leaked the future.** Drawing the random bar from the
   whole window let it enter *before* the breakout, in the direction the
   breakout would later go. On a pure random walk that earned +9 bps. It now
   draws only from the signal bar onwards.

## Synthetic checks (tests/test_end_to_end.py)

- **On a random walk,** H1 loses about its slippage and no more. All three
  controls are unbiased, pooled over 8 runs (|t| < 3).
- **With a drift planted after breakouts,** H1 finds it (CI clear of zero) and
  beats the coin by a clear margin. The coin still earns something there, as
  it should: break days trend, so a guess with a stop rides the right calls
  and gets cut on the wrong ones.
- **One real trade was checked by hand** against the raw bars (ICICIBANK short,
  2017-05-05), covering the opening range, signal bar, entry, stop and
  slippage.

## Known biases, stated rather than fixed

- **Survivorship:** the universe is today's F&O list.
- **Day validity is decided on the whole day** (≥90% of bars), which a live
  screener couldn't know at 10:00. It affects about 0.6% of symbol-days,
  mostly the Sept 2021 vendor gap.
- **No per-stock margin or circuit limits.** A trade into a frozen stock would
  fill here and wouldn't fill live.
- **2021–2023 bar ranges are slightly narrow** (CLEANING.md). Every run
  reports results with and without those years.
- **Order of events inside a 5-minute bar is unknown.** It's resolved against
  the trade (stop first), so this costs the backtest, not the account.
