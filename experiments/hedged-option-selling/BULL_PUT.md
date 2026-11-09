# BULL_PUT — is the one positive structure real, or a cell from a grid?

The bull put spread is the only defined-risk structure in the sweep that made
money: **+₹98,180, +26.00% a year on capital at risk**, against a risk-free rate
of 5.64%.

A single positive cell out of forty is exactly what a selection artefact looks
like, so it was tested harder than anything else here. Reproduce with
`python scripts/bull_put_robustness.py`; full output in
`out/bull_put_robustness.txt`.

> **These figures are the second version.** An expiry-selection bug
> (`EXPIRY_MIX.md` §3) was found after the first write-up and fixed. It changed
> no strategy rule — it only stopped the engine silently skipping sessions when
> the nearest-to-target expiry happened to be illiquid. The fix **removed the
> objection this document previously called the strongest**, and a different one
> took its place. Both versions are described below, because which objections
> survive a bug fix is itself evidence.

---

## The prediction, made before the tests

Stated first so that what follows is a prediction rather than a rationalisation.

On an equity index the volatility smile is skewed: the put sold sits on a steeper
part of it than the put bought. A put spread is therefore short the richer option
and long the cheaper one **in volatility terms**, and a call spread is the
reverse.

Measured on this data — NIFTY 2025-08-01, 27 DTE:

| | implied volatility |
|---|---|
| 16-delta **put** | **12.8%** |
| 16-delta **call** | **10.6%** |

If that is the mechanism, the **bear call spread should be badly negative**. It
is: **−88.07% a year** on capital at risk, against the bull put's +26.00%. The
sign of the other side is the test the mechanism could have failed, and did not.

---

## 1. The surface — one parameter at a time

**18 of 19 variants positive; 18 of 19 beat the risk-free rate. Median +24.45% a
year.** The single negative is DTE 7.

| | range across the dimension |
|---|---|
| short delta (0.08 → 0.40) | all positive |
| DTE (7 → 60) | **DTE 7 negative**, 14–60 positive |
| wing (0.02 → 0.10) | all positive |
| management (hold, PT25/50/75, stop) | all positive |

This is much flatter than the first run, which had 4 of 19 negative and was
described here as "bumpy". The bumpiness was largely the bug: the skipped
sessions were not randomly distributed, they were concentrated wherever a thin
weekly happened to sit nearest the target.

**A flat surface is better evidence than a bumpy one, but it is not strong
evidence.** These 19 variants are not 19 independent tests — they are one sample
re-cut 19 ways, and they share the same trades, the same decade and the same
seven bad days.

---

## 2. Is it an artefact of one period?

| slice | n | total P&L | R/risk /yr |
|---|---|---|---|
| full sample | 308 | +98,180 | +26.00% |
| before SEBI 2024-11-20 | 260 | +124,448 | **+43.33%** |
| **after SEBI 2024-11-20** | **48** | **−26,269** | **−29.08%** |
| excluding 2020 (COVID) | 272 | +85,935 | +25.73% |
| excluding the worst 3 trades | 305 | +224,569 | +60.91% |
| **first half (to mid-2021)** | 152 | **+37,578** | **+21.71%** |
| **second half (from mid-2021)** | 156 | **+60,602** | **+29.64%** |

### The objection that went away

The first version of this document reported the first half at **−5.28%** and
called that "the strongest evidence against" — a structural premium should appear
in both halves and this one did not.

**It does now: +21.71% and +29.64%.** The earlier negative first half was an
artefact of the expiry bug, which bit hardest in 2019–2021 when far-dated
weeklies were listed but not yet liquid. That is a genuine strengthening of the
result, and it is also a caution about how confidently the original objection was
stated.

### The objection that replaced it

**Since SEBI's index-derivatives framework took effect on 2024-11-20, the same
rules return −29.08% a year**, against +43.33% before it.

48 trades is far too few to conclude from. But it is the period that describes
trading this *today*, and it is the window containing every structural change at
once: lot sizes roughly tripled, one weekly expiry per exchange, +2% expiry-day
ELM, and an STT rate that rose again in April 2026. `CHARGES.md` shows the STT a
seller pays is now 2.4× what it was at the start of the sample.

This is now the strongest single objection to trading this.

---

## 3. Does it survive a worse spread assumption?

| slippage | n | total P&L | R/risk /yr |
|---|---|---|---|
| none (unreal) | — | — | — |
| 1% / 1 tick (base) | 308 | +98,180 | +26.00% |
| 4% / 4 ticks | 284 | +60,054 | +16.11% |
| **8% / 8 ticks** | 256 | **+11,052** | **+2.87%** |

Robust to **eight** times the assumed half-spread, where the first run flipped
sign at 8%. It stays positive but falls below the risk-free rate there, so an
8% effective half-spread on the wing is enough to make this not worth doing even
though it does not make it lose.

Given the 5-delta wing traded 101 times on a day the near-money put traded
40,466, that is not an unreasonable stress.

---

## 4. Significance

Block bootstrap, 20,000 draws over whole calendar months:

| | |
|---|---|
| observed total | +₹98,180 |
| 5th percentile | **−₹112,795** |
| median | +₹101,610 |
| 95th percentile | +₹285,822 |
| **P(total P&L < 0)** | **20.1%** |

One chance in five that the true expectation is negative. Better than the 25.3%
of the first run, and still not significance.

---

## Verdict

**The direction is real.** The put-side bias is theory-consistent, its mechanism
was measured in the data rather than assumed, the sign of the opposite structure
confirms it, and it now survives both halves of the sample, the removal of 2020,
and eight times the assumed slippage.

**The magnitude is not established**, and four things say so:

- **the current regime is negative** — −29.08% a year since 2024-11-20, on 48
  trades;
- 20% of bootstrap resamples lose money, with a 5th percentile of −₹112,795;
- removing the worst three trades more than doubles the result, so a handful of
  observations still carry it;
- it is one market, one underlying, 2,643 sessions containing seven days worse
  than −5%.

What this supports is **"index put skew was worth something after costs on this
sample"** — not "here is a strategy", and specifically not "here is a strategy
for the market as it exists now". Establishing the magnitude needs data this
sample does not contain: more sessions, not more searching.
