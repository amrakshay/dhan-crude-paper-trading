# B1 Breakdown short: **FAILS** on development

**Rule** (`METHOD.md`):
- **When:** regime-B-on days, on the day's 5-name shortlist.
- **Trigger:** the first close below the opening-range low with the name
  weaker than NIFTY since the open, between bars 3 and 56.
- **Entry:** the next bar's open.
- **Stop:** the opening-range high, then trailing at close + 3 × ATR5.
- **Exit:** at the stop, or by time at the bar-71 close.

Source: `out/dev_B1_breakdown.json`.

## Development, 2017-04 → 2022-12

| | Value |
|---|---|
| Trades | 979 on 314 days (3.1/day) |
| Gross (after 5 bps/side) | +4.9 bps |
| Charges @ ₹5 L | 4.5 bps |
| **Net @ 5 bps** | **+0.4 bps, CI [−15.9, +17.1]** |
| Net @ 2 / 10 bps | +6.4 / −9.6 |
| After dropping the best 10 days | **−15.5** |
| Without 2020-02 → 05 | +3.4 |
| Win rate (net) | 41.6%; median trade −35.7 bps |
| Exit mix | stop 79%, time 21%, stop-gap 0.4% |
| Median stop distance | 2.0% of price. One round trip including slippage costs **0.07 R**. Not the order-block trap: the stops are wide enough. |
| Locked exits | 0 |
| By year, net bps | 2017 −89 (few trades) · 2018 +6 · 2019 +12 · 2020 −18 · 2021 −11 · 2022 +3 |

### Episodes (regime B, development)

| Episode | Trades | Net bps |
|---|---|---|
| 2018-02 | 113 | +5.1 |
| 2018-05 | 103 | +16.6 |
| 2018-09 | 91 | −4.9 |
| 2019-01 | 71 | −47.4 |
| 2019-07 | 106 | +48.2 |
| 2020-02 | 152 | −15.7 |
| 2021-11 | 132 | +2.0 |
| 2022-05 | 130 | +9.5 |

**5 of 8 positive**, but driven by two episodes. The COVID crash (2020-02) was
a **loss**: in that crash the shortlist's breakdowns were met by violent
intraday reversals.

### Controls (5 reps each; strategy minus control, bps, day bootstrap)

| Control | Control net | Difference | 95% CI | Beaten? |
|---|---|---|---|---|
| index_short | −8.5 | +8.9 | [−6.7, +24.7] | no |
| random_name | −10.7 | +11.2 | [−0.3, +22.5] | no (just) |
| random_time | −1.0 | +1.4 | [−11.0, +13.2] | no |
| mirror_long | −14.5 | +15.0 | [−16.1, +45.2] | no |
| regime_off | −3.0 | +3.4 | [−14.7, +20.7] | no |
| *random_shortlist (diagnostic)* | *−14.1* | *+14.6* | *[+2.0, +27.9]* | *yes* |

### Portfolio (₹5 L, 0.5% risk, pre-registered limits)

| | 0.5% risk | 1% risk |
|---|---|---|
| Trades taken | 891 | 600 (the daily limit binds more often) |
| Total, % of capital | −0.4% | −49% |
| Max drawdown | −17.6% | −55% |
| Traded months positive | **40.5%** (15/37) | 24% |
| Worst month | −3.6% | −5.2% |
| Longest losing streak | **7** | 8 |
| Monthly mean, month bootstrap | −0.005%, CI [−0.44, +0.48]% | −0.72% |

## Pass bar

| Condition | Result |
|---|---|
| 1. Net > 0, CI clear | **no** |
| 2. Beats every control | **no** (beats none of the five) |
| 3. ≥ 5/8 episodes | yes (5/8) |
| 4. > 0 after the best 10 days | **no** |
| 5. Monthly bar | **no** (40%, streak 7) |

**Verdict: FAILS.** It's indistinguishable from entering the same name at a
random later time. Its positive mean comes from a few days.

**Robustness:**
- regime A: −7.3 bps, CI [−37, +21], 1/3 episodes;
- regime C: −11.4 bps, CI [−34, +12], 1/4 episodes.

**The holdout was not run.**
