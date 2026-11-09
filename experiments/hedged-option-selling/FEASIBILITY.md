# FEASIBILITY — the three things that had to exist first

The handoff set a gate: option price history, a margin model and an NSE F&O
charge card must have an established path before a single strategy rule is
written. It budgeted honestly for the possibility that the answer was "not
obtainable at acceptable quality, here is what it would take."

**All three are obtainable, and the price history is at the best-realism tier —
real traded option prices, not synthesis.** Probed 2026-09-20. Every claim below
was verified by fetching, not by recollection.

---

## 1. Option price history — SOLVED, tier 1 (real prices)

The handoff ranked three paths. The best one works.

### What is reachable

NSE publishes the F&O bhavcopy publicly, with no authentication. `www.nseindia.com`
refuses a scripted request (403), but the **archive hosts do not** — they answer a
plain `curl` with a browser user-agent. Two formats:

| Format | Host / path | Coverage (probed) |
|---|---|---|
| **UDiFF** | `nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_<YYYYMMDD>_F_0000.csv.zip` | **2024-01-01 → 2026-09-17** (present) |
| **Legacy** | `archives.nseindia.com/content/historical/DERIVATIVES/<YYYY>/<MON>/fo<DD><MON><YYYY>bhav.csv.zip` | **2016-01-01 → 2024-07-05** |

Boundaries are sharp and were probed, not assumed. Legacy stops at the UDiFF
cutover (2024-07-05 answers 200, 2024-07-08 answers 404). Before 2016-01-01 the
legacy host answers **403, not 404** — a retention wall rather than a holiday, so
2015 and earlier is simply gone from this source.

Together: **2016-01-01 → 2026-09-17, ~2,650 sessions, unbroken.**

### What each row carries

Per contract per session, from the UDiFF format:

- `OpnPric / HghPric / LwPric / ClsPric` — daily OHLC of the **option itself**
- `SttlmPric` — the exchange settlement price
- `UndrlygPric` — the underlying at settlement, **in the same row** (no join)
- `OpnIntrst`, `ChngInOpnIntrst`, `TtlTradgVol`, `TtlTrfVal`
- `TtlNbOfTxsExctd` — executed trade count, the honest liquidity filter
- `NewBrdLotQty` — **the board lot size, in-band**

That last field matters more than it looks. SEBI raised index contract value in
November 2024 and lot sizes changed with it. Because the lot size is *in the
data*, the backtest never has to hardcode it across the structural break.

### The overlap validation

2024-01-01 → 2024-07-05 is served by **both** formats. The legacy parser was
checked against the UDiFF parser on three sessions spanning that window:

| Session | UDiFF rows | Legacy rows | Common | Close mismatches | Settle mismatches | OI mismatches |
|---|---|---|---|---|---|---|
| 2024-01-02 | 57,054 | 57,054 | 57,054 | 0 | 0 | 0 |
| 2024-04-01 | 35,965 | 35,965 | 35,965 | 0 | 0 | 0 |
| 2024-07-01 | 32,106 | 32,106 | 32,106 | 0 | 0 | 0 |

Identical contract sets, identical prices, identical open interest. The splice at
2024-07-05 is safe. `scripts/validate_bhavcopy.py` re-runs this.

### What legacy does NOT carry

Honest gap, and it is why the two sources are tagged by `source` in the cache
rather than silently merged:

- **no `UndrlygPric`** — recovered from the index close file (below)
- **no executed-trade count** — liquidity before 2024-07 must be judged on
  open interest and contract volume alone
- **no lot size** — must come from a dated table, not from the row
- **volume is a CONTRACT count, not units** — a different denominator. Stored as
  given and flagged, never compared to a UDiFF volume without conversion.

### Underlying and India VIX

`nsearchives.nseindia.com/content/indices/ind_close_all_<DDMMYYYY>.csv` returns
every NSE index for that session with full OHLC — including **Nifty 50, Nifty
Bank and India VIX**. Verified present at 2016-01-04 and 2025-08-01. This is the
VIX history the handoff wanted as a regime anchor, and it covers the same span as
the option data.

### Consequence: the Black-Scholes trap is avoided entirely

The handoff's longest warning was about synthesising option prices — that pricing
with realised volatility erases the variance risk premium and pricing with fixed
IV assumes the answer, and that a flat-IV model misprices the wing, which is the
one thing this project is about.

