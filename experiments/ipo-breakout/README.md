# IPO breakouts on the NSE: does the setup pay, and does the lock-in calendar?

An experiment. Take the IPO base — O'Neil's exception for stocks too young to
form a normal one — turn its published rules into arithmetic over post-listing
candles, and measure honestly whether it pays. Then test the thing India has and
the American literature does not: a **statutory supply calendar** that is
knowable from the listing date alone.

Nothing here is imported by the paper-trading application, nothing places or
simulates a real order, there is no broker surface even as a stub, and the app's
database is opened read-only. `scripts/check_isolation.py` asserts all of that.

| File | What it is |
|---|---|
| `RESEARCH.md` | the setup, every criterion sourced; India's lock-in law; listing-day mechanics |
| `METHOD.md` | how the detector works, and the bugs worth remembering |
| `README.md` | this file — findings, and what they mean |
| `SETUPS.md` | three candidate breakouts, measured against each other (**a negative**) |
| `LOCKINS.md` | lock-in expiry as a tradable event (**the positive**) |
| `EXITS.md` | eight exit rules; every trail made it worse |
| `FINAL_LOGIC.md` | **the decided rule set**, with a full stop spec and what is unproven |
| `ipolib/` | the library |
| `scripts/` | universe build, benchmark, sweep, scan, analysis, backtest |
| `out/` | every result quoted below, including `ipo_dashboard.html` |

```bash
cd experiments/ipo-breakout
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/run_all.py              # everything except the data pull
.venv/bin/python scripts/run_all.py --with-data  # including it (~50 min, live token)
```

---

## 0. Findings at a glance

**Read §1 first.** Every number below is an upper bound, and the reason is
structural.

**Detection**, on a synthetic benchmark of 186 drawn bases and 108 negative
controls where the answer is known:

- The strict profile scores **100% recall and 98.8% signal precision** — one
  false breakout across 108 controls. Relaxing it to practitioner tolerances
  keeps recall at 100% and drops signal precision to **71.0%**, with false
  alarms rising 275 → 455 per 1,000 bar-scans. As with the VCP, **the strictness
  of O'Neil's criteria is the entire reason the pattern means anything.**
- 100% recall is *not* impressive here and is reported as such: the generator
  enforces its own geometry, so a correct implementation finds all of them by
  construction. Precision is the discriminating number.
- The parameter sweep puts the knees of the three load-bearing thresholds
  **exactly at IBD's published values** — `left_high_window` 25, `max_depth_pct`
  50, `min_base_len` 7 — which is the closest thing to independent corroboration
  the published rules get here.

**Trading**, on 521 NSE mainboard IPOs, 2016–2026:

- **The breakout has an edge that survives a block bootstrap.** +3.93% excess
  over a date-matched market baseline at 20 sessions, CI **[+1.28%, +6.67%]**;
  +5.08% at 60 sessions. That is roughly four times the VCP work's +1.07%.
- **The base structure is not what pays.** "Above the listing-day high" — no
  literature, and a pivot that is a circuit limit a fifth of the time — measured
  *nominally better* (+4.64% at 20, +9.20% at 60). A flat negative for the
  published rule set. `SETUPS.md`.
- **The lock-in calendar is the real result.** New listings drift **−3.32%**
  against the market into an anchor unlock they have known about since
  allotment, CI [−5.07%, −1.51%], against flat placebos. The effect is *before*
  the date, not after. `LOCKINS.md`.
- **And it makes a falsifiable prediction that holds.** SEBI split the anchor
  tranche on 1 April 2022. The day-30 effect is **−3.32%** when the whole
  tranche unlocks there and **−1.22%** when only half does; a day-90 effect of
  **−2.52%** appears only in the regime that has a day-90 unlock. The size of
  the effect tracks the size of the tranche.
- **One filter improved return, drawdown and win rate together**: refuse a
  breakout landing within 14 days before an anchor unlock. At matched exposure,
  CAGR 10.5% → **12.3%**, drawdown −11.7% → **−8.6%**, MAR 0.90 → **1.44**, win
  rate 53.8% → 58.7%. It is also the only filter tried that has a mechanism
  rather than a number.
