# REGIME — is it a bear market, and when were the rules on?

Written **before any strategy result was computed.** `scripts/breadth_regime.py`
computes no strategy return. In particular it does not compute the 09:30 → 15:10
NIFTY short, which is the key control, so nothing below was chosen by looking at
how a strategy did. Numbers are in `out/regime.json`, and the daily series is in
`out/regime_daily.csv`.

Sources, all read-only:

| Series | Source | Window |
|---|---|---|
| NIFTY 50 daily | `daily_bars` (IDX_I `NIFTY`), the application DB opened `mode=ro` | 2015-07-01 → 2026-09-30 |
| 210 F&O names | `intraday-screener/data/clean/days.parquet`, raw close × `adj` | 2017-04-03 → 2026-09-25 |
| Nifty 500 | `daily_bars` (NSE_EQ), **today's** 500 constituents | 2015-07-01 → 2026-09-30 |
| Nifty Midcap Select, India VIX | `hedged-option-selling` `index_close` | → 2026-09-17 |

The NIFTY daily series agrees with the 5-min panel's last close to a median of
0.05% (7 of 2,339 days differ by more than 0.5%). In the Nifty 500, 25 isolated
one-day excursions of more than 15% that reverse the next day were removed:
these are never-adjusted prints, the BHARTIARTL 2018-04-20 kind (CLEANING.md).

---

## Step 0b: the premise

The handoff measured this to 2026-09-25. `daily_bars` now runs to 2026-09-30.

| Fact | Value (as of 2026-09-30) |
|---|---|
| NIFTY close | 22,620.45 |
| High | 26,328.55 (2026-01-02) |
| **Drawdown** | **−14.1%** |
| 50-day / 200-day SMA | 23,902 / 24,368. Price is below both, and the 200-day is falling. |
| Monthly returns 2026, % | Jan −3.1, Feb −0.6, **Mar −11.3**, Apr +7.5, May −1.9, Jun +1.4, Jul +2.2, Aug −1.2, **Sep −6.1** |

September got worse after the handoff's cut. The index went from −12.1% to
−14.1%, and the month from −3.9% to −6.1%.

**Breadth:**

| Universe | As of | Below SMA50 | Below SMA200 | Median drawdown from own 52-week high |
|---|---|---|---|---|
| 210 F&O names | 2026-09-25 | 77% | 59% | **−17.3%** |
| Nifty 500 | 2026-09-30 | 80% | 59% | **−19.8%** |
| Nifty 500 excluding F&O (288 names, mostly mid/small) | 2026-09-30 | 75% | 53% | **−20.5%** |
| Nifty Midcap Select (25 large midcaps, index) | 2026-09-17 | — | — | −4.4% |

**Verdict:**
- **The index is in a deep correction inside a downtrend, not a −20% bear
  market.**
- **The typical stock is at the bear threshold.** The median Nifty 500 name is
  20% off its high, and the median mid/small name a little more.
- **The damage is broad, not concentrated.** Four in five names are below their
  50-day average.

The Midcap Select index's −4.4% is real, but it is 25 large midcaps and doesn't
represent the median stock. The strategy below doesn't depend on the word
"bear". It depends on the causal rules that follow.

---

## The three rules (pre-registered, the handoff's examples, untuned)

Each rule is evaluated at the **close of day t** from data up to that close, and
it governs **trading on day t+1** (`regime.for_trading`, the only way a backtest
reads it). Parameters were taken from the handoff's examples as written. None
were adjusted after seeing the output below.

| Rule | On when | Parameters |
|---|---|---|
| **A `sma200`** | NIFTY close < its 200-day SMA **and** that SMA is below its value 20 sessions earlier | 200, 20 |
| **B `breadth`** | More than **60%** of the F&O names with a defined SMA are below their own 50-day SMA | 50, 0.60 |
| **C `drawdown`** | NIFTY close is more than **10%** below its 252-session closing high | 252, −10% |

An SMA needs 80% of its window to exist; a missing name-day is skipped, not
filled. Rule B is undefined (so off) for the first ~50 sessions of the panel.

## How each rule behaved, 2017-04-03 → 2026-09-25

| | A sma200 | B breadth | C drawdown |
|---|---|---|---|
| Share of sessions on | 11.8% | 24.3% | 13.2% |
| Switches per year | 1.8 | **12.8** | 6.2 |
| Median on-run, sessions | 6 | 4 | 4.5 |
| On-runs of ≤ 5 sessions | 3 of 9 | 36 of 61 | 17 of 30 |
| On-days, development (2017-04 → 2022-12) | 141 | **364** | 197 |
| On-days, holdout (2023-01 → 2026-09) | 137 | 208 | 113 |
| Episodes, development | 3 | **8** | 4 |
| Episodes, holdout | 1 | 5 | 4 |
| On at the 2026-09-25 close | yes | yes | yes |

An **episode** is fixed here: on-runs separated by at most 20 off-sessions form
one episode, and an episode needs at least 10 on-days.

**Agreement is low.** On the days either rule is on (Jaccard), A and B overlap
on 12%, A and C on 41%, and B and C on 31%. These are three different
definitions, not three versions of one.

### Episodes

| Rule | Episodes (traded dates) |
|---|---|
| A | 2018-10-31 → 12-13 · 2020-03-13 → 07-17 · 2022-06-16 → 07-29 · **2026-03-19 → 09-25** (still on) |
| B | 2018-02 → 04 · 2018-05 → 07 · 2018-09 → 11 · 2019-01 → 02 · 2019-07 → 09 · 2020-02 → 05 · 2021-11 → 2022-03 · 2022-05 → 07 · 2023-01 → 04 · **2024-10 → 2025-04** · 2025-07 → 09 · 2026-01 → 04 · 2026-09 → |
| C | 2018-10 → 12 · 2019-08 → 09 · 2020-03 → 08 · 2022-05 → 07 · **2024-11 → 2025-04** · 2026-03 → 04 · 2026-05 → 06 · 2026-09 → |