**None of that applies.** Prices are real and traded. The variance risk premium,
the skew and the wing's true cost are all *in the data*, not modelled.

Black-Scholes is still used, but only in two places that cannot contaminate P&L:

1. **backing IV out of a real traded price**, to select strikes by delta and to
   reprice under margin scenarios;
2. **the synthetic ground-truth harness**, where the right answer is known by
   construction.

P&L is always computed from real prices.

### Cost of acquisition

Measured, not estimated: ~0.2 s per session per feed. 2,650 sessions × 3 feeds
(bhavcopy, index closes, volatility) ≈ **40 minutes, once**, and the cache is
incremental — a `fetch_log` row per (session, feed) means a holiday is remembered
as a holiday and never refetched. Filtering to index underlyings collapses ~2.5 GB
of raw zips to a few hundred MB of SQLite.

The handoff said "budget for it honestly — this is a data-engineering project
before it is a strategy project." It is not. It is an afternoon.

---

## 2. Margin model — SOLVED, sourced, and date-varying

This was the item most likely to end in an invented number. It does not, because
**NSE publishes its own risk inputs daily**.

### The unlock: FOVOLT

`nsearchives.nseindia.com/archives/nsccl/volt/FOVOLT_<DDMMYYYY>.csv` gives, per
underlying per session:

> `Applicable Daily Volatility (M) = Max (E or K)` — the greater of the
> underlying's and the futures' EWMA daily volatility

Verified present at **2016-01-04 and 2026-09-17** — the same span as the option
data. This is the exact σ that NSE's own price scan range is defined on. Using it
makes the margin model *sourced* rather than *invented*, and makes it **expand in
a crisis exactly when the strategy is losing** — which is the whole reason a
naive fixed-percentage margin model flatters this kind of strategy.

### The rules that consume it

| Parameter | Value | Source |
|---|---|---|
| Price scan range, **index** | 3σ of applicable daily volatility | NSE Clearing, NSCCL SPAN |
| Price scan range, **stock** | 6σ × √2, min 14.2% of underlying | NSE Clearing, NSCCL SPAN |
| ELM, **short index options** | **2% of notional**; 3% deep-OTM; 5% long-dated | NCL Risk Management FAQ Q12 |
| ELM, **short stock options** | 3.5%; 5.25% deep-OTM | NCL Risk Management FAQ Q12 |
| ELM, **expiry-day add-on** | **+2%** on all short index options, from 2024-11-20 | NCL FAQ Q12; SEBI Oct-2024 measures |
| Net option premium | included in upfront margin **from 2025-02-10** | NCL FAQ Q55 |
| Calendar spread benefit | **withdrawn on expiry day** for index, from 2025-02-10 | NCL FAQ Q33 |

Quoted verbatim from the NCL FAQ (Q12):

> "ELM shall be (i) 2% of the notional value of gross open positions in futures
> contracts; (ii) 2% on short index options contracts, other than deep-out-of-the-money
> option contracts where it shall be 3% and for long dated index option contracts
> where it shall be 5%"

> "On the day of options contracts expiry, to increase the tail risk coverage, an
> additional Extreme Loss Margin (ELM) of 2% shall be levied on all short index
> options contracts at the start of the day as well on short index options
> contracts initiated during the day that are due for expiry on that day."

### The model, and what it is not

A SPAN-style scenario model: reprice both legs across the 16 SPAN scenarios
(underlying ±price-scan-range at several fractions, volatility ±vol-scan-range),
take the worst portfolio loss, add ELM on the net short leg, cap at the
structure's true maximum loss.

Both legs are repriced, so **the wing's margin benefit is modelled rather than
assumed** — which the handoff correctly identified as the entire economic reason
a retail seller buys it.

**Labelled `APPROXIMATION`, in the `conf/` convention, with source URL, as-of
date and confidence marker.** It is not real SPAN. It differs from the exchange's
number because:

- NSE's actual par files (`nsccl.<date>.s<n>.zip`) are **not archived** — probed,
  404 at every path tried. Only today's parameters are practically obtainable,
  so a historical backtest cannot use true SPAN at all. This is a hard limit, not
  a shortcut.
- Inter-month and inter-commodity spread charges are not modelled.
- The volatility scan range is a parameter, not a published daily figure, and is
  therefore swept rather than asserted.

