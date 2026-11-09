# MARGIN — the denominator, and why it is not the one to size on

For a hedged premium seller the hedge exists to collapse the margin, so return
on margin is supposed to be the metric. Building the model changed that
conclusion: **margin is the wrong denominator here, and the model is what shows
it.**

The model is `conf/margin/nse-index-options-margin.yaml` and
`hedgelib/margin.py`. Reproduce every figure with
`python scripts/margin_report.py`.

---

## 1. What it is

A SPAN-**shaped** scenario model, marked `APPROXIMATION`:

1. Reprice **both legs** across the 16-scenario SPAN risk array — the forward
   shifted by 0, ±⅓, ±⅔ and ±1 of the price scan range, each with volatility up
   and down, plus two extreme moves at reduced weight.
2. Scan risk = the worst portfolio loss across those scenarios.
3. Add Extreme Loss Margin on the net short legs.
4. Add net option premium payable, from 2025-02-10 (nil for a credit structure).

Two properties a flat percentage-of-notional model does not have:

- **The wing's relief is measured, not assumed**, because both legs are
  repriced. That relief is the entire economic reason to buy a wing.
- **The requirement expands in a crisis**, because the price scan range is driven
  by NSE's own published daily volatility rather than a constant.

### The input that makes it sourced rather than invented

`FOVOLT`, NSE's daily volatility file, published per underlying per session and
verified available **2016-01-04 → 2026-09-17**:

> `Applicable Daily Volatility (M) = Max (E or K)`

That is the exact σ the exchange's own price scan range is defined on. It is
fetched, not modelled.

One session in the sample (2021-03-31) has a bhavcopy and no volatility file.
The previous session's σ is carried forward and the substitution is counted —
before that was handled, the model returned **zero margin** for that trade, and a
zero denominator produced a return-on-margin of several hundred times that
poisoned every tail statistic downstream.

---

## 2. Why it cannot be real SPAN

NSE's historical par files (`nsccl.<date>.s<n>.zip`) are **not archived** — 404
at every path probed on 2026-09-20. Only the current day's parameters are
practically obtainable.

**No historical backtest by anyone can use the exchange's actual number for a
past day.** That is a limit of the public record, not a shortcut taken here, and
it is stated on the dashboard rather than buried.

Direct calibration against a live broker margin calculator was attempted and
abandoned: Zerodha's endpoint sits behind a Cloudflare bot challenge, and working
around bot detection is out of bounds. Every *rate* here comes from a published
rule; the one structural choice that could not be read off a rule is calibrated
against broker-*reported* figures.

---

## 3. The one assumption with no rule behind it

The NCL FAQ says ELM "shall be netted at client and prop levels" and does not say
whether a long option at a **different strike** offsets a short one. Both
readings are implemented.

Calibration case — NIFTY 500-wide bull put spread, 1 lot, F = 24,500, 30 DTE,
σ = 0.0085. Credit ₹6,387, max loss ₹31,113, naked requirement ₹88,314:

| reading | scan risk | ELM | margin | vs naked | vs max loss |
|---|---|---|---|---|---|
| gross | 18,060 | 36,750 | **₹54,810** | 62.1% | 1.76× |
| **netted** | 18,060 | 0 | **₹18,060** | 20.5% | 0.58× |

Brokers report hedged index spreads at **₹15,000–30,000 per lot** and at
"roughly a quarter to a third of the margin a naked short needs".

**Netted is used**: inside the rupee band and near the ratio band, where gross is
outside both by roughly a factor of two.

**The residual gap is reported, not tuned.** Netted gives 20.5% of naked against
a reported 25–33%, so even on the calibration case this model is somewhat *more
generous* than reality.

A sign error found while writing this is worth recording: net option premium was
initially added with the wrong sign, so the **credit received was being added to
the margin** of every credit structure. That inflated the calibration case to
₹24,448 — which happened to sit neatly inside the reported band. The calibration
looked better when it was wrong.

---

## 4. The finding that changed the metric

Full netting zeroes the ELM for any balanced spread *however far the wing sits*,
so margin collapses to the SPAN scan alone. The scan reaches only ±3σ, floored at
5% of spot — and a 5-delta wing sits 8–12% away.

Measured over the real backtest: **margin runs at a median of 26% of the
structure's own maximum loss.**

The exchange is not collateralising the whole tail. Consequences:

- **Return on margin is a leveraged number.** It flatters every structure by
  roughly 4×.
- **The worst trade lost 667% of the margin it was blocking.** You cannot lose
  that and stay solvent at any meaningful size.
- **Sizing by margin is incoherent.** For a defined-risk structure the capital at
  risk is the **maximum loss**, and that is what every headline return in this
  study is measured against. `annualised_return_on_max_loss` exists for this
  reason, and `max_loss_per_margin` is on every summary as the counterweight.

This is a genuine property of scan-based margining rather than a modelling
artefact, and it is the most useful thing the margin model produced.

---

## 5. When it responds to volatility, and when it does not

| daily σ | annualised | scan range | margin | vs calm |
|---|---|---|---|---|
| 0.0050 | 9.6% | 1,225 | 18,060 | 1.00× |
| 0.0085 | 16.2% | 1,225 | 18,060 | 1.00× |
| 0.0120 | 22.9% | 1,225 | 18,060 | 1.00× |
| 0.0200 | 38.2% | 1,470 | 21,767 | 1.21× |
| 0.0300 | 57.3% | 2,205 | 28,829 | 1.60× |
| 0.0500 | 95.5% | 3,675 | 31,097 | 1.72× |

NIFTY's typical daily σ is about 0.0085, which sits **below the 5% scan floor**,
so in calm markets the floor binds and margin is flat. It only begins to expand
above roughly 1.7% daily — 32% annualised.

**Margin relief arrives late.** The requirement is constant through ordinary
volatility and only rises once a genuine crisis is already under way, which is
the opposite of the smooth response a naive model would show and is worse for a
seller than either.

---

## 6. Sensitivity — and the conclusion does not depend on it

Full backtest, iron condor, 2016–2026:

| variant | mean margin | return on margin | max loss ÷ margin |
|---|---|---|---|
| **BASE (netted ELM)** | 10,303 | **−4.53%** | −666.9% |
| ELM gross, not netted | 50,667 | −0.92% | −107.1% |
| scan range 2σ | 10,167 | −4.59% | −729.7% |
| scan range 6σ | 13,737 | −3.40% | −358.8% |
| scan floor 3.0% | 5,567 | −8.38% | −1003.0% |
| scan floor 9.3% | 23,581 | −1.98% | −309.3% |
| vol scan range 5% | 10,175 | −4.58% | −666.9% |
| vol scan range 25% | 10,835 | −4.31% | −608.5% |
| ELM on strike, not forward | 10,303 | −4.53% | −666.9% |

**Return on margin is negative under every variant**, from −0.92% to −8.38%.
The ELM netting choice moves the denominator by a factor of five and does not
change the sign of the answer. That is the useful thing to take from this table:
the study's conclusion does not rest on its most uncertain assumption.

---

## 7. What is not modelled

- Inter-month and inter-commodity spread charges. This study trades one expiry at
  a time so they should not arise — but a calendar or diagonal would need them,
  which is why those structures are researched and not backtested.
- Concentration margin, delivery margin, intraday crystallised losses. None apply
  to a cash-settled index spread held to the close.
- Peak-margin and intraday snapshot rules. This is a daily-resolution study.
- The volatility scan range is a parameter, not a published daily figure. Swept.
