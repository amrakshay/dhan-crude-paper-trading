# SWEEP — the parameter trade-offs, and why almost none of them are findings

One dimension at a time, over 2016-01-01 → 2026-09-17, 2,643 NIFTY sessions.
Base cell: iron condor, 16-delta shorts, 5-delta wings, ~30 DTE, closed at 50% of
maximum profit.

Reproduce with `python scripts/sweep.py`. Full output in `out/sweep_report.txt`.

**`R/risk /yr`** is the annualised return per rupee-**year** of *maximum loss*
deployed — the capital actually at risk, and the only figure here that can
honestly be set beside the risk-free rate of **+5.64%** over this sample. Return
on *margin* is a leveraged number that flatters every cell by roughly 4×; see
`MARGIN.md` §4.

---

## 0. Read this before the tables

These tables were produced twice: once with a bug in expiry selection, and once
after it was fixed. The fix (`EXPIRY_MIX.md` §3) did not change any strategy
rule — it only made the engine try the *next* expiry in the DTE window when the
first one's chain could not be built, which added about 20% more trades.

**Almost every "best cell" moved, and several changed sign:**

| dimension | best cell before | best cell after |
|---|---|---|
| management | PT50 + stop 2× (**+₹33,550**) | PT50 + stop 2× is now **−₹44,583**; best is exit at 10 DTE (+₹84,494) |
| short delta | 0.40d (+₹29,723) | 0.40d is now **−₹71,898**; best is 0.30d (+₹114,036) |
| wing | 0.02d (−₹70,572, least bad) | 0.10d (−₹8,307, least bad) |
| DTE | 45 (−30.12%, least bad) | 30 (−25.91%, least bad) |

What survived unchanged: **the structure table** — condor negative, bull put
positive, bear call very negative — and **the fact that most cells lose**.

That is the most useful thing in this document. A parameter ranking that reverses
when you add 20% more trades is measuring the sample, not the parameter. Every
specific cell below should be read as an illustration of a trade-off, never as a
recommendation, and the retraction in §5 is there because I made exactly that
mistake the first time.

---

## 1. Structure — the one large, theory-consistent effect

| structure | n | total P&L | **R/risk /yr** | worst trade | win |
|---|---|---|---|---|---|
| iron condor | 205 | −95,632 | **−25.91%** | −43,086 | 83.9% |
| **bull put spread** | 308 | **+98,180** | **+26.00%** | −48,812 | 94.2% |
| bear call spread | 297 | **−253,967** | **−88.07%** | −44,796 | 88.2% |
| naked strangle * | 220 | +134,865 | unbounded | **−135,030** | 85.0% |
| naked put * | 319 | +293,246 | unbounded | **−138,110** | 95.0% |

\* unhedged, shown as the comparator the wing must beat. Not traded.

**This is the only large, stable, explicable result in the whole sweep**, and the
only one that survived the bug fix intact. The put-side structure makes money and
the call-side structure loses more than three times what the condor does. The
mechanism is the equity index volatility skew: the put sold sits on a steeper
part of the smile than the put bought, so a put spread is short the richer option
and long the cheaper one in volatility terms, and a call spread is the reverse.
Measured on this data (NIFTY 2025-08-01, 27 DTE): 16-delta put at **12.8%**
implied volatility, 16-delta call at **10.6%**.

The iron condor is the average of the two, which is where it lands. It is not a
diversified structure; it is a good trade stapled to a bad one.

`BULL_PUT.md` interrogates the positive half. The *direction* holds; the
*magnitude* does not.

### What the wing is worth

| | hedged condor | unhedged strangle |
|---|---|---|
| net P&L | −₹95,632 | **+₹134,865** |
| worst single trade | **−₹43,086** | −₹135,030 (3.1× worse) |
| mean of the worst 1% | **−₹39,530** | −₹75,361 |
| worst month | **−₹62,801** | −₹170,167 (both 2020-03) |
| skew of trade P&L | **−2.75** | −5.88 |
| charges as % of gross | 64.4% | 13.7% |

