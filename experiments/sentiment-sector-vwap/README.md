# sentiment-sector-vwap — live paper experiment

A command-line process that runs during NSE hours, paper-trades with virtual
money, and sleeps the rest of the time. It places no orders anywhere: its
"orders" are rows in a CSV.

1. **Every 5 minutes from 09:15 to 15:30.** It reads the NIFTY OI market
   sentiment from <https://stockmojo.in/open-interest/nifty> (Bullish/Bearish
   and the %).
2. **Every 5 minutes from 09:45.** It reads <https://stockmojo.in/intraday-booster>
   and picks a sector: the most bearish if sentiment is Bearish, the most
   bullish if Bullish. It then tracks the **top 3 names** in that sector,
   always 3. The list is recomputed every cycle.
3. **For the 3 tracked names, on 5-minute candles**, it applies a **9 EMA**
   and a **session VWAP**:
   - **Bullish, long:** enter when the 9 EMA crosses above VWAP. The stop is
     the crossover candle's low.
   - **Bearish, short:** enter when the 9 EMA crosses below VWAP. The stop is
     the crossover candle's high.
   - **Crossover already done when the name is picked up:** wait for a retest
     of VWAP and enter on the retest candle. The stop is that candle's low or
     high.
   - **After every candle:** the stop trails to the **9 EMA** (VWAP until
     2026-10-09; see below).
4. **At 15:00:** every trade is closed. No new entries from the candle that
   closes at 15:00.

## Running it

```bash
cd experiments/sentiment-sector-vwap
./start.sh --bg       # start in the background (recommended); output -> logs/console.log
./start.sh tail       # watch it
./start.sh status     # running? + last console lines
./start.sh stop
./start.sh            # or run in this terminal instead (Ctrl-C stops it)
./start.sh check      # one read of both pages + bars for the 3 names, then exit
```

Leave it running for days: it sleeps by itself outside 09:13–15:32 and on
weekends, and says "still sleeping, alive" once an hour. Only one copy can run
(a lock in `logs/`). It runs under `caffeinate -i`, which stops idle sleep;
closing a laptop's lid still sleeps it unless it is on power with an external
display. Restarting mid-day resumes the open trades from `state_*.json`.

**What the console shows.** One line per 5-minute cycle, plus a line for each
PLAN-book entry and exit, each change to the tracked list, each sentiment
flip, and warnings (each distinct warning at most once per 15 minutes):

```
[10:00:17] 10:00 | BEARISH 70% PCR 0.71 | Nifty -0.73% | METAL -2.33% | NATIONALUM -4.7 ADANIENT -3.9 HINDALCO -2.9 | PLAN 2/2 ADANIENT S @2831.08 sl 2834.50 ltp 2829.10 +Rs69 ... closed +Rs0 | all: 2 taken 2 open +Rs0 | feed UP
[10:00:17] PLAN ENTER SHORT HINDALCO CROSS @ 919.74 (LIVE) stop 922.25 qty 108 risk 0.2228% angle 6.9deg  [2/2]
[10:05:16] PLAN EXIT  SHORT ADANIENT @ 2835.92 STOP net -Rs252 (-2.104R, 5 min)
```

If no new line has appeared for more than ~6 minutes during market hours, it
is stuck: check `logs/run.log`.

**Log files** (`logs/`, git-ignored): `console.log` (what the console showed,
for `--bg` runs), `run.log` (INFO, rotated daily, 30 days), `debug.log`
(everything, including every stop move and fetch; 20 MB x 5). The app's own
logging is detached in this process, so an error here never becomes a
Telegram alert from the trading app.

The script needs Playwright, which is installed into `backend/.venv` but
deliberately **not** in `backend/requirements.txt`, because the app does not
use it:

```bash
.venv/bin/pip install playwright && .venv/bin/python -m playwright install chromium
```

The Dhan token is the one saved on the app's Settings page. It is re-read
every cycle, because the running server rotates it when it renews.

Tests: `cd backend && .venv/bin/python -m pytest ../experiments/sentiment-sector-vwap/tests -q -n0`

## Two books: what you can trade, and the data to choose with

The operator can take **at most 2 trades a day**. Every day's signals run
through two books at once:

