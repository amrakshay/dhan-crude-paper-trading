# Does the NSE large-cap universe contain cointegrated pairs?

The central question, and the one that decides everything downstream. If the
answer is no, no amount of care in the trading rules recovers anything.

```bash
.venv/bin/python scripts/select_pairs.py --tag base
.venv/bin/python scripts/select_pairs.py --placebo-seed 11 --tag placebo
.venv/bin/python scripts/select_pairs.py --log-prices --tag logpx
.venv/bin/python scripts/compare_screens.py
```

Sixteen formation dates, each a 756-bar (three-year) window, each screening
every unordered pair of its eligible universe. Trading spans 2018-07-18 to
2026-09-07.

---

## 1. The scale of the screen

| | |
|---|---|
| formation dates | 16 |
| eligible symbols per date | 110 → 190 (rising as names acquire three years of history) |
| pairs tested per date | 5,995 → 17,955 |
| **hypothesis tests in total** | **179,673** |
| p-value resolution (200,000-draw null) | 5 × 10⁻⁶ |

At a nominal 5%, **8,984** of those pass *by chance alone*. That is the number
any claim about "finding cointegrated pairs" has to be read against, and it
is the single most important number in this document.

## 2. What the screen actually found

| | tests | nominal 5% passes | expected by chance | ratio | **FDR survivors (q=5%)** |
|---|---:|---:|---:|---:|---:|
| real panel, **price levels** | 179,673 | 7,307 | 8,984 | **0.81×** | **1** |
| real panel, **log prices** | 172,912 | 8,227 | 8,641 | **0.95×** | **6** |
| **real-data placebo** | 175,985 | 5,115 | 8,799 | **0.58×** | **0** |

**One pair in 179,673 tests survives a false-discovery-rate correction** on
price levels, and **six in 172,912** on log prices, in eight years of the
Nifty-500 F&O universe.

## 3. The placebo, and why 1.0 is the wrong baseline

The obvious reading of `0.81×` is "fewer than chance — there is nothing
here". That reading is wrong, and the placebo is what shows why.

The nulls are simulated from **Gaussian** random walks. Real share prices are
not Gaussian: they have fat tails and volatility clustering, and both make the
ADF statistic reject *less* often than a Gaussian null predicts. So the real
panel's absolute pass rate cannot be compared with the nominal 5%. It has to
be compared with a panel that has the same marginal behaviour and in which no
relationship can possibly exist.

That is the placebo: **every symbol's formation window is replaced by a window
of its own history from a different, randomly chosen date**. Same fat tails,
same volatility clustering, same drift and price level for that name — and no
two series are contemporaneous any more, so nothing genuine can connect them.

It scores **0.58×**. That is the floor.

### Real against placebo, paired by date

| | mean ratio | vs placebo |
|---|---:|---:|
| real, price levels | 0.830 | **1.43×** |
| real, log prices | 0.928 (median) / 0.952 (pooled) | **1.64×** |
| placebo | 0.581 | 1.00× |

| | |
|---|---|
| paired t-test | t = +4.19, p = **0.0008** |
| Wilcoxon signed-rank | p = **0.0008** |
| dates where real exceeded placebo | **15 of 16** |

Paired across dates rather than pooled over tests, deliberately. Within one
date the tests are nowhere near independent — 180 symbols make 16,000 pairs
and every symbol appears in 179 of them — so a two-proportion test on the
pooled counts would claim far more significance than it has. The 16 formation
dates are the closest thing here to independent units, and even they overlap.

### What this means, precisely

**There is genuine cointegration in the NSE large-cap universe, and it is
detectable in aggregate.** The real panel passes 43% more often than a panel
that cannot contain any, on 15 of 16 dates, at p = 0.0008. That is a real
finding and it should not be rounded down to "no signal".

**It cannot be localised to individual tradeable pairs.** The same screens
yield one and six FDR survivors. An excess pass rate of 1.43× over a floor of
0.58× means roughly 2,200 of the 7,307 level-price passes are genuine and
roughly 5,100 are noise — and on logs, roughly 3,200 of 8,227 — but *which*
is exactly what the screen cannot say, because the p-values of the genuine
ones are not separated from the noise.

