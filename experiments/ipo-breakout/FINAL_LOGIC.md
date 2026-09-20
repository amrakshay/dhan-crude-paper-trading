# The decided rule set

Everything needed to run this, and everything still unproven about it. Findings
are in `README.md`; this is the specification.

Nothing here is wired into the paper-trading application, and nothing should be
until §5 is shorter.

---

## 1. Universe and setup

**Universe.** NSE mainboard IPOs. A listing qualifies when it appears in the
mainboard IPO calendar *and* resolves to a tradable symbol whose first Dhan bar
is within 3 calendar days of the calendar's listing date. Demergers, re-listings
and exchange migrations are excluded by construction — they are not in the IPO
calendar. SME (NSE Emerge) is out of scope.

**Setup.** `base` — the IBD IPO base (`ipolib/ipo_base.py`, profile `strict`).

`SETUPS.md` records that this is a *judgement*, not a result: `day1` measured
nominally better (+4.64% vs +3.93% excess at 20 sessions). `base` is chosen
because its pivot is always a traded high rather than a circuit limit, it enters
13–20 sessions earlier, and its gates refuse the deep-collapse and
volume-inverted shapes. Anyone preferring `day1` on the measured numbers has a
fair case.

---

## 2. Entry

A **breakout** is signalled at bar `t` when all of the following hold on bars
`0..t`, where bar 0 is the listing bar:

| # | Rule | Value |
|---|---|---|
| E1 | Bar `t` is at least `1 + min_base_len` sessions after listing | ≥ 8 |
| E2 | Bar `t` is no more than `max_age_sessions` after listing | ≤ 250 |
| E3 | **Pivot** = highest high over bars `1 .. min(25, t − 7)`. Bar 0 excluded | `left_high_window` 25 |
| E4 | **Base length** = `t − pivot_idx` | 7 ≤ len ≤ 40 |
| E5 | **Depth** = (pivot high − lowest low in base) / pivot high | ≤ 50% |
| E6 | No close in `pivot_idx+1 .. t−1` exceeded the pivot price | — |
| E7 | `close[t]` > pivot high × 1.001 | `pivot_buffer_pct` 0.1 |
| E8 | **Volume** at `t` ≥ 1.4 × median volume over the prior 30 sessions, bar 0 excluded | `breakout_volume_ratio` 1.4 |
| E9 | **Not within 14 days before an anchor lock-in expiry** | see §4 |

E8 is a **gate**, not a score: failing it demotes the signal to `forming`, so
the base stays on a watchlist and the reason is visible. `METHOD.md` §6.3 has
why.

E1–E8 come from IBD's published rules (`RESEARCH.md` §1). E9 is this project's
own, and `LOCKINS.md` is its evidence.

**Entry price: the next session's open.** Not the signal bar's close — the signal
is only known once that bar has closed.

**Sizing: fixed notional per position.** ₹1.33 lakh on a ₹10 lakh book in the
reference run. The number of open positions is an output of the cash, not a
setting.

---

## 3. The stop, in full

| | |
|---|---|
| **Initial stop** | the **base low** — the lowest low over `pivot_idx+1 .. t` |
| **ATR floor** | stop is widened to at most `entry − 0.5 × raw ATR(14)` if the base low is closer than that |
| **ATR** | **raw** mean true range over the 14 sessions ending at the signal bar, **excluding bar 0**. Not Wilder-smoothed |
| **Trail** | **none** |
| **Time cap** | **80 sessions**, exit at that day's close |
| **Fill on a stop** | at the stop level; at the bar's **open** when the bar gapped below it |
| **Fill on a time exit** | that day's close |

**The ATR floor is inactive on this universe and is retained deliberately.** The
median stop sits **19.0%** below entry, so half a daily range never binds. It
was the VCP work's single best filter and was re-measured rather than inherited;
it is kept so a future universe with tighter bases gets it, and documented as
inactive so nobody re-derives it. `EXITS.md` §4.

**There is no trail, and that is a measured result, not an omission.** Eight
trailing rules were tested and all eight lost to holding, monotonically in how
tight the trail was. The strategy's median trade is a small loss and its return
lives in a right tail that every trail clips. `EXITS.md` §1.

**80 sessions, not 250.** A 250-session cap earns more per trade (19.46% vs
10.28%) and is worse per unit of capital: it doubles the drawdown to −22.5% for
15.7% CAGR, against −11.7% for 10.5%. `EXITS.md` §3.