- **Every trailing exit made it worse**, monotonically in how tight it was. Eight
  rules, all beaten by holding to a stop or an 80-session cap. The median trade
  is a small loss and the profit is a right tail that trails clip. `EXITS.md`.
- **Detector scores predict nothing**, again. Filtering to scores ≥ 0.7 moved
  CAGR 10.5% → 9.8%. They measure textbook conformity, which did not pay — the
  same result the VCP work got.
- **The strategy cannot fill a book.** At 22.7% average exposure it is
  three-quarters in cash, and it **loses to buy-and-hold on absolute return**
  (18.6% equal-weight mean CAGR over the same decade, fully invested). Its claim
  is capital efficiency and an −8.6% drawdown, not return.

The decided rule set is in [`FINAL_LOGIC.md`](FINAL_LOGIC.md); the dashboard is
`out/ipo_dashboard.html`.

---

## 1. The data problem, and what was done about it

This is first because it conditions everything.

The paper-trading application's `daily_bars` holds **today's Nifty 500**. 181 of
those 500 have a first bar after 2016 — but they are 27–48% of the mainboard
IPOs of those years, and they are in the file *because they later joined the
index*. "IPOs that became index members" is close to a definition of the
winners. For a momentum study that is uncomfortable; for an IPO study it is
circular.

So the universe was rebuilt. Dhan's **public instrument master** carries 2,690
NSE `SERIES=EQ` tickers against the app's 500, and `/charts/historical` serves
each one's whole history back to its listing day — verified in Step 0 against
five known listings, including three that never entered any index.

| | app's `daily_bars` | rebuilt universe |
|---|---:|---:|
| symbols | 500 | **2,906** |
| daily bars | 1.1M | **7,254,796** |
| mainboard IPOs 2016–2026 | 181 first-bar dates | **521 matched of 549** |
| usable (≥ 90 sessions) | — | **466** |

**A first bar is not an IPO**, so the calendar join is not optional: the raw
first-bar field also captures demergers (JIOFIN, ADANIGREEN, TIINDIA, MSUMI —
no anchor investors, no statutory lock-in) and plain data gaps (FORCEMOT has
traded for decades and still shows a 2019 first bar). Matching against a
mainboard IPO calendar on **listing date, with the name only as a tiebreaker**,
removes them.

### What survivorship remains

**28 of 549** mainboard IPOs could not be matched — 5%, against the 52–73% the
Nifty-500 file was missing. Companies delisted or merged away since are absent
from the instrument master entirely and cannot be counted from here.

Both baselines are drawn from the same surviving universe, so they are biased up
too, which makes the *excess* a lower bound — the conservative direction. The
absolute returns are not. **Every headline figure here is an upper bound**, and
the dashboard says so in its subtitle.

---

## 2. The listing bar is not a normal bar, and it shows

`RESEARCH.md` §4 argues from NSE's rules that the listing-day bar is special.
`scripts/listing_bar_stats.py` measures it across 520 listings rather than
assuming it:

- listing-bar volume is a median **21.0×** the median of the next twenty
  sessions, reaching **291×**;
- the **median** listing bar's high sits **exactly +5.00%** above its open, and
  the maximum is **exactly +20.0%** — the two statutory bands, visible in the
  distribution's own quantiles;
- **14%** close exactly at the day's high and 4% exactly at the low — frozen at
  a circuit;
- **17%** have an extreme sitting on a ±5% band, **7%** on a ±20% band.

Worked example: **Paras Defence**, listed 1 Oct 2021, issue ₹170.8 crore, so
±5%. Dhan's bar is open 234.50, high 246.23, close 246.23 — and
246.23 / 234.50 = **1.0500** exactly.

Two consequences, both acted on:

1. The listing bar is excluded from every average and every volatility estimate.
   A 50-session mean volume that includes it is dominated by it for fifty
   sessions, and "volume 40% above average" then fails by construction.
2. For a fifth of listings, **"the listing-day high" is a number the exchange
   chose**, not a price where supply met demand. That is why the base setup
   searches its pivot from bar 1.

**Prices are corporate-action adjusted** (Paras: 469 / 2 = 234.50 to the paisa).
Returns and ratios are safe; rupee comparisons against an unadjusted issue price
are not, and are not made.