A book that trades the nominal passes is therefore trading a roughly
one-third-genuine mixture. `FINAL_LOGIC.md` §4 shows what each produces: on
logs, a gross return of **+0.69%/yr of deployed exposure against costs of
0.91%** — the genuine third is real and is worth less than the charges. On
levels, gross P&L is **negative before a single charge**, which is the
mixture being diluted past the point where the genuine part shows at all.

This distinction — a population-level signal that is not extractable
case by case — is the whole result of the experiment, and it is the outcome
that a screen reporting only "6,000 pairs found at p<0.05" would hide
completely.

## 4. Multiple comparisons, and why FDR rather than Bonferroni

Bonferroni controls the probability of even one false pair. Over 17,955 tests
its threshold is 2.8 × 10⁻⁶ — **below the resolution of even a 200,000-draw
simulated null**, so the number it produces would be an artefact of the
simulation rather than a measurement. It is computed anyway and reported; on
this data it agrees with FDR, because both admit one pair.

Benjamini-Hochberg at q = 5% answers the question a portfolio actually asks:
of the pairs I am about to trade, what share are noise? It is the primary
correction here.

The audit also reports how many survivors sit **at the p-value floor**, so a
reader can tell when the correction is resolution-limited rather than
data-limited. Across the real panel: one survivor, at the floor.

## 5. The log-price specification, which does better

**Logs beat levels on every measure taken**: 0.95× the chance rate against
0.81×, 1.64× the placebo against 1.43×, six FDR survivors against one, and —
when traded — a positive gross return on exposure against a negative one.

There is a reason to expect this and it is not a fitting artefact. A level
spread `P_b − β·P_a` assumes the *rupee* relationship between two shares is
stable over three years. Over a window in which one leg triples, it is not:
the same β that hedged at ₹400 does not hedge at ₹1,200, and the residual
picks up a level-dependent drift that reads as non-stationarity. A log spread
assumes the *ratio* is stable, which is the scale-free version of the same
claim and the one a share price is more likely to satisfy.

The cost is real, though, and it is why levels remain the default elsewhere in
this experiment. The two are **different strategies**, not two views of one:

- a **level** spread `P_b − β·P_a` is held as a fixed *share* ratio and needs
  no rebalancing;
- a **log** spread `ln P_b − β·ln P_a` is a fixed *value* ratio, which drifts
  out of hedge the moment prices move, so holding it means rebalancing — and
  rebalancing means paying the round-trip costs of `COSTS.md` §3 again, on a
  schedule.

The log book **is** backtested (`FINAL_LOGIC.md` §4), holding the value ratio
struck at entry and **not rebalancing it**. Over an 18-bar mean hold the hedge
drift is small, but not modelling the rebalancing — or its costs — is an
optimism in favour of the specification that did best, and it is listed among
the known gaps rather than buried here.

**And choosing logs is itself a selection over two specifications, made on
this data.** It is the same error this document measures at the pair level,
committed at the strategy level with n = 2. The discount is small, because two
is not 180,000 and because the log specification has a prior reason to be
right, but it is not zero.

## 6. What would change this answer

- **A shorter horizon.** Everything here is daily bars. Statistical arbitrage
  as practised is largely intraday, where half-lives are minutes. This
  experiment says nothing about that and could not.
- **A longer formation window.** At 756 bars the test has 81% power against a
  20-bar half-life and 24% against a 40-bar one
  (`NEGATIVE_CONTROLS.md` §2). Slow pairs are not being rejected, they are
  invisible. A 1,260-bar window would see them — and would cut the eligible
  universe further, and leave fewer trading windows.
- **A narrower universe chosen on economics rather than statistics.** Same-sector
  pairs, dual-listed share classes, holding company against subsidiary. That
  is a prior imposed *before* the screen, which is the legitimate way to cut
  180,000 tests down to 200 — and it is the most promising unexplored
  direction here.
- **Survivorship.** The panel is today's index constituency. The pairs most
  likely to have cointegrated are the ones where one leg was taken over, and
  none of those are present.
