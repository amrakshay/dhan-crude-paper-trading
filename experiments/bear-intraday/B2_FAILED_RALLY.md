# B2 Failed relief rally: **FAILS** on development

**Rule** (`METHOD.md`):
- **When:** regime-B-on days, on the 5-name shortlist.
- **Setup:** by 10:45, the price rallies into `min(previous close, VWAP)` from
  below. Then a bar at least two bars after the rally high closes below that
  high bar's low, and below VWAP.
- **Entry:** short at the next bar's open.
- **Stop:** the rally high. Time exit.

Source: `out/dev_B2_failed_rally.json`.

## Development, 2017-04 → 2022-12

| | Value |
|---|---|
| Trades | 874 on 307 days (2.9/day) |
| Gross (after 5 bps/side) | +12.5 bps |
| **Net @ 5 bps** | **+8.0 bps, CI [−12.3, +29.6]** |
| Net @ 2 / 10 bps | +14.0 / −2.0 |
| After dropping the best 10 days | **−13.9** |
| **Without 2020-02 → 05** | **+1.5** |
| Win rate (net) | 37.5%; median trade −66.6 bps (a few large winners carry it) |
| Exit mix | stop 52%, time 48% |
| Median stop distance | 1.14%. One round trip costs 0.13 R. |
| By year, net bps | 2017 −79 (few) · 2018 +0.5 · 2019 +2.4 · **2020 +46.8** · 2021 −1.2 · 2022 −1.4 |

### Episodes

| Episode | Trades | Net bps |
|---|---|---|
| 2018-02 | 104 | −8.2 |
| 2018-05 | 84 | +25.7 |
| 2018-09 | 90 | −13.0 |
| 2019-01 | 56 | −44.3 |
| 2019-07 | 109 | +18.4 |
| **2020-02** | 143 | **+41.1** |
| 2021-11 | 128 | +3.3 |
| 2022-05 | 110 | +2.0 |

5 of 8 positive.

**The opposite of B1 in 2020.** In a crash, a short that waits for the rally to
fail did well. A short that sells the breakdown sold the low. Outside 2020 the
result is about zero.

### Controls (strategy minus control, bps)

| Control | Control net | Difference | 95% CI | Beaten? |
|---|---|---|---|---|
| index_short | −8.5 | +16.5 | [−2.6, +36.3] | no |
| **random_name** | −12.3 | **+20.3** | **[+6.0, +34.7]** | **yes** |
| random_time | −2.1 | +10.1 | [−2.8, +23.3] | no |
| mirror_long | −7.2 | +15.2 | [−23.5, +53.9] | no |
| regime_off | −0.0 | +8.0 | [−13.9, +32.5] | no |
| *random_shortlist* | *−10.1* | *+18.0* | *[+2.1, +34.8]* | *yes* |

**The setup carries information about which names fall.** It beats a random
name at the same moment by about 20 bps. It doesn't beat costs, the
regime-off version of itself, or a later entry.

### Portfolio (₹5 L)

| | 0.5% risk | 1% risk |
|---|---|---|
| Total, % of capital | −18% | −22% |
| Max drawdown | −34% | −35% |
| Traded months positive | **32%** | 24% |
| Worst month | **−4.3%** | −5.5% |
| Longest losing streak | 7 | 10 |

The per-trade mean is positive but the portfolio loses. There are two
verified reasons:

- **The pre-registered loss limits skip the winners.** With limits it takes
  547 of 874 trades and makes −18%. With no limits it takes all 874 and makes
  +13.9% (+3.3 bps on notional). The trades the limits refuse, taken after a
  day or month had already lost its allowance, were net winners. The limits are
  pre-registered, so they stay. The no-limits figure is a diagnostic, not a
  result.
- **Risk-based sizing leans on the tightest stops, which are the worst
  trades.** Net by stop-distance tercile: −7.0 / +11.1 / +19.8 bps. 0.5% risk
  puts the biggest notional on the first tercile.

Neither the per-trade nor the portfolio figure is a pass.

## Pass bar

| Condition | Result |
|---|---|
| 1 | **no** |
| 2 | **no** (beats 1 of 5) |
| 3 | yes (5/8) |
| 4 | **no** |
| 5 | **no** |

**Verdict: FAILS.**

**Robustness:**
- regime A: +7.1 bps, CI [−29, +48], 1/3 episodes;
- regime C: 0.0 bps, CI [−29, +30], 1/4 episodes.

**The holdout was not run.**
