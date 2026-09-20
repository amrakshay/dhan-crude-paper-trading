# IPO breakouts: the setup, the sources, and what India changes

Background research for the detector in `ipolib/`. Everything here that is a
number is sourced; everything that is a judgement is marked as one; everything
that is a claim nobody has checked is marked **unverified**.

The short version: the *shape* of the setup is American and well documented,
the *supply calendar* underneath it is Indian and has no US analogue, and the
two published quantitative claims most people repeat — that the grey market
premium predicts something, and that heavy subscription predicts something —
are the two this project cannot test at all.

---

## 0. The idea, and the tension inside it

An IPO breakout is a **supply-absorption story with a known start date.**

Every other base pattern has to infer when the supply arrived. A new listing
does not: on day one a known quantity of stock changes hands from people who
bought it to sell (anchor allottees, pre-IPO funds, retail flippers) to people
who bought it to hold. The first weeks are that handover. A base that forms
after it, and a breakout out of that base, is the claim that the handover is
finished.

The tension worth stating up front, because it decides how this must be
measured:

- **Academically, IPOs as a class underperform.** Ritter (1991) found 1,526
  US IPOs from 1975–84 returned 34.47% over three years from the first day's
  close against 61.86% for industry- and size-matched controls. Loughran and
  Ritter (1995) named it the "new issues puzzle"; the usual summary is 5–6%
  per year of underperformance for three to five years.
- **Practitioner-wise, IPOs produce the biggest winners.** O'Neil's own
  studies of past market leaders are full of names bought out of a first base
  within a year of listing.

These are not contradictory. One is the unconditional mean of the class; the
other is a heavily selected subset of it. But it has a hard consequence for
this project: **beating "every other stock" is the wrong test.** In a decade
that mostly went up, any long strategy clears it. The right test is beating
*other IPOs of similar vintage* — and because Ritter says that cohort drifts
*below* the market, an IPO-cohort baseline is a **softer** benchmark than a
market one, not a harder one. Both are reported, and which is which is stated
next to the number. See `METHOD.md` §6.

---

## 1. The IPO base (O'Neil / IBD)

### 1.1 Why it is a separate pattern at all

Every standard O'Neil base — cup-with-handle, flat base, double bottom —
requires a **prior advance** of roughly 30% into the base. That is the supply
the base is supposed to absorb. A stock that listed five weeks ago cannot have
one, so the requirement is waived, and with it most of the length rules that
depend on a stock having a history.

IBD's own framing: the IPO base is the exception granted to issues too young to
satisfy the standard requirements.

### 1.2 Criteria, with sources

