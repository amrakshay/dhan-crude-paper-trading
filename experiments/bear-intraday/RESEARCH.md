# Bear-market intraday research: prior evidence and constraints

Historical backtesting only. No orders, not investment advice. Compiled 2026-10-03.

Conventions: **[IN]** Indian evidence, **[US]** US-only, **[INTL]** multi-country with
India not confirmed in the sample. **UNVERIFIED** = not confirmed from a primary or
reputable source. "Reported" = secondary source (press, broker) quoting a primary
document I could not open.

Corrections to the brief, all checked against sources below:

- The Oct-2024 SEBI index-derivatives circular is **SEBI/HO/MRD/TPD-1/P/CIR/2024/132**, not /129.
- SEBI's intraday cash-segment study came out on **24 Jul 2024** (PR 37/2024), not July 2023.
  It covers FY19, FY22 and FY23.
- An option **buyer does pay STT when closing the trade by selling it**. STT on options is
  levied on the sell side of the premium, whoever sells. The buyer pays nothing on the
  opening buy. Exercise is taxed separately.
- SEBI's own list of when each measure took effect gives **10 Feb 2025** for upfront premium
  and the calendar-spread change, and **2 Jan 2025** for the larger NSE contract size.
  The circular's own dates (1 Feb 2025; 20 Nov 2024 for new contracts) differ.
- There is a newer study than FY25: **SEBI, Aug 2026, covering FY25–FY26**.

---

## 1. Base rates (all [IN], SEBI primary unless marked)

### 1a. Equity F&O, individuals