---

## 3. What the detector actually finds

```
setup 'base'
  listings where a base formed at all   463  (99% of usable)
  listings that triggered a breakout    184  (39% of usable)
```

**A base "forms" on 99% of listings**, which means `forming` carries close to no
information: any early high followed by any pullback satisfies the structure.
Only the breakout state does any work. That is a fact about the pattern rather
than the code, and it is reported rather than dropped.

Signals are spread thinly and unevenly — 5 in 2020, 38 in 2025 — because the
strategy can only trade when companies are listing. `FINAL_LOGIC.md` §5 treats
that as a constraint, not a detail.

---

## 4. Is it better than what was available that day?

Forward returns from the entry, against two date-matched baselines, block
bootstrapped by calendar month (overlapping windows make a naive t-statistic far
too generous).

| horizon | n | signal | market | **excess** | 95% CI | p≤0 |
|---|---:|---:|---:|---:|---|---:|
| 5 | 184 | 1.47% | 0.50% | +0.98% | [−0.38%, +2.29%] | 0.074 |
| 10 | 184 | 2.62% | 1.01% | +1.60% | [−0.31%, +3.49%] | 0.050 |
| **20** | 184 | 5.43% | 1.50% | **+3.93%** | **[+1.28%, +6.67%]** | 0.001 |
| 40 | 184 | 7.12% | 2.88% | +4.24% | [+0.59%, +8.20%] | 0.013 |
| 60 | 184 | 9.39% | 4.30% | +5.08% | [+0.83%, +9.61%] | 0.010 |

The edge is not there at 5 and 10 sessions and is clear from 20 on. Win rate at
20 sessions is 57.6%, median +3.04%, with the 5th percentile at −17.0% and the
95th at +33.0% — a wide, right-skewed distribution, which is what makes the
exit result in `EXITS.md` come out the way it does.

Against the **IPO-cohort** baseline the excess is slightly *larger* (+4.30% at
20). That is the softer comparison, not the harder one: Ritter's result is that
IPOs underperform as a class, so beating same-vintage IPOs is a weaker claim
than beating the market. Both are reported and every table says which is which.

---

## 5. The portfolio

Cash-accounted, fixed notional per position, marked to market daily, 0.3% round
trip. The number of open positions is an output of the cash.

| | CAGR | max DD | MAR | win | PF | trades | avg positions | exposure |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| every signal, ₹1.00L a slot | 10.5% | −11.7% | 0.90 | 53.8% | 2.45 | 184 | 4.4 | 25.7% |
| **+ lock-in filter, ₹1.33L** | **12.3%** | **−8.6%** | **1.44** | **58.7%** | **3.38** | 126 | 3.2 | 22.7% |
| equal-weight buy-and-hold | 18.6%* | — | — | — | — | — | — | 100% |

\* mean across 1,188 symbols listed throughout the decade; the median is 9.2%.
Itself survivorship-inflated, and fully invested.

The two strategy rows are compared at **matched exposure**, not matched position
size — the brief's correction, and it matters: the filter removes a third of the
trades, so at equal position size it would have looked flat while sitting in
cash.

**The honest summary**: against buy-and-hold the strategy loses on return and
wins on drawdown and on capital efficiency, earning 12.3% on a quarter of the
book. Whether that is useful depends entirely on what the other three quarters
do, and this experiment does not answer that.

---

## 6. What this does not show

The full list is `FINAL_LOGIC.md` §5. The four that matter most:

1. **Survivorship.** 28 unmatched IPOs, plus every delisted company, absent.
   Upper bounds throughout.
2. **The strategy cannot fill a book** (22.7% exposure), so its CAGR is not
   comparable with a fully-invested strategy's.
3. **GMP and subscription multiples could not be tested at all** — no historical
   source exists in or reachable from this repo. `FINAL_LOGIC.md` §5 states the
   minimum data each would need rather than substituting a proxy.
4. **126 trades** underpin the lock-in filter's portfolio benefit. The
   unconditional study behind it rests on 460 listings, which is why the
   direction is believed; the filter itself is not independently significant at
   that sample size.