| Criterion | Value | Source |
|---|---|---|
| Prior advance into the base | **waived** (a standard base needs ~30%) | O'Neil / IBD |
| Base length | **2 to 5 weeks**, occasionally as short as 7 sessions | [IBD via Yahoo Finance](https://sg.finance.yahoo.com/news/ipo-bases-unconventional-lead-big-224800356.html) |
| Base length, standard base, for contrast | 5 to 7 weeks minimum | O'Neil |
| Base depth | usually **< 20%**; **to 50%** in volatile markets | IBD (same source) |
| Base depth, standard base, for contrast | 12–33%, 50% only in bear markets and weaker for it | O'Neil |
| Left-side high | typically formed **within the first 25 trading days** of listing | IBD (same source) |
| Buy point | **10 cents above the left-side high of the base** | IBD (same source) |
| Breakout volume | ≥ **40% above the 50-day average** | O'Neil's standard rule |
| Breakout volume, for new issues | 50-day average may not exist; compare against the sessions available | IBD (same source) |
| Failure risk | explicitly higher than a standard base | [LuxAlgo](https://www.luxalgo.com/library/concept/ipo-base/) |

**A correction to the brief.** The handoff describes the IPO base as "shorter
than a normal base — five weeks minimum rather than seven". The published rule
is shorter still and stated as a *range, not a floor*: 2–5 weeks **total**,
with 7 sessions cited as the extreme. Five weeks is the top of the range, not
the bottom. Worked example from the same source — Concho Resources, listed
August 2007, initial high 13.49 on 14 August, 13 sessions of consolidation
correcting under 8%, buy point 13.59, +64% by late October.

### 1.3 The three numbers that matter most for a detector

1. **The left-side high is the pivot.** Not the base's highest high to date —
   the high on its *left* side, formed early. This distinction is the whole
   detector: a pivot taken as "highest high through `t`" includes the breakout
   bar and can never be exceeded. That bug cost the VCP work days
   (`pattern-detection/METHOD.md`), and the IPO base's own definition happens
   to prevent it, because the left-side high is by construction in the past.
2. **The base may be deeper than any other base.** A 50% drawdown that would
   disqualify a cup is inside tolerance here.
3. **Nothing requires a moving average.** No 200-day, no 52-week high, no
   trend template. This is the only base in the O'Neil catalogue a six-week-old
   stock can satisfy, and that is not an accident.

### 1.4 What is *not* published

No source gives a measured win rate, expectancy or failure rate for IPO bases
specifically. Bulkowski's *Encyclopedia* has statistics for cups, triangles and
flags; it has none for this. TraderLion advertises "a comprehensive study" but
the page is behind a 403. **So there is no published benchmark to compare this
project's numbers against.** That is worth knowing before anyone reads a result
here as confirming or contradicting the literature: there is no literature.

---

## 2. Which breakout? Three different setups

The brief asks which of three the literature supports. They are genuinely
different trades and the honest answer is that only one of them is documented.

| Setup | Definition | Support |
|---|---|---|
| **A. Above the listing-day high** | close/high exceeds day one's high | **None found.** No source in this review defines a setup this way. |
| **B. Above the first-week range** | exceeds the high of the first 5 sessions | **None found.** |
| **C. Above the first proper base** | exceeds the left-side high of a 2–5 week base | **This is the IBD IPO base**, §1. |

Only **C** is a published setup. A and B are folk constructions, and in India
they are worse than undocumented — they are **mechanically contaminated**, for
the reason in §4: the listing-day high is frequently not a supply level at all
but a circuit limit, and the first week's range is frequently the same circuit
applied five times.

That does not make A and B untestable — they are cheap, and a negative is a
result — but it does mean C is the setup and A/B are comparisons.

---

## 3. India's supply calendar: lock-in expiry

This is the part with no US analogue worth the name, and the part the brief is
right to call the most defensible thing in the project. US lockups are a single
contractual date, typically 180 days, set by the underwriter. India's are
**statutory, tiered, and knowable from the prospectus** — and therefore
knowable to a backtest from the listing date alone.

### 3.1 The schedule

| Holder | Locked for | Since | Before that |
|---|---|---|---|
| Anchor investors, 50% of allotment | **30 days** from allotment | always | — |
| Anchor investors, the other 50% | **90 days** from allotment | **IPOs opening on/after 1 Apr 2022** | there was no other 50%: all anchor stock was one flat 30-day lock |
| Non-promoter pre-IPO shareholders | **6 months** | **notified 13 Aug 2021** | 12 months |
| Promoter holding above the minimum | **6 months** | **notified 13 Aug 2021** | 12 months |
| Promoter minimum contribution (20% of post-issue capital) | **18 months** | **notified 13 Aug 2021** | 36 months |

Sources: SEBI ICDR (Second Amendment) Regulations 2021, approved at the board
meeting of 6 Aug 2021 and notified 13 Aug 2021; the anchor split is the
ICDR amendment applying to issues opening on or after 1 April 2022.

### 3.2 Two regime breaks sitting in the middle of the sample

**This is the trap.** A study that applies "50% of anchor unlocks at day 30,
the rest at day 90" to the whole 2016–2026 window is applying a rule that did
not exist for the first six years of it. Likewise "pre-IPO unlocks at 6 months"
is wrong for every IPO before August 2021, where the answer is 12.

So the unlock calendar is a **function of the IPO's own date**, not a constant:

```
anchor_unlocks(listing_date):
    if issue opened on/after 2022-04-01:  [+30d (50%), +90d (50%)]
    else:                                 [+30d (100%)]

pre_ipo_unlock(listing_date):
    if issue opened on/after 2021-08-13:  +6 months
    else:                                 +12 months
```

`ipolib/lockins.py` implements exactly this and nothing smarter.

### 3.3 Two approximations that have to be declared

1. **Lock-in runs from ALLOTMENT, not from listing.** This project knows
   listing dates (the first bar Dhan serves) and does not know allotment dates.
   The gap is the allotment-to-listing leg of the IPO timeline, which SEBI cut
   from T+6 to **T+3** for issues from December 2023. So "listing + 30 days" is
   roughly "allotment + 27 days" for recent IPOs and "allotment + 24 days" for
   older ones — a **2–4 session error that itself shifts mid-sample**. Any test
   of "the days around the unlock" must use a window wide enough to swallow it,
   and must say so. A ±3-session window is the minimum defensible one.
2. **Only the anchor dates are reliable.** Whether a promoter or a pre-IPO fund
   *actually sells* at 6 or 18 months is discretionary; whether anchor stock
   *becomes sellable* at 30 and 90 days is statutory. The 30/90 tests are the
   strong ones; 6 and 18 months are weaker and should be reported as such.

### 3.4 The mechanism, which is the point

Aggarwal, Krigman and Womack (2002) give the reason this is more than a
calendar curiosity: managers **strategically underprice** an IPO to generate
"information momentum" — attention that shifts the demand curve outward — and
then sell into it at lockup expiration. The selling at the unlock is not a
side effect; on that account it is the *plan*. That is a mechanism, not a
number, and per the brief's own rule a filter with a mechanism beats a filter
with a better backtest number.

---

## 4. Listing-day mechanics: the thing that silently shifts every entry

Getting this wrong moves every entry by a day and inverts the meaning of the
first bar. Verified against NSE's published rules and, where marked, against
this project's own data.

### 4.1 The special pre-open session

A newly listed security does **not** open at 09:15 like everything else. It
trades in a **special pre-open session (SPOS)** first; the equilibrium price
discovered there is what everyone calls "the listing price", and continuous
trading starts afterwards.

- The equilibrium price is the price at which the **maximum volume** can trade;
  ties are broken by the lowest order imbalance, then by proximity to the issue
  price.
- **No price band applies inside the SPOS.** A dummy filter of 25–75% exists
  only to reject non-genuine orders.
- If no equilibrium price is found, orders are cancelled and the session
  continues until one is.

**Consequence.** The listing-day bar's **open is the listing price**, which is
an auction outcome and not a traded price in the usual sense, and the bar
covers a shorter session than every bar after it. The "first traded price" and
the "listing price" are the same number here, which is *not* true of the
intraday tape.

### 4.2 The day-one band, and why it makes setups A and B suspect

Once the listing price is set, the **normal session's band on listing day is
measured from it**:

| Issue size | Listing-day band |
|---|---|
| ≤ ₹250 crore | **±5%** |
| > ₹250 crore | **±20%** |

Small issues are additionally put in **trade-to-trade (T2T)** for the first 10
days — delivery compulsory, no intraday.

**This is visible in the data and it is not subtle.** Paras Defence listed
1 Oct 2021 (issue ₹170.8 crore, so ±5%). Dhan's first bar: open 234.50, high
246.23, close 246.23. The close equals the high, and 246.23 / 234.50 = **1.0500
exactly**. That bar's high is not a supply level. It is the circuit.

So "break above the listing-day high" (setup A) is, for a large fraction of
small issues, "break above listing price × 1.05" — an arithmetic fact about
the exchange's rulebook, not an observation about buyers and sellers. Setup B
inherits the same problem for as long as the band binds.

`METHOD.md` §2 turns this into a measurement rather than an assumption: count
what fraction of listing-day bars close at exactly ±5% or ±20%, and report it.

### 4.3 The listing bar is not a normal bar, three times over

1. **Shorter session** (SPOS then a part-day of continuous trading).
2. **Different band** (±5/±20 from an auction price, not the usual limits).
3. **Volume that never recurs.** EaseMyTrip's first bar: 42.7 million shares.
   DMART's: 85.7 million. Any 50-day average volume that includes the listing
   bar is dominated by it for 50 sessions, which would make every subsequent
   "volume 40% above average" test fail by construction.

**Decision: the listing-day bar is excluded from every average and every
volatility estimate, and retained only as a price level.** See `METHOD.md` §3.

### 4.4 Prices are corporate-action adjusted

Verified arithmetically, not from vendor documentation. Paras Defence listed at
₹469 on NSE and later did a 1:2 split; Dhan's first bar opens at 234.50, which
is 469 / 2 to the paisa. EaseMyTrip's first bar opens at 6.63 against a ₹206
listing price, consistent with its subsequent bonus issues.

**Consequences:**
- Percentage returns, base depths, ATR-in-percent and every ratio are **safe**.
- Any comparison of a *rupee level* against the issue price — "listed at a 30%
  premium", "still below its issue price" — is **wrong** unless the issue price
  is adjusted by the same factor, and this project does not have the corporate
  action history to do that. Such comparisons are therefore **not made**.
- **Unverified:** whether Dhan adjusts volume alongside price. If it does not,
  a share-count volume series is discontinuous across a split. `METHOD.md` §3
  uses volume only as a ratio to its own trailing median, which is robust to a
  level shift everywhere except the ~50 sessions that straddle one.

---

## 5. Grey Market Premium

The repo now collects GMP (`ipo_gmp_readings`), so it is worth being precise
about what is and is not known.

**What the literature says.** It is thin, mostly Indian, mostly recent, and it
contradicts itself. One widely repeated study claims ~83.7% accuracy predicting
the *direction* of listing gains; a study of 270 Indian IPOs reports a strong
positive correlation with *listing-day* performance; other work finds no
statistically significant link to the actual listing price and little
explanatory power once institutional participation and market conditions are
controlled for. The fair summary is that GMP is a sentiment reading that
indicates the likely *sign* of the listing pop and estimates its *size* poorly.

**What matters here, and is untested anywhere.** Every one of those studies
stops at listing day. This strategy does not trade listing day; it trades a
breakout weeks later. **Whether GMP predicts anything past the listing bar is,
as far as this review found, unstudied.** Treat any claim that it does as
promotional until someone measures it.

**What this project can do about it: nothing, yet.** `ipo_gmp_readings` holds
24 rows against 12 IPOs — a forward feed that started in September 2026. It is
not a dataset and is not treated as one. The GMP question is written up in
`FINAL_LOGIC.md` §5 as future work with a stated minimum sample.

---

## 6. Subscription data as a predictor

**The claim.** QIB subscription is the informed-money signal — QIBs run
research desks — and retail enthusiasm without institutional backing is the
classic weak-listing tell. Scale, for context: KPMG puts FY2025's 80 mainboard
IPOs at an average 71× total and 102× QIB oversubscription. Against that
baseline "oversubscribed 40 times" is a *cold* issue, which is worth
remembering before treating a raw multiple as a signal.

**The evidence.** Weak. Long-run studies explain around 20% of the variation in
IPO underperformance, with leverage and offer price significant and
oversubscription not. High QIB subscription is widely *believed* to be
positive; it is not established that it survives as a predictor past the
listing day.

**What this project can do about it: nothing.** Subscription multiples exist in
no source available here — not in Dhan (market data only), not in the repo
(`ipos` has 12 rows and no subscription columns), and not in
investorgain's JSON endpoint, which **ignores its year parameter and returns
only the live list** regardless of the year requested. Chittorgarh has the
history and paywalls it past five rows per year.

**So subscription-as-predictor is declared untestable here**, with the data it
would need named in `FINAL_LOGIC.md` §5. It is not quietly dropped and it is
not approximated by something else.

---

## 7. What this project inherits, and what it must re-derive

The brief's warning that "no history means no indicators" is the single biggest
design constraint, so here it is as a table.

| Inherited criterion | Needs | Verdict for a 6-week-old listing |
|---|---|---|
| Price > 200-day MA | 200 sessions | **impossible** — dropped |
| Price within 25% of 52-week high | 250 sessions | **impossible** — dropped |
| 200-day MA trending up ≥ 1 month | 220 sessions | **impossible** — dropped |
| Relative strength rank vs market | ~250 sessions, conventionally | **re-derived** over the sessions available, against an IPO cohort |
| Prior advance ≥ 30% into the base | a pre-base history | **waived by the pattern itself** (§1.1) |
| ATR(14), Wilder-smoothed | ~60 sessions to settle | **re-derived** as raw true range over the window measured — the brief's trap, and the fix that took VCP recall from 52% to 89% |
| Volume > 1.4 × 50-day average | 50 sessions, excluding the listing bar | **re-derived** as a ratio to the trailing median of available non-listing bars |

**The minimum viable history** falls out of this rather than being chosen: the
longest window any surviving criterion needs. With a 2–5 week base, a left-side
high inside 25 sessions, and volume compared against a trailing median, that is
about **30 sessions after the listing bar**. `METHOD.md` §3 fixes the number and
says what is refused below it.

---

## 8. Open questions this research could not close

Carried forward verbatim into `FINAL_LOGIC.md` rather than resolved quietly.

1. **No published benchmark for IPO-base performance exists** (§1.4). Nothing
   here can be compared against a prior number.
2. **Does GMP predict anything after listing day?** Unstudied, and untestable
   here for want of history (§5).
3. **Do subscription multiples survive as a post-listing signal?** Weakly
   supported, untestable here for want of data (§6).
4. **Does Dhan adjust volume for splits as it adjusts price?** Unverified
   (§4.4). Mitigated by construction, not resolved.
5. **How long does the ±5/±20 band bind after listing day?** Day one is
   sourced; beyond that this review found no clean statement, so `METHOD.md` §2
   measures it from the data instead of citing it.
6. **What fraction of mainboard IPOs since 2016 have been delisted?**
   Unknown, and it is the residual survivorship hole in even the rebuilt
   universe (`README.md` §1).

---

## Sources

- [IBD — "IPO Bases Are Unconventional, But They Can Lead To Big Gains"](https://sg.finance.yahoo.com/news/ipo-bases-unconventional-lead-big-224800356.html)
- [LuxAlgo — IPO Base concept](https://www.luxalgo.com/library/concept/ipo-base/)
- [Ritter (1991), "The Long-Run Performance of Initial Public Offerings", *Journal of Finance*](https://onlinelibrary.wiley.com/doi/full/10.1111/j.1540-6261.1991.tb03743.x)
- [Ritter — updated long-run IPO statistics](https://site.warrington.ufl.edu/ritter/files/IPOs-long-run-returns-on-IPOs.pdf)
- [Aggarwal, Krigman & Womack — strategic underpricing, information momentum and lockup expiration selling](https://www.sciencedirect.com/science/article/abs/pii/S0304405X02001526)
- [SEBI ICDR lock-in framework](https://www.corporateprofessionals.com/articles/demystifying-the-ipo-lock-in-framework-under-the-sebi-icdr-regulations/)
- [SEBI halves promoter lock-in to 18 months (Aug 2021)](https://www.business-standard.com/amp/article/markets/sebi-halves-the-post-ipo-lock-in-period-for-promoters-to-18-months-121080601554_1.html)
- [SEBI extends anchor lock-in to 90 days for half the allotment](https://www.devdiscourse.com/article/business/1863663-sebi-extends-anchor-investors-lock-in-period-in-ipo-to-90-days-from-1-month)
- [NSE — Special Pre-Open Session](https://www.nseindia.com/static/products-services/equity-market-special-pre-open-session)
- [NSE — Member FAQs on the Special Pre-Open Session (PDF)](https://archives.nseindia.com/common/pdf/Member_FAQ_on_SpecialPreOpenSession_inCapitalMarketSegment.pdf)
- [Chittorgarh — year-wise mainboard IPO counts](https://www.chittorgarh.com/report/list-of-ipo-by-year-fund-raised-success-mainboard/85/mainboard/)
- [Research on GMP and listing performance](https://www.researchgate.net/publication/402245833_Evaluating_the_Influence_of_Grey_Market_Premium_on_IPO_Subscription_and_Listing_Performance)
