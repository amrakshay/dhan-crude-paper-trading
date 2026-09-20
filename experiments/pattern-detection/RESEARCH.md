# Cup-and-Handle, VCP and Triangles: how they work, and how to measure them

Background research for the detectors in `patlib/`. Everything here that is a
number is sourced; everything that is a judgement is marked as one.

---

## 0. The one idea the three patterns share

All three are **supply-absorption stories told in price**. The claim is not
geometric, it is about who is left holding the stock:

- A **cup** is the round trip of a crowd that bought the old high, suffered,
  and sold into the recovery. By the time price returns to the rim, that
  supply is gone.
- A **VCP** is the same process compressed and repeated: each pullback finds
  fewer sellers than the last, so each is shallower, until nobody is left to
  sell and the next buyer moves the price.
- A **triangle** is two populations with fixed opinions — a supply wall at a
  price, a demand floor that keeps rising — squeezing the range until one
  side gives.

This matters for detection because it tells you which measurements are
load-bearing. **Volume and range contraction are not decoration on these
patterns; they are the evidence for the story.** A shape with the right
outline and rising volume is not a weaker instance of the pattern, it is a
different thing that looks similar.

---

## 1. Cup with Handle

### 1.1 Origin

William O'Neil's, from *How to Make Money in Stocks* — the "cup-with-handle"
is the flagship base of the CANSLIM method and of Investor's Business Daily's
chart school. Thomas Bulkowski later measured it statistically in
*Encyclopedia of Chart Patterns*.

### 1.2 The shape

```
                    prior advance (>= ~30%)
                   /
    left rim  ----+-----------------------+----  right rim
                   \                     /   \
                    \                   /     \__/  <- handle
                     \_______________ _/            (upper half, shallow)
                          rounded bottom
```

### 1.3 Criteria, with sources

| Criterion | Value | Source |
|---|---|---|
| Prior advance into the base | ~30%+ | O'Neil (proper-base precondition) |
| Cup depth | 12–33% normal; to 50% in volatile/bear markets, and weaker | O'Neil / IBD |
| Cup duration | 7 to 65 weeks; 3–6 months typical | O'Neil; [Bulkowski](https://thepatternsite.com/cup.html) |
| Cup shape | U-shaped, **not** V-shaped ("allow variations") | Bulkowski |
| Rim levels | "near the same price level but be flexible" | Bulkowski |
| Handle duration | 1 week minimum, no maximum; 1–4 weeks typical | Bulkowski; O'Neil |
| Handle position | **upper half of the cup** | O'Neil (hard rule) |
| Handle depth | flaw beyond "the low teens" in percent; ≤10–15% off the right rim | IBD |
| Handle slope | should drift **down**; an upward-sloping handle is a defect | IBD |
| Handle volume | dries up notably along the lows | O'Neil |
| Breakout volume | 40–50% above average | O'Neil |
| Buy point ("pivot") | just above the handle's high | O'Neil |
| Stop | 7–8% below entry, or below the handle low | O'Neil |

### 1.4 Reported performance

Bulkowski, bull market, upward breakouts, 913 "perfect" trades: overall rank
**3 of 39**, break-even failure rate **5%**, average rise **54%**, throwback
rate 62%, 61% reach their price target. Cups with handles also *bust* least
often of any pattern with an upward breakout (10%).

These numbers describe hand-picked perfect examples, not a screener's output.
Treat them as the ceiling, not the expectation.

### 1.5 Variations

| Variation | What changes | Detector treatment |
|---|---|---|
| **Cup without handle** | no final consolidation; breakout straight off the right rim | `allow_no_handle`, variant `no_handle` |
| **Saucer / rounding bottom** | shallower (12–20%) and much longer (7 weeks to over a year) | tagged `:saucer` |
| **Deep cup** | 33–50%; weaker, more likely to fail | tagged `:deep` |
| **V-ish cup** | fails the roundness test but passes everything else | tagged `:v_ish` (rejected under the strict profile) |
| **Inverted cup-and-handle** | the bearish mirror: rounded top, weak rebound, breakdown | `detect_inverted_cup_and_handle`, via a log-space mirror |
| **Double-bottom base** | a W rather than a U; a distinct O'Neil base | **not** implemented; different geometry |
| **High tight flag** | +100–120% in 4–8 weeks, then a 10–25% pause over 3–5 weeks | not implemented; not a cup |

