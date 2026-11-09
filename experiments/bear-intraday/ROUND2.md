# ROUND 2 — price action, technicals and order-flow proxies

Pre-registered 2026-10-03, **after** round 1 (B1, B2, B6, index short: all fail
development) and **before** any round-2 result. The operator asked for
intraday shorting that picks stocks using price action, technicals and order
flow. Everything inherits `METHOD.md`:
- the bar conventions;
- fills, the market-halt and locked-limit rules;
- costs, the controls, and the README pass bar (all five conditions);
- the development period 2017-04 → 2022-12;
- the holdout run once, and only with the operator's consent.

**Round 1 already constrains round 2.** On regime-on days NIFTY falls in the
first 15 minutes (−8.8 bps) and then drifts **up** through the held session
(+4.2 bps) (`INDEX_SHORT.md`). Continuation shorts start with a headwind. Any
short that works has to come from *stock selection* or *timing*, not from the
market falling. The shortlist does carry selection information (`SHORTLIST.md`).

## What "order flow" can and cannot mean here

**Real order flow can't be tested historically.** That means bid/ask imbalance,
5-level depth, trade-by-trade aggressor side and delta. We have 5-minute OHLCV
bars, not ticks, and no order book history. Round 2 uses the standard
**OHLCV proxies** for buying and selling pressure. They are proxies, and are
named as such:

| Proxy | Definition at bar *k* (causal) |
|---|---|
| **Close location (CLV)** of a bar | `(2·close − high − low) / (high − low)`, in [−1, +1]. 0 if high = low. |
| **Money flow since open** | `Σ CLV_j · volume_j / Σ volume_j`, j = 0..k. Below 0 means volume has traded on bars closing near their lows (the Chaikin money-flow idea). |
| **Relative volume** | `islib` `relvol`: cumulative volume ÷ the 20-day norm *at the same bar* |

Forward collection of real depth from the live feed is a separate proposal
(`FEASIBILITY.md` §6). It needs a working Dhan token.

## Hypotheses (6), short only

**Common rules:**
- **When:** regime-B-on days.
- **Who:** the day's shortlist (N = 5), except S5, which trades NIFTY.
- **Timing:** no decision before the 09:30 close, none after the 14:00 close,
  flat by 15:10. S5 has its own window.
- **Sizing:** ₹5 L per trade for the per-trade metric.

| # | Name | Trigger (decided on the close of bar *k*) | Stop | Exit |
|---|---|---|---|---|
| **S1** | VWAP rejection + selling pressure | *k* in [6, 56]: the bar's high reaches VWAP, but it closes below VWAP, in its own lower third (CLV ≤ −0.33), with money flow since open < 0 | max(high_k, VWAP_k) + 0.5 × ATR5 | time |
| **S2** | Prior-day-low breakdown on volume | first *k* in [3, 56] with close < the previous day's low, relvol ≥ 1.5, CLV_k ≤ −0.4, and NIFTY down since its open | max(previous low, high_k) + 1.0 × ATR5, then trail at close + 3 × ATR5 | trail / time |
| **S3** | Failed breakout (bull trap) | a bar *j* in [3, 40] closes above the opening-range high. Then the first *k* in (*j*, *j*+6] closes back below the OR high **and** below VWAP. | the day's high so far + 0.25 × ATR5 | time |
| **S4** | Afternoon weakness continuation | decided once, at bar 45 (13:00 close): return since open < 0, close < VWAP, weaker than NIFTY since the open, money flow since open < −0.1 | close + 3 × ATR5 | time |
| **S5** | Index intraday momentum (Gao, Han, Li & Zhou, *JFE* 2018) | NIFTY's return from the previous close to the 10:00 close (bar 8) is **< 0** → short NIFTY futures at the 15:00 open (bar 69), on **every** day | none | close of bar 74 (15:30) |
| **S6** | Opening drive down + selling pressure | decided once at 09:30 (bar 2): return since open ≤ −0.75%, close in the bottom 25% of the day's range so far, money flow since open < −0.3, NIFTY down since its open | the day's high so far + 0.25 × ATR5, then trail at 3 × ATR5 | trail / time |

**Notes, fixed now:**
- **S5 is the one published, peer-reviewed intraday effect** (`RESEARCH.md` §2,
  US-only evidence). It trades every day the signal fires, not only
  regime-on days. Regime-on vs off and before vs after 2024-11-20 are reported
  as splits, not as alternative rules.
  - It trades futures, so it may hold to 15:30. The closing auction affects
    F&O **stocks' cash** legs, not index futures.
  - Costs: the futures card, slippage 1 bp per side.
  - Its long mirror (first half-hour > 0 → long) is reported as a diagnostic.
  - Its controls are a **random day** (same count, same window) and the
    **mirror long**. The stock controls don't apply.
