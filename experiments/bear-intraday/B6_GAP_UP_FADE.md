# B6 Gap-up fade, short only — **CANNOT BE VALIDATED** (and fails dev anyway)

**Contaminated.** The gap-fade family (screener H3b/H3c) has already used the
2023 onward holdout. B6 is reported for completeness and can't pass.

**Rule:** on regime-B-on days, from the liquid 100 (not the shortlist): a gap
up of at least 1% whose 09:30 close is back below the day's open. Short at the
bar-3 open, stop at the opening-range high, time exit. This is H3b's short
side, restricted to the regime.

Source: `out/dev_B6_gap_up_fade.json`.

| | Value |
|---|---|
| Trades | 3,179 on 286 days (11.1/day) |
| Gross | +9.8 bps |
| **Net @ 5 bps** | **+5.3 bps, CI [−19.0, +31.5]** |
| Net @ 2 / 10 bps | +11.3 / −4.7 |
| After dropping the best 10 days | **−25.6** |
| Without 2020-02 → 05 | −0.3 |
| Episodes positive | 6 / 8 |
| Beats random_name | +10.1, CI [+3.0, +17.4] |
| **Same rule, regime OFF** | **+14.0 bps**, which is *better* than regime-on (difference −8.7, CI [−34, +19]) |
| Portfolio, 0.5% risk | +11% total, 43% of months positive, worst month −4.7%, streak 5 |

**What it adds:** the gap-up fade is no better in a bear regime than outside
one. The regime doesn't sharpen it. Like H3b, its mean is a handful of
market-wide reversal days.

**Verdict:** fails dev (bars 1, 2, 4, 5), and it is unvalidatable in any case.