| Study (published) | Period | Loss-makers | Aggregate net loss | Avg per trader | Source |
|---|---|---|---|---|---|
| SEBI study, 25 Jan 2023 | FY22 | 89% of individuals (90% of "active" traders) | — | Avg loss ₹1.1 lakh. Active loss-makers ₹1.25 lakh. Loss-makers paid a further **28%** of their losses as transaction costs. Profit-makers paid 15–50% of profits. | [SEBI](https://www.sebi.gov.in/reports-and-statistics/research/jan-2023/study-analysis-of-profit-and-loss-of-individual-traders-dealing-in-equity-fando-segment_67525.html); figures reported by [Business Standard](https://www.business-standard.com/amp/article/markets/sebi-study-suggests-89-retail-traders-in-equity-f-o-suffered-losses-123012501466_1.html) |
| SEBI updated study, 23 Sep 2024 | FY22–FY24 | **93%** of more than 1 crore individuals | **> ₹1.8 lakh crore** over 3 years | About **₹2 lakh** each over 3 years, including costs. The top 3.5% of loss-makers (~4 lakh people) lost ₹28 lakh each. Only 1% made more than ₹1 lakh. | [SEBI PR](https://www.sebi.gov.in/media-and-notifications/press-releases/sep-2024/updated-sebi-study-reveals-93-of-individual-traders-incurred-losses-in-equity-fando-between-fy22-and-fy24-aggregate-losses-exceed-1-8-lakh-crores-over-three-years_86906.html) |
| SEBI comparative study, Jul 2025 (Table 11) | FY22 / FY23 / FY24 / **FY25** | 90.2% / 91.7% / 91.1% / **91.0%** | ₹40,824 / 65,747 / 74,812 / **₹1,05,603 crore** | ₹95,517 / 1,12,677 / 86,728 / **₹1,10,069** | [SEBI PDF](https://www.sebi.gov.in/sebi_data/attachdocs/jul-2025/1751900271726.pdf). Top 13 brokers, ~96 lakh traders. |
| **SEBI study, Aug 2026** | FY25 (revised) / **FY26** | 90.9% / **87.7%** (options 87.7%, futures 66.0%) | ~₹1.12 lakh crore / **₹91,685 crore** | ₹1.13 lakh / **₹1.17 lakh**. Avg loss among loss-makers ₹1.47 lakh; avg profit among profit-makers ₹1.22 lakh. | [SEBI PDF](https://www.sebi.gov.in/sebi_data/attachdocs/aug-2026/1787233506209.pdf), pp. 5–8. Broker sample widened from 13 to 15, so FY25 was revised. |

Other figures from the Aug 2026 study that matter here:

- **Options account for 92% of individual losses.**
- In FY25, **70%** of index-options turnover was on expiry day itself (0DTE). That fell to **59%** in FY26.
- Individuals pay about **₹25,000 crore a year** in transaction costs.
- STT's share of those costs rose from 13% (FY22) to 27% (FY26).
- 99% of FPI and prop profits were made by "algo entities".

### 1b. Intraday, cash segment, individuals

SEBI study, 24 Jul 2024 ([PR 37/2024](https://www.sebi.gov.in/media-and-notifications/press-releases/jul-2024/sebi-study-finds-that-7-out-of-10-individual-intraday-traders-in-equity-cash-segment-make-losses_84948.html)).
The sample is the top 10 brokers, about 86% of individual cash-segment clients.

| Metric | FY19 | FY22 | FY23 | Source |
|---|---|---|---|---|
| Loss-makers | **65%** | UNVERIFIED (not found) | **71%** ("7 in 10") | Reported: [tradingqna summary](https://tradingqna.com/t/key-findings-from-sebis-analysis-of-intraday-trading-by-individuals-in-equity-cash-segment/170633), [BS](https://www.business-standard.com/amp/markets/news/7-in-10-intraday-traders-in-equity-cash-suffered-losses-in-fy23-sebi-study-124072400975_1.html) |
| Loss-makers among very frequent traders (>500 trades a year) | — | — | **80%** | [Motilal summary](https://www.motilaloswal.com/learning-centre/2024/7/sebi-study-reveals-key-insights-on-individual-intraday-traders-in-india) |
| Trading costs | — | — | Loss-makers: **57%** of losses. Profit-makers: **19%** of profits. | same |
| Intraday traders (top 10 brokers) | 15 lakh | — | 69 lakh | same |
| Average loss per trader | — | — | ₹5,371 (reported, UNVERIFIED against the PDF) | [BS](https://www.business-standard.com/amp/markets/news/over-70-intra-day-traders-incur-losses-during-fy23-reveals-sebi-study-124072401110_1.html) |

**Takeaway.** The cash intraday base rate (about 70% lose) is less bad than F&O (88–93%).
In both, costs are a first-order share of losses. Any edge the tests find must clear costs
first, and the 57% and 28% figures show how much costs alone take.

---

## 2. How falling markets behave intraday

| Claim | Evidence | Market | Source |
|---|---|---|---|
| Volatility rises more after falls than after rises (asymmetric volatility, the "leverage effect") | EGARCH/TGARCH on NIFTY 50 finds a significant leverage effect | [IN] | Narayan & Madegowda (2019), *IJFM* 8(4) [link](https://publishingindia.com/archive/ijfm/a-study-on-unfolding-volatility-and-leverage-effect-in-indian-stock-market) |
| India VIX reacts more to negative NIFTY returns than to positive ones | The negative return–VIX relation is asymmetric. Large up-moves are largely unrelated to VIX. | [IN] | Dhanaiah, Reddy & Prasad (2012), *Indian J. Finance* [link](https://www.indianjournalofentrepreneurship.com/index.php/IJF/article/view/72415). Also Chakrabarti & Kumar (2020) [link](https://ideas.repec.org/a/jda/journl/vol.54year2020issue3pp53-68.html) (match to that paper UNVERIFIED). |
| Larger opening gaps in bear markets | No study found | — | **UNVERIFIED.** Measure it on our own 9.5 years of 5-min bars, split by regime. |
| The largest up-days cluster inside bear markets | The S&P 500's 10 biggest one-day gains all came in bear markets, and so did the Nasdaq's. The S&P had 11 sessions of +4% or more in the 2008–09 bear market. | [US] | [Irish Times, citing D. Rosenberg](https://irishtimes.com/business/personal-finance/stocktake-big-one-day-gains-are-bearish-not-bullish-1.4195043) |
| Same, as shares of the best days | About 42–50% of the S&P 500's best days in 20 years fell in bear markets, and another 34–36% in the first two months of a bull market | [US] | Hartford Funds, *10 Things About Bear Markets* [PDF](https://www.hartfordfunds.com/dam/en/docs/pub/whitepapers/CCWP073.pdf) (two versions give different numbers) |
| India example | NIFTY rose **+8.7%** on 7 Apr 2020, inside the COVID bear market. That was its largest one-day gain since May 2009. | [IN] | Reported: [DSIJ 8 Apr 2020](https://insights.dsij.in/dsijarticledetail/index-trend-and-stocks-in-action-on-april-08-2020-12309). Verify from our own daily bars. |
| Past losers rebound violently in "panic states" (after declines, when volatility is high), so short-the-losers strategies crash | Momentum crashes happen when bear markets turn. Loser-decile betas can exceed 3. | [US] 1927–2013 | Daniel & Moskowitz (2016), *JFE* [NBER w20439](https://www.nber.org/papers/w20439) |
| **Intraday momentum:** the first half-hour return (measured from the prior close) predicts the last half-hour | Slope 0.069, R² 1.6%. R² is **3.3% on high-volatility days**. Also stronger on recession and macro-news days. | [US] SPY 1993–2013 | Gao, Han, Li & Zhou (2018), *JFE* 129(2) [link](https://profiles.wustl.edu/en/publications/market-intraday-momentum/); [SSRN pdf](https://www.smallake.kr/wp-content/uploads/2015/01/SSRN-id2440866.pdf) |
| Intraday momentum across countries | Significant in 12 of 16 developed markets. Stronger when **volatility is high** and liquidity is low. | [INTL] developed markets only (India not in the sample) | Li, Sakkas & Urquhart (2022), *J. Fin. Markets* 57 [RePEc](https://ideas.repec.org/a/eee/finmar/v57y2022ics138641812100001x.html) |
| Mechanism: dealers hedging **short gamma** trade with the move, which builds momentum into the close. The effect **reverts over the next days.** | Found in 60+ futures, 1974–2020 | [INTL] (whether India is in the sample is UNVERIFIED) | Baltussen, Da, Lammers & Martens (2021), *JFE* 142 [pdf](https://www3.nd.edu/~zda/intramom.pdf) |
| Intraday momentum in India | A preprint reports strong last-half-hour momentum and an out-of-sample R² of about 2.96 for the last 20 minutes | [IN] | **UNVERIFIED.** ResearchGate preprint, authors and venue not confirmed [link](https://www.researchgate.net/publication/383567351_Hedging_Demand_and_Intraday_Momentum_within_the_Indian_Stock_Market) |
| Intraday **reversal** after extreme moves | Returns reverse within minutes of extreme rises and falls. **Unjustified falls are corrected faster** than unjustified rises. | [IN] | Tripathi & Aggarwal (2015), *IJFM* 5(1) [link](https://publishingindia.com/archive/ijfm/prior-return-effect-in-indian-stock-market-an-intra-day-analysis) |
| Intraday overreaction carries into the next day's return | Positive and significant. A long-overreaction / short-underreaction factor is not explained by standard factors. | [IN] | Siddiqui & Misra (2025), *J. Asset Mgmt* 26(5) [RePEc](https://ideas.repec.org/a/pal/assmgt/v26y2025i5d10.1057_s41260-025-00418-y.html) |
| Does intraday momentum or reversal **change by regime**? | US and developed-market work says momentum is stronger on high-volatility days. No India regime split found. | [US]/[INTL] | **India: UNVERIFIED.** Test directly. |

---

## 3. Indian short-selling mechanics (constraints) [IN]

| Rule | Detail | Source |
|---|---|---|
| Cash intraday (MIS) shorts must be closed the same day | A short that is not covered becomes a **short delivery** and goes to the clearing corporation's auction | [SEBI 2007 short-selling circular](https://www.sebi.gov.in/legal/circulars/dec-2007/short-selling-and-securities-lending-and-borrowing_9463.html) |
| No naked short sales; all investor classes may short sell; institutions may not day-trade | The 20 Dec 2007 framework. Securities Lending & Borrowing (**SLB**) is the only route to an overnight cash short. | same, plus [SEBI PR](https://www.sebi.gov.in/media/press-releases/dec-2007/sebi-permits-short-selling-by-institutional-investors-securities-lending-and-borrowing-scheme-to-be-put-in-place_8405.html) |
| **Close-out price** when the auction fails | The **higher** of (a) the highest price in that scrip from the trade's settlement up to the auction or close-out day, and (b) **20% above** the latest closing price on the day auction offers are called. Defined in SEBI Master Circular SEBI/HO/MRD/MRD-PoD-2/CIR/P/2024/00181 (30 Dec 2024). Auction penalty applies to clearing-corporation-level shortages. Since 2025, broker-internal shortages are auctioned too. | [NCL/CMPT/71441, 24 Nov 2025](https://nsearchives.nseindia.com/content/circulars/CMPT71441.pdf) (primary) |
| Day of the auction under T+1 settlement | One older broker page says T+2, 2:00–2:45 pm | **UNVERIFIED for current T+1** ([Ventura/Rupeezy-type pages](https://support.rupeezy.in/support/solutions/articles/21000004910-how-does-the-short-delivery-auction-work)) |
| Price bands on F&O stocks | There is **no fixed circuit**. A **dynamic price band** starts at **10%** and flexes by **5%** once a trade prints at 9.90% or more and the flex criteria are met: 50 trades, 10 distinct client codes and 3 trading members on each side (since 30 May 2024). The band **slides** rather than widens, and orders beyond it are cancelled (since 18 Nov 2024). A 15-minute cooling-off before a flex is reported by a broker but not seen in the primary circular. | [NSE/FAOP/64995, 8 Nov 2024](https://nsearchives.nseindia.com/content/circulars/FAOP64995.pdf); [NSE FAQ](https://nsearchives.nseindia.com/web/sites/default/files/inline-files/FAQ_on_Sliding_Price_Bands.pdf); cooling-off from [Fyers](https://fyers.in/notice-board/revised-dynamic-price-bands-and-fo-segment-rules/) |
| Market-wide circuit breakers | Triggered by NIFTY 50 or SENSEX moving **10%, 15% or 20%**. 10%: 45-min halt before 13:00, 15 min from 13:00 to 14:30, no halt after 14:30. 15%: 1h45m before 13:00, 45 min from 13:00 to 14:00, rest of the day after 14:00. 20%: rest of the day. Trading resumes with a pre-open call auction. | [NSE MWCB page](https://www.nseindia.com/static/products-services/equity-market-circuit-breakers); [SEBI 2001](https://www.sebi.gov.in/legal/circulars/jun-2001/index-based-market-wide-circuit-breaker-in-compulsory-rolling-settlement_17986.html) |
| F&O ban (OI > 95% of MWPL; lifted below 80%) | Blocks **new F&O positions only**. **Cash-segment trading, including intraday shorts, is not restricted.** SEBI changed the ban metric to a future-equivalent OI basis on 29 May 2025 (details UNVERIFIED here). | [Motilal](https://www.motilaloswal.com/learning-centre/2023/7/why-is-intraday-trading-not-allowed-for-contracts-if-the-underlying-security-is-under-a-ban-period), [tradingqna](https://tradingqna.com/t/securities-under-ban-what-does-this-mean-can-we-trade-in-intrady-equity-and-f-o-in-these-scrips-which-are-banned/4895); SEBI Jul-2025 study, para 3 (above) |
| Peak margin and intraday leverage | Phased in from Dec 2020 under SEBI/HO/MRD2/DCAP/CIR/P/2020/127 (20 Jul 2020). Reached 100% on **1 Sep 2021**. Intraday equity margin is the greater of VaR+ELM and **20%**, so **at most 5x**. Intraday F&O is 1x of the NRML margin. | [Zerodha bulletin](https://zerodha.com/marketintel/bulletin/304576/changes-in-intraday-leverages-from-september-2021), [Sharekhan copy of SEBI circular](https://www.sharekhan.com/MediaGalary/Newsletter/Annexure-A-SEBI-circular-dated-July-20-2020.pdf) |
| **Dhan** intraday margin | "a limit which is as per the exchange defined VAR+ELM for the respective scrips and subject to a minimum of 20%". Trade-to-trade, restricted and illiquid scrips are not allowed intraday. | [Dhan RMS policy](https://dhan.co/risk-management-policy/) |
| **Dhan: MIS short on *all* F&O stocks?** | Dhan's pages say the intraday product is "available in all segments". **No page found that says intraday shorting is allowed on every F&O name.** | **UNVERIFIED.** Check Dhan's live intraday scrip list. [Dhan product types](https://dhan.co/support/orders-and-positions/order-types/what-are-the-product-types-on-dhan-while-placing-orders-what-is-intraday-delivery-cnc-mis-mtf-normal-margin/) |
| Dhan auto square-off time | RMS policy: cash **15:10 onwards**, derivatives 15:25 onwards. A support page says 15:20, with no new orders after 15:18. Post-CAS times for F&O stocks are **UNVERIFIED** (one broker summary says about 15:00). | [Dhan RMS](https://dhan.co/risk-management-policy/), [Dhan square-off](https://dhan.co/support/orders-and-positions/order-placement-and-modifications/what-is-intraday-square-off/), [Share.Market](https://www.share.market/buzz/circulars/closing-auction-session/) |
| **Closing Auction Session (CAS)** | **Live from 3 Aug 2026** for cash-segment stocks that have F&O. Continuous trading ends at **15:15**. The auction runs 15:15–15:35 within a ±3% band (band reported by a broker) and replaces the VWAP close. Equity derivatives now trade until **15:40**. | [NSE/CMTR/75479 (30 Jul 2026)](https://nsearchives.nseindia.com/content/circulars/CMTR75479.pdf), [NSE/FAOP/75472](https://nsearchives.nseindia.com/content/circulars/FAOP75472.pdf); timeline from [Share.Market](https://www.share.market/buzz/circulars/closing-auction-session/) |

**Backtest consequences:**

- For F&O names, exits after 3 Aug 2026 must happen by about 15:10 at the latest.
- Before that date the cash close was 15:30.
- The same strategy therefore has a shorter last half-hour in the two eras. That matters
  for any rule that relies on intraday momentum into the close.

---

## 4. Put-buying mechanics

### 4a. Expiries and the Oct-2024 framework [IN]

**Expiry days**

- **NIFTY weekly expiry is Tuesday**, for contracts expiring on or after 1 Sep 2025.
  The first Tuesday weekly was 2 Sep 2025.
- Monthly NIFTY contracts expire on the last Tuesday of the month.
- SEBI's 29 May 2025 circular restricts each exchange to Tuesday or Thursday. BSE (SENSEX) is Thursday.
- Sources: [NSE FAOP68589 via tradingqna](https://tradingqna.com/t/sebi-confirms-derivatives-expiries-tuesday-for-nse-thursday-for-bse/183366),
  [Business Today](https://businesstoday.in/markets/stocks/story/nse-revises-expiry-dates-for-index-derivatives-contracts-from-august-29-481484-2025-06-23).

**BANKNIFTY has no weekly options**

- The last BANKNIFTY weekly expired on **13 Nov 2024**. Only monthlies remain, on the last Tuesday.
- FINNIFTY and MIDCPNIFTY weeklies were discontinued at the same time.
- Source: [Motilal](https://www.motilaloswal.com/learning-centre/2024/10/nse-discontinues-weekly-derivatives-on-bank-nifty-nifty-midcap-select-and-finnifty).

**Circular SEBI/HO/MRD/TPD-1/P/CIR/2024/132, 1 Oct 2024** ([SEBI](https://www.sebi.gov.in/legal/circulars/oct-2024/measures-to-strengthen-equity-index-derivatives-framework-for-increased-investor-protection-and-market-stability_87208.html)).
Effective dates below are SEBI's own, from its Jul 2025 study, Table 1.

| Measure | Effective |
|---|---|
| One weekly index product per exchange | 20 Nov 2024 |
| Extra **2% ELM on short options** on expiry day | 20 Nov 2024 |
| Minimum contract value **₹15 lakh** (previously ₹5–10 lakh) | NSE 2 Jan 2025 (new contracts from 20 Nov 2024) |
| Upfront collection of option premium from buyers | 10 Feb 2025 (circular said 1 Feb) |
| No calendar-spread margin benefit on expiry day | 10 Feb 2025 (circular said 1 Feb) |
| Intraday monitoring of position limits | 1 Apr 2025 (penalties waived per 28 Mar 2025 circular) |

The brief's "₹15–20 lakh" range is not confirmed. Sources state a ₹15 lakh minimum.
Upper bound UNVERIFIED.

### 4b. Lot sizes [IN]

| Date (contracts affected) | NIFTY | BANKNIFTY | Source |
|---|---|---|---|
| 2021 (month UNVERIFIED) | 75 → 50 | 25 | [tradingqna history](https://tradingqna.com/t/historical-fno-lot-sizes/187320) |
| From Jul-2023 expiry | 50 | 25 → **15** | [Zerodha bulletin](https://zerodha.com/marketintel/bulletin/347941/revision-in-lot-size-of-index-derivative-contracts) |
| 26 Apr 2024 | 50 → **25** | 15 | [Business Standard](https://www.business-standard.com/markets/stock-market-news/nse-halves-lot-size-for-trading-derivatives-contracts-for-nifty50-index-124040201141_1.html) |
| 20 Nov 2024 (₹15 lakh minimum) | 25 → **75** | 15 → **30** | SEBI circular /132 (above) |
| Jul-2025 expiries onward (NSE FAOP67372) | 75 | 30 → **35** | [Fyers](https://fyers.in/notice-board/revision-in-market-lot-for-index-derivative-contracts-from-25th-april-2025/) |
| NSE/FAOP/70616 (Oct 2025). Weeklies from 6 Jan 2026 expiry; monthlies from 27 Jan 2026; quarterlies re-lotted at EOD 30 Dec 2025. | 75 → **65** | 35 → **30** | [Zerodha bulletin](https://zerodha.com/marketintel/bulletin/429705/revision-in-lot-size-of-index-derivative-contracts-from-december-30-2025) |

**Current (2026-10-03): NIFTY 65, BANKNIFTY 30.** No later revision found, but a 2026
semi-annual review could have changed this. Treat as **UNVERIFIED for current
contracts** and read the lot from the instrument master.

### 4c. STT on derivatives [IN]

| Leg | Before 1 Oct 2024 | 1 Oct 2024 | **1 Apr 2026 (current)** | Source |
|---|---|---|---|---|
| Futures, sell side | 0.0125% | 0.02% | **0.05%** | [Business Standard 2024](https://www.business-standard.com/markets/news/higher-stt-and-transaction-charges-on-nse-bse-mcx-effective-today-124100100146_1.html); [ICICI Direct 2026](https://www.icicidirect.com/ilearn/futures-and-options/articles/stt-changes-in-budget-2026-what-f-o-traders-should-know); [Zerodha](https://support.zerodha.com/category/trading-and-markets/trading-faqs/articles/how-is-the-securities-transaction-tax-stt-calculated) |
| Options, **sell side of premium**. This includes a long put closed by selling it. | 0.0625% | 0.1% | **0.15%** | same; buyer-pays-on-exit mechanism: [Zerodha Z-Connect](https://zerodha.com/z-connect/trending/stt-options-nse-bse-mcx-sx) |
| Options **exercised**, on intrinsic value, paid by the buyer | 0.125% | 0.125% | **0.15%** | ICICI Direct / Zerodha (above) |
| Equity intraday, sell side | 0.025% | 0.025% | 0.025% (unchanged) | Zerodha (above) |

For a long put closed on the same day, the round trip is **0.15% of the exit premium**,
plus brokerage, exchange charges, GST and stamp duty. It is **not** zero.

### 4d. Greeks near weekly expiry

Rows marked "computed" use standard Black-Scholes, not an empirical claim
([Greeks reference](https://en.wikipedia.org/wiki/Greeks_(finance))).

| Topic | What is known | Market / source |
|---|---|---|
| Theta near expiry | An ATM option is worth about **0.4·S·σ·√T**, so time value goes with √T. Going from 2 trading days to 1 loses about 29% of ATM value. **All remaining time value goes in the final session.** Illustration (S=25,000, σ=15%): ATM ≈ 133 points at T=2 days and ≈ 94 points at T=1 day, which works out to about 15 points an hour on expiry day (front-loaded per unit of variance). | Computed |
| Vega near expiry | ATM vega ≈ 0.4·S·√T. At T=1 day that is about **6 points per vol point** in the illustration above, so a 5-point India VIX spike adds about 30 points, roughly a third of the premium. Vega shrinks with √T, so a vol spike helps a near-expiry put less than a farther expiry. | Computed |
| Vega gain in a selloff, IV crush on the bounce | India VIX rises more on down days than it falls on up days. On a relief rally a long put loses both **delta and vega** at once. | [IN] Dhanaiah et al. 2012 (§2) |
| Skew (puts rich) | NIFTY options show a smile or smirk: OTM puts trade at higher IV than ATM | [IN] [JIBR 2013 (doi 10.1108/JIBR-12-2013-0103)](https://www.doi.org/10.1108/JIBR-12-2013-0103); [Varma, IIMA WP 2002-04-01](https://faculty.iima.ac.in/~jrvarma/papers/WP2002-04-01.pdf) |
| Expiry-day gamma | 59–70% of index-options turnover is 0DTE (§1). Dealer short-gamma hedging pushes moves toward the close (Baltussen et al., §2). | [IN] SEBI 2026 / [INTL] mechanism |
| **Strike choice (ATM vs 1-ITM vs OTM)** for an intraday move | **No peer-reviewed Indian study found.** US: expected put returns are below the risk-free rate and **rise with strike** (deep OTM puts are the worst). Zero-beta ATM straddles lose about 3% a week. | [US] Coval & Shumway (2001), *J. Finance* [link](https://deepblue.lib.umich.edu/items/455dbe73-b56b-44f3-841f-c7751deffc7a). India: UNVERIFIED. Test ATM vs 1-ITM empirically. |

---

## 5. Shortlisting literature

| Feature | Evidence | Market | Verdict |
|---|---|---|---|
| **12-1 month momentum** (cross-sectional) | WML factor averaged 21.9% a year, 1994–2014 | [IN] Agarwalla, Jacob & Varma, IIMA WP 2013-09-05 [link](https://www.iima.ac.in/publication/four-factor-model-indian-equities-market), [data](https://faculty.iima.ac.in/iffm/Indian-Fama-French-Momentum/) | **Evidence** (monthly horizon, long-short). The link to *intraday* short returns is UNVERIFIED. |
| Momentum **crashes after bear markets** | Losers rally hardest when a panic turns | [US] Daniel & Moskowitz 2016 (§2) | **Evidence**, against shorting the weakest names late in a decline |
| **1-month short-term reversal** | Short-horizon continuation (not reversal) and long-horizon contrarian returns. Monthly NSE work finds short-term momentum and long-term overreaction. | [IN] Sehgal & Balakrishnan (2002), *Vikalpa* 27(1) [profile](https://vidwan.inflibnet.ac.in/profile/40561); [overreaction, Inderscience](https://inderscience.com/filter.php?aid=132959) | **Weak / mixed** in India. The 1-month reversal is mainly a [US] result. |
| Intraday relative weakness | Reversal within minutes of extreme moves, and falls are corrected faster | [IN] Tripathi & Aggarwal 2015; Siddiqui & Misra 2025 | **Weak.** It points toward **reversal**, not continuation. |
| **Sector rotation / industry momentum** | Industry momentum explains much of stock momentum | [US] Moskowitz & Grinblatt (1999), *J. Finance* [RePEc](https://ideas.repec.org/a/bla/jfinan/v54y1999i4p1249-1290.html). India: sector-level momentum in 4 sectors, 2000–13 (Garg & Varshney, per [Capitalmind](https://www.capitalmind.in/insights/best-momentum-investing-comprehensive-guide)). | **Evidence [US]; weak [IN]** |
| **Futures OI "short buildup"** (price down, OI up) | Indian work studies price–volume–OI *correlation* and volatility, not out-of-sample return prediction. SSF quotes lead stock quotes for price discovery. | [IN] [IIMB repository](https://repository.iimb.ac.in/handle/2074/19063); [Christ Univ.](https://christuniversity.in/major-research-project-on-causal-nexus-between-settlement-prices,-trading-volume,-open-interest-and-volatility-on-indian-capital-market:-evidence-from-stock-futures) | **Folklore** as a directional signal (no peer-reviewed predictive result found) |
| **Prior-day close near low** (CLV) | No peer-reviewed study found | — | **Folklore.** UNVERIFIED. |
| **Gap behaviour** (gap-down continuation or fill) | Fill rates come from trader blogs only. US: momentum profits accrue **overnight** and other strategies' profits accrue intraday, with opposite signs. | [US] Lou, Polk & Skouras (2019), *JFE* [pdf](https://personal.lse.ac.uk/polk/research/TugOfWar.pdf). India: blogs only ([example](https://optionx.trade/blogs/nifty-bank-nifty-gap-fill-strategy)). | **Evidence [US] for the overnight/intraday split; folklore [IN] for gap rules** |
| **Betting against beta** (low-beta beats high-beta, unconditionally) | The BAB factor beats size, value and momentum in India | [IN] Agarwalla, Jacob, Varma & Vasudevan, IIMA WP 2014 [link](https://www.iima.ac.in/hindi/index.php/node/16275) | **Evidence** |
| **High beta in down markets** (conditional) | High-beta stocks fall more in down markets, mechanically. Indian *downside* beta also earns a **positive premium**: shorting high-downside-beta names pays only on the down day itself. | [IN] Menon et al. (2025), *IJoEM* 20(1) [link](https://emerald.com/insight/content/doi/10.1108/IJOEM-01-2021-0026/full/html). Conditional-beta framework (Pettengill et al. 1995): [US] | **Evidence** (the contemporaneous down-day effect is near-mechanical; the forecasting value is UNVERIFIED) |

---

## What this means for the tests

- **Costs come before edge.** Cash intraday losers already pay 57% of their losses in costs,
  and F&O losers pay 28%.
  - Since 1 Apr 2026, futures STT is 0.05% of the sell side (4x the pre-Oct-2024 rate).
  - Selling a long put to close it costs 0.15% of the premium.
  - Any gross edge below about 10–15 bps a trade on futures is probably dead. Model costs
    by date (three STT eras) and do not use today's rates for the whole history.
- **The relief-rally left tail is the main risk, not a footnote.** In the US the biggest
  up-days cluster inside bear markets. NIFTY's +8.7% (Apr 2020) did the same, and past losers
  crash hardest at the turn. Report worst-day and worst-week P&L by regime, and cap exposure
  on high-VIX days, which are exactly when shorts feel most right.
- **Puts versus futures or MIS.**
  - A same-day long ATM put pays theta that is entirely front-loaded on expiry day (Tuesday).
  - On a bounce it loses vega as well as delta, because India VIX falls on up-days.
  - In exchange the loss is capped at the premium. A short future or MIS short has no cap,
    and the close-out rule (highest price, or +20%) punishes any failed square-off.
  - Test ATM vs 1-ITM puts against futures on identical signals. US evidence says avoid
    deep OTM. There is no Indian strike evidence.
- **Rules that depend on the close need two eras.** CAS (3 Aug 2026) ends continuous
  trading at 15:15 for F&O names, and Dhan's cash square-off starts by 15:10 at the latest.
  A "momentum into the close" rule has a different window before and after that date.
- **Intraday momentum is the best-supported intraday prior.** It is stronger on
  high-volatility days in the US and developed markets, and gamma-driven. India has only an
  unverified preprint, and Indian work on extreme moves finds *reversal*. Test both signs on
  NIFTY 5-min bars and report the bear-regime split explicitly.
- **Shortlist features with real priors:**
  - 12-1 momentum, with the crash caveat.
  - Beta / BAB: high beta falls more on down days, and is unconditionally a poor long.
  - Industry momentum (US-strong, India-weak).

  Features with **no peer-reviewed predictive support**, to be treated as hypotheses needing
  out-of-sample proof:
  - OI short buildup.
  - Close-near-low.
  - Gap rules.
- **The base rate is the null hypothesis.** About 88–91% of individual F&O traders and
  about 70% of cash intraday traders lose. A backtest that beats this needs a pre-registered
  rule, costs by date, and a holdout that is not reused.
