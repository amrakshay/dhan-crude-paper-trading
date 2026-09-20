# Which breakout? Three setups, measured against each other

`RESEARCH.md` §2 finds that of the three candidate IPO breakouts, only one is a
published setup. This is the measurement of all three, so that finding is
demonstrated rather than asserted.

| | Definition | Literature |
|---|---|---|
| **A `day1`** | first close above the listing day's high | none found |
| **B `week1`** | first close above the high of the first five sessions | none found |
| **C `base`** | first close above the left-side high of a 2–5 week base | **the IBD IPO base** |

All three are run through the identical machinery: same universe, same
`max_age_sessions` deadline, same entry at the next open, same baselines, same
block bootstrap. The only difference is where the pivot comes from.

---

## The mechanical objection, before any return is measured

`scripts/listing_bar_stats.py` measures the listing bar across every matched
listing:

- the **median** listing bar's high sits **exactly +5.00%** above its open, and
  the maximum is **exactly +20.0%** — those are the two statutory bands, visible
  in the distribution's own quantiles;
- **11%** of listing bars close exactly at the day's high and **4%** exactly at
  the low — frozen at a circuit;
- **~21%** have an extreme sitting on a ±5% or ±20% band.
- listing-bar volume is a median **21.6×** the median of the following twenty
  sessions, reaching **152×**.

For a fifth of listings, then, "the listing-day high" is a number the exchange
chose, not a price at which supply met demand. Setup A's pivot is that number.
Setup B inherits the problem for as long as the band binds.

This is why `ipo_base.IPOBaseParams.allow_listing_bar_pivot` defaults to
**False** for setup C: the left-side high is searched from bar 1, so the base
setup's pivot is always a traded high.

---

## Results

**The published setup is not better than the folk ones.** Excess over the
market, block-bootstrapped by calendar month:

| setup | signals | median entry | excess @20 | 95% CI | excess @60 |
|---|---:|---:|---:|---|---:|
| **C `base`** | 184 | session 22 | +3.93% | [+1.28%, +6.67%] | +5.08% |
| **A `day1`** | 188 | session 35 | **+4.64%** | [+2.32%, +6.95%] | **+9.20%** |
| **B `week1`** | 210 | session 42 | +4.48% | [+2.15%, +6.83%] | +7.72% |

All three intervals clear zero. All three overlap each other heavily. `day1` —
the setup with no literature behind it and a pivot that is a circuit limit a
fifth of the time — is nominally the **best** of the three at both horizons.

## So are they one setup?

Partly, and the funnel says which parts:

```
base vs day1     106 same listing,  71 same trade   (39% of the smaller set)
base vs week1    111 same listing,  86 same trade   (47%)
day1 vs week1    151 same listing, 141 same trade   (75%)
```

- **A and B are the same setup.** 75% of the smaller set is the same listing
  entered within a week. That is one rule with two descriptions, and their
  near-identical returns follow from that rather than corroborating each other.
- **C is genuinely a different sample** — under half its trades coincide with
  either — and it performs no better for it.

And a third fact explains why C is not as different as it looks: **61% of `base`
signals have their left-side high on session 0–4**, median session 2. For most
signals the "left-side high of a 2–5 week base" *is* the first week's high. The
base machinery mostly relabels the same level.

## The conclusion

The honest reading is the one this document anticipated. The effect being
measured is **"a new listing that clears an early high keeps going for a while"**,
and O'Neil's base criteria — the depth limit, the length window, the volume
dry-up, the tightening — neither add to it nor subtract from it. They select a
somewhat different third of the listings and earn about the same.

That is a **flat negative for the published rule set**, and it is worth as much
as a positive would have been: `RESEARCH.md` §1.4 notes there is no published
performance benchmark for IPO bases anywhere. There is now one data point, and
it says the structure is not what is paying.

**Why `base` is still the decided setup** (`FINAL_LOGIC.md` §1) despite this:
its pivot is always a traded high rather than a circuit limit (§ above), it
enters 13–20 sessions earlier, and its hard gates refuse the deep-collapse and
volume-inverted cases that the synthetic benchmark shows `day1` has no way to
see. Choosing it costs about a point of measured excess and buys a rule whose
failure modes are understood. That is a judgement, it is not forced by the
numbers, and anyone preferring `day1` on this evidence has a fair case.
