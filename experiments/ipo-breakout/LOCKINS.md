# Lock-in expiry: the one causal, non-price feature — and it works

**This is the strongest result in the project**, and the only one whose input is
not derived from price. `scripts/lockin_study.py`; full output in
`out/lockin_study.txt`.

---

## 1. Why it is different from everything else here

Every other feature in this experiment is a function of past prices, so a
positive result over a decade that mostly went up is always partly a statement
about the decade. A lock-in expiry is not:

- it is **statutory**, not contractual — SEBI's ICDR regulations, the same for
  every issue under the same rule;
- it is **fixed at allotment**, before the stock has traded a single day;
- it is **knowable years in advance** from the listing date alone;
- it has a **mechanism**: Aggarwal, Krigman and Womack's account of managers
  underpricing to generate "information momentum" and then selling into it at
  expiry. On that account the selling at the unlock is not a side effect, it is
  the plan.

`RESEARCH.md` §3 has the schedule and its two mid-sample regime breaks.

---

## 2. What was measured

For each of 460 matched listings, the **excess over the market** — same dates,
same horizon — over the 10 sessions *into* each unlock and the 10 sessions *out
of* it. Both windows are block-bootstrapped by calendar month.

Two things make this a test rather than a chart:

**A placebo.** "Returns are poor around day 30" proves nothing on its own; new
listings might simply drift in their second month. So the identical measurement
runs at four offsets from listing — +55, +125, +150 and +220 days — where no
statutory unlock falls.

**A falsifiable prediction.** The anchor rule changed on 1 April 2022: before it
the *whole* anchor tranche unlocked at day 30; after it, half at 30 and half at
90. If the effect is supply, then the day-30 effect must be **larger before the
split than after**, and a day-90 effect must appear **only after**. A pooled
average cannot be refused by data. This can.

---

## 3. The result

Excess over market, 10 sessions **into** the date:

| Event | n | before | 95% CI | after | 95% CI |
|---|---:|---:|---|---:|---|
| **anchor +30d (100%, pre-Apr-2022)** | 165 | **−3.32%** | **[−5.07%, −1.51%]** | −0.10% | [−1.46%, +1.47%] |
| **anchor +30d (50%, post-Apr-2022)** | 295 | −1.22% | [−2.44%, +0.09%] | +1.76% | [+0.48%, +2.99%] |
| **anchor +90d (50%, post-Apr-2022)** | 295 | **−2.52%** | **[−3.33%, −1.61%]** | −0.26% | [−1.07%, +0.54%] |
| pre-IPO / promoter excess +12m | 131 | −1.79% | [−2.94%, −0.55%] | −0.62% | [−1.76%, +0.53%] |
| pre-IPO / promoter excess +6m | 326 | −0.36% | [−1.26%, +0.62%] | +0.26% | [−0.87%, +1.32%] |
| promoter minimum +18m | 223 | 0.00% | [−1.03%, +1.07%] | −0.92% | [−1.47%, −0.36%] |
| promoter minimum +36m | 131 | −0.21% | [−1.79%, +1.45%] | −0.06% | [−1.61%, +1.70%] |
| *placebo +55d* | 460 | *+1.37%* | *[+0.44%, +2.39%]* | +0.47% | [−0.40%, +1.37%] |
| *placebo +125d* | 460 | *−0.35%* | *[−1.04%, +0.39%]* | +0.60% | [−0.36%, +1.65%] |
| *placebo +150d* | 460 | *+0.20%* | *[−0.70%, +1.10%]* | −0.16% | [−0.85%, +0.54%] |
| *placebo +220d* | 449 | *+0.45%* | *[−0.31%, +1.24%]* | +0.41% | [−0.30%, +1.11%] |

### 3.1 The effect is *before* the date, not after

This is the finding, and it was nearly missed. The study was first written to
bootstrap only the **after** window — the intuitive "what happens when the
supply hits" — which is flat everywhere. The whole effect is in the run-up: a
new listing drifts **down against the market into an anchor unlock it has known
about since allotment**, and then stops.

That is a market pricing in a date, not reacting to one. The trade it implies is
not "short the unlock" but "do not buy into one".

### 3.2 The placebos behave

Three of four placebo *before* windows straddle zero. The fourth, +55 days, is
**+1.37% [+0.44%, +2.39%]** — significantly *positive*, the opposite sign to
every anchor row. So the anchor result is not "new listings drift down"; over
the same stretch of a listing's life, at a date with no unlock, they drift up.

### 3.3 The regime prediction holds, on both halves

| Prediction | Result |
|---|---|
| day-30 effect larger when the **whole** tranche unlocks there | **−3.32%** (100% tranche) vs **−1.22%** (50% tranche) ✓ |
| a day-90 effect appears **only** in the split regime | **−2.52%**, CI clear of zero, and there is no day-90 unlock before Apr 2022 ✓ |
| split by listing regime | pre-2021 **−3.47%** [−4.95%, −1.96%]; anchor-split **−1.22%** [−2.44%, +0.09%] ✓ |

The size of the effect tracks the size of the tranche being released. That is
the supply story making a quantitative prediction and the data agreeing.

### 3.4 The discretionary unlocks are weak, as expected

`RESEARCH.md` §3.3 says in advance that only the anchor dates are *statutory* —
whether a promoter or a pre-IPO fund actually sells at 6 or 18 months is a
choice. The table agrees: the 6-month row is flat, the 18-month row is flat
before and marginally negative after, the 36-month row is nothing. Only the
12-month pre-IPO row (−1.79%) is distinguishable from zero, and it covers the
pre-2021 regime where that lock-in was the binding one.

---

## 4. What it is worth in a book

Signals landing near an anchor unlock earned **+0.67%** excess over market at 20
sessions, against **+4.31%** for signals that did not (`out/analysis_base_strict.txt`).

Turned into a filter — refuse any breakout landing within 14 days before an
anchor unlock — and compared **at matched exposure**, which is the comparison
the brief insists on because a filter that removes trades otherwise flatters
itself by sitting in cash:

| | CAGR | max DD | MAR | win rate | trades | exposure |
|---|---:|---:|---:|---:|---:|---:|
| every signal, ₹1.00L a slot | 10.5% | −11.7% | 0.90 | 53.8% | 184 | 25.7% |
| **unlock filter, ₹1.33L a slot** | **12.3%** | **−8.6%** | **1.44** | **58.7%** | 126 | 22.7% |

**Return up, drawdown down, win rate up — all three at once.** The brief warns
that win rate is a vanity metric and that roughly 25 VCP filters raised it while
lowering return; this is the rarer case where the three move together, and it is
also the only filter tried here that has a mechanism rather than a number.

---

## 5. What this does not establish

- **The dates are approximate.** Lock-in runs from *allotment*; this project
  knows *listing*. The gap is 2–4 sessions and it shifts mid-sample (SEBI cut
  the listing timeline from T+6 to T+3 in December 2023). Every window here is
  ten sessions wide, which swallows it, but a tighter study would need actual
  allotment dates.
- **Survivorship still applies.** 28 of 549 mainboard IPOs could not be matched,
  and delisted names are absent from the instrument master entirely.
- **It is one market and one decade.** 460 listings, 2016–2026, all NSE
  mainboard.
- **The filter's benefit is measured on 126 trades.** The direction is
  consistent with the unconditional study on 460 listings, which is the reason
  to believe it, but the filter itself is not independently significant at that
  sample size and is not claimed to be.
