# Exits: does a 9 EMA trailing stop work?

Short answer: **no, not on its own.** A 9 EMA trail is materially worse than
the flat hold it replaces, for one reason — it truncates the right tail that
the whole strategy depends on. Two repairs help, and a slower average helps
a lot more.

```bash
.venv/bin/python scripts/vcp_ema_exit.py     # the 9 EMA rule as asked
.venv/bin/python scripts/vcp_exit_grid.py    # the grid below
```

Same 3,188 bases as `VCP_PREBREAKOUT.md`, same stop (the final contraction's
low, on an intraday touch, filled at the open when price gaps through). The
only thing varying is the exit.

---

## 1. The result

### Entry at the breakout (1,777 trades)

| Exit rule | Mean R | Median R | Win% | Mean return | Median bars | R/bar | p95 R |
|---|---|---|---|---|---|---|---|
| flat 40 bars *(baseline)* | 0.39 | −0.21 | 46% | 4.20% | 40 | 0.013 | 3.74 |
| flat 60 bars | 0.54 | −0.62 | 43% | 6.02% | 59 | 0.014 | 4.71 |
| **EMA9** | **0.12** | −0.16 | 38% | 1.19% | 7 | 0.013 | **2.00** |
| EMA9, 2 closes below | 0.18 | −0.17 | 40% | 2.02% | 10 | 0.014 | 2.48 |
| EMA9, 0.5 ATR buffer | 0.25 | −0.20 | 40% | 2.63% | 13 | 0.015 | 2.99 |
| **EMA9, armed at +1R** | 0.29 | **+0.30** | **56%** | 3.33% | 19 | 0.011 | 2.53 |
| EMA9, 2 closes + armed +1R | 0.36 | +0.23 | 54% | 4.16% | 22 | 0.012 | 3.02 |
| EMA20 | 0.31 | −0.23 | 39% | 3.34% | 13 | **0.018** | 3.20 |
| EMA20, 2 closes | 0.40 | −0.22 | 40% | 4.23% | 17 | **0.018** | 3.64 |
| EMA20, armed at +1R | 0.47 | +0.14 | 52% | 5.38% | 25 | 0.014 | 3.87 |
| EMA50 | 0.65 | −0.34 | 39% | 7.05% | 27 | **0.018** | 5.11 |
| **EMA50, armed at +1R** | **0.75** | −0.19 | 46% | **8.42%** | 36 | 0.016 | **5.53** |

### Entry at the forming bar, trail arming at the breakout (3,188 trades)

| Exit rule | Mean R | Median R | Win% | Mean return | R/bar | p95 R |
|---|---|---|---|---|---|---|
| flat 40 bars *(baseline)* | 0.87 | −1.00 | 21% | 1.94% | 0.060 | 8.98 |
| flat 60 bars | 1.14 | −1.00 | 20% | 2.85% | 0.061 | 10.66 |
| **EMA9** | **0.64** | −1.00 | 29% | 1.63% | 0.049 | **6.52** |
| EMA9, 0.5 ATR buffer | 0.83 | −1.00 | 26% | 2.09% | 0.053 | 6.85 |
| EMA20 | 0.86 | −1.00 | 27% | 2.33% | 0.055 | 6.91 |
| EMA20, 2 closes | 1.03 | −1.00 | 25% | 2.63% | 0.059 | 7.79 |
| **EMA50** | **1.62** | −1.00 | 22% | 4.15% | **0.073** | **9.99** |
| EMA50, armed at +1R | 1.63 | −1.00 | 23% | 4.27% | 0.071 | 9.99 |

(The median is −1.00R for every early-entry row because 64% of those trades
are stopped out *before* the breakout. No exit rule can touch them; the exit
only governs the third that get through.)

---

## 2. Why 9 EMA fails here

**It exits in a median 7 sessions and truncates the tail.** Look at the p95
column: EMA9 caps the 95th-percentile trade at 2.00R where the flat 40-bar
hold reaches 3.74R and EMA50 reaches 5.53R. These strategies earn
essentially everything from the top few percent of trades — the earlier study
found the top 5% contributing 131% of total R — so an exit that clips large
winners is attacking the only part that pays.

A 9 EMA on daily closes of a mid-cap Indian equity is touched constantly.
95% of breakout-entry trades ended on the moving average rather than on the
stop or the holding cap: the rule is doing nearly all the exiting, and it is
doing it early.

**Costs make it worse.** A 0.3% round-trip is 25% of EMA9's 1.19% mean return
and 4% of EMA50's 7.05%. Shorter holds do not reduce cost per trade; they
reduce the gain the cost is subtracted from.

**Capital efficiency does not rescue it either.** R/bar is the number a fast
exit is supposed to win on, and EMA9 ties the flat hold (0.013) while EMA20
and EMA50 both beat it (0.018). So the faster exit is not even buying
turnover — the slower averages dominate on both axes.

---

## 3. What actually helps

**Arm the trail late.** `EMA9, armed at +1R` — don't trail at all until the
trade is up 1R, then trail — takes mean R from 0.12 to 0.29, the median from
−0.16 to **+0.30**, and the win rate from 38% to **56%**. That is a very
different trade to live with: most of them now win. It still earns less than
the slower averages in total, so it is a rule to choose deliberately if you
want a high hit rate, not because it maximises return.

The reason it works is that a breakout needs room. Trailing from bar one
manages a position that has not yet done anything; the first normal pullback
after the pivot ends the trade.

**Use a slower average.** EMA50 nearly doubles the flat-hold baseline on both
entries (breakout 0.65 vs 0.39; early 1.62 vs 0.87) *and* wins on R/bar.
EMA20 is the compromise: it matches the flat 40-bar hold's return in 17 bars
instead of 40.

**The trail genuinely adds value — it is not just "hold longer".** The
control for that is the flat 60-bar row. EMA50 beats flat 60 on both entries
(0.65 vs 0.54, and 1.62 vs 1.14), so the improvement is the trailing, not
merely the extra time.

**Requiring two consecutive closes below** is a small, consistent
improvement everywhere (EMA9 0.12 → 0.18, EMA20 0.31 → 0.40). Cheap to add.

---

## 4. What I would actually use

If the goal is total return: **EMA50, armed at +1R**, with the contraction
low as a hard floor until then. 0.75R per breakout trade against 0.39R for
the flat hold, and 8.4% mean return per trade.

If the goal is a strategy you can stick to: **EMA9 armed at +1R** wins 56% of
the time with a positive median. You give up roughly half the expectancy for
that, which is a real price and worth naming rather than discovering later.

If you want turnover: **EMA20 with two-close confirmation** — the flat hold's
return in 40% of the time.

**Caveats.** Same as everywhere else here: NSE 2015-2026 was a bull market, a
slow trailing stop flatters itself badly in one, the universe is today's index
members, and overlapping positions are not capital-constrained. The exit
grid was also evaluated on the same data the entry study used, so treat the
ranking as more reliable than the levels.
