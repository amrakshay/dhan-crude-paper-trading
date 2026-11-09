# Why some setups ran, and how to cut the losers

**Question (2026-10-04):** some of the channel's names made 20–30%. Why? And
how can the losers be exited so capital keeps rotating?

**Scope: channel data only** (his call). 169 setups from 40 sessions in one
market (Aug–Sep 2026). Every "best" here was picked on the same sample it is
scored on. **Nothing here is validated.** The 10-year check was offered and
declined.

```bash
.venv/bin/python scripts/winners_and_exits.py   # after analyse.py
```

Outputs: `out/why_winners.csv` (features and outcomes per setup),
`out/exits/<rule>.csv` (every trade under every rule),
`out/winners_exits_summary.json`.

---

## 1. Why the winners won

Outcome: net return from the next session's open to the close **10 sessions
later**. That is a fixed horizon, so early listings get no advantage from simply
having had longer. 135 setups have it: **41 won more than 5%, 28 lost more than
5%, 66 in between.**

**About a fifth of it is just the day.** The list date alone explains 19% of
the spread in outcomes. Setups listed on the same day rise and fall together.

Twenty-two features were measured at the list date, using no data after it.
Six have a 95% interval clear of zero; with 22 tries about one would by chance.
They point one way:

| Clears zero | ρ | Won >5% | Lost >5% | Reads as |
|---|---:|---:|---:|---|
| List-day volume ÷ 50-day average | +0.23 | 0.69× | 0.47× | volume already coming in |
| 6-month return | +0.20 | +63% | +50% | stronger, longer trend |
| Sessions since the 52-week high | −0.19 | 6 | 22 | **at** the high, not pulling back from it |
| 5-day range ÷ 20-day range | +0.18 | 0.32 | 0.24 | range **widening**, not tightening |
| Distance below the 252-day high | +0.16 | −4.1% | −5.6% | closer to the high |
| Listed on BSE | −0.17 | 2% of winners | 14% of losers | the BSE names did worse |

What did **not** matter, every interval spanning zero:
- the channel's **rank** (top-ranked names did no better);
- **inside day**;
- **volume dry-up**, which leans the *wrong* way: winners had more volume, not less;
- base depth;
- tightness of the last 10 days;
- ADR;
- RS63;
- liquidity;
- distance to the pivot.

**So the winners were not the best VCPs. They were stocks already breaking
out:** on or within days of a 52-week high, a 6-month trend behind them, volume
picking up and the range opening out. The classic VCP signals (contraction,
volume drying up, inside days) said nothing, and volume pointed the wrong way.
This matches the README's finding that the list is a momentum screen. It also
means the things to watch for in a new sheet are **"at the high, on rising
volume"**, not "tight and quiet". That remains a hypothesis from 135 setups.

## 2. Do the first few days tell you which ones to cut?

**No.** This was the obvious basis for cutting losers early, and the data does
not support it.

What a stock did over the first 1, 3 or 5 sessions says **nothing** about what
it did afterwards: Spearman ρ −0.02, −0.04 and 0.00 against the return from that
point to session 20, all intervals spanning zero. Of the stocks that were down
after 5 sessions, 55% rose from there to session 20, the same share as those
that were up.

A first pass showed a strong link (ρ 0.60, "up after 3 days" +5.6% vs −2.8%).
**That was an artefact:** it compared early returns with a 20-day return that
**contains** those early days. Corrected, it vanishes. Do not reuse the
uncorrected figure.

## 3. Exits

Every setup is bought at the next open; only the exit varies. Figures are net
of charges and slippage. "Per 100 capital-days" is the rotation measure: net
return earned per 100 days of money tied up.

| Exit rule | Mean | Median | Winners | Avg days | **Per 100 cap-days** | Worst |
|---|---:|---:|---:|---:|---:|---:|
| Hold to the end (baseline) | +0.98% | −0.47% | 43% | 21.3 | 4.6% | −24.3% |
| **Hold 10 sessions** | +0.88% | −0.19% | 49% | 9.2 | **9.6%** | −22.2% |
| Hold 20 sessions | **+1.14%** | −0.44% | 46% | 15.9 | 7.1% | −20.1% |
| Stop −5% | −0.72% | −5.42% | 25% | 10.9 | −6.6% | −7.7% |
| Stop −8% | −0.97% | −5.27% | 33% | 14.6 | −6.6% | −11.2% |
| 10-day-low stop + 50 EMA (FINAL_LOGIC) | −0.68% | −3.40% | 32% | 14.2 | −4.8% | −18.7% |
| 10-day-low stop + 20 EMA | −0.46% | −2.61% | 30% | 10.0 | −4.6% | −18.7% |
| 2×ATR stop + 20 EMA | −0.58% | −2.82% | 29% | 9.9 | −5.9% | −10.6% |
| Chandelier 3×ATR | −0.35% | −2.19% | 38% | 15.8 | −2.2% | −15.2% |
| 10-day-low trail | −1.11% | −2.96% | 26% | 9.8 | −11.4% | −14.0% |
| 5-day time stop (below 0%) + 20 EMA | +0.07% | −1.97% | 28% | 8.8 | 0.7% | −15.6% |
| 5-day time stop (below +3%) + 20 EMA | +0.03% | −1.42% | 38% | 7.4 | 0.4% | −15.6% |
| Stop −8% + 5-day time stop + 20 EMA | −0.11% | −2.03% | 27% | 8.7 | −1.3% | −8.4% |
| *[post hoc] 5-day time stop, else hold 20* | *+0.86%* | *−1.48%* | *36%* | *10.9* | *7.9%* | *−16.8%* |
| *[post hoc] 5-day time stop, else hold 10* | *+0.66%* | *−1.21%* | *40%* | *7.2* | *9.2%* | *−16.8%* |

