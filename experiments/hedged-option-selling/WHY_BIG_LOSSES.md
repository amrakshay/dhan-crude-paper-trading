# WHY_BIG_LOSSES — a −2% stop that loses 40%

Asked after the weekly-target test: **why do some trades lose more than 10%?**

It is not a bug and it is not slippage in the ordinary sense. It is that on a
short-dated credit spread **the distance between "fine" and "maximum loss" is
about one ordinary day's move**, and a stop evaluated at the close has no
observation inside that distance.

Reproduce with `python scripts/why_big_losses.py`; output in
`out/why_big_losses.txt`.

---

## 1. They happen in a single session

Bull put spread, 16Δ short / 5Δ wing, ±2% of capital at risk:

| DTE | trades | lost >10% | **in ONE session** | mean stop slip | worst |
|---|---|---|---|---|---|
| **7** | 1,438 | 146 | **100 (68%)** | **−9.79%** | −86.4% |
| 14 | 1,190 | 66 | 41 (62%) | −6.79% | −32.1% |
| 30 | 913 | 40 | 20 (50%) | −5.61% | −30.6% |
| 45 | 658 | 12 | 8 (67%) | −5.07% | −73.0% |

Two things to read off it:

- **Half to two-thirds of the big losses occur between one close and the next.**
  They do not drift down through the stop; they jump past it.
- **The slip halves as the entry moves away from expiry** — −9.79% at 7 DTE
  against −5.07% at 45. That gradient is gamma, and it points straight at the
  cause.

Note the stop *never* delivers −2% at any tenor. Further out is less bad, not
fixed.

---

## 2. The cause: the loss zone is narrower than a day's move

| DTE | short strike | long strike | **loss zone** | **zone ÷ 1-day σ** |
|---|---|---|---|---|
| **7** | −2.22% | −4.31% | **2.09%** | **2.38×** |
| 14 | −3.07% | −5.96% | 2.89% | 3.29× |
| 30 | −4.37% | −8.23% | 3.86% | 4.39× |
| 45 | −5.58% | −10.52% | 4.95% | 5.62× |

NIFTY's median daily σ over this sample is **0.88%** — NSE's own published
figure, the same one the margin model uses.

**At 7 DTE the entire distance from full profit to maximum loss is 2.4 standard
deviations.** The sample contains **20 sessions that fell more than 3%** and
**7 that fell more than 5%**. On any of those, the spread crosses the whole zone
between one close and the next.

A −2% stop sits *inside* that zone, near the top of it. There is almost no room
between the level where the stop triggers and the level where the position is
already at maximum loss.

---

## 3. The worst trade, traced end to end

Entered 2026-01-16, expiry 2026-01-20, forward 25,717.

- **SHORT 25450 PE / LONG 25200 PE** — a gap of 250 points, **0.97% of the index**
- credit ₹1,059, maximum loss ₹15,190

| | NIFTY | mark |
|---|---|---|
| 2026-01-19 | 25,585.50 | −₹270 (**−1.8%** of max loss) |
| 2026-01-20 | 25,232.50 | −₹13,078 (**−86.1%**) |

Settlement 25,232.50: the short 25450 finished **217.50 in the money**, the long
25200 finished **worthless** — the index stopped 32.5 points short of the wing.

**The stop never had a chance to fire.** At the previous close the position was
1.8% underwater, comfortably inside the −2% stop, because the index was still
135 points *above* the short strike with a day to run. The next observation was
settlement.

NIFTY fell **1.38%** that session. The loss zone was **0.97%** wide. The day's
move was **1.4× the entire distance from fine to ruined.**

---

## 4. And near expiry the mark understates the outcome

This is the part that is easy to miss, and it compounds the first three.

The mark-to-market a stop watches is an **expectation** over what could still
happen. Settlement is **one draw** from that distribution. With a day to run and
the index just above the short strike, the expectation is only mildly negative —
the position might still expire worthless — while the realisation is either fine
or nearly the whole loss.

So a stop placed on the mark is watching the wrong variable at exactly the moment
it matters most.

**This is not fixable by moving the stop.** A tighter one fires more often on
noise; a looser one gives up more before it fires. Both were measured
(`WEEKLY_TARGET.md` §3) and both are worse.

It *is* fixable by not being there. That is what "exit at 10 DTE" does, and it is
why that was the best management rule in the sweep (`SWEEP.md` §5, +20.79% a year
against −25.91% for the base) — it leaves before the zone narrows.

---

## What this means for the rules

1. **Do not set a rupee or percentage stop on a short-dated spread and expect it
   to hold.** At 7 DTE it will average about 5× its nominal level.
2. **If you want a stop to work, the loss zone has to be wide relative to a day's
   move.** That means further from expiry, or wider strikes, or both — and both
   cost credit.
3. **Leaving before expiry week dominates stopping.** The exposure that produces
   these losses is expiry-week gamma, and the reliable way to avoid it is to not
   hold into it.
4. **Intraday management would reduce this but not remove it.** The 2026-01-20
   move was a single session, so an intraday stop would have caught part of it —
   but the 2020 sample contains gaps that opened past the strike, and no stop
   catches those. This data cannot measure intraday, so the study does not claim
   to.
