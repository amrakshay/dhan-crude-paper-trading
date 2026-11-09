# SHORTLIST — the daily list, fixed before 09:15

Pre-registered on 2026-10-03, before any result. **N = 5.**

## Inputs: data up to the previous close only

The daily series come from the 5-minute panel (adjusted, `CLEANING.md`):
- **close:** the day's last close;
- **high / low:** the max and min of its bars;
- **NIFTY:** the panel's index close.

Sectors come from `order-block/data/sectors.csv` (20 sectors, NSE Indices'
Nifty 500 industry list, Financial Services split). That is **today's**
classification, not point-in-time.

## Eligible on day *d*

- The name is in the liquid top 100 on day *d* (point-in-time turnover, judged
  from days before *d*).
- Its close-to-close move on day *d*−1 was between −9% and +9%, so it is not
  at or near a price band.
- It is not `gap_flag` on day *d*. That is an unadjusted corporate action,
  known at 09:15 from the open against the previous close.
- All six features below are defined.

**No ban-list filter.** The F&O ban restricts new F&O positions, not
cash-segment intraday shorts (see `RESEARCH.md`).

**No news or results-calendar filter.** `intraday-screener/NEWS_DATA.md` found
that NSE's terms prohibit automated collection, and no licensed feed was
bought. A short into a scheduled result is therefore possible here and would be
avoidable live. This is a known gap, and it cuts both ways.

## The six features (each oriented so that HIGHER = better short)

| # | Feature | Definition at the close of *d*−1 | Orientation |
|---|---|---|---|
| 1 | Weak vs NIFTY | `r20(stock) − r20(NIFTY)`, where `r20 = C/C₋₂₀ − 1` | lower is better, so it is negated |
| 2 | Weak vs sector | `r20(stock) −` the median `r20` of the other eligible names in its sector (0 if none) | negated |
| 3 | Below the trend | `C / SMA50 − 1` | negated |
| 4 | Weak close | `(C − L) / (H − L)` of day *d*−1 (0.5 if H = L) | negated |
| 5 | Weak sector | the median `r20` of all eligible names in its sector | negated |
| 6 | Beta | 60-session OLS beta of daily returns to NIFTY's | higher is better |

**Score** = the mean of the six cross-sectional **percentile ranks** among the
day's eligible names. The top 5 by score form the shortlist (ties are broken by
symbol).

The handoff phrased feature 3 as "price below a falling 20/50-day SMA". It is
coded as the distance from SMA50, which ranks rather than filters. The
direction of feature 6 (high beta = better short in a bear regime) is the
market-down bet. It is a choice, not established evidence; see `RESEARCH.md`.

**Left out, and why:**
- **Short buildup** (stock futures price down with OI up): the data isn't
  local. It is on the pending list (README decision 5).
- **News:** see above.

## How the shortlist is judged on its own

`random_shortlist` uses the same days (regime-on), the same trigger, the same
stops and the same execution. The only change is that the 5 names are drawn at
random from the day's eligible names, pooled over 5 reps. If the random 5 do as
well, the shortlist adds nothing. That would be a finding, and it isn't fixed
by re-weighting the features.

## Results (development, regime B)

The same trigger and execution on the real shortlist against 5 random eligible
names (`random_shortlist`, 5 reps pooled), in bps net per trade @ 5 bps:

| Trigger | Shortlist | Random 5 | Difference | 95% CI |
|---|---|---|---|---|
| B1 breakdown | +0.4 | −14.1 | **+14.6** | **[+2.0, +27.9]** |
| B2 failed rally | +8.0 | −10.1 | **+18.0** | **[+2.1, +34.8]** |

**The shortlist adds something.** Against both triggers it beats random
eligible names by about 15–18 bps, with the CI clear of zero. The six
pre-registered features do pick names that underperform intraday on bear days.
B2 also beats a random liquid name at the same moment (`random_name`, +20.3,
CI [+6, +35]).

**It isn't enough.** In both cases the improvement takes the trade from clearly
negative to roughly zero after costs. The shortlist moves a losing trigger to
break-even. It doesn't create an edge that survives charges, the best-days
test or the monthly bar (`B1_BREAKDOWN.md`, `B2_FAILED_RALLY.md`).

**Not done, on purpose:**
- no re-weighting of the features after seeing this;
- no per-feature ablation on development data.

Either would be a new, post-hoc variant. If the shortlist is pursued, the
cleaner next step is to pre-register a per-feature test and run it **forward**
in the shadow journal, not on the development data again.

**Prior evidence, per `RESEARCH.md` §5:**

- **Beta (feature 6):** has Indian evidence. Low beta beats high beta
  (betting-against-beta), and high beta falls more on down days. "High beta =
  better short" is consistent with that.
- **The 20-day relative-weakness features (1, 2 and 5):** sit at the horizon
  where the Indian evidence is **weak or mixed**. The well-documented momentum
  effect is the 12-month one, which isn't what these measure. There is also a
  US result that matters directly: losers rebound violently when a decline
  turns, the Daniel & Moskowitz "momentum crash". That is a reason to expect
  weak-name shorts to suffer in relief rallies.
- **Trend distance and close location (features 3 and 4):** classified as
  folklore.