Direct calibration against a broker's live margin calculator was attempted and
**abandoned**: Zerodha's endpoint sits behind a Cloudflare bot challenge, and
working around bot detection is out of bounds. The model is therefore calibrated
against *published rules*, not against observed broker output, and that
limitation is stated on the dashboard rather than buried here.

---

## 3. NSE F&O charge card — SOLVED, and it has THREE regimes, not two

The handoff flagged the October 2024 STT rise as a structural break. There is a
**second one it did not know about**, and it is larger.

### STT on option sales — the seller's cost, and it tripled

| Effective | Sale of option (on premium) | Option exercised (on intrinsic) | Sale of futures |
|---|---|---|---|
| until 2024-09-30 | 0.0625% | 0.125% | 0.0125% |
| **2024-10-01** | **0.1000%** | 0.125% | 0.02% |
| **2026-04-01** | **0.1500%** | **0.150%** | 0.05% |

A premium seller pays STT **on the sell side, on the premium** — that is, on
revenue, at entry. Between the start of this sample and today that charge has gone
from 0.0625% to 0.15%: **2.4×**. Any result quoted over a span crossing either
date is an average of different markets, and the April 2026 regime is the only one
that describes trading this strategy *now*.

### Exchange transaction charges

| Effective | NSE options (on premium) | NSE futures (on turnover) |
|---|---|---|
| until 2024-09-30 | 0.0495% (top slab) | 0.00188% |
| **2024-10-01** | 0.03503% | 0.00173% |
| current (as of 2026-09) | 0.03553% | 0.00183% |

Driven by SEBI circular `SEBI/HO/MRD/TPD-1/P/CIR/2024/92` (2024-07-01), which
required MIIs to charge uniformly rather than by volume slab. The pre-Oct-2024
figure is the retail-applicable slab and is marked `APPROXIMATION` for that reason
— the true pre-2024 charge depended on the *broker's* monthly turnover, which is
not knowable from here.

### The rest

- **Brokerage** — ₹20 per executed order (flat, options). Four legs per iron
  condor round trip = 8 orders.
- **SEBI turnover fee** — ₹10 per crore of premium turnover.
- **Stamp duty** — 0.003% (₹300/crore) on the **buy side only**.
- **GST** — 18% on (brokerage + SEBI charges + transaction charges).

All of it goes in `conf/charges/nse-index-options.yaml` with source URL, as-of
date and confidence marker, following the repo's existing convention. Asserted
against a published broker calculator, with **any divergence recorded, not tuned
away** — the same discipline the VCP charges work used.

---

## Scope consequence: index only, and why

**Single-stock options are excluded.** They are physically settled: a short leg
left in the money at expiry is a *delivery obligation*, not a cash difference.
The handoff called this a first-order hazard and required it be modelled or the
cases excluded loudly. Modelling delivery — margin escalation from E-4, the
auction risk, the cash outlay — is a project of its own, and half-modelling it
would produce exactly the confident nonsense this study exists to avoid.

Excluded, loudly, here and in the README.

**BANKNIFTY is collected but is not the primary.** SEBI's late-2024 rules left one
weekly expiry per exchange, and NSE kept NIFTY's. BANKNIFTY's weekly series ends
mid-sample, which makes a continuous weekly study of it impossible across the
break. NIFTY is the primary; BANKNIFTY is a robustness check on monthlies.

---

## What is still unverified

Stated plainly, and carried onto the output rather than left here:

1. **The volatility scan range** is not published daily. Swept, not asserted.
2. **Intraday management cannot be tested.** This is daily data. Entry and exit
   are at settlement or close. The "defend the tested side" school, stop-losses
   on a spread, and 0DTE intraday management are **out of reach of this data**,
   and any claim about them from this study would be invented.
3. **Bid-ask spreads are not in the bhavcopy.** Modelled from the day's own
   high/low and trade count, as an explicit, swept assumption — never assumed
   to be mid.
4. **Pre-2024-07 liquidity** is judged on open interest and contract volume only,
   because the legacy format carries no trade count.

---

## Verdict

The gate is passed on all three counts, and on the first count at the tier the
handoff called "the realistic path" rather than the dangerous one.

Proceeding to charges, margin and the synthetic ground-truth harness.
