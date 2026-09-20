# The VCP rule set, as decided

One entry, one exit, one sizing rule. Everything here was selected by the
measurements in `VCP_PREBREAKOUT.md` and `VCP_EXITS.md` and then checked
year-by-year and at portfolio level (`scripts/vcp_final.py`).

**Read §4 before using any of it.** The headline is that this does NOT beat
buying and holding the same universe on return — its entire case is that it
does so at a third of the drawdown.

---

## 1. Entry

**Signal.** `detect_vcp` reports `state == "forming"` — every structural gate
passed, no close yet above the pivot. In detail, all of:

- **3–6 contractions** (the detector finds 2T bases too; they are skipped —
  see §4), each ≤ 85% of the depth of the one before
- first contraction 8–40% deep, last ≤ 12%
- base does not undercut its own start; price within 10% of the base's high
- prior advance ≥ 25% into the base
- final-contraction volume below the base average
- range contraction measured **inside the base** (last contraction's mean
  true range ≤ 75% of the first's) — raw true range, not a smoothed ATR
- ≥ 6 of the 7 candle-computable Trend Template criteria
- composite score ≥ 0.65

**Action.** Buy at the close of the first bar the base is visible — **unless
the stop sits closer than 0.5 ATR14 from entry, in which case skip it**. A
stop inside half a day's normal range is not a stop, it is a coin flip:
those signals won 8% of the time and held a tenth of the profit while
occupying a third of the capital. Adding this one gate took CAGR from 19.8%
to 21.8%, drawdown from −21.0% to −17.1% and the win rate from 20% to 27%
(`VCP_LOSSES.md`).

**Do not wait for it to coil closer to the pivot.** Measured directly: a
"wait for within 3% of the pivot" rule earns 0.58R against 0.91R for
entering immediately, because while you wait the stop stays put and your
risk grows from 2.55% to 5.90% while the stop-out rate only falls from 76%
to 59%. Waiting is the worst option tested.

**Do not wait for the breakout either** — 0.40R, and you forgo a median 7.0%
of price.

## 2. Exit — the stop, in full

There are **two stops in sequence**, and neither is a percentage rule.

### Phase 1, before the breakout: the final contraction's low

```
stop    = the low of the last contraction in the base   (vcp.py: stop_price)
trigger = an intraday LOW touching it
fill    = at the stop, or at the OPEN if the bar gapped through it
```

It is **static** — it never moves while the base forms. The reasoning is
that this low is precisely where the VCP's own claim ("each pullback is
shallower than the last") is falsified. Below it the base is not tightening
any more, so there is nothing left to be long of.

This is where 63% of positions die, in a median of 5 bars, for about −1R.
That is the design working: failures are cheap, fast, and free the slot.

### Phase 2, after the breakout: the 50 EMA

```
exit on the first CLOSE below the 50 EMA
the contraction low stays armed as a hard floor throughout
```

Whichever fires first. In practice the floor rarely binds after a breakout —
price is well above it by then and the moving average does the work. 33% of
trades end this way; 3% run into the holding cap.

### The gate that decides whether the stop is usable at all

```
skip the signal if (entry - stop) < 0.5 x ATR14
```

Really a statement about stop QUALITY rather than placement: a stop inside
half a day's normal range is not a stop, it is a coin flip. Signals under
0.4 ATR won 8% of the time (`VCP_LOSSES.md`).

### What that produces

| | All signals | Adopted (3T + 0.5 ATR) |
|---|---|---|
| Risk at entry, median | 2.55% | **3.57%** |
| p10 / p90 | 0.56% / 8.22% | 1.71% / 8.70% |
| Stop distance in ATR, median | 0.79 | **1.16** |

The gate lifts the typical stop from inside a day's noise to just outside it.

### What the stop deliberately is NOT

- **Not a fixed percentage.** O'Neil's 7–8% rule is not used; the stop is
  wherever the structure puts it, which is why risk runs from 1.7% to 8.7%.
- **Not an ATR multiple.** ATR only gates whether the trade is taken. It
  never places the stop.
- **Not trailed before the breakout.** Tightening into an unconfirmed base
  guarantees a shakeout.
- **Not moved to breakeven.** Untested, and given how much of the profit
  sits in the tail it has the same shape as every other filter here that
  bought win rate and paid in return.

Two consequences worth internalising: **the stop defines the position size**
(risk is entry-to-stop, so a tight base earns a bigger position at the same
rupee risk), and **a stop-out is cheap and fast**, which is the only reason
a 63% loss rate is survivable.

## 3. Sizing and capacity

- Risk 1% of equity per trade (entry to the contraction low).
- **Cap each position at 1/slots of equity.** This binds constantly and it
  matters: a 2.55% stop asks for a 39% position to risk 1%, which is how a
  tight stop quietly becomes leverage.
- 10–12 concurrent slots.
- Take signals mechanically. The median trade loses 1R and 78% are losers;
  the expectancy is entirely in a handful of large winners. Skipping the
  uncomfortable ones after a losing streak is enough to turn it negative,
  and you cannot know in advance which trade is the one in twenty.

---

## 4. What it actually produced

474 NSE symbols, 2016-07 to 2026-09, 12 slots, 1% risk, 0.3% round-trip cost:

On the ₹10,00,000 / fixed-position book, with both gates:

| Variant | Per trade | Deployed | CAGR | Max DD | MAR | Win | Trades |
|---|---|---|---|---|---|---|---|
| **3T + 0.5 ATR** | ₹1,00,000 | 35% | 16.9% | **−11.9%** | **1.42** | **30.7%** | 502 |
| **3T + 0.5 ATR** | ₹1,50,000 | 41% | **20.4%** | −14.5% | **1.41** | 31.4% | 437 |
| 3T + 0.5 ATR | ₹2,00,000 | 45% | 21.6% | −17.1% | 1.27 | 31.2% | — |
| all T + 0.5 ATR | ₹1,00,000 | 57% | 21.8% | −17.1% | 1.27 | 26.7% | 1,386 |
| no gates | ₹1,00,000 | 62% | 19.8% | −21.0% | 0.94 | 20.2% | 1,806 |

**Read that table at matched deployment, not matched position size.** The 3T
filter looks like it costs five points of CAGR only because it leaves
two-thirds of the book in cash. Sized up to a comparable exposure, `3T +
0.5 ATR at ₹2,00,000` and `all T at ₹1,00,000` land on *identical* risk —
both −17.1% drawdown, both MAR 1.27, 21.6% against 21.8% CAGR — but the 3T
version gets there on **502 trades instead of 1,386** and a 31% win rate
instead of 27%. Same outcome, a third of the activity.

₹1,50,000 a position is the pick: 20.4% CAGR at −14.5%, MAR 1.41.

| Earlier reference points | CAGR | Max DD | Return/DD |
|---|---|---|---|
| risk-based sizing, 12 slots (no gates) | 21.3% | 15.8% | 1.35 |
| *equal-weight buy-and-hold, same universe* | *22.9%* | *46.7%* | *0.49* |
| early entry + EMA20×2 | 15.0% | 19.9% | 0.75 |
| breakout entry + EMA9 @ +1R | 15.6% | 16.9% | 0.92 |
| early entry + flat 40-bar hold | 11.0% | 24.3% | 0.45 |
| *equal-weight buy-and-hold, same universe* | *22.9%* | *46.7%* | *0.49* |

**It loses to buy-and-hold on return and wins by roughly 3× on risk-adjusted
return.** That is the whole case, and it should be stated that way rather
than as "21% CAGR".

Sensitivities:

- **Slots** (early + EMA50): 5 → 23.6% CAGR / 17.0% DD; 8 → 19.8% / 16.4%;
  12 → 21.3% / 15.8%; 20 → 19.0% / 14.3%. Stable across the range. The
  5-slot figure is the highest but rests on 424 trades.
- **Costs** at 12 slots: 0% → 23.8%; 0.3% → 21.3%; 0.6% → 18.8%; 1.0% →
  15.5%. Survives realistic Indian delivery costs, with real erosion.
- **Year by year** (mean R): 2016 0.37, 2017 1.64, 2018 0.07, 2019 0.01,
  2020 3.50, 2021 1.79, 2022 −0.15, 2023 6.03, 2024 −0.10, 2025 0.39,
  2026 0.31. Two negative years, and 2020 and 2023 carry a large share.

That last line is the honest counterweight to choosing EMA50. The flat-hold
variant is positive in all eleven years at a much lower return; EMA50 buys
its extra return partly by holding longer in years when holding longer
worked. If you want consistency over return, `early + EMA20×2` has the same
two-negative-year profile with less of the 2023 dependence.

---

## 5. What is still unproven

Ranked by how much it could change the answer.

1. ~~Slot allocation is arbitrary.~~ **Tested and closed** — see
   `VCP_RANKING.md`. Eleven ranking keys against date order and 40 random
   allocations: nothing survives a multiple-comparison correction, relative
   strength only helps at one of three lookbacks, and random allocation
   matches date order to within 0.2% CAGR. The 79% skip rate is a *capital*
   constraint, not a *selection* one; the signals you miss are no worse than
   the ones you take. One thing did come out of it: do not rank by the
   detector's score — highest-first was the worst of the eleven keys.
2. **Survivorship bias**, inflating both the strategy and the benchmark. The
   474 symbols are today's Nifty 500 members held back to 2016.
3. **The rules were selected on this data.** Detector parameters came from
   the synthetic benchmark, so those are out-of-sample here — but the entry
   policy and the exit rule were both chosen by looking at these results.
   The year split is the defence and it is a weak one with eleven points.
4. **A bull market.** A 50-day trailing stop flatters itself in one, and
   2015-2026 on the NSE was one.
5. **No liquidity or impact model.** Position sizes are fractions of equity
   with no check against traded volume, and stop fills assume the open on a
   gap, which is optimistic for a thin name.
6. ~~No regime filter.~~ **Tested and closed** — see `VCP_REGIME.md`. Every
   variant of a NIFTY 200-day filter lowers CAGR *and* raises drawdown
   (19.8%/−21.0% becomes 16.7%/−25.5%), and it does not remove the losing
   years — 2022 gets four points worse. Not explained by 2020 or by reduced
   diversification. The likely reason is that the Trend Template gate
   already imposes a per-stock regime test, which is better targeted than an
   index-level one.