- **S6 runs against round 1's decomposition.** The fall before 09:30 tends to
  reverse. It is registered anyway because opening-drive shorts are what
  people trade, and an expected failure is still a measurement.
- **Stop distance and costs** are reported for every rule
  (the order-block lesson: a tight stop turns charges into R). S1's and S3's
  stops are the tightest. If the median stop is under 0.5% of price, that is
  flagged as the likely cause of any failure.

**Count: 6 new hypotheses**, on top of round 1's 5 plus its index control.
That is 12 in total, so the bar for a pass is correspondingly high. The
README's five conditions stay unchanged. **No parameter in the table is
changed after a result.** A variant, if any is run, is added to the README's
variant table and labelled post hoc.

---

## Results (development 2017-04 → 2022-12, regime B; `scripts/run_round2.py`, `out/r2_*.json`)

Net bps per trade at 5 bps per side and ₹5 L. Day-bootstrap 95% CI.

| # | Trades | Gross | **Net** | 95% CI | Drop best 10 days | Without 2020-02 → 05 | Episodes + | Beats all controls | Months + · worst · streak | Median stop | **Verdict** |
|---|---|---|---|---|---|---|---|---|---|---|---|
| S1 VWAP rejection | 831 | +3.8 | −0.7 | [−18, +18] | −23.4 | −17.1 | 3/8 | no | 32% · −4.5% · 6 | 0.71% | **FAIL** |
| S2 PDL breakdown | 192 | +3.3 | −1.2 | [−35, +34] | −42.5 | +7.1 | 2/8 | no | 52% · −1.9% · 4 | 2.63% | **FAIL** |
| S3 failed breakout | 165 | −0.3 | −4.8 | [−41, +31] | −49.1 | −14.7 | 3/8 | no | 46% · −3.4% · 2 | 1.27% | **FAIL** |
| S4 afternoon weakness | 242 | +10.4 | +5.9 | [−19, +33] | −27.0 | −3.9 | 4/8 | no | 52% · −3.7% · 3 | 1.09% | **FAIL** |
| S6 opening drive | 200 | +23.9 | **+19.4** | [−20, +60] | **−35.6** | +16.3 | **6/8** | no | 50% · −1.5% · 5 | 2.35% | **FAIL** |

**S5, index intraday momentum** (NIFTY futures, 15:00 → 15:30 when the first
half-hour is down; 560 days; net at 1 bp per side):

| | Value |
|---|---|
| Gross | −1.5 bps |
| **Net** | **−3.8 bps, CI [−6.1, −1.5]**: significantly *negative* |
| Regime on / off | −3.8 / −3.7 |
| Years positive | 0 / 6 |
| Short on random days, minus S5 | S5 better by 0.9, CI [−0.9, +2.6] |
| Long on the same days, minus S5 | S5 better by 1.0, CI [−3.6, +5.6] |
| Long side of the rule (first half-hour up → long) | −3.6, CI [−5.2, −2.1] |
| **Verdict** | **FAIL.** The first half-hour carries **no** information about the last half-hour of NIFTY in 2017–22. Every variant just pays the ~2.3 bps of futures costs. The published US effect doesn't replicate here. |

### What round 2 says

1. **Nothing passes**, and this time the technical rules don't even beat a
   random name or a random entry time with a CI clear of zero. Round 1's
   shortlist-level information (B2 beat `random_name`) doesn't show up through
   these triggers.
2. **The order-flow proxies didn't help.** Money flow and close location
   gated S1, S4 and S6; S1 is the most heavily filtered and the worst.
3. **The one interesting row is S6, the opening drive.** It has the highest
   gross (+23.9 bps), is positive without the COVID months, is positive in 6/8
   episodes, and its worst month is −1.5%. But dropping its 10 best days takes
   it to **−35.6**: 200 trades whose profit sits on a handful of days. It beats
   no control with a CI clear of zero, and only half its months are positive.
   **It is not a candidate.** Tweaking it now would be the H3c mistake from the
   screener (a rule picked on development data and then spent on the holdout).
   If it is ever pursued, it needs a fresh pre-registration and **forward**
   data.
4. **Stops were not the problem this time.** Every rule's median stop is at
   least 0.7% of price, and costs are 0.06–0.20 R.

**Variant count after round 2: 16 development runs** (10 from round 1, 6 here).
The holdout is still unspent for every stock rule.
