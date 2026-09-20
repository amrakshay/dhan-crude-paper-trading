# Reducing the losing trades

**One filter works, and it is the mildest one.** Requiring the stop to sit at
least half an ATR from entry lifts the win rate from 20% to 27%, the CAGR
from 19.8% to 21.8%, and cuts the drawdown from −21.0% to −17.1%. Everything
stricter than that buys more win rate and pays for it in return.

```bash
.venv/bin/python scripts/vcp_losses.py          # the analysis
.venv/bin/python scripts/vcp_backtest.py        # now applies the filter
```

---

## 1. Where the losses actually come from

3,188 signals: 708 winners (22%), 2,480 losers (78%), +5,173R in total.

| | n | Share | Mean R | Total R | Median bars held |
|---|---|---|---|---|---|
| never broke out (base failed) | 2,080 | 65% | −1.06 | −2,196 | **2** |
| broke out, then failed | 402 | 13% | −0.80 | −321 | 15 |
| broke out, worked | 706 | 22% | **+10.89** | +7,690 | 51 |

Two things follow immediately.

**Almost all the losses are base failures, not breakout failures** — 65% of
signals versus 13%. The trade dies before the pattern ever confirms.

**They die in a median of two bars.** That is the design working: the tight
stop makes a failure cheap and fast, and the slot is freed almost
immediately. A loser costs −1.02R on average against a winner's +10.89R.

Losers cost 2,517R in total; winners make 7,690R. **The losses are the price
of the optionality**, and the interesting question is never "how do we have
fewer" but "can we have fewer without losing the tail that pays for them".

## 2. The trap, in one table

Stop distance measured in the stock's own ATR:

| Stop distance | n | Win rate | Mean R | Share of rupee profit |
|---|---|---|---|---|
| **< 0.4 ATR** | 887 | **8%** | 2.14 | **10%** |
| 0.4 – 0.7 ATR | 563 | 17% | 2.55 | 23% |
| 0.7 – 1.0 ATR | 417 | 25% | 1.97 | 16% |
| 1.0 – 1.5 ATR | 510 | 25% | 0.92 | 14% |
| > 1.5 ATR | 811 | **38%** | 0.67 | 37% |

Win rate rises fivefold from left to right and mean R collapses. Filter to
the right-hand side and you get a comfortable strategy that earns less.

That is exactly what happens:

| Filter | Kept | Win rate | CAGR | Max DD |
|---|---|---|---|---|
| none | 100% | 22% | 19.8% | −21.0% |
| **stop ≥ 0.5 ATR** | 66% | **27%** | **21.8%** | **−17.1%** |
| stop ≥ 0.7 ATR | 55% | 31% | 18.5% | −20.4% |
| stop ≥ 1.0 ATR | 41% | 33% | 15.9% | −22.2% |
| stop ≥ 1.25 ATR | 32% | **36%** | **15.7%** | −22.8% |

**Win rate 22% → 36%, CAGR 19.8% → 15.7%.** You can buy comfort at a fixed
exchange rate. Only the gentlest setting is a free lunch.

## 3. The rule adopted

**Skip any signal whose stop sits closer than 0.5 ATR14 from entry.**

It has a mechanism, which is the main defence against it being one more
threshold fitted to one dataset: *a stop inside half a day's normal range is
not a stop, it is a coin flip.* Signals under 0.4 ATR won 8% of the time.

| | Baseline | With the filter |
|---|---|---|
| CAGR | 19.8% | **21.8%** |
| Max drawdown | −21.0% | **−17.1%** |
| MAR | 0.94 | **1.27** |
| Win rate | 20.2% | **26.7%** |
| Profit factor | 2.12 | **2.43** |
| Trades | 1,806 | 1,386 |
| Negative years | 6 of 11 | 5 of 11 |
| Final on ₹10L | ₹62.7L | **₹74.2L** |

**Why it helps is not quite what it looks like.** The discarded signals were
90% losers, but their *average* outcome was +2.15% and they held 18% of gross
rupee profit — they are not pure waste. The gain comes from capital: 1,382
signals were being skipped for want of cash, so refusing to tie ₹1,00,000 up
in a coin flip lets a better queued signal in. Skipped signals fall from
1,382 to 714. On a book with no slot pressure the filter would help much
less.

## 4. What else was tested, and failed

| Candidate | Kept | Win rate | CAGR | Verdict |
|---|---|---|---|---|
| turnover ≥ ₹5cr/day | 89% | 21% | 13.3% | liquidity is not the problem |
| price ≥ ₹100 | 87% | 21% | 16.3% | sub-₹100 names hold 44% of the profit |
| ATR ≤ 3% of price | 40% | 24% | 10.0% | volatility is the edge, not the enemy |
| entry bar closed up | 44% | 27% | 17.6% | raises win rate, costs return |
| all 7 Trend Template | 78% | 22% | 17.7% | the 6-of-7 gate is already enough |
| 3+ contractions | 29% | 22% | 18.1% | see below |

**`3+ contractions` is the one honourable mention, and it has since been
adopted.** Combined with the stop rule it gives 16.9% CAGR at −11.9%
drawdown — MAR 1.42, the best risk-adjusted result anywhere in this project
— on only 502 trades.

The apparent cost in CAGR turned out to be an artefact of **under-deployment**:
with only 502 signals and a fixed ₹1,00,000 position, the book is 35%
invested against 57% for the unfiltered version. Compared at matched
exposure the two are indistinguishable on risk — `3T at ₹2,00,000` gives
21.6% / −17.1% / MAR 1.27 against `all T at ₹1,00,000` at 21.8% / −17.1% /
MAR 1.27 — but the 3T version needs a third of the trades. ₹1,50,000 a
position is the best balance at 20.4% / −14.5% / MAR 1.41.

Comparing selective strategies at a fixed position size, rather than a fixed
exposure, systematically understates them. Worth remembering.

## 5. What will not work, and why

The three biggest sources of loss are not filterable:

1. **65% of bases simply fail.** No feature tested separates them in advance.
   Every conditioner moves the breakout rate by a handful of points at most.
2. **The profit is in the top 5% of trades**, which produce 120% of total R.
   Any filter aggressive enough to matter has a good chance of removing one
   of them, and there is no way to know which.
3. **The detector's own score is useless for this** — correlation with
   outcome +0.024, and ranking by it made results worse
   (`VCP_RANKING.md`).

Roughly twenty-five filters were tried here. One improved CAGR. That ratio
is itself the finding: the loss rate is structural to a tight-stop breakout
strategy, and the way to live with it is position sizing and taking every
signal, not cleverness about which ones.