**The ₹10L book**, ₹1.5L a position (6 slots), with freed cash going into the
next new setup and open positions valued at the 2026-10-01 close. With six
slots it can take only 6–39 of the 164 setups, so *which* setups it happened to
get dominates. Each rule was re-run 200 times with random same-day order and
a random start:

| Exit rule | Median return | 10th–90th percentile | Setups taken (ranked order) |
|---|---:|---|---:|
| Hold to the end | +2.1% | −3.5% … +7.0% | 6 |
| Hold 10 sessions | +2.7% | −4.4% … +9.4% | 30 |
| Hold 20 sessions | +4.1% | −3.9% … +9.4% | 14 |
| Stop −5% | −1.0% | −8.4% … +4.3% | 18 |
| Stop −8% | −1.9% | −9.4% … +6.7% | 12 |
| 10-day-low stop + 50 EMA | +0.9% | −6.9% … +5.7% | 18 |
| 10-day-low stop + 20 EMA | −1.9% | −9.5% … +4.9% | 23 |
| 2×ATR stop + 20 EMA | −4.6% | −12.8% … +1.6% | 22 |
| Chandelier 3×ATR | −0.2% | −5.3% … +6.0% | 14 |
| 5-day time stop (below +3%) + 20 EMA | +1.9% | −5.6% … +10.0% | 35 |
| *[post hoc] 5-day time stop, else hold 20* | *+4.4%* | *−2.8% … +10.1%* | *20* |

### What it says

1. **Price stops make it worse, every one of them.** A −5% stop takes the
   mean from +1.0% to −0.7% and the median to −5.4%. On stocks that move 4–8%
   a day, a 5–8% stop is one ordinary bad day. It sells names that then
   recover and keeps the ones that keep falling. Trailing stops (20/50 EMA,
   chandelier, 10-day low) have the same problem one step later: they shake
   out the eventual winners. This is the same lesson as `VCP_EXITS.md` (the
   9 EMA truncated the tail), only stronger here because these stocks are
   noisier.
2. **There is no evidence-based way to spot the losers early** (§2). A time
   stop that sells laggards after 5 days sells as many future winners as
   losers. The post hoc "time stop, else hold 20" row looks good partly
   because it was built after seeing the results; its book median (+4.4%) is
   indistinguishable from plain "hold 20" (+4.1%).
3. **What actually rotates capital is a time exit.** "Hold 10 sessions, then
   sell" earns **9.6% per 100 capital-days**, more than double holding to the
   end (4.6%). Its mean per trade is almost the same; the money is simply free
   twice as often. It does **not** cut losers: its worst trade is −22%. It
   recycles everything on a schedule.
4. **The book differences are within noise.** The 10th–90th ranges of the
   better rules overlap almost completely. Over two months, choosing the right
   exit matters less than which six setups the book happened to take.

### If something has to be traded on this evidence

- **Exit on time, not on price:** sell after 10 sessions (or 20; the gap
  between them is noise here). Re-enter only if the name is relisted after the
  20-session cooldown.
- **Do not use a tight price stop** on these names. If a disaster stop is
  wanted for peace of mind, it has to sit outside a normal week's range. None
  was tested beyond −8%, which already hurt. Size positions so a −20% trade
  is survivable, because that is what the worst ones did.
- **Prefer, as a hypothesis to watch**, names that are at a 52-week high on
  above-average volume with a strong 6-month trend. Do not rely on the
  channel's rank or on inside days.

## Known gaps

- **169 setups, one regime, two months.** A rule that wins here can lose on
  the next two months. Validating it on 2015–2026 history of the same screen is
  the step that would make any of this tradable. It was offered and declined
  for now.
- The "won/lost" profile uses 10-session outcomes; setups listed after
  2026-09-17 do not have one yet.
- The book allows one position per name, does not reuse a slot on the day it
  frees, and ignores dividends.
- Same caveats as the README: proxied pivot and stop, five NSE `BE` names
  priced from BSE, BSE names costed with the NSE card.