- **`plan`**: the first 2 eligible signals of the day (`plan.max_trades_per_day`).
  This is what you would actually have done.
- **`all`**: every eligible signal, one open trade per symbol, no daily cap.
  Never meant to be traded. It is the data that answers *which* 2 a day were
  worth taking.

"First 2" is only the starting selector. Choosing better needs the features
below, measured on many days of the `all` book.

## Output — `data/YYYY-MM-DD/`

| File | One row per |
|---|---|
| `sentiment.csv` | 5-minute OI sentiment reading (label, %, PCR, raw card text) |
| `tracking.csv` | 5-minute pick: sector, the 3 names, what was added or removed, every sector's % |
| `dhan_sentiment.csv` | 5-minute Dhan sentiment: label, score and its parts, PCR, OI flow, Nifty %, and stockmojo's reading of the same moment |
| `dhan_sectors.csv` | every sector and stock % from Dhan, every cycle (the numbers the pick used) |
| `booster.csv` | every stock in every sector on the booster, every cycle (tests other sector/stock rules later) |
| `signals.csv` | **entry signal**, with its features and what each book did (`TAKEN <id>` or `SKIP: <why>`) |
| `trades_plan.csv`, `trades_all.csv` | closed trade: the signal's features, then entry/exit, P&L, R, and excursions |
| `decisions.csv` | event: armed, setup change, entry, skip, stop trailed, exit (`book` column) |
| `bars.csv` | closed 5m bar evaluated, with EMA 9 and VWAP |
| `summary.json` | both books' totals |

`data/daily_pnl.csv` has one row per day per book.

**Features recorded on every signal** (and copied onto its trades):
- *Time:* time of day, minutes since open, minutes the name had been tracked.
- *Market:* sentiment and its %, PCR, sentiment flips so far today, Nifty's %
  on the day.
- *Selection:* sector and its %, the stock's rank in the sector (1–3), its
  booster %.
- *Angle:* the 9 EMA's and VWAP's slope (% per bar, and degrees on a chart
  where 1 bar = 0.1% of price), the angle between them, and the EMA's 3-bar
  slope.
- *Position:* the EMA−VWAP gap and close−VWAP distance, in % of price.
- *Candle:* range and body (%), where the close sat in the range, volume
  relative to the last 20 bars.
- *Volatility and risk:* ATR14 (%), risk to the stop in % and in ATRs.
- *The stock's day:* its % on the day and from the open, where it sits in
  the day's range, EMA/VWAP crosses so far today, and bars since the last
  cross.

**Outcomes added to each trade at 15:30** (the data an exit rule is tuned on):
- MFE and MAE in R, both inside the trade and up to 15:00.
- Whether the trade reached +1R, +2R or +3R before the initial stop. A candle
  that touches both is counted as the stop.
- The R if the trade had been held to 15:00.

If the process was down at 15:30, run
`CONFIG_PATH=conf .venv/bin/python ../experiments/sentiment-sector-vwap/scripts/run_live.py --enrich YYYY-MM-DD`
from `backend/`.

## How the rules were pinned down

These are the places where the brief left a choice. Each one is a config
value or a single function, so it can be changed.

- **Where the data comes from.** stockmojo's data API (`data.stockmojo.in`)
  returns 403 to anything except the page's own session. The script
  therefore drives a headless Chromium and reads the **rendered page**, which
  is the same text a person reads. Both pages were checked by hand on
  2026-10-07 against the scraper's output: Bearish 70%, PCR 0.71; METAL
  −2.33% the most bearish sector; NATIONALUM, ADANIENT and HINDALCO its top 3.
  The two sentiment figures differ: the booster page's own gauge said
  23% vs 77% while the OI page said Bearish 70%. As asked, **only the OI page
  decides the side**.
- **Not every bar on the booster is a sector.** NIFTY, MIDCAP, SENSEX,
  BANKNIFTY and FINNIFTY are index buckets and are excluded
  (`selection.exclude_sectors`). Otherwise NIFTY would be the "most bearish
  sector" whenever the whole market falls.
- **"Top 3 stocks".** The sector's gainers and losers are ranked together in
  the sentiment's direction. A bullish sector with only one gainer still
  yields 3 names: the gainer, then the next two strongest.
