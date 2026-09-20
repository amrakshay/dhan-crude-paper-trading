# Step 0 — what this repository's data can support

Answered before anything was built, from the data rather than from
assumption. Every number here is reproduced by

```bash
.venv/bin/python scripts/feasibility.py          # --refresh-master to re-download
```

which writes `out/feasibility.json`.

---

## 1. The decision, in one table

| Strategy | Needs | Status |
|---|---|---|
| **Pairs / statistical arbitrage** | two cash equities | **feasible — this is what gets built** |
| Index arbitrage | index futures history | **not feasible** — no history obtainable |
| Cash-futures basis (cash & carry) | per-stock futures history | **not feasible** — no history obtainable |
| Calendar spreads | two futures expiries over time | **not feasible** — same cause |
| ETF / NAV arbitrage | ETF prices + daily NAV | **not feasible** — neither is in the database |

---

## 2. Why futures history cannot be obtained

`dhan_charts_client` fetches `/charts/historical` for any instrument with a
`security_id`. A security_id for an **expired** contract can only come from
the instrument master. So the question reduces to: how many expiries does the
master carry?

The public detailed scrip master (`images.dhan.co/api-data/api-scrip-master-detailed.csv`,
35 MB, already in the application's read-only allowlist) was downloaded and
counted. Ignoring the `0001-01-01` null expiry used for cash and indices, and
the synthetic 2036 expiry on the `NSETEST` symbols:

| | rows | distinct expiries | span |
|---|---:|---:|---|
| NSE FUTSTK (single-stock futures) | 647 | **3** | 2026-09-29 .. 2026-11-23 |
| NSE FUTIDX (index futures) | 18 | **3** | 2026-09-29 .. 2026-11-23 |
| MCX FUTCOM | 157 | 45 | 2026-09-21 .. 2027-12-17 |

**Three.** Near, next and far month — the contracts that are live today.
CRUDEOIL has traded on MCX for more than a decade and the master carries not
one expired FUTCOM row either. The master is a *live* master by construction:
an expired contract is removed from it.

The application's own `instruments` table is narrower still. It ingests
`NSE_EQ` `EQUITY` rows only; the master's FUTSTK rows are read during the same
pass solely to set the `fno_eligible` flag, and are never stored
(`src/instruments/services/instrument_master_service.py`). So there is no
futures row in the database at all, live or expired — confirmed by the survey:
`NSE_EQ/EQUITY` 499 rows, `MCX_COMM/FUTCOM` 10, `MCX_COMM/OPTFUT` 1,230, and
nothing else.

### The most that could be stitched, and why it is not enough

Each NSE contract is introduced about three months before it expires, so the
three live ones between them cover roughly **late June 2026 to today** — about
one quarter. That window *shrinks by a month every month*, because the front
contract expires and leaves the master before the new far month has any
history. It is not a backtest dataset; it is a rolling three-month snapshot
that would have to be harvested forward from today for years before it became
one. It also needs a working access token, which the chart endpoint requires
and the equity panel did not.

### What was explicitly not done

**No futures series was synthesised from a carry assumption.** A cash-futures
basis backtest whose futures leg is `cash × (1 + r·τ)` measures the
assumption, not the market: the basis *is* the subject of that strategy, and
manufacturing it settles the question in advance. The handoff forbids this and
it is the right call.

---

## 3. The constraint that shapes everything that follows

**A short position in Indian cash equity cannot be carried overnight.** There
is no borrow in the ordinary cash segment: an unsquared short is auctioned in
by the exchange. Overnight shorts are available through SLB, which is thin,
and through single-stock futures, which exist for **210** of the 500 symbols
in the panel.

This is a *hard constraint on executability*, not a cost, and it lands
squarely on pairs trading — whose entire structure is long one name and short
another for days to weeks. It has three consequences, all carried into the
design:

1. The tradeable universe is the **210 F&O-eligible names**, not 500. Anything
   measured on the other 290 is a paper result with no execution behind it.
2. The short leg must be a **futures** short, which is the data we do not
   have. The futures leg is therefore *modelled* — priced off cash, charged at
   the F&O rate card, and rolled at every expiry — and every such number is
   labelled as modelled, not measured. This is not the forbidden move above:
   the subject of a pairs trade is the spread between two equities, not the
   basis. But the basis noise, roll slippage and lot granularity it hides are
   real and are stated as limitations, not buried.
3. A **cash-only** variant is measured too, because it is fully sourced and
   fully measurable, even though the shorting rule makes it unexecutable. It
   exists to answer one question exactly: what would the costs be if you could
   do it? The answer turns out to matter more than the strategy — see
   `COSTS.md`.

---

## 4. What the equity panel actually is

| | |
|---|---|
| Symbols | 500 NSE_EQ + NIFTY (`IDX_I`) |
| Range | 2015-07-01 .. 2026-09-17, 2,779 trading days |
| Depth | median 2,779 bars; **313 symbols have the full panel**; p10 is 598 bars |
| F&O-eligible | 210 |

The depth distribution matters more than it looks. A formation window plus a
disjoint trading window needs three to four years of overlapping history from
*both* legs, so the effective universe on any given formation date is the
symbols listed by then — not 500, and not 210.

### Splits and bonuses: adjusted. Demergers: not.

Three known capitalisation changes were checked against the ex-date:

| | event | prev close → close | ratio |
|---|---|---|---|
| INFY | 1:1 bonus, ex 2018-09-11 | 730.85 → 734.30 | ×1.005 |
| IRCTC | 1:5 split, ex 2021-10-29 | 913.50 → 845.70 | ×0.926 |
| RELIANCE | 1:1 bonus, ex 2024-10-28 | 1327.85 → 1334.35 | ×1.005 |

All three are continuous, so the vendor **back-adjusts for splits and
bonuses**. A full sweep of the panel for overnight moves outside
[0.6×, 1.75×] returns exactly **six**, and none of them is a split:

| | date | ratio | what it is |
|---|---|---|---|
| JSL | 2015-11-19 | ×0.391 | Jindal Stainless demerger |
| PATANJALI | 2020-01-27 | ×5.062 | Ruchi Soya, post-insolvency relisting |
| TATACHEM | 2020-03-04 | ×0.435 | consumer-business demerger |
| YESBANK | 2020-03-06 | ×0.439 | **genuine** — the moratorium |
| HEXT | 2025-02-19 | ×3.239 | Hexaware relisting |
| HEG | 2026-07-13 | ×0.377 | demerger |

Demergers and relistings are *not* adjusted, and one of the six is a real
price move that must not be filtered. These six dates are hard-coded in
`arblib/data.py:EXTREME_MOVES` and the pair filter is tested against them —
see `METHOD.md`.

> **A caution about the sweep itself.** Written the obvious way, in SQL, it
> reported 6,069 breaks. SQLite gives INTEGER affinity to whole-number
> `NUMERIC` values, so `close / prev_close` integer-divides to 0 whenever both
> happen to be round — and every such day reads as a 100% collapse. The cast
> to `REAL` is load-bearing and is commented as such in the script.

### Volume is not adjusted

Turnover computed as `close × volume` is therefore **understated before a
split** by the split factor, because the price is adjusted and the share count
is not. The liquidity floor is applied on a rolling median, which limits the
damage, and the sensitivity of the result to that floor is reported.

---

## 5. Survivorship

The 500 symbols are **today's** index constituency, held back to 2015. Nothing
that was delisted, merged away or suspended is in the panel.

Pairs trading is unusually exposed to this. Its catastrophic case is not a
drawdown; it is the leg that stops trading while you are short it or long it,
and by construction the dataset contains none. Every result here is flattered
by an unknown amount and **it cannot be fixed with this dataset**. It is
restated in `README.md` and `FINAL_LOGIC.md` rather than mentioned once here.
