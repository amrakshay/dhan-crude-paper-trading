# EXPIRY_MIX — weekly or monthly? Both, and that was a bug

Asked after the first results were written up: *did we trade weekly expiries or
monthly?*

The answer was **both, in a proportion that drifted across the sample without
being declared** — and finding that out exposed a second, worse problem in how
expiries were chosen. Both are fixed; neither changes the study's conclusions,
but one of them changes the *interpretation* of a caveat I had called the
strongest evidence against the one positive finding.

---

## 1. What was actually traded

The entry rule was "the listed expiry nearest **30 DTE**, within ±10". NIFTY
lists weeklies alongside monthlies, so that rule silently picks whichever
happens to be closest — a weekly 28 days out beats a monthly 31 days out.

Across the 172 completed condor trades:

| | trades | share | DTE at entry (min / median / max) |
|---|---|---|---|
| monthly | 99 | **57.6%** | 20 / 31 / 40 |
| weekly | 73 | **42.4%** | 26 / 28 / 34 |

So: a mix, leaning monthly. But the mix was not stable.

### The drift

| year | weekly | monthly |
|---|---|---|
| 2016 | 0 | 15 |
| 2017 | 0 | 15 |
| 2018 | 1 | 14 |
| 2019 | 0 | 14 |
| 2020 | 1 | 12 |
| 2021 | 7 | 9 |
| **2022** | **18** | **0** |
| 2023 | 15 | 4 |
| 2024 | 12 | 5 |
| 2025 | 10 | 7 |
| 2026 | 9 | 4 |

**The strategy traded monthlies until roughly 2021 and weeklies thereafter.**
NIFTY weekly options were introduced in February 2019, and the far-dated weeklies
took a further two years to become liquid enough to pass the 100-trade gate at
~30 DTE. Once they did, they started winning the "nearest to 30" contest.

That is an **undeclared regime change inside the strategy**, and it is exactly
the kind of thing a backtest should not be allowed to do quietly.

---

## 2. The confound it creates

`BULL_PUT.md` §2 reported the half-split as "the strongest evidence against" the
bull put spread result: the first half of the sample negative, the second
strongly positive. That split is **almost perfectly collinear with the expiry
mix**:

| period | trades | % weekly | total P&L |
|---|---|---|---|
| first half (to 2021-07) | 78 | **3%** | −₹150,013 |
| second half (from 2021-07) | 94 | **76%** | +₹50,021 |

The era effect and the weekly/monthly effect cannot be separated in this sample
as it was run. "The strategy got better after 2021" and "the strategy switched to
weeklies after 2021" are the same statement about this data.

**That caveat stands, but its interpretation is now ambiguous rather than
clean** — and an ambiguous caveat is weaker evidence than the unambiguous one I
had claimed.

---

## 3. The worse bug underneath it

`_pick_expiry` chose **one** expiry and, if that chain could not produce a
complete structure, skipped the session entirely.

So in 2019–2021, when the nearest-to-30 expiry was often a thin weekly that
failed the liquidity gate, those sessions produced **no trade at all** — even
though a perfectly liquid monthly sat one day further away. The engine was not
just picking an undeclared expiry type; it was **silently discarding sessions
because of which expiry it happened to look at first**.

Fixed: `_candidate_expiries()` now returns every expiry inside the DTE window,
ranked best-first, and `run()` tries them in order until one builds.

### What the fix changed

| structure | | trades | total P&L | return on capital at risk /yr |
|---|---|---|---|---|
| iron condor | before | 172 | −₹99,993 | −31.52% |
| | **after** | **205** | **−₹95,632** | **−25.91%** |
| bull put spread | before | 247 | +₹61,959 | +20.52% |
| | **after** | **308** | **+₹98,180** | **+26.00%** |

Both gained roughly 20–25% more trades, and both moved in the same direction they
already pointed. **No conclusion changes.** The condor still loses, the bull put
spread is still the only positive defined-risk structure, and the put/call
asymmetry is untouched.

---

## 4. So: are weeklies better than monthlies?

**Not distinguishably.** Forcing each type explicitly, over the full sample:

| structure | expiry | trades | total P&L | R/risk /yr | bootstrap 5–95% |
|---|---|---|---|---|---|
| iron condor | monthly | 173 | −₹50,921 | −14.92% | [−230,338, +125,096] |
| iron condor | **weekly** | 143 | **+₹118,236** | **+47.95%** | [−43,978, +267,509] |
| bull put | monthly | 268 | +₹92,109 | +27.52% | [−83,304, +255,085] |
| bull put | **weekly** | 190 | +₹100,280 | +41.26% | [−43,737, +222,734] |

The weekly condor's +47.95% is the eye-catching cell in this whole study. It is
**not a finding**:

- its bootstrap interval is **[−43,978, +267,509]** — it straddles zero;
- the confidence intervals of the weekly and monthly variants **overlap heavily**
  for both structures;
- on a matched window (from 2019-02, when both types are listed) **the sign of
  the difference flips between structures** — weekly beats monthly for the
  condor (+46.8% vs +13.1%) and monthly beats weekly for the bull put
  (+55.0% vs +41.2%).

An effect whose direction depends on which structure you apply it to, with
overlapping intervals, is sample noise. There is no weekly-versus-monthly edge
here that this data can support.

### Why it might not be noise, and why that does not rescue it

There is a plausible mechanism — weeklies decay faster and are more liquid at the
money — and a plausible counter-mechanism — a weekly held ~30 days has been
through more of its own gamma, and four legs of brokerage buy less premium. Both
are arguable, neither is measurable at this sample size, and reaching for the
favourable one after seeing the number is how a selection artefact gets a story
attached to it.

---

## 5. What changed in the specification

`StrategyConfig.expiry_type` now exists and takes `any | monthly | weekly`. It
defaults to `any`, which reproduces the measured headline, and the composition is
reported rather than left implicit.

`FINAL_LOGIC.md` §A.3 specifies **monthly** — not because it measures better (it
does not, distinguishably) but because a declared rule beats an emergent one, and
monthlies are the series that exists across the whole sample. A rule that
silently changes what it trades halfway through a backtest is not a rule.
