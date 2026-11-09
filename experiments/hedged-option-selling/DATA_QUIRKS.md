# DATA QUIRKS — verified facts about the NSE bhavcopy

Every entry here was **verified against the data**, not recalled. Two of them
silently changed the answer before they were found, and one produced a perfect
equity curve. They are recorded in the style of the repo's root `CLAUDE.md`
table: changing code that depends on them without re-verifying will produce
silently wrong numbers.

---

## 1. On EXPIRY DAY, `SttlmPric` is the UNDERLYING's settlement, not the option's

**The most dangerous item in this file.**

On any ordinary session, an option row's `SttlmPric` is that option's own
settlement price. **On its expiry day it is not.** It becomes the settlement
value of the *underlying*, written identically into every row of the chain.

```
2026-03-24, NIFTY expiring that day:
  22800 PE  close   0.75   settle 22912.40
  23000 PE  close  93.25   settle 22912.40
  23400 PE  close 491.85   settle 22912.40
  24000 PE  close 1094.05  settle 22912.40      <- NIFTY settled at 22912.40
```

Verified across **all 434 expiry sessions** in the cache: the value is identical
across strikes in 434/434, and equals the bhavcopy's own `UndrlygPric` in 142/142
of the sessions that carry one. In the early legacy era it is simply `0.0`.

### Why it is lethal

Marking a multi-leg structure at a price that is the same for all legs makes the
legs cancel exactly:

```
(short_put + short_call − long_put − long_call) × qty  =  0
```

So every expiry settles at **zero cost to close**, every trade books its **full
credit**, and no trade can ever lose.

That is precisely what happened. Before this was found, the backtest reported:

| | before the fix | after the fix |
|---|---|---|
| win rate (hold to expiry, 2024-2026) | **100.0%** | 84.4% |
| worst trade | **+₹499** | **−₹51,824** |
| total P&L | ₹140,228 | ₹61,594 |

It reported a **100% win rate through March 2026, a month in which NIFTY fell
11.3%**. The trade spanning that month booked a full win; it should have lost —
and after the fix does lose — essentially its entire maximum loss (−₹51,824
against a maximum of ₹51,650).

This is exactly the failure mode the handoff warned about: a smooth equity curve
with a perfect win rate and no disaster in the sample.

### The fix

`chain.settlement_value()` establishes the underlying settlement with an explicit
precedence, and expiring legs are then priced at their **intrinsic** against it:

| source | used when | exactness |
|---|---|---|
| `underlying` | UDiFF's own `UndrlygPric` | exact |
| `settle` | expiry-day `settle`, where non-zero | exact |
| `parity` | median of `K + C − P` over strikes where both sides traded | approximate |
| `None` | nothing usable — **refused**, counted in `settlement_faults` | — |

The `parity` branch is needed for the early legacy era. It is approximate
because the closes are last-traded prices struck at 15:30 while NSE settles on
the 15:00–15:30 average of the index. Which branch was used is **counted and
reported** on every run, so a result resting on the approximate one says so.

---

## 2. The legacy format carries no lot size, and a date table will not do

The legacy bhavcopy has no board-lot column. Before this was handled, `build()`
refused every chain whose `lot_size` was `None` — which **silently skipped
2016-01-01 → 2024-07-05: 8.5 of the 10.7 years, including COVID.** The backtest
ran, reported 47 trades, and never mentioned that it had discarded 80% of the
sample. The remaining window was the calmest part of it.

### Why a date-keyed table is wrong

NIFTY's lot size changed four times inside the sample, and **a change applies to
newly introduced expiries only** — existing contracts keep the lot they were
listed with, so two different lot sizes are live on the same day:

| month | lot sizes live simultaneously |
|---|---|
| 2024-03 | 50 |
| 2024-04 | **25 and 50** |
| 2024-11 | **25 and 75** |
| 2025-01 | **25 and 75** |
| 2025-10 | **65 and 75** |
| 2026-01 | 65 |

