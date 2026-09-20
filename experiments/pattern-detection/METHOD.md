# How the detectors work

Three detectors, one substrate. This file is the design; `RESEARCH.md` is
where the criteria come from; `README.md` has the measured results.

---

## 1. The substrate: swing pivots

Every pattern here is described in terms of highs and lows — rims, bottoms,
contractions, touches. So the first decision, and the one that determines
most of the outcome, is **what counts as a swing**.

### The choice

An **ATR-scaled zigzag** (`patlib/pivots.py`). A reversal is confirmed when
price retraces

```
threshold(i) = clip( k * ATR14(i) / close(i),  min_pct,  max_pct )
```

from the running extreme, with the threshold evaluated at the **extreme's**
bar, not the current one — so a volatility spike mid-leg cannot retroactively
change what already counted as a swing.

Why not fixed-width fractals: they have a fixed *time* width, so they mark
noise on a quiet stock and miss the turn on a violent one. Why not a fixed
percentage: 5% is a shrug for one name and a crash for another.

### Two things about it that matter more than they look

**`max_pct` is load-bearing.** Scaling by ATR is right until volatility
spikes, at which point the requirement can reach a quarter of the price and
the zigzag stops marking swings entirely — over exactly the crash-and-recover
stretch where the interesting bases get built. With the original 25% cap the
detector was blind across March 2020 on every symbol. Capping at 15% fixed
it.

**The last pivot is provisional.** A zigzag only confirms a pivot once price
has retraced from it, so the most recent extreme is never confirmed. For a
base that is still forming — the only kind worth trading — that unconfirmed
extreme *is* the pattern's right edge. It is returned, flagged
`provisional`, rather than discarded.

### Causality

Every detector call takes bars `0..t` and returns what was knowable at `t`.
Pivots carry `confirm_idx` (when the reversal was large enough to confirm)
separately from `idx` (where the extreme was), so nothing treats "the high
was at bar 40" as "we knew at bar 40". The evaluation harness walks `t`
forward rather than calling once on a finished chart.

---

## 2. Turning prose into arithmetic

The interesting engineering is in `patlib/shapes.py`, which holds one
definition per English phrase.

| Phrase from the books | Measurement | Ideal values |
|---|---|---|
| "U-shaped, not V-shaped" | `time_in_bottom_third` — share of bars with their low in the lowest third of the range | parabola 0.58, V 0.33 |
| same, second opinion | `u_vs_v_fit` — fit a quadratic and a best symmetric V, return `sse_V/(sse_U+sse_V)` | parabola 1.00, V 0.00 |
| "a rounded turn, not a spike" | `max_single_leg_fraction` — largest uninterrupted one-way move / total range | low for a U |
| "volume trends downward" | `volume_trend` — least-squares slope / mean volume | negative |
| "volume dries up" | `volume_dryup` — window mean / reference mean | < 1 |
| "price fills the triangle, not white space" | `channel_fill` — cut the pattern into 5 vertical slices, average each slice's price range over the channel width there | 0.5–0.9 traversing, ~0.2 hugging one edge |

`channel_fill` is worth a note. The obvious reading of Bulkowski's guideline
— average bar height over channel width — measures how tall individual
candles are, which is a property of the instrument, not of the pattern. The
guideline is about **coverage**, so it has to be measured over slices.

---

## 3. Cup and Handle (`patlib/cup_handle.py`)

Enumerate every pair of swing highs `(L, R)`; the cup's bottom is the lowest
low between them; the handle is everything after `R`.

**Hard gates** (fail one and it is a different shape, not a worse cup):
duration 30–325 daily bars; depth 12–50%; rims within 6%; bottom interior
(15–85% of the way across); rounded (`bottom_third ≥ 0.28` **and**
`u_vs_v ≥ 0.50`); handle present and at least 5 bars; handle strictly in the
upper half of the cup; handle not more than 24% deep; handle not undercutting
the cup; some prior advance; price still within 12% of the pivot.

**Scored, not gated** (a cup with rising volume is a worse cup, not a
non-cup): depth inside the 12–33% band, rim levelness, roundness quality,
prior advance ≥ 30%, cup volume receding, handle shallow / in the ideal
retracement / drifting down / dry, breakout volume ≥ 1.4×.

### Three bugs worth recording

