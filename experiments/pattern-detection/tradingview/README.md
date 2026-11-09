# TradingView indicator — Triangles & Wedges

`triangles.pine`, Pine Script **v6**. Ported from
`experiments/pattern-detection/`, where the rules were measured against
synthetic ground truth with negative controls and then ten years of NSE data.

Pine Editor → new indicator → paste → Save → Add to chart.

## Detects

Ascending · Descending · Symmetrical triangle · Pennant · Rising wedge ·
Falling wedge.

## Draws

- Both fitted trendlines, extended to the apex.
- Shaded pattern body, coloured by bias.
- Name on the chart, with **% progress to the apex** (Bulkowski: breakouts
  happen 61–75% of the way; past ~85% the pattern has run out of room).
- Dashed breakout and invalidation lines that track the converging lines.
- On a break: the **measured move** anchored at the breakout bar, with target
  and expected %.
- Info panel: pattern, status, bias, quality + touch count, current upper and
  lower levels, apex progress, target, reward:risk, and the published odds
  for that specific shape.
- Three alerts: forming, upside breakout, downside breakdown.

## Two things it does deliberately

**Direction comes from price, not from the shape.** A triangle can resolve
either way. The `bias` field only says which way the odds lean; the target is
computed from whichever line price actually closed through.

**Patterns expire.** A detection is retired when price hits the target, falls
back through the far line, passes the apex, or `expireBars` (default 40) pass
unresolved. The first version of this indicator cached detections forever and
was still showing an eight-month-old wedge quoting a buy level 60% below
spot.

## Settings worth understanding

| | Why |
|---|---|
| **Pivots → Ceiling 15%** | Load-bearing. An ATR-scaled swing threshold can reach a quarter of price in a volatility spike, and the zigzag then stops marking swings altogether. |
| **Last swing ≤ 55% of the first** | The gate noise fails. Two fitted lines converge by chance constantly; the *swings themselves* shrinking does not. This did more for precision than every threshold combined. |
| **Fraction of pivots on a line (0.8)** | Without it the search reaches back past the pattern, picks up earlier pivots, and reports a triangle starting twenty bars early — scoring well, because the extra pivots were never asked to fit. |
| **Volume recedes** | Bulkowski measures this in 78–86% of real triangles. At those rates it is part of the definition. |
| **Max drift 1.5× the pattern's own height** | A wedge is a *consolidation*. If its midline travels several times its own height it is a narrowing trend channel, not a wedge. Raymond fell 750→370 inside one "falling wedge" — about 3× its height. Raise this to see looser shapes; lower it to demand tighter consolidations. |

## Read this before trading it

| Pattern | Published |
|---|---|
| Ascending | up 63%, avg rise 43%, fails 17% |
| Descending | up 53%, avg decline 15%, fails 23% |
| Symmetrical | up 60%, avg rise 34%, **ranks 36 of 39** |
| Falling wedge | avg rise 38%, fails 26% |
| Rising wedge | **51% break-even failure — last of 36** |

Our own testing agreed: 96% recall, **56% precision**. Two converging lines
occur constantly in noise. Treat a triangle as weak evidence.

## Known behaviour

A falling wedge is *bullish*, but a given one can still break down — direction
is taken from price. When that happens the drawing turns red and the label
says so explicitly (`Falling Wedge ▼ broke down`), so the colour and the text
cannot disagree.

## Verification status

**Not compiled.** The earlier three-pattern version compiled clean, and a
focused probe compiled every construct used here (user-defined types with
named arguments, the zigzag, the loops, tables, lines, labels, boxes,
alerts) — but this file is a rewrite and has not itself been through the
compiler. If it errors, send me the message and I will fix it.
