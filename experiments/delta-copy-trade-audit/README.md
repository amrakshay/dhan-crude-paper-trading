# Copy-trade leader audit (Delta, Shark)

**Question (2026-09-28):** someone runs an automated strategy on Delta Exchange
India and says it makes good returns. Given his closed-trade export, what does
each trade make **after Delta's fees**, what is the P&L month by month, and what
does a $500 start turn into?

This is a read-only analysis of a CSV. It places nothing and calls no API.

## Run

```bash
../hedged-option-selling/.venv/bin/python scripts/analyse.py          # both leaders
../hedged-option-selling/.venv/bin/python scripts/analyse.py 6003     # one
open out/leader-6001/dashboard.html out/leader-6003/dashboard.html
```

Each leader is a `LEADERS` entry in `scripts/analyse.py`: its export, its
exchange's fee schedule, and whether its Exit Price and exit times can be
trusted. Outputs in `out/leader-<id>/`: `trades_net.csv` (per trade),
`monthly.csv`, `summary.json`, `dashboard.html`.

| Leader | Venue | Export | Rows | Range |
|---|---|---|---:|---|
| 6001 | Delta Exchange India | `leader_6001_closed_trades_1790595548209.csv` | 2,109 | 2024-09-24 → 2026-09-28 |
| 6003 | Shark Exchange | `leader_6003_closed_trades_1790596642637.csv` | 136 | 2026-05-07 → 2026-09-22 |

## Leader 6001 (Delta) — result

| | USD |
|---|---:|
| Gross P&L (the export's own figure, 2,100 trades) | +1,552.38 |
| Fees, taker on both sides + 18% GST | 973.81 |
| **Net P&L** | **+578.57** |
| Net if every fill had been maker (best case) | +1,170.40 |
| $500 becomes | 1,078.57 (+115.7%) |
| Lowest equity | 146.37 (2026-06-22) |
| Max drawdown | −859.18 (2026-06-22) |
| Win rate, gross → net | 55.6% → 50.1% (115 winners turned into losers by fees) |

Fees took **63% of the gross profit**. By type:

| Type | Trades | Gross | Fees | Net |
|---|---:|---:|---:|---:|
| Put options | 360 | +604.02 | 178.30 | **+425.73** |
| Futures (crypto + tokenised stocks) | 1,071 | +728.98 | 431.77 | **+297.21** |
| Metal futures (XAUT, PAXG, SLVON) | 163 | +111.10 | 125.74 | **−14.64** |
| Call options | 506 | +108.27 | 238.00 | **−129.73** |

Nearly all of the net profit is from Aug–Sep 2026 (+$780 of the +$579 total).
Sep 2025 to Mar 2026 lost money in every month, and Jun 2026 alone lost $410.
Nov 2024 to Jul 2025 has almost no trades, so this is not a continuous two-year
record.

### The $500 figure is not what $500 would have made

The quantities are the leader's own. At peak he had **$32,302 of futures
notional** open (65× $500) and had written **$161,260 of underlying in short
BTC calls** (2 BTC of C-BTC-80400-030526, 323× $500), with up to 105 positions
open at once. $500 could not have carried that. The dollar P&L is his; the
equity curve from $500 only shows the shape. Scaling down would keep the fee
*percentages* but Delta's minimum lot sizes would drop many of the small trades.

### Fee model (what is verified and what is not)

Source: <https://www.delta.exchange/fees> and support article 80001177864,
read 2026-09-28.

| Product | Taker | Maker | Basis |
|---|---|---|---|
| Futures / perpetuals | 0.05% | 0.02% | qty × price, each side |
| Gold/silver futures | 0.05% | 0.01% | qty × price, each side |
| Options | 0.03% | 0.01% | qty × spot, **capped at 3.5% of premium** |
| All | +18% GST on the fee | | |

- **Taker on both sides**, the user's choice for an automated bot.
- **The current schedule is applied back to 2024.** Historical rates were not
  published; the fees page shows a 10% option cap struck through to 3.5%, so
  older option trades may have paid more. UNVERIFIED.
- **Options maker rate** is 0.01% on the fees page and worked example, but
  0.03% on other Delta pages. It only affects the maker best case.
- **Spot proxy:** the export has no spot price, so the option fee's notional
  uses the strike. On 523 of 866 option trades the 3.5% premium cap binds at
  entry regardless.
- **Settlement:** 57 options were settled at 17:30 IST on expiry day. Their exit
  fee is the taker formula on the settlement price, which is zero for an option
  that expired worthless.
- **Funding is NOT modelled.** Perpetual funding isn't in the export and could
  move the result either way. Median hold is ~19 h, the longest 50 days.
- **9 exact-duplicate rows dropped** (gross −$13.66 between them). They look
  like an export artefact.
- **Gross P&L is the export's figure.** It equals side × qty × (exit − entry)
  on every row except 9 micro-price coins (PEPE, NEIRO, 1000SATS, DOGS, NOT),
  whose prices are rounded in the export.
- Timestamps are IST: options settle at 17:30, which is Delta India's expiry.

## Leader 6003 (Shark) — result

| | USD |
|---|---:|
| Gross P&L (the export's own figure, 136 trades) | +875.63 |
| Fees, taker on both sides + 18% GST (estimated) | 33.28 |
| **Net P&L** | **+842.34** |
| Net if every fill had been maker | +862.31 |
| $500 becomes | 1,342.34 (+168.5%) |
| Max drawdown | −16.70 |
| Win rate, gross → net | 89.7% → 89.0% |

| Month | Trades | Net |
|---|---:|---:|
| 2026-05 | 20 | −2.39 |
| 2026-06 | 27 | +222.84 |
| 2026-07 | 32 | +217.25 |
| 2026-08 | 33 | +275.39 |
| 2026-09 (to the 22nd) | 24 | +129.26 |

Fees barely matter here: 136 trades of ~$200 notional each is too little
turnover for 0.04% to add up. What matters is whether the export is true.

### Why this export cannot be taken at face value

- **Entry time equals exit time on all 136 rows.** Hold periods and
  concurrency are unknowable, so unlike 6001 there is no exposure check.
- **"Exit Price" is not the exit.** It is on the losing side of entry on
  136/136 rows (below it for a Buy, above it for a Short) while 90% of the trades
  won. It reads as the stop-loss, median 7.9% from entry. Exit fees are
  charged at the entry price instead.
- **If that is the stop**, the median trade risked $15.76 to make $6.08. A
  90% win rate built that way is normal *until* the stops get hit. There are
  14 losers; the worst is −$16.42 (about one median stop) and the next three
  are −$8.56, −$6.77 and −$5.95. So 4.5 months have produced one loss the
  size of a typical stop, which is too few to say how often they come.
- **5 rows' Qty cannot have produced their P&L** (MU 0.02 × $961 making
  $24.69 needs a +128% move). Their true size was larger, so their fees are
  understated; at 0.047% a side the error is small.
- **The style changed after May.** May: 20 gold/oil trades, both
  directions, net −$2. June onward: every one of 116 trades is a LONG, 99 of
  them US stock perps (MU, CBRS, INTC, AMD, PLTR, HOOD…) and 17 commodities.
  All the profit is from a long-only book over one four-month stretch.
- Shark's zero-fee scalping (closing fee waived inside 30 min) is NOT applied;
  hold times are unknown. Whether stock/commodity perps pay the crypto futures
  rate is not stated on Shark's fee page (assumed, unverified).
