# Does an index regime filter help?

**No. It makes things worse on both axes.** Every variant tested lowers CAGR
*and* raises maximum drawdown, which is not a trade-off — it is a double
loss. This was the last open item in `FINAL_LOGIC.md`, and like the ranking
question before it, the answer is negative.

```bash
.venv/bin/python scripts/vcp_regime.py
```

Regime is computed on the NIFTY index from the application's own
`daily_bars`, causally — the average at date *d* uses closes up to *d*.
Applied to the decided rule set (`early entry + EMA50 trail`) with the
₹10,00,000 / ₹1,00,000-a-trade book.

---

## 1. The result

| Filter | In market | Trades | CAGR | Max DD | MAR | Final |
|---|---|---|---|---|---|---|
| **none (baseline)** | 100% | 1,806 | **19.8%** | **−21.0%** | **0.94** | ₹62.7L |
| above 200-day | 73% | 1,435 | 16.7% | −25.5% | 0.66 | ₹48.0L |
| above 200-day + flatten | 73% | 1,398 | 15.7% | −24.6% | 0.64 | ₹44.0L |
| 200-day rising | 76% | 1,037 | 9.1% | −25.2% | 0.36 | ₹24.2L |
| above **and** rising | 65% | 945 | 7.4% | −28.5% | 0.26 | ₹20.7L |
| 50-day > 200-day | 70% | 1,305 | 14.8% | −30.3% | 0.49 | ₹40.6L |
| *BELOW 200-day (inverse)* | 27% | 275 | 9.4% | **−7.0%** | **1.34** | ₹24.9L |

The more the filter filters, the worse it gets: "above and rising", the
strictest, is in the market 65% of the time and turns 19.8% into 7.4%.

## 2. It fails at the thing it was supposed to do

The whole premise was that standing aside in weak markets would remove the
losing years. It does not. Negative years, out of eleven:

| | Negative years |
|---|---|
| none | **6** |
| above 200-day | **6** |
| above 200-day + flatten | **6** |
| above and rising | **7** |

And the specific years it was meant to fix get *worse*:

| Year | none | above 200-day |
|---|---|---|
| 2018 | −2.0% | **−3.0%** |
| 2019 | −6.7% | **−7.6%** |
| 2022 | −6.0% | **−10.0%** |
| 2025 | −5.2% | −4.3% |
| 2026 | −1.8% | **−2.2%** |
| 2020 | **+64.0%** | +24.2% |

2022 — the clearest "bad market" in the sample — is 4 points *worse* with
the filter on. The one large change is 2020, where the filter sat out most
of the COVID rebound because the index spent the sharpest recovery below its
own 200-day average.

## 3. Two confounds, both ruled out

**Was it all 2020?** No. Removing every 2020 entry from both runs: baseline
12.1% CAGR / −21.4% DD, filtered 8.6% / −25.7%. The filter still loses on
both axes with the rebound taken out entirely.

**Was it just less diversification?** Partly, and not enough. Average
concurrent positions fall from 14.8 to 12.0 under the 200-day filter, which
would plausibly raise drawdown a little — but it does not explain losing
three points of CAGR, and the strictest variants fall to 8.7 positions while
losing ten points.

## 4. Why it probably fails

Interpretation, not measurement: **the strategy already contains a regime
filter, and a better-targeted one.** The VCP detector requires at least 6 of
the 7 computable Trend Template criteria — price above its own 150-day and
200-day averages, the 50-day above both, price within 25% of its 52-week
high. That is a per-stock regime test, applied to the thing actually being
bought. Layering an index-level test on top mostly removes trades in stocks
that were strong *while the index was not*, which on this evidence is a good
place to be rather than a bad one.

That reading is supported by the inverse. Trading **only** when the index is
below its 200-day produces the best risk-adjusted result in the whole table
— MAR 1.34 against the baseline's 0.94, on a −7.0% drawdown — though at a
much lower 9.4% CAGR from only 275 trades and 27% time in the market. A VCP
that completes and breaks out while the index is falling is selecting for
genuine relative strength in a way nothing else here does.

**That is a lead, not a finding.** 275 trades, one market, and it emerged
from a test built as a sign check rather than as a hypothesis. It would need
its own out-of-sample work before anyone traded it.

## 5. What this changes

Nothing in `FINAL_LOGIC.md` — the rule set stays as it is, with no regime
filter. The last open item closes negative.

Both of the "obvious improvements" have now been tested and both failed:
ranking same-day candidates made no reliable difference, and the index
regime filter actively hurts. The honest summary is that the rule set is
what it is, and the remaining uncertainty is in the caveats — survivorship,
in-sample selection, and a bull-market sample — not in missing refinements.