On this sample **the wing does not pay for itself on return** — the unhedged
strangle ends ₹230k ahead. What it buys is a worst trade three times smaller, a
worst month under half the size, and a much less violent left tail.

That is the handoff's anticipated finding, and it is worth stating in its exact
form: *the hedge costs more than the tail it removes, at these prices, on this
sample*. The counter-argument is that a sample containing seven 5%-down days is
precisely the sample in which tail insurance looks overpriced, and the naked
strangle's −₹135,030 trade is what happens when the tail arrives at a size the
sample did contain.

---

## 2. Days to expiry — every cell loses

| DTE | n | total P&L | R/risk /yr | worst |
|---|---|---|---|---|
| 7 | 538 | −187,946 | −109.82% | −32,709 |
| 14 | 325 | −123,034 | −50.86% | −64,281 |
| 21 | 264 | −96,339 | −29.83% | −43,086 |
| **30** | 205 | −95,632 | **−25.91%** | −43,086 |
| 45 | 131 | −140,725 | −29.89% | −67,584 |
| 60 | 68 | −207,424 | −62.84% | −62,329 |

The only shape that is stable across both runs: **7 DTE is much the worst**.
Gamma dominates and four legs of fixed brokerage are paid against a much smaller
credit. The published 30–45 DTE preference is the least-bad region, which is
consistent with it — but "least bad among negatives" is not support for it.

---

## 3. Short-strike delta — the win rate is a trap, demonstrated

| short delta | n | total P&L | R/risk /yr | worst | **win rate** |
|---|---|---|---|---|---|
| 0.08 | 233 | −41,029 | −25.18% | −24,830 | **89.7%** |
| 0.10 | 225 | −46,875 | −20.34% | −30,531 | 88.4% |
| 0.16 | 205 | −95,632 | −25.91% | −43,086 | 83.9% |
| 0.20 | 192 | −18,350 | −4.26% | −56,511 | 80.7% |
| **0.30** | 168 | **+114,036** | **+22.73%** | −54,754 | **72.6%** |
| 0.40 | 152 | −71,898 | −12.99% | **−121,769** | **61.2%** |

**The only positive cell has the second-worst win rate.** The 8-delta cell wins
almost 90% of the time and loses money; the 30-delta cell wins less than
three-quarters of the time and is the only one that makes any.

That is the whole argument against win rate as evidence, visible in one column,
and it is the one parameter claim that held up across both runs. The far-OTM end
fails on arithmetic rather than luck: a spread sold for a small credit loses a
double-digit percentage of it to charges before the market moves at all
(`CHARGES.md` §7).

Note also that 0.40d — the best cell in the earlier run — is now among the worst,
with a −₹121,769 trade in it. The column is non-monotone and unstable. Do not
read a preferred delta off it.

---

## 4. Wing distance

| wing delta | n | total P&L | R/risk /yr | worst | maxL ÷ margin |
|---|---|---|---|---|---|
| 0.02 | 208 | −12,068 | −1.79% | −71,892 | −724% |
| 0.03 | 212 | −81,378 | −15.10% | −72,010 | −839% |
| 0.05 | 205 | −95,632 | −25.91% | −43,086 | −667% |
| 0.08 | 189 | −46,438 | −20.87% | −31,458 | −527% |
| 0.10 | 186 | −8,307 | −5.23% | −31,458 | −466% |

The reliable part is the **tail**, not the return: a closer wing (higher delta)
monotonically improves the worst trade (−71,892 → −31,458) and the maximum loss
per unit of margin. The return column is not monotone and moved between runs.

The fixed-point-width variant is worse nearly everywhere, confirming the
objection in `RESEARCH.md` §3: a fixed width does not adapt to volatility, so it
is too wide in calm markets and too narrow in panics.

---

## 5. Management — and a retraction