A date-keyed table would mis-size every position held across a change.

### The derivation, and the quirk that makes it work

Legacy carries `CONTRACTS` (a lot count) and `VAL_INLAKH` (turnover), so the lot
can be recovered. The non-obvious part is **what option turnover is measured
on**. It is notional, and the notional is **strike plus premium**:

```
VAL_INLAKH × 100000  =  contracts × lot × (strike + premium)
```

Checked against UDiFF's own `NewBrdLotQty` on 2024-03-01 across 279
(symbol, expiry) groups covered by both formats:

| basis | median error | worst |
|---|---|---|
| **strike + premium** | **0.018%** | **0.50%** |
| strike only | 2.20% | 12.52% |
| premium only | useless — implied "lots" in the thousands |

Taken as the **median** over every contract of an expiry that traded, not the
single largest: one contract with a small premium amplifies any rounding in the
published turnover.

### Accuracy of the result

Measured against UDiFF on four overlap sessions:

| | wrong | of |
|---|---|---|
| **index symbols** (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY) | **0** | **116** |
| all symbols including single stocks | 99 | 1,382 |

The single-stock misses are all within ~2%, and come from board lots that are not
round at all (1375, 1925, 4462, 7100) being pulled onto a magnitude grid they
were never on. Single-stock options are out of scope for this study, and on the
index — the only thing traded here — the derivation is **exact**.

---

## 3. An untraded strike still gets a row, and its close is stale

`ClsPric` on a contract that did not trade is a carry-forward. `SttlmPric` is the
exchange's theoretical value. They diverge badly:

```
2025-08-01, NIFTY 07-Aug 23550 CE — close 1297.10, settle 1042.05, volume 0
```

A 24% gap. Consequences, enforced in `chain.py`:

- **`close` is only trusted when the contract actually traded that session.**
  Entering on a stale close fills you at a price nobody was quoting, and biases
  a far-OTM premium seller towards selling wings at yesterday's richer price.
- **`settle` is used for daily marking**, because it is defined for every
  contract — but *not* on expiry day, per quirk 1.

---

## 4. The legacy format's `CONTRACTS` is a LOT count, UDiFF's `TtlTradgVol` is UNITS

Two different denominators with the same name in ordinary speech. Stored as
given and tagged by `source`, never compared across the splice without
conversion.

---

## 5. Legacy carries no executed-trade count

UDiFF has `TtlNbOfTxsExctd`; legacy has nothing equivalent. The liquidity gate
therefore falls back to contract volume on the older 8.5 years of the sample.

This is a **real weakening** of the filter on the majority of the history, and it
runs in the dangerous direction: the far-OTM wing is exactly where the book is
thinnest, and before 2024-07 there is no way to tell a wing that traded 5,000
times from one that traded twice.

---

## 6. `UndrlygPric` is UDiFF-only

The legacy format has no underlying price column. Recovered from put-call parity
on the day's own chain instead, which is better anyway — it absorbs dividends,
the repo rate and any basis into an observed number.

Sanity check, NIFTY 2025-08-01, 27 DTE: spot 24,565.35, recovered forward
24,635.65, implied carry +70.30 points ≈ 3.9% annualised.

---

## 7. Format and host boundaries

Probed 2026-09-20; sharp, and not what a guess would produce.

| | boundary | behaviour outside |
|---|---|---|
| UDiFF | 2024-01-01 → present | 404 before |
| legacy | 2016-01-01 → 2024-07-05 | **403** before 2016 (retention wall), 404 after the cutover |
| `www.nseindia.com` | — | **403** to a scripted request |
| `nsearchives.nseindia.com`, `archives.nseindia.com` | — | answer a plain `curl` with a browser user-agent |

The 403-vs-404 distinction matters: 404 is a holiday or a file that never
existed, 403 is the archive refusing. They are handled as different outcomes
(`NotPublished` vs `Blocked`) so a retention wall is never silently recorded as a
market holiday.