---

## 4. The lock-in filter (E9)

Refuse a signal whose entry falls in the **14 days before an anchor lock-in
expiry**. Unlock dates are computed from the listing date by
`ipolib/lockins.py`, which is a function of the issue's own date because the
rules changed twice inside the sample:

```
anchor unlocks:
  issue listed on/after 2022-04-01 :  listing +30d (50%), listing +90d (50%)
  before that                      :  listing +30d (100%)
```

Only the **anchor** dates are used. The 6-month and 18-month promoter and
pre-IPO unlocks are computed but not filtered on: they are discretionary rather
than statutory, and `LOCKINS.md` §3.4 shows them flat.

The dates are approximated from *listing*; the statute runs from *allotment*,
2–4 sessions earlier, and that gap itself shifts mid-sample. The 14-day window
is wide enough to swallow it.

---

## 5. What is still unproven

Listed rather than buried, and this section is the reason nothing here is wired
into the application.

1. **Survivorship remains, smaller but real.** 28 of 549 mainboard IPOs could
   not be matched to a live symbol, and companies delisted since are absent from
   Dhan's instrument master entirely. Every return here is an **upper bound**.
2. **The strategy cannot fill a book.** At 22.7% average exposure the reference
   run is three-quarters in cash. The CAGR is therefore not comparable with a
   fully-invested strategy's, and scaling it up means either more slots per
   signal or another strategy sharing the book. Neither is tested.
3. **It loses to buy-and-hold on absolute return.** Equal-weight buy-and-hold
   over the same decade returned 18.6% CAGR on the mean (9.2% on the median),
   fully invested. The strategy returns 12.3% on a quarter of the capital. The
   honest claim is capital efficiency and drawdown, not return.
4. **The base structure is not what pays** (`SETUPS.md`). The decided setup is
   chosen on judgement against a nominally better alternative.
5. **The published high/low field mapping is still unverified.** Root
   `CLAUDE.md` flags that the Quote/Full packet's four price fields are mapped
   per the SDK and never checked against a live feed. This experiment uses
   `/charts/historical`, not the feed, so it is not exposed — but any live
   implementation would be.
6. **GMP is untested and untestable here.** `ipo_gmp_readings` holds 24 rows.
   The literature stops at listing day; whether GMP predicts anything weeks
   later is, as far as `RESEARCH.md` §5 found, unstudied. **Minimum to test it:
   about 150 IPOs with a GMP series captured before listing — roughly two years
   of the current forward feed.**
7. **Subscription multiples are untestable here.** QIB/HNI/retail data exists in
   no source available: not Dhan, not the repo's 12-row `ipos` table, and not
   investorgain's JSON endpoint, which ignores its year parameter. **Minimum to
   test it: a historical subscription table keyed on company and listing date,
   2016 onwards.**
8. **Corporate-action adjustment is inferred, not documented.** Dhan's history
   is split- and bonus-adjusted (verified arithmetically on Paras Defence:
   469 / 2 = 234.50 exactly). Whether **volume** is adjusted alongside price is
   unverified; the code uses volume only as a ratio to its own trailing median,
   which is robust everywhere except the ~50 sessions straddling a split.
9. **No transaction-cost model beyond a flat 0.3% round trip.** The application
   has a real charges engine with sourced rate cards; this experiment does not
   use it. Small IPOs are also in trade-to-trade for their first 10 days, which
   this ignores.
10. **126 trades.** The lock-in filter's portfolio benefit rests on that many.
    The direction is corroborated by the 460-listing unconditional study, which
    is why it is believed at all, but the filter is not independently
    significant at this sample size and is not claimed to be.
11. **One market, one decade, one long-only direction.** 2016–2026, NSE
    mainboard, no short side, no regime conditioning.

---

## 6. Reproducing it

```bash
cd experiments/ipo-breakout
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

.venv/bin/python scripts/run_all.py              # everything except the data pull
.venv/bin/python scripts/run_all.py --with-data  # including it (~50 min, live token)
```

The data pull is opt-in because it is the only step that talks to a broker API.
`scripts/check_isolation.py` runs first and asserts the three claims this
experiment makes about itself: no broker surface, the application's database
opened read-only, and nothing in `backend/` importing any of it.
