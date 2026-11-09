# VCP channel audit (Markets Decoded Alerts)

**Question (2026-10-04):** the Telegram channel *Markets Decoded 🔔 Alerts by
NovaWealth Capital* posts a `vcp_setups_YYYY-MM-DD.xlsx` watchlist every trading
day after the close. Did the **Mainboard** setups it shared actually work?

Read-only. Places nothing. The only Dhan contact is the application's own
`DhanChartsClient` (`/charts/historical`), using the token stored from the
Settings page.

## What the sheets contain

Each file has a `Mainboard` and an `SME` sheet with 10 ranked names:
rank, symbol, exchange, close, ADR %, % from 52-week high, 20-day traded value,
63-day RS, inside day, sector, industry. **No entry, no stop, no target.** The
guide sheet calls it a watchlist "for educational purposes only".

- 40 sheets, list dates **2026-08-06 → 2026-10-01**. 400 Mainboard rows,
  163 distinct names (383 NSE, 17 BSE rows; 17 rows are NSE `-BE` series).
- The sheet's "Close" is the **list date's** close, give or take: it is closer
  to that day's close than to the previous day's in 86% of rows, median error
  0.28% (probably a price taken shortly before the close). So the scan uses
  data up to the list date, and the earliest anyone could act is the **next
  session** — which is where every entry here starts.

## Rules (agreed 2026-10-04)

| Decision | Choice |
|---|---|
| What "worked" means | **Both tests, side by side** (below) |
| Repeats | A name counts on its **first listing**, and again only if it reappears **20+ sessions** after it was last listed. 400 rows → **169 setups** |
| Benchmark | **None** |
| Costs | **Net**: the repo's NSE delivery rate card (`backend/conf/charges/nse-equity-delivery.yaml`, read at run time) on ₹1,50,000 a trade, ≈ ₹348 a round trip, plus 0.10% slippage each side |

**Test A: breakout trade** (how a VCP list is meant to be used). The sheet gives
no pivot, so it is taken from the bars up to the list date: pivot = highest high
and stop = lowest low of the last **10** sessions (the final contraction). Wait
up to 10 sessions for a high above the pivot. Buy there, or at the open if the
stock gaps above the pivot. A setup whose low hits the stop first is a *failed
setup*, not a trade. Exits are the ones in `../pattern-detection/FINAL_LOGIC.md`:
the stop, or the first close below the 50 EMA. Trades still running are valued
at the 2026-10-01 close. 5- and 20-session bases are run as sensitivity checks.

**Test B: next-open buy** (does the list carry any edge at all?). Buy every
setup at the next session's open and sell at the close 5, 10 or 20 sessions
later.

**Test C: your own detector.** `detect_vcp` from `../pattern-detection`
(unchanged) is run on each setup as of its list date. This asks whether the names
are VCPs by your rules, and if they are, where the real pivot and stop are.

## Results

### Test A: breakout trade (10-day base), 169 setups

| Outcome | Setups |
|---|---:|
| Broke out → traded | 109 (64%) |
| Broke the stop **before** breaking out (median 4 sessions) | 37 (22%) |
| Neither within 10 sessions | 12 |
| Listed too recently to tell | 11 |

| 109 trades | Count | Net per trade |
|---|---:|---:|
| Stopped out | 21 | −10.2% avg |
| Closed below the 50 EMA | 21 | −7.0% avg |
| **Still open** at 2026-10-01 | 67 | +4.2% avg (38 in profit) |
| **All 109** | | **−0.76% mean** (95% CI −2.6% … +1.2%), median −3.4%, 34.9% in profit |

At ₹1.5L a trade the 109 trades total **−₹1.24L** so far.

- **Every closed trade so far is a loss.** That is partly how the exit works:
  winners have not hit their 50 EMA yet, so the closed set contains only the
  failures. The verdict on Test A is **provisional**. It depends on 67 open
  positions, and on whether the winners (SVRL +28%, SHILPAMED +27%,
  SIGMAADV +24%, FILATEX +23%, WELSPUNLIV +22%) keep their gains.
- **The stops are wide.** The median distance from pivot to stop is **13%**,
  against 3.6% for the VCPs in `FINAL_LOGIC.md`. These are volatile small caps
  (ADR 4–8%), and a 10-day range on them is not a tight contraction.
- Sensitivity: a **5-day base** (tighter, 6.8% stops) is worse: −1.1% mean,
  28% winners, and 67 of 169 setups break down before triggering. A **20-day
  base** (26% stops) is flat: +0.1% mean, mostly still open. **No version
  shows an edge** that a confidence interval clears.

### Test B: next-open buy, net of costs