| rule | n | total P&L | R/risk /yr | worst | win |
|---|---|---|---|---|---|
| hold to expiry | 131 | −77,512 | −20.40% | −51,824 | 70.2% |
| profit take 25% | 294 | +10,665 | +3.01% | −36,699 | 91.2% |
| profit take 50% | 205 | −95,632 | −25.91% | −43,086 | 83.9% |
| profit take 75% | 158 | +53,658 | +14.18% | −61,269 | 81.0% |
| **exit at 10 DTE** | 162 | **+84,494** | **+20.79%** | −35,327 | 70.4% |
| exit at 5 DTE | 140 | +40,884 | +10.30% | −55,740 | 74.3% |
| PT50 + stop 2× | 219 | **−44,583** | **−12.29%** | **−23,295** | 76.7% |
| PT50 + stop 1× | 254 | −11,283 | −3.03% | **−26,010** | 68.5% |

### Retraction

An earlier version of this document said: *"A stop-loss is the single most
effective rule tested, and I expected the opposite… a 2× credit stop nearly
halves the worst trade and turns the strategy's sign."*

**The first half of that is now false.** After the expiry-selection fix, the same
2× stop cell goes from **+₹33,550 to −₹44,583**. It did not survive a change that
touched no strategy rule at all.

**The second half survives, and is the part that was actually about risk**: the
stop still roughly halves the worst trade (−43,086 → −23,295) and nearly quarters
the maximum loss per unit of margin (−667% → −302%). A stop on a defined-risk
spread buys tail reduction and costs return. That is a coherent trade, and it is
what `RESEARCH.md` §5 predicted; what it does *not* do is turn a losing strategy
into a winning one.

I recorded the reversal rather than quietly restating the conclusion, because the
original claim is exactly the kind this study exists to be sceptical of: a single
cell, in one sample, with a story attached after the fact.

### On the published 50%-of-max-profit rule

It does not replicate. Profit-taking at 25% and 75% both beat taking at 50%, and
so does simply leaving before expiry week — there is no ordering here that
supports "50% is the number". What is stable across both runs is that **holding
to expiry is poor** and that **leaving before expiry-week gamma helps**, which is
the defensible part of the folklore.

---

## 6. Slippage — the assumption most able to flip the answer

| slippage | n | total P&L | R/risk /yr |
|---|---|---|---|
| none (unreal) | 209 | −46,150 | −12.62% |
| 0.5% / 1 tick | 207 | −85,174 | −22.85% |
| **1% / 1 tick (base)** | 205 | −95,632 | **−25.91%** |
| 2% / 2 ticks | 199 | −171,030 | −46.93% |
| 4% / 4 ticks | 188 | −116,154 | −31.74% |

Removing slippage entirely halves the loss; doubling it nearly doubles the loss.
The 4% row is non-monotone because the wider spread also *rejects* trades (188 vs
209), so it is partly a different sample rather than the same one costed
differently — worth noting rather than smoothing over.

The bhavcopy carries no bid-ask, so all of this is modelled. On NIFTY 2025-08-01
the near-money put showed **40,466** executed trades and the 5-delta wing showed
**101**. The wing is the leg the strategy must buy and the one the model is least
able to price, and it remains the largest single source of doubt in the study.

---

## 7. What to take from this

1. **The put/call asymmetry** — large (a 114-point spread in annual return on
   capital at risk), predicted by the observed skew before it was measured,
   confirmed by the sign of the opposite structure, and stable across a code
   change that moved everything else.
2. **Almost everything loses.** Of roughly forty cells, six are positive. A
   family whose parameter surface is mostly below zero after costs is not one a
   better parameter choice rescues.
3. **The parameter rankings are not robust**, demonstrated rather than asserted:
   a fix that changed only which expiry was tried first reversed most of them.
   Any single cell quoted from this table as a recommendation would be a
   selection artefact, and §5 records the one time I nearly published one.