- **Neutral sentiment** tracks nothing (no direction), so no new entries.
  Open trades keep being managed. A failed page read keeps the last good
  list.
- **A name that drops off the list** stops being watched for entries. If it
  is in a trade, the trade runs to its stop or 15:00.
- **EMA** runs across previous sessions' bars, as on a chart, so it is valid
  at 09:45. It is seeded with an SMA, as Pine's `ta.ema` does. **VWAP**
  resets each session and uses the typical price (H+L+C)/3.
- **"Crossover already done".** If the EMA is already on the trend side at
  the last closed bar when a name joins the list, the setup waits for a
  **retest**. A retest is a bar that touches VWAP (low ≤ VWAP for a long) and
  closes back on the trend side. If the EMA crosses back the other way, the
  retest wait is dropped and the setup waits for a fresh crossover.
- **The stop trails to the 9 EMA, and only tightens** (`strategy.trail_with`).
  Until 2026-10-09 it trailed to VWAP, which barely moves late in the session
  and gives most of a move back. A trade keeps the trail it was opened under
  (`trail_with` column), and at 15:30 every trade also gets
  `r_trail_ema9_bars` and `r_trail_vwap_bars`: the same entry replayed under
  each trail on closed candles, so the two can be compared on identical
  trades. A long's stop never moves down; a candle closing beyond the trail
  line puts the price past its own stop and the trade exits at once.
- **Stops are taken live.** A separate Dhan Ticker WebSocket (Dhan allows 5
  connections; the app holds 1) watches open trades' LTP. A breach exits at
  that LTP. As a backstop, each closed bar is also checked. A bar that gaps
  through the stop fills at its open, not at the stop.
- **No trade before 10:00** (`schedule.no_entries_before`, added 2026-10-07).
  Setups still arm from 09:45. A signal on a candle that closes before 10:00
  is skipped. A crossover skipped that way counts as "already done", so the
  stock then waits for a VWAP retest. The candle closing at exactly 10:00 may
  trade, so the earliest possible entry is about 10:00:15.