### 1.6 The hard part: "U-shaped, not V-shaped"

This is the only criterion in the whole list that is stated purely visually,
and it is the one that does most of the work — every failed bounce in a
downtrend is a V, and there are far more of them than there are cups. Three
measurements are used, all scale-free:

1. **Time in the bottom third.** The fraction of the cup's bars whose low
   sits in the lowest third of its range. For an ideal parabola this is
   0.577; for an ideal V it is 0.333. Measured on synthetic shapes:
   **0.57 for a parabola, 0.33 for a V** — matching the analytic values.
2. **U-versus-V least squares.** Fit both a quadratic and a best symmetric
   V to the normalised lows; report `sse_V / (sse_U + sse_V)`. Above 0.5 the
   U wins. Measured: **1.00 parabola, 0.00 V**.
3. **Largest single one-way leg** as a fraction of the cup's range. A V is
   one long leg down and one long leg up.

---

## 2. Volatility Contraction Pattern (VCP)

### 2.1 Origin

Mark Minervini, *Trade Like a Stock Market Wizard*. Not a shape so much as a
**sequence**: a base in which each successive pullback is shallower than the
last, with volume and daily range contracting alongside.

### 2.2 The footprint notation

Minervini writes a VCP as e.g. **`19W 19/7 4T`**:

- `19W` — the base took 19 weeks
- `19/7` — the largest contraction was 19%, the smallest 7%
- `4T` — four contractions ("T" for the tightening count)

The notation *is* the specification, which makes it unusually easy to
codify. The detector emits exactly this string as the detection's variant.

### 2.3 Criteria

| Criterion | Value |
|---|---|
| Number of contractions | 2–6; typically 2–4 |
| First contraction | ~20–25% typical; to 30–35% in volatile names |
| Each subsequent | smaller than the last — roughly halving (20 → 10 → 5) |
| Final contraction | tight; single digits |
| Volume | contracts through the base, with a clear dry-up near the end |
| Range | ATR compresses — Minervini cites ~1/3 of the 50-day average |
| Pivot | the high of the final contraction |
| Breakout volume | 40–50%+ above average |
| Precondition | a Stage-2 uptrend (the Trend Template below) |

### 2.4 The Trend Template (the filter that runs before the pattern)

All eight must hold; a stock failing one is out regardless of the rest.

1. Price above both the 150-day and 200-day moving averages
2. 150-day above the 200-day
3. 200-day trending up for at least 1 month (4–5 months preferred)
4. 50-day above both the 150-day and the 200-day
5. Price above the 50-day
6. Price ≥ 25% above its 52-week low (30% in *Trade Like a Stock Market Wizard*)
7. Price within 25% of its 52-week high — the closer the better
8. IBD Relative Strength rating ≥ 70

**Only seven of these are computable from one symbol's candles.** Criterion 8
is a cross-sectional rank against the whole market. The detector computes the
seven and reports the count; it never pretends to know the eighth.

### 2.5 Variations

`2T` through `6T` are not really different patterns, they are the same
pattern with a different count — so they are reported as the footprint rather
than as separate variants. Related setups that are *not* implemented:
**Power Play / high tight flag** (6-month gain > 85%, then a shallow 15-day
pause) and **the "cheat" / low-cheat entry** (an earlier entry inside the
base rather than at the pivot).

---

## 3. Triangles (and wedges, pennants, broadening formations)

### 3.1 Why one detector, not five

They are all **two trendlines fitted to alternating pivots**. Only the pair
of slopes differs. Writing five detectors would mean five copies of the same
pivot-fitting code disagreeing with each other at the boundaries.

| Pattern | Upper line | Lower line | Bias |
|---|---|---|---|
| Ascending triangle | horizontal | rising | bullish |
| Descending triangle | falling | horizontal | bearish |
| Symmetrical triangle | falling | rising | neutral |
| Pennant | falling | rising, short, after a near-vertical move | continuation |
| Rising wedge | rising, less steeply than the lower | rising | bearish |
| Falling wedge | falling, more steeply than the lower | falling | bullish |
| Broadening formation | rising | falling (diverging) | unreliable |

### 3.2 Bulkowski's identification guidelines