**All five expected episodes are there in rules B and C. Rule A misses one:**

| Expected episode | Found? |
|---|---|
| 2018 mid/small-cap bear | The median Nifty 500 name was ≥ 20% off its high from 2018-05-21 to 2019-10-14. B is on in five separate waves across it, C in two, A in one. |
| Mar 2020 | All three |
| Oct 2021 → Jun 2022 | B from 2021-11-23. C from 2021-12-21, then properly from 2022-05. A only from 2022-06-16, the day before the trough. |
| Oct 2024 → Mar 2025 | B from 2024-10-08, C from 2024-11-14. **A misses it entirely:** it first switched on 2025-04-09, after the trough. |
| 2026 | All three |

### How late: measured against the ex-post NIFTY declines of 10% or more

These are hindsight peaks and troughs, used **only** to measure the rules,
never as a rule. "Missed" is the share of the peak-to-trough fall that had
already happened before the rule first traded on (1.0 means it switched on
after the trough, or never). "Rally" is how far NIFTY had risen from the trough
by the last on-session before the rule turned off for at least 10 sessions.
That is the relief rally a regime-gated short book stays exposed to.

| Decline | Fall | A missed | A rally | **B missed** | **B rally** | C missed | C rally |
|---|---|---|---|---|---|---|---|
| 2018-01 → 03 | −10.2% | 1.00 | 0 | 0.41 | +3.3% | 1.00 | +1.3% |
| 2018-08 → 10 | −14.6% | 1.00 | +7.6% | 0.45 | +5.9% | 0.81 | +6.0% |
| 2019-06 → 09 | −11.5% | 1.00 | 0 | 0.38 | +5.3% | 0.82 | +5.3% |
| 2020-01 → 03 | −38.4% | 0.51 | **+43.3%** | 0.14 | +19.1% | 0.40 | **+47.2%** |
| 2021-10 → 2022-06 | −17.2% | 0.98 | +12.2% | 0.31 | +5.5% | 0.54 | +8.8% |
| 2024-09 → 2025-03 | −15.8% | 1.00 | 0 | 0.29 | +3.7% | 0.65 | +8.0% |
| 2026-01 → 03 | −15.2% | 0.83 | still on | 0.29 | +7.5% | 0.79 | +7.5% |

- **Rule A is the classic trend filter, and it's the worst here.** It switches
  on after most of the fall and stays on through the rebound: +43% off the 2020
  low. It has been on since 2026-03-19, so it was on through April 2026's +7.5%.
  It also missed the 2024–25 bear.
- **Rule C is nearly as late** on the way in, and it's the worst on the way out
  in 2020.
- **Rule B is the earliest in every decline.** It catches about 55–85% of each
  fall and leaves soonest after the trough. The price is whipsaw: 12.8 switches
  a year, and 36 of its 61 on-runs last five sessions or fewer.

## Relief rallies sit inside the regime: the left tail

Big NIFTY days that fell on regime-on days, 2017-04 → 2026-09:

| | A | B | C | All sessions |
|---|---|---|---|---|
| On-days | 278 | 572 | 310 | 2,335 |
| **+2% close-to-close** | 19 | 29 | 28 | 52 |
| **+3% close-to-close** | 12 | **14** | 12 | 19 |
| +2% open-to-close (the intraday part) | 10 | 11 | 12 | 20 |
| +3% open-to-close | 5 | 5 | 6 | 8 |
| −2% close-to-close | 19 | 33 | 26 | — |
| −3% close-to-close | 9 | 14 | 11 | — |

**Most of the market's biggest up-days happen inside the bear regime.** Under
rule B, 14 of the decade's 19 days of +3% fall on 24% of the sessions. Under
every rule, **there are as many +3% days as −3% days inside the regime.** This
is the short book's left tail. It is in the sample, not missing from it, so a
regime-gated short will meet it.

## Pre-registered choices for everything that follows

1. **Primary regime: rule B (breadth).** It is chosen on the timing evidence
   above alone, before any strategy result:
   - it is on in all five expected episodes, and it is on before the
     trough in every one (A misses 2024–25 entirely);
   - it is the earliest on, and the soonest off after a trough;
   - it gives the most independent episodes: 8 in development, against 3 for
     A.

   Its whipsaw is a known cost, and it is measured in the strategy results,
   not tuned away. No hysteresis or persistence filter is added.
2. **A and C are reported as robustness runs**, and they count as variants in
   the variant count.
3. **When the regime is off, every bear hypothesis trades nothing.** The same
   rules on regime-off days are run as the `regime_off` control, so the price
   of a late or missing switch is measured rather than assumed.
4. **The episode definition** (bridge of 20 sessions, at least 10 on-days) is
   fixed here, and so is the pass-bar condition "positive in the majority of
   development episodes". **Under rule B the development period has 8
   episodes: positive in at least 5.**

## The headline caveat

**The development period contains about four genuine bear phases:** the 2018
mid/small-cap bear, the 2019 midcap leg, March 2020 and mid-2022. Rule B splits
these into 8 episodes, but they aren't 8 independent draws. 2018 alone gives
four of them.

**The holdout has two:** 2024–25 and 2026. Every result below rests on a
handful of market events, and the March 2020 crash is the largest of them by a
wide margin. Each strategy result is therefore also reported **with 2020-02 →
2020-05 removed.**