- **Entries** go in at the live LTP when the signal candle has closed (or that
  candle's close if no tick has arrived). Each entry is refused if that price
  is already through the stop.
- **Limits:** the plan book takes at most 2 trades a day; the all book is
  uncapped. Both hold one open trade per symbol at a time. Each trade is
  sized at ₹1,00,000 notional (`sizing.capital_per_trade`).
- **Costs:** Dhan intraday charges (`conf/intraday_costs.yaml`, copied from
  `experiments/intraday-screener`) plus **5 bps slippage per side**.
- **Holidays:** if Dhan has no Nifty 50 bar for today by 09:30, the day is
  recorded as a holiday and the process sleeps until the next weekday.

## Where the data comes from (Dhan since 2026-10-09)

Signed out, stockmojo stops live data at about **10:00** behind a "Login
Required" banner: the OI page keeps its 10:00 sentiment, and the booster its
~10:05 stock percentages; only its sector chart stays live. That froze the
readings on 2026-10-08 and 2026-10-09. (After the close the full day shows
again signed out, which hid it at first.) So every DECISION now comes from
Dhan, live all day, through the token the app already holds:

| Decision | Source now | Checked against stockmojo |
|---|---|---|
| Sector % | the NSE sectoral index on Dhan (`selection.sector_indices`) | 2026-10-09: all 18 index-backed sectors within 0.00–0.24 pts at ~10:55, and 0.00–0.04 at 11:40 |
| Stock % | Dhan live LTP vs the previous OFFICIAL close (daily candle) | TCS etc. live; stockmojo's signed-out copy was frozen |
| Sector members | `conf/sector_members.yaml`, built from recorded stockmojo pages (`scripts/refresh_sector_members.py`) | that IS stockmojo's list |
| Sentiment | our rule on Dhan's NIFTY option chain (`svlib/market.py`, `sentiment_rule`) | see below |

**Sentiment.** stockmojo's card numbers are plain sums of the option chain
for the nearest expiry. PCR is put OI ÷ call OI (Oct 7: 4.62Cr ÷ 6.5Cr = 0.71,
exactly its figure), and its "PCR OI change" is the ratio of the two OI
changes (0.27, also exact). Its LABEL (Neutral 50 / Bullish-Bearish 70 /
Very 85) is its own unpublished scoring. Ours is a first guess in the same
shape: score = PCR level (−2..+2) + net new OI towards puts (−1..+1) +
Nifty's direction (−1..+1). Every component is recorded in
`dhan_sentiment.csv` next to stockmojo's reading of the same moment
(`stockmojo_*`, `agree`), so the thresholds can be tuned on real
side-by-side data. Until then, treat the labels as uncalibrated. Whether
stockmojo's PCR uses ±500 points of strikes or all of them is not verified;
both are recorded.

**Left out:** sectors with no index on Dhan (CHEMICALS, and the broad
NIFTY/MIDCAP/... buckets) are not picked, because a members' average is a
different number (CHEMICALS read 0.99 vs stockmojo's 0.42).

To go back to stockmojo for either decision, set `sentiment_source` or
`sector_source` to `stockmojo`. That needs a signed-in session:
`./start.sh stop; ./start.sh login; ./start.sh --bg` (a window opens, you sign
in yourself, then close it). Whether a free account lifts the gate is not
verified. stockmojo is still read every cycle and recorded (`sentiment.csv`,
`booster.csv`, with `login_gate`), for comparison only.

**Each morning** the run fetches the previous close of ~200 stocks and
indices (about a minute; cached in `prev_close.json` for restarts) and
subscribes them all on its own Dhan feed connection.

## Closing the laptop lid

Closing the lid sleeps the Mac and freezes the process; opening it resumes
it within a minute, with no restart needed. What happens to the time it was
asleep:

- **Missed cycles are not invented.** No sentiment or booster reading exists
  for them. The console prints `WARNING: missed N cycle(s) HH:MM-HH:MM`, and
  the gap is recorded in `decisions.csv` (event `GAP`) and in
  `data/YYYY-MM-DD/gaps.json`, so a hole in the data is never mistaken for a
  quiet market.
- **Open trades are caught up from the candles.** Every missed candle is
  checked against the stop, gap-aware, and the stop trails as usual. What the
  live feed would have caught mid-candle is taken at the candle's stop or
  open instead.
- **Signals from missed candles are not traded.** They are logged as `stale`,
  because entering late at a different price is not the rule.
- **Asleep through 15:00:** on waking, the missed candles up to 15:00 are
  checked for stops first. Anything still open exits at the **15:00 price**
  (the close of the 14:55 candle), stamped 15:00 and marked
  `SQUARE_OFF_LATE`. It is never filled at whatever price is live when the
  lid opens.
- **Asleep through the whole session:** that day is skipped, not recorded as
  an empty day.
- **What reconnects on its own:** the Dhan feed reconnects, and the hidden
  browser is restarted if it came back broken.

This was verified with a simulated sleep (a fake clock jumping 10:32→11:47
and 14:42→15:40 during a replay of 2026-10-07): the totals matched the
uninterrupted run exactly. **A real lid close has not been tested yet.**

## Known gaps

- **Not backtested.** This only measures going forward. A historical test is
  impossible from these inputs: stockmojo shows only the live sector picture,
  so the list cannot be rebuilt for past days without lookahead.
- **Dependence on the page.** If stockmojo changes its markup, the scraper
  reads nothing. That shows up as `ok=False` in `sentiment.csv` and as "read
  failed; list kept" in `tracking.csv`, not as wrong numbers. The selectors
  are `#sector-card-<NAME>` and the "Market Sentiment" card.
- **Setups are not persisted across a restart.** Open trades are. After a
  restart, a name whose crossover happened while the process was down is
  treated as "already crossed" and waits for a retest.
- **Dhan stops serving F&O names' 5-minute bars at 15:15** (NSE's closing
  auction session; see the root `CLAUDE.md`). That is after the 15:00
  square-off, so it does not matter here.
- **MIS margin** (per-stock VaR+ELM) is not modelled. Sizing is plain
  notional.
- **The runner was verified by replaying 2026-10-07** with a fake clock, real
  Dhan bars, and the page reads pinned to that evening's values. It produced
  6 trades: crossovers, retests, VWAP trails, bar stops, the 2-trade cap and
  the 15:00 square-off. The page reads at 09:45 would have differed, so **that
  replay's P&L means nothing**.
