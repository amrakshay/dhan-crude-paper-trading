# Entering a VCP before the breakout

Yes, it can be detected before the breakout. Whether you should enter there
is a different question, and this file answers both.

Reproduce with:

```bash
.venv/bin/python scripts/vcp_pre_breakout.py --step 3      # the pre-breakout window
.venv/bin/python scripts/vcp_entry_policies.py --step 3    # four entries, same bases
```

---

## 1. The mechanism

`detect_vcp` returns `state="forming"` when every structural condition is
satisfied and no close has yet cleared the pivot. The pattern is complete;
only the confirmation is absent. Three things make that signal real rather
than an artefact:

**The provisional pivot.** `pivots.zigzag` returns the running extreme since
the last confirmed reversal, flagged `provisional`. A zigzag only *confirms*
a low once price has rallied far enough away from it — so had unconfirmed
pivots been discarded, the final contraction would have become visible long
after the low, and most of the pre-breakout window would be gone before the
detector could see it. Returning it is what makes early detection possible
at all.

**The dry-up window stops at the base.** Volume and range contraction are
measured over bars still under the pivot (`last_inside` in `vcp.py`), so the
forming state is computed from exactly the same evidence the breakout state
would use, minus the breakout.

**Nothing looks forward.** The detector is called with bars `0..t` and the
pivot and stop are frozen at first sighting.

Worked example — TITAN, 2017:

```
first seen forming  2017-07-27   close 527.45   footprint 7W 12/4 2T
  pivot 569.20   (+7.9% above)
  stop  524.40   (-0.6% below)
actual breakout     2017-08-04   close 611.20   (6 sessions later, +15.9%)
```

---

## 2. How much room the window gives you

3,188 distinct bases, 474 NSE symbols, 2015-2026, each taken at the first bar
it was visible as forming:

| | Median | p25 | p75 |
|---|---|---|---|
| Distance up to the pivot | 6.05% | 3.96% | 8.42% |
| Distance down to the stop | 2.61% | 1.15% | 5.14% |
| Reward:risk to the pivot | 2.22 | 1.00 | 5.60 |

Of the bases that did break out, the median took **5 sessions** (p90: 19),
and the forming-bar entry was **7.0% cheaper** than the breakout close.

But the base rate is the thing to lead with:

| Outcome within 40 bars | Share |
|---|---|
| Reached the pivot first | **34%** |
| Hit the stop first | **64%** |
| Neither | 2% |

**Two thirds of complete, textbook-qualifying VCPs never break out.** The
breakout is not a formality you can front-run for free; it is the event that
separates the third that work from the two thirds that do not.

---

## 3. Four entries, same bases

The comparison that matters. Same 3,188 bases, same stop, hold 40 bars or
stop out; the waiting policies give up after 25 bars.

**Stop at the final contraction's low:**

| Policy | Traded | Median wait | Median risk | Stopped | Mean R | Mean return | R per base |
|---|---|---|---|---|---|---|---|
| **Immediate** (first sighting) | 3,188 (100%) | 0 | 2.55% | 76% | **0.91** | 1.99% | **0.91** |
| Wait for within 3% of pivot | 1,887 (59%) | 3 | 5.90% | 59% | 0.58 | 3.28% | 0.34 |
| Wait for within 1% of pivot | 817 (26%) | 6 | 6.77% | 51% | 0.50 | 3.84% | 0.13 |
| **Breakout** (textbook) | 1,777 (56%) | 8 | 9.83% | 42% | 0.40 | **4.26%** | 0.22 |

With the stop 1 ATR lower, stop-outs fall a long way (immediate 76% → 57%)
but risk more than doubles, and expectancy falls with it (0.91R → 0.51R).
For early entry the tight stop is the better one, which is not obvious.

### Three things this says

**1. Early entry wins on risk, the breakout wins on capital.** Immediate
entry earns more than twice the R per trade, because its stop is four times
closer. Per rupee deployed it earns *less* — 1.99% against 4.26% — because
that same tight stop means a small position move. Which is better depends
entirely on whether you size to constant risk or to constant capital. Sized
to risk, early entry dominates; sized to capital, the breakout is the bigger
single trade but you only get 56% as many of them.