| Hold | Setups | Mean | 95% CI (by list date) | Median | Winners |
|---|---:|---:|---|---:|---:|
| 5 sessions | 154 | +0.56% | −0.56% … +1.69% | +0.41% | 53% |
| **10 sessions** | 135 | **+1.76%** | **+0.38% … +3.17%** | +0.72% | 56% |
| 20 sessions | 91 | +1.61% | −0.15% … +3.34% | +0.19% | 52% |

Over the next 10 sessions, the median best point was +7.0% above entry and the
median worst point −5.3% below.

- The 10-session hold is the only figure whose interval clears zero, and only
  just. The mean is pulled up by a few large winners; the median is +0.7%.
- **Without a benchmark this cannot tell picking from the market.** Small caps
  in Aug–Sep 2026 might have done the same. Noted, not pursued.
- By rank (10-session, descriptive only, small samples): ranks 1–3 +0.5%,
  4–6 +4.1%, 7–10 +0.8%. **The channel's top-ranked names did not do best.**
  Inside-day names did no better (+1.5% vs +1.8%).

### Test C: are they VCPs at all?

- Your detector sees a VCP on the list date for **12 of 169** setups
  (**7.1%**). On **every** day of the same period, for the same 163 names, it
  fires 6.6% of the time. **Being on the channel's list barely changes the
  odds that a name is a VCP by your rules.**
- Only 2 of the 12 pass the `FINAL_LOGIC.md` gates (3+ contractions, stop ≥ 0.5
  ATR). The 10 that traded on the detector's own levels: −1.8% mean, 4 winners,
  6 still open.

## Verdict, as of 2026-10-01

**Not demonstrated to work.**
- Traded as VCP breakouts, the Mainboard setups are slightly negative after
  costs, and the 95% interval includes zero.
- Bought blind, they made a small positive 10-session return that no benchmark
  has been checked against.
- By your own detector, they are no more VCP-like than a random day for the
  same stocks.

The list reads as **a momentum screen** (stocks near their highs with strong
RS), not a VCP detector.

## Follow-up: why the winners won, and exits

See [WINNERS_AND_EXITS.md](WINNERS_AND_EXITS.md) (2026-10-04, channel data
only, exploratory). In short:
- The winners were stocks **already breaking out**: at a 52-week high, on
  rising volume, with a strong 6-month trend. Tight, quiet VCPs did no better.
- The first 1–5 days say **nothing** about the rest of a trade.
- Every price stop and trailing stop tested made results **worse**.
- A plain **10-session time exit** roughly doubles return per capital-day.

## Known gaps

- **Two months, one market.** 169 setups from 40 sessions. Treat everything
  here as provisional, especially Test A's 67 open trades.
- **The pivot and stop are proxies.** The channel publishes neither. The
  10-day window is a choice, so 5 and 20 are reported too. Test C uses your
  detector's levels where it finds a base.
- **Five NSE names were priced from their BSE listing** (AVG, HALDYNGL,
  KILITCH, ORBTEXP, PPL). They are NSE trade-to-trade (`BE`) ids, and the charts
  endpoint refuses those with DH-905. Same ISIN, same shares. See
  `data/bse_substitutes.csv`.
- **HEG** is listed under its old symbol and was mapped to **HEGAM** (same
  ISIN).
- BSE-listed names are costed with the NSE rate card. The difference is a few
  rupees a trade.
- Daily bars only. The order of a stop touch and a breakout on the same day is
  unknown, so it is resolved pessimistically (the stop is assumed to come
  second, and is therefore hit).

## Layout and re-running

```
data/raw/                 the 40 sheets, as posted (data/manifest.json: Telegram doc ids, post times, sha256)
data/setups_mainboard.csv every Mainboard row, with its Dhan security id
data/bars/                daily bars from 2025-03-01, one CSV per instrument
out/setups_scored.csv     every row, counted flag, Test A (3 windows) and Test B
out/detector_check.csv    Test C per setup
out/summary.json, out/detector_summary.json
```

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/parse_sheets.py
(cd ../../backend && CONFIG_PATH=conf .venv/bin/python ../experiments/vcp-channel-audit/scripts/download_bars.py --force)
.venv/bin/python scripts/analyse.py
.venv/bin/python scripts/detector_check.py
```

**Adding new sheets:** the channel's files were pulled from Telegram Web's own
page cache, because clicking each file gave one browser download per file.
Each file row in the channel's Files tab carries `data-doc-id`, and the cached
copy sits under `caches.open('cachedFiles')` as `document_<id>`. Put new files
in `data/raw/` and re-run all four steps. Re-run the bar download with
`--force`, or the existing files will not get the new days.