| | Ascending | Descending | Symmetrical |
|---|---|---|---|
| Shape | top horizontal, bottom rising | bottom horizontal, top falling | both converging |
| Touches | ≥3 on one line, ≥2 on the other | same | same |
| Crossing | price must fill the triangle, "not white space" | same | same |
| Volume recedes | 78% of the time | 78% | 84–86% |
| Breakout direction | up 63% | up 53% | up 60% |
| Breakout position | 64% of the way to the apex | 61–65% | 74% |
| Confirmation | a close outside a trendline | same | same |

Sources: [ascending](https://thepatternsite.com/at.html),
[descending](https://thepatternsite.com/dt.html),
[symmetrical](https://thepatternsite.com/st.html).

### 3.3 Reported performance

| Pattern | Rank (up/down) | Break-even failure | Avg rise / decline | Target hit |
|---|---|---|---|---|
| Ascending | 16/39, 30/36 | 17% / 38% | +43% / −13% | 70% / 44% |
| Descending | 33/39, 15/36 | 22% / 23% | +38% / −15% | 64% / 50% |
| Symmetrical | 36/39, 34/36 | 25% / 37% | +34% / −12% | 58% / 36% |
| Falling wedge | 31/39 | 26% | +38% | — |
| Rising wedge | 36/36 (last) | 51% | −9% | — |

**Read that table before trusting a triangle.** The symmetrical triangle
ranks 36th of 39 on upward breakouts and the rising wedge is dead last among
bearish patterns with a 51% break-even failure rate. This is not a detector
problem; it is the pattern.

### 3.4 The apex rule

Bulkowski scanned 388 stocks and found 221 triangles; in **165 of them (75%)**
the apex coincided with or came close to a later minor high or low, and in
144 of 239 (60%) price changed direction there. Practically: a breakout more
than ~85% of the way to the apex has run out of room, and the detector scores
it down.

---

## 4. What the literature does not tell you

Three gaps that only appear once you try to write the code.

**There is no canonical first bar.** Every source draws a cup on a chart and
says "here". None defines which bar the left rim is, which means neither
recall nor precision is defined on real data without someone hand-labelling
it first. This is why the accuracy numbers in `README.md` come from
synthetic data, where the answer is known by construction.

**The criteria are stated at different scales.** Cup duration is in weeks,
handle depth in percent of price, handle position in percent of the *cup*,
volume as a trend. Mixing them requires choosing a reference for each, and
different reasonable choices change the answer. The worst offender: an
O'Neil handle is "10–15% deep" AND "in the upper half of the cup". On a
14%-deep cup those two rules contradict each other — the upper half is only
7% — and the second must win.

**The published examples do not meet the published rules.** Checked here:
AAPL 2018-19, NVDA 2019-20 and POOL 2020 are all cited as cup-and-handles in
secondary write-ups. All three are 30–43% crash-and-V-recoveries, outside
O'Neil's depth band and failing the roundness test outright. See
`README.md` §"Public examples".

---

## 5. Detection approach: why pivots, and which kind

A survey of what is used in practice:

| Method | How it works | Why it was or was not used |
|---|---|---|
| **Fixed-width fractals** | a high with N lower highs either side | Fixed *time* width. Marks noise on a quiet stock and misses the turn on a violent one. Implemented as `fractal_pivots` for comparison; used by nothing. |
| **Percentage zigzag** | confirm a reversal after an X% retracement | Defined by price travelled, which is how the patterns are described. But one X cannot serve a ₹40 stock and a ₹4,000 one. |
| **ATR-scaled zigzag** | X = k × ATR / price, floored and capped | **Chosen.** Adapts to the instrument. The cap turned out to matter more than the scaling — see `README.md`. |
| **Perceptually Important Points** | recursively keep the point of greatest deviation | Elegant, and good for template matching against a fixed-length window. Awkward here because these patterns have no fixed length. |
| **Template / DTW matching** | correlate against a stored archetype | Needs an archetype per variation, and cannot answer "is the volume receding". |
| **CNN on chart images** | train on rendered charts | Needs a labelled corpus, which is the thing that does not exist. Also unauditable: it cannot tell you the handle was 4% too deep. |

The decisive argument for explicit rules over a learned model here is not
accuracy, it is that **the rules are the specification**. When a detection is
wrong you can ask which criterion was too loose, and change that one.