**2. Waiting for price to coil closer to the pivot is the worst of both.**
It has lower expectancy than entering immediately AND lower return than
waiting for the actual breakout. While you wait, the stop stays put and
price rises towards the pivot, so your risk grows from 2.55% to 5.90% —
and the stop-out rate only falls from 76% to 59%. You pay for accuracy you
do not get.

**This corrects an earlier reading of the same data.** Bases *first spotted*
within 2% of their pivot break out 70% of the time against 34% overall,
which looks like a recipe for waiting. It is not: those are a different,
tighter, later-stage population of bases, not the same bases observed later.
Conditioning on where a base happened to be when you met it is selection;
waiting is a decision. Only the second is tradeable, and it does not work.

**3. It is a tail strategy, and an extreme one.** The median immediate trade
loses **−1.00R**. 76% lose. The top 5% of trades contribute **131%** of total
R — remove them and the strategy is net negative. The breakout entry is far
better behaved: median −0.21R, top 5% contribute 78%, and 27% of its trades
exceed +1R against 18% for immediate entry.

---

## 4. Does it survive contact with reality?

**Costs.** Round-trip cost eats the early entry faster than the breakout,
because the edge per trade is smaller in percent:

| Round-trip cost | Immediate, mean return/trade | In R |
|---|---|---|
| 0.0% | 1.99% | 0.91R |
| 0.1% | 1.89% | 0.83R |
| 0.2% | 1.79% | 0.76R |
| 0.5% | 1.49% | 0.53R |

At Indian retail costs it survives but loses roughly a third to a half of
its edge. A strategy taking 3,188 trades is not cost-insensitive.

**Gaps through the stop.** A −1R loss assumes you get filled at the stop. Of
2,431 stopped-out trades, **6% gapped through it**, filling at the open
instead; the mean realised loss is −1.05R and the worst 1% is −2.38R.
Corrected for that, expectancy is **0.87R rather than 0.91R**. Gap risk is
real but not decisive here.

**Year by year** (mean R, immediate, tight stop): 2016 0.15, 2017 1.17,
2018 0.13, 2019 0.23, 2020 0.90, 2021 0.81, 2022 0.15, 2023 3.26, 2024 0.11,
2025 0.73, 2026 0.38. Positive in all eleven years, which is a genuine
robustness signal — but 2023 alone carries a large share of the total, and
strip it out and the average is closer to 0.5R. The breakout entry by
contrast was negative in four of the eleven.

---

## 5. What does NOT improve the odds

Conditioning the breakout rate on the things the textbook emphasises barely
moves it:

| Conditioner | Breakout rate across buckets |
|---|---|
| Range squeeze inside the base | 33% → 38% (essentially flat) |
| Contraction count (2T / 3T / 4T+) | 34% / 34% / 30% |
| Trend Template criteria passed (6 vs 7) | 31% → 35% |
| **Final-contraction volume vs base average** | driest 29% → wettest **45%** |

That last row runs **against** the textbook: the bases with the most extreme
volume dry-up broke out *less* often. Two caveats before reading anything
into it. The detector already gates on dry-up, so every bucket has passed a
threshold and the comparison is between degrees of dryness, not dry against
wet. And the wettest bucket qualifies via the alternative dry-up measure
(final contraction against the *first* contraction rather than the base
average), so it is partly a different population. It is a lead worth
chasing, not a finding.

---

## 6. The honest summary

You can see a VCP roughly 5 sessions and 7% before its breakout, and on this
data entering there had more than twice the risk-adjusted expectancy of
waiting for confirmation. That is a real result and it survives costs and
gap risk.

It is also a strategy where three quarters of your trades lose, the median
trade loses, and essentially all of the profit arrives in one trade in
twenty. It only works if you take every signal mechanically and size to
constant risk. Skipping the uncomfortable ones — which is what a human does
after six losses in a row — is enough to turn it negative, because you
cannot know which trade is the one in twenty.

**Limitations that apply to everything above**: NSE 2015-2026 was a bull
market; the universe is today's index members, so there is survivorship
bias; positions overlap and no capital constraint is modelled; the exit is a
flat 40 bars with no target or trailing stop, which is not what anyone
actually trades; and the detector's parameters were chosen on the synthetic
benchmark, so while this data is out-of-sample for the *parameters*, it is
the same data the earlier breakout study used.
