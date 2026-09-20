# Exits: every trail tested here made it worse

`scripts/exit_grid.py`, then `scripts/ipo_backtest.py` to settle it at matched
exposure. Full output in `out/exit_grid.txt`.

---

## 1. The result, stated plainly

Eight exit rules over the same 184 `base` signals. Holding to a stop or a time
cap beat **every** trailing rule, in both currencies, at every holding period.

At an 80-session cap:

| rule | win % | mean R | median R | mean % | total % | avg hold | worst % |
|---|---:|---:|---:|---:|---:|---:|---:|
| **stop only** | **53.8%** | **0.66** | **+0.20** | **10.28%** | **1891.8%** | 62 | −36.0% |
| chandelier 4.5 | 44.0% | 0.45 | −0.19 | 7.19% | 1323.4% | 41 | −36.0% |
| chandelier 3.5 | 40.2% | 0.32 | −0.18 | 5.25% | 966.7% | 31 | −29.4% |
| EMA 50 | 35.3% | 0.30 | −0.23 | 4.93% | 907.6% | 24 | −26.7% |
| chandelier 3.0 | 42.4% | 0.28 | −0.13 | 4.45% | 818.5% | 25 | −26.7% |
| chandelier 2.5 | 44.0% | 0.19 | −0.09 | 2.69% | 495.4% | 19 | −26.7% |
| EMA 20 | 38.6% | 0.14 | −0.18 | 2.47% | 454.4% | 12 | −18.6% |
| EMA 10 | 36.4% | 0.07 | −0.16 | 1.49% | 274.9% | 6 | −13.3% |

The ordering is monotone in how loose the trail is. **The looser the trail, the
better the result, and no trail at all is best.** That is not a tuning outcome;
it is the same fact eight ways.

This extends the VCP work's finding rather than contradicting it. There, a 9 EMA
trail was too tight and a 50 EMA was better — the direction was already "loosen
it". Here the direction runs all the way to the end of the scale.

### Why: the return is in a right tail every trail clips

Median R is **negative for every rule** including the winner. The strategy is not
a high-hit-rate machine; it loses small most of the time and the profit comes
from a handful of listings that run. Any trail that exits on a normal pullback
takes the position off before that can happen, and since it exits the *winners*
early — losers hit the stop regardless — it removes profit without removing
much risk. Note that the worst single trade is −36.0% for both the best and the
fourth-best rule: the trail did not improve the tail it was supposed to protect.

---

## 2. Two currencies, and they agree here

The brief warns that R and rupees diverge under fixed-notional sizing — a trade
with a 1% stop earning 10R makes 10% of a slot, one with a 5% stop earning 3R
makes 15% — and that a table in R alone picks the wrong rule.

Both columns are reported for that reason. **In this case they happen to agree**,
ranking the eight rules identically. That is worth saying out loud rather than
quietly dropping one column: the safeguard was necessary to *check*, and it
found nothing to correct.

---

## 3. The holding period is the real parameter

`stop only` at three caps:

| cap | win % | mean R | mean % | avg hold |
|---|---:|---:|---:|---:|
| 40 sessions | 50.5% | 0.38 | 5.77% | 34 |
| 80 sessions | 53.8% | 0.66 | 10.28% | 62 |
| 250 sessions | 38.0% | **1.29** | **19.46%** | 143 |

Per trade, 250 wins by a mile. **Per unit of capital it does not**, and this is
exactly the exposure trap the brief describes. A 250-session hold occupies a
slot for four times as long:

| cap | CAGR | max DD | MAR | exposure |
|---|---:|---:|---:|---:|
| 40 | 6.8% | −10.8% | 0.63 | 16.5% |
| **80** | **10.5%** | **−11.7%** | **0.90** | 25.7% |
| 250 | 15.7% | −22.5% | 0.70 | 52.8% |

The 250-session book earns more and is worse: it doubles the drawdown to buy
half again the return, and its MAR falls. 80 sessions is the best risk-adjusted
point and is the decided value.

Note the win rate *falls* from 53.8% to 38.0% as the cap extends, while mean R
doubles. A rule chosen on win rate alone would pick the 40-session cap, which is
the worst of the three on every other measure.

---

## 4. The stop, and a filter that turned out not to apply

**The stop is the base low**, with no ATR floor in practice.

The VCP work found that requiring the stop to sit ≥ 0.5 ATR from entry was the
single best filter it had: win rate 20% → 27%, CAGR 19.8% → 21.8%, drawdown
−21.0% → −17.1%. That filter was inherited here and **re-measured rather than
assumed**, which was the right call: the rows for `min_stop_atr` 0.0 and 0.5 are
byte-identical, because

```
stop distance at the signal, as a fraction of price:
  median 19.0%   10th 9.7%   90th 35.8%
```

An IPO base's low sits nineteen percent below its pivot. Half a daily range never
binds against that. The VCP needed the floor because its bases are tight by
construction — contraction *is* the pattern — and an IPO base is the opposite
kind of object.

The floor is kept in the code at `MIN_STOP_ATR = 0.5` because it costs nothing
and would bind on a future universe with tighter bases. It is reported as
inactive rather than removed, so nobody re-derives it in six months.

---

## 5. Entry

**The next session's open, not the signal bar's close.** The signal is known only
once the breakout bar has closed; entering at that close is lookahead worth about
a day of the move, and on a breakout bar that is the most expensive day there is.
The VCP work entered at the close and said so. This one does not, so its numbers
are not directly comparable with that experiment's and are a little worse for
being honest.

---

## 6. What was not tested

- **Partial exits / scaling out.** Plausibly the right answer for a
  right-tail-driven strategy — take a third off at 1R, let the rest run — and
  not measured here.
- **Time stops conditional on progress.** "Exit after 20 sessions unless up 10%"
  is a different object from a flat cap.
- **A trail that only starts after the trade is in profit.** Every rule here
  trails from entry.
- **Intraday stops.** Everything is measured on daily bars; a stop is triggered
  by the day's low and filled at the level, or at the open when the bar gapped
  through it.