1. **The pivot must be found by walking, not by taking a maximum.** Defining
   the pivot as the highest high over the handle region silently includes
   the breakout bar, lifting the pivot to that bar's own high so no close can
   ever exceed it. Every cup then read `forming` forever and the detector
   produced no signals at all. The fix walks the handle bar by bar: the pivot
   is the highest high made *while price was still below it*, and the
   breakout is the first close above that.
2. **The volume dry-up window must stop at the base.** Running it to `t`
   swallows the breakout bar, whose volume is 2–3× by definition — so the
   very expansion that confirms the pattern reads as the handle failing to
   dry up.
3. **One zigzag resolution is not enough.** Coarse enough to draw a 30% cup's
   rims is too coarse to see the 6% handle that confirms the right rim; fine
   enough for the handle shatters the cup. The chart is read at three
   resolutions and the readings merged by score.

**Variants**: `with_handle`, `no_handle`, tagged `:saucer` / `:deep` /
`:v_ish`. `detect_inverted_cup_and_handle` reflects the series in log price
(`p → C²/p`), runs the same detector, and maps the results back.

---

## 4. VCP (`patlib/vcp.py`)

Pair consecutive pivots into contractions `(high → low, depth)`, then take
the last *n* of them and test the sequence.

**Hard gates**: 2–6 contractions; each ≤ 85% of the one before; first
8–40% deep; last ≤ 12%; the base does not undercut its own start; price
within 10% of the base's high; a prior advance ≥ 25%; volume in the final
contraction below the base average; **range contraction measured inside the
base**; at least 5 of the 7 computable Trend Template criteria.

The variant string is Minervini's own footprint — `19W 19/7 4T`.

### Two bugs worth recording

1. **A one-bar dip is not a contraction.** A fine zigzag emits many. Because
   a VCP is read as a *sequence*, one spurious dip between two genuine
   contractions breaks the monotone chain and the whole pattern is lost.
   Contractions now need at least 3 bars.
2. **ATR(14) cannot measure a 12-bar contraction.** The in-base squeeze was
   first written as ATR over the final contraction against ATR over the
   first. Wilder smoothing has barely finished adapting to a 12-bar leg by
   the time that leg is over, so the ratio was a lagged blend of both legs
   rather than a comparison of them. Switching to the raw mean true range
   took 2T recall from 24/60 to 60/60 and overall VCP recall from 52% to
   89%. This was the single largest fix in the project.

   The version before *that* was worse still: ATR(10) against ATR(50), which
   for a 30-bar base reaches 20 bars back past the base's own start, so the
   "contraction" was partly measured against price action outside the
   pattern. It is retained only as a scored extra.

---

## 5. Triangles (`patlib/triangles.py`)

One engine. Fit a line to the swing highs and another to the swing lows,
then classify on the pair of slopes — expressed as **total drift across the
pattern as a fraction of price**, so "flat" means the same thing on a 12-bar
pennant and a 90-bar triangle.

**Hard gates**: 5–9 alternating pivots with ≥5 legs; ≥3 touches on one line
and ≥2 on the other, within 0.6 ATR; **≥90% of the selected pivots must lie
on a line**; converging to ≤58% of the starting width; `channel_fill ≥ 0.55`;
starting width ≥ 3 ATR; **the swing legs themselves must shrink** (last ≤ 55%
of first, and a negative slope through all of them); volume receding; some
prior move to consolidate.

### The gate that does the work

Two fitted lines converge by chance constantly. What does not happen by
chance is the **actual swings getting smaller, one after another** — measured
on the pivots, not on the lines drawn through them. That single test,
together with requiring the selected pivots to actually sit on the lines,
did more for triangle precision than every threshold combined.

The pivots-must-touch rule fixed a specific failure: without it the search
happily reached back past the start of the pattern, picked up pivots from
the move that preceded it, and reported a triangle beginning twenty bars
early — scoring *well*, because the extra pivots were never asked to fit.

### R² is not used as a gate

An ascending triangle's upper line is horizontal by definition, so the
variance it is asked to explain is zero and R² collapses towards zero
however perfectly the line fits. Fit is measured in ATRs, which is a scale
that means something. This cost most of the triangle recall before it was
found.

---

## 6. Two profiles

`patlib/profiles.py` provides **strict** (the textbook, as written) and
**relaxed** (how the patterns are labelled in practice). The difference
turned out to be the most interesting result in the project — see
`README.md`.
