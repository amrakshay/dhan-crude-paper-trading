# CHARGES — the NSE index-options rate card, and what it does to a seller

The card itself is `conf/charges/nse-index-options.yaml`, every rate date-keyed
and sourced. This document records what was found building it, including the
divergence that is **reported rather than tuned away**.

Reproduce every figure here with `python scripts/validate_charges.py`.

---

## 1. STT on option sales rose ~9× inside the sample

A premium seller pays STT **on the sell side, on the premium** — on revenue, at
entry, before knowing whether the trade works. It changed five times between
2016 and today:

| Effective | Sale of option (on premium) | Option exercised (on intrinsic) | Source |
|---|---|---|---|
| to 2016-05-31 | 0.017% | 0.125% | UNCONFIRMED (the long-standing pre-2016 rate) |
| 2016-06-01 | 0.05% | 0.125% | Finance Act 2016, assent 14 May 2016 |
| 2023-04-01 | 0.0625% | 0.125% | Finance Act 2023, passed 24 Mar 2023 |
| **2024-10-01** | **0.10%** | 0.125% | Finance (No. 2) Act 2024 |
| **2026-04-01** | **0.15%** | **0.15%** | Budget 2026 |

The handoff flagged the October 2024 change. **The April 2026 change is a second
structural break it did not know about, and it is larger** — it is also the only
regime that describes trading this strategy today.

---

## 2. The break is structural for a sized book and invisible for a toy one

The same trade (sell 100, buy back at 40), only the date changing:

**One lot (75 units, ₹7,500 premium sold)**

| date | STT rate | STT ₹ | brokerage ₹ | total ₹ | % of premium |
|---|---|---|---|---|---|
| 2016-01-15 | 0.0170% | 1.27 | 40.00 | 55.16 | 0.735% |
| 2016-07-15 | 0.0500% | 3.75 | 40.00 | 57.63 | 0.768% |
| 2023-06-15 | 0.0625% | 4.69 | 40.00 | 58.25 | 0.777% |
| 2025-01-15 | 0.1000% | 7.50 | 40.00 | 59.20 | 0.789% |
| 2026-06-15 | 0.1500% | 11.25 | 40.00 | 62.95 | 0.839% |

STT multiple **8.82×**, total cost multiple **1.14×**.

**Forty lots (3,000 units, ₹300,000 premium sold)**

| date | STT ₹ | total ₹ | % of premium |
|---|---|---|---|
| 2016-01-15 | 51.00 | 365.46 | 0.122% |
| 2026-06-15 | 450.00 | 677.38 | 0.226% |

STT multiple **8.82×**, total cost multiple **1.85×**.

At one lot, flat ₹20 brokerage swamps the tax and the two structural breaks are
nearly invisible. At forty, STT dominates. **A one-lot backtest would
under-report the very break it means to measure**, which is one reason the
portfolio simulation sizes properly.

---

## 3. The divergence, recorded not tuned

NSE circular **NSE/FA/56129** (24 Mar 2023) gives the pre-October-2024 options
transaction charge schedule verbatim: Rs 50.00 per lakh of premium value for the
₹3–100 crore slab from 1 April 2023, plus an IPFT contribution "recalibrated …
to Rs 50 per crore traded value (premium value)". That totals **Rs 5,050 per
crore = 0.0505%**.

Zerodha published **0.0495%** to clients for the same period.

The gap is a **member-slab artefact**: before SEBI's "True to Label" rule
(circular SEBI/HO/MRD/TPD-1/P/CIR/2024/92, 1 July 2024) the amount a broker
recovered from a client did not have to equal what the exchange charged the
broker, and the exchange's schedule was banded on the *member's* monthly
turnover.

**The primary source is used** (0.0505%, the more conservative of the two) and
the divergence is recorded here. It is worth 0.001 percentage points of premium.

This also means something worth stating plainly: **for the pre-October-2024 era
there is irreducible uncertainty about what a retail client actually paid**,
because the published schedule and the client-facing charge were different
objects by design.

After 1 October 2024 they must match, and the live broker card agrees with the
circular exactly (0.03553%), which is the strongest available confirmation that
a rate is currently in force.

---

## 4. Transaction charges, with the trap NSE's own circular sets

| Effective | Total (charge + IPFT) | Source |
|---|---|---|
| 2016-01-01 | 0.0530% | NSE/FA/56129 records the prior schedule: Rs 53.00/lakh + IPFT Rs 0.01/crore |
| 2023-04-01 | 0.0505% | NSE/FA/56129: Rs 50.00/lakh + IPFT Rs 50/crore |
| 2024-10-01 | 0.03553% | NSE/FA/64232: "Equity Options: Rs. 35.03 each side per lakh of premium value"; NSE/FA/73061 confirms the total as Rs 3,553/crore |
| 2026-03-01 | **0.03553%, unchanged** | NSE/FA/73061 |

The last row is the trap. NSE/FA/73061 (27 Feb 2026) *looks* like a rate change —
it re-splits the transaction charge and the IPFT contribution — but its own table
shows the total moving from Rs 3,553 to Rs 3,553 per crore, "thereby ensuring no
impact in the overall outflow". It is listed explicitly in the card so that
someone who finds the circular does not conclude the card missed a change.

---

## 5. Expiry is not a transaction

A spread allowed to expire worthless pays **nothing**: no brokerage, no exchange
transaction charge, no GST, and — because there is no sale and no exercise — no
STT.

| case | charge |
|---|---|
| spread expires worthless | **₹0.00** |
| long wing exercised, 1000 points intrinsic, 75 units | **₹112.50** |

Letting a position expire is genuinely cheaper than closing it, and the backtest
must not charge for a trade that never happened. `ChargeCard.charge_expiry()` is
a separate entry point from `charge_trade()` precisely so the two can never be
confused.

### The exercise limb matters more than its size suggests

It is charged on **intrinsic value, not premium**. On a disaster trade — the one
where the long wing finishes deep in the money — 0.15% of intrinsic can exceed
every other charge on the position combined. A model that omits it flatters
exactly the left-tail cases that decide the outcome, which is the worst possible
place to be optimistic.

---

## 6. Leg count is a real cost

At ₹20 per executed order:

| structure | orders to open | brokerage | total entry charge |
|---|---|---|---|
| 2-leg vertical | 2 | ₹40 | ₹66.34 |
| 4-leg iron condor | 4 | ₹80 | ₹131.79 |

A condor round trip is eight executed orders. Any comparison between a two-leg
and a four-leg structure that ignores this is comparing on the wrong basis.

---

## 7. Charges against the credit — why "sell far OTM for a tiny premium" fails

Entry charges only, current regime, one lot, a vertical:

| credit/unit | gross credit | entry charges | % of credit |
|---|---|---|---|
| ₹10 | ₹750 | ₹50.42 | **6.72%** |
| ₹25 | ₹1,875 | ₹55.25 | 2.95% |
| ₹50 | ₹3,750 | ₹63.29 | 1.69% |
| ₹100 | ₹7,500 | ₹79.38 | 1.06% |
| ₹200 | ₹15,000 | ₹111.57 | 0.74% |

A spread sold for a small credit loses a double-digit percentage of it to
charges before the market moves at all — and that is *before* slippage, which on
a far-OTM wing is plausibly larger than every line item here combined.

This is the arithmetic reason the far-OTM, high-win-rate end of the parameter
space does worse than its win rate suggests, and it is visible in the delta
sweep.

---

## 8. What the card cannot price

- **The bid-ask spread.** Not a charge, and not in the bhavcopy. Modelled
  separately in `SlippageModel` and swept.
- **Physical settlement.** Index options are cash settled; this card would be
  wrong for single-stock options, which are out of scope.
- **Pre-2020 stamp duty** was state-wise. The post-2020 national rate is carried
  back for want of a sourced alternative, and marked UNCONFIRMED.

---

## 9. Confidence summary

| Rate | Confidence |
|---|---|
| Transaction charges from 2023-04-01 | **CONFIRMED** — read verbatim from NSE circular PDFs |
| STT 2016-06-01, 2023-04-01, 2024-10-01, 2026-04-01 | **CONFIRMED** by multiple independent contemporaneous sources; UNCONFIRMED against the gazetted Acts |
| STT pre-2016 (0.017%) | UNCONFIRMED |
| SEBI turnover fee from 2019-04-01 | CONFIRMED for the rate |
| Stamp duty | UNCONFIRMED against the gazette; consistent across brokers |
| GST 18% and its base | UNCONFIRMED against a CBIC notification; consistent across every broker checked |
| Brokerage ₹20/order | CONFIRMED as a commercial rate, not a statutory one |

The gazetted Acts were not fetched. Where that leaves a rate UNCONFIRMED it says
so, rather than borrowing confidence from the sources that do agree.
