# Options Trader: buy and sell options on any symbol, aligned to a real broker (Dhan)

Status: **plan, revision 2, 2026-11-24. Phases 0, 1 and 2 are built on branch `feature/options-trader` (sections 10 and 11). Phase 2's comparison against Dhan's own basket figures is still open: it needs your numbers.**

**Goal:** behave as close to Dhan as a paper simulator can. Where this plan
departs from Dhan, the gap is named in section 9. Revision 1 had treated
simplicity as acceptable; the owner rejected that, and the decisions in
section 8 replace revision 1's.

## 1. What is being asked

- Buy **and sell** options on any NSE optionable symbol: indices and F&O stocks.
- **Basket orders** whose margin reflects the hedge, the way Dhan's basket
  shows "final margin" against "overall margin".
- **A portfolio of its own, and more than one of them.** The existing
  portfolio framework already does this, and books never mix.
- **Dhan is used for market data only:** the option chain, the live feed and the
  instrument master. All three are already allowlisted.

## 2. Reuse from the existing code (verified by reading it)

| Need | Exists today | Gap |
|---|---|---|
| Contracts | `instruments` table and the master ingester. The cached master (2026-09-23) has **12,248 NSE OPTIDX** and **68,256 NSE OPTSTK** rows across 210 stocks, and its NSE lot sizes are correct (NIFTY 65, BANKNIFTY 30). The master also carries `SM_FREEZE_QTY` and the day's price band. | A strategy that claims these rows for the nearest N expiries. |
| Chain, greeks, IV | `dhan_option_chain_client` and `greeks_poller`, both already allowlisted. | Polling has to follow the underlying and expiry **being viewed**. |
| Prices and depth | One shared feed; Full mode gives 5-level depth. It carries 5000 instruments per connection. | An **on-demand** subscription: open positions plus a strike window around the viewed chain. |
| Orders and fills | `submit_paper_order` and the pessimistic fill simulator. | A product type (Intraday or Carry-forward) on the order, and baskets. |
| Positions | Netted per (portfolio, contract). Sell-to-open already works in the core. | Grouping legs for display, and equity positions created by delivery. |
| Money | `BalanceService` is the single place for cash, blocked margin, available and equity. | Margin is **per position, a flat 10% of premium**, so it has no hedge, no SPAN and no ELM (section 5). |
| Charges | A generic engine plus rate cards. | A new card, `nse-equity-options`. Delivery uses the existing `nse-equity-delivery` card. |
| Expiry | **None anywhere.** | Settlement and delivery (section 4.5). |
| Risk desk | None. | An RMS monitor (section 4.6). |

**Dhan's `/margincalculator` is not used.** It is banned in
`test_no_real_orders.py` as both an endpoint and an identifier. It also computes
against the **real account's** funds, and whether its multi-leg version applies
a hedge benefit is **unverified**: a Dhan staff reply in Jan 2026 said combined
margin was still being worked on. Margin is therefore computed locally from the
**exchange's own risk parameters**, which is what the broker itself starts from.

## 3. Shape: a shared options engine, and the first module built on it

The work is split in two (decision D7, 2026-11-24), so that automated option
strategies can be built later without rework:

**`src/options/` is the ENGINE.** It is generic and owned by no strategy, and it
serves **every NSE option position, whichever strategy opened it**.

| Piece | Public surface | Used by |
|---|---|---|
| SPAN and ELM margin | `OptionMarginService.for_portfolio(portfolio_id)` and `.preview(portfolio_id, legs)`, dispatched from `BalanceService` through the per-strategy `margin.model` | every strategy with `margin.model: nse_span` |
| Baskets | `submit_paper_basket(strategy_key, portfolio_id, legs, product, reason=...)`, which applies buys first, a margin check per leg and a per-leg outcome. It is built on `submit_paper_order` and adds no privileged fill path. | the Options Trader page today; an automated module later |
| Exit and hedge-break | `exit_group(...)` covers shorts first; `check_hedge_break(...)` | the UI, the RMS and automated exits |
| Contract picker | `pick_contract(underlying, right, expiry_rule, strike_rule)`. `expiry_rule` is nearest, monthly, or at least N days to expiry; `strike_rule` is ATM, ATM±k, target delta, or a premium band. It refuses illiquid contracts on configured spread and OI thresholds, and says why. | automated modules (and templates in P5) |
| RMS | One `options-rms` task over **all** NSE option positions in every portfolio | everyone |
| Expiry | One settlement and delivery job over **all** NSE option positions | everyone |

**`src/options_trader/` is the first MODULE.** It is the discretionary page, its
routes and its YAML, and it calls the engine. It owns no margin, RMS or expiry
logic of its own.

**Margin is per PORTFOLIO, not per strategy.** A portfolio can already host
several strategies (`portfolio_strategies`). A broker margins the whole account,
so SPAN nets every option position on the same underlying in a portfolio,
whichever strategy placed it. The MTM-80% and shortfall rules also act on the
portfolio, as Dhan's RMS acts on the account.

### The first module: `conf/strategies/nse-options-trader.yaml`

- **Universe:** the NSE indices plus the F&O stock list, derived from the
  master's own OPTSTK rows. That is the same way the F&O set is already derived
  from FUTSTK.
- **Discretionary.** It has no `automation` block and cannot be armed.
  - The only orders it places on its own are the **RMS square-offs** and the
    **expiry settlement**.
  - Those are risk-desk actions a broker also takes without asking, and each one
    is journalled and alerted.
- **Capabilities:** `option-chain`, `greeks`, `trade-notes`, `pnl-reports`, and a
  new `option-baskets`.
- **Hours:** 09:15–15:30 IST.
- **Shipped OFF.** Portfolios are created from the Portfolios page, as many as
  wanted.
- MCX crude and its "Sell is a long put" chart-trading rule are **unchanged**.

## 4. Build

### 4.1 Contracts and data
- Ingest NSE OPTIDX and OPTSTK rows for the nearest expiries, plus the
  underlying index and equity rows for the spot price.
- Lot size, tick size, freeze quantity and price band all come from the master.
- **On-demand subscription:** the open positions in every portfolio of this
  strategy, plus ATM±N strikes for the one chain open in the UI.
  - These are released when the view closes.
  - The total is budgeted against the 5000-instrument limit shared with the
    other strategies.
- Chain and greeks polling follow the viewed (underlying, expiry), at 1 request
  per 3 s per pair.

### 4.2 Options page (`/options`)
- **Chain:** pick an underlying and an expiry. The table shows LTP, bid/ask, OI,
  IV and delta. B and S on any strike **add a leg to the basket**.
- **Order ticket per leg:** lots, MKT/LMT, and product **Intraday** or
  **Carry-forward**.
- **Basket panel**, laid out like Dhan's:
  - each leg;
  - **Overall margin** (each leg margined alone, summed), **Final margin**
    (SPAN on the combined portfolio) and the **Hedge benefit** (the difference);
  - charges;
  - a payoff-at-expiry chart with max profit, max loss and breakevens.
- **Positions** are grouped by underlying and expiry. Each group shows its P&L
  and SPAN/ELM split, and has an "Exit all" action, which covers the shorts
  first.
- Funds are shown as Dhan shows them: available, blocked SPAN, blocked
  exposure, premium received and MTM.
- The page follows the header's **active portfolio**.

### 4.3 Product types
- **Carry-forward (NRML)** is held until it is exited, or settled at expiry.
- **Intraday** is squared off by the RMS at **15:25 IST**. That is Dhan's
  F&O intraday cut-off, verified only from Dhan's own FAQ.
  - Dhan also blocks fresh intraday orders after about 15:18. That figure is
    from the risk policy page and is **unverified**.
  - The margin is the same SPAN plus ELM. Dhan gives no extra intraday
    leverage on option selling, also **unverified**.

### 4.4 Basket execution: mirror Dhan, leg by leg
- Each leg is its **own paper order** through `submit_paper_order`, so it
  crosses the spread and walks the book. The legs are linked by `basket_id`.
- **Buy legs go first, then sells.** Dhan users report that baskets succeed
  only when the buys come first.
- **Each leg is margin-checked when it is sent**, against SPAN on the portfolio
  as it stands *after* the earlier legs. A short leg therefore gets the hedge
  only if its long leg actually filled.
- **A leg can reject or partially fill, and the others still go.** The basket
  view shows each leg's state and the reason, the way Dhan's order book does.
- **Hedge-break check on exits.** Exiting a long leg is refused if the shorts
  left behind would breach available margin. The message is Dhan's own
  "Hedge Break Alert" wording. The fix is to exit the short first, or use
  "Exit all".
  - This is the **one** case where a closing order can be refused for funds.
    Today every close skips the funds check, so this is a deliberate exception
    and needs a test of its own.
- **Limit legs are allowed.** A resting leg blocks its margin while pending, as
  Dhan does.

### 4.5 Expiry: settlement and physical delivery
- **Index options** are cash-settled at intrinsic value against the closing
  index value. There is **STT on exercise**, and Dhan charges ₹20 per exercised
  or assigned contract (dhan.co/pricing).
- **OTM contracts** expire at zero.
- **Stock options are physically settled.**
  - **Delivery margin ramps up** on positions that could deliver.
    - The ramp is NSE's: 10% of contract value at E-4 end of day, 25% at E-3,
      45% at E-2, 70% at E-1, and 100% on expiry day.
    - It applies to ITM longs, and to shorts near the money.
    - This margin is added to blocked margin, so a book that can't carry it is
      short of margin.
  - **If margin is short, the RMS squares off before expiry**, following Dhan's
    policy of squaring off where there is a shortfall in contract value. The
    exact cut-off time Dhan uses on expiry day is **unverified**. Until it is
    checked, the plan uses the expiry-day 14:30 time that brokers commonly use.
  - **At expiry, an ITM contract becomes a cash-equity trade in the same
    portfolio:**
    - a long call or short put **buys** shares at the strike;
    - a long put or short call **sells** shares at the strike.
  - The resulting share position is charged under `nse-equity-delivery`, plus
    Dhan's **0.10% physical delivery** charge.
  - **A sell obligation with no shares held in that portfolio is squared off
    beforehand** rather than settled. Settling it would be a short delivery,
    which NSE auctions at a penalty this simulator has no price for.

### 4.6 RMS monitor (risk desk)
This is a new background task shaped like `bracket_monitor`. It runs off the tick
path, in its own session, and is registered in `TASK_DESCRIPTIONS` and in
`expected_task_names`. Every action it takes is a normal paper order, a
journalled reason and an alert.

| Rule | Trigger | Source |
|---|---|---|
| Intraday square-off | 15:25 IST, Intraday positions | Dhan FAQ |
| MTM loss | MTM loss ≥ **80%** of the portfolio's blocked margin → square off the portfolio's NSE option positions, **whichever strategy opened them** | Dhan risk policy (figure to re-verify) |
| Margin shortfall | SPAN plus delivery margin is more than available. This can happen overnight when a new SPAN file raises it. → square off, the biggest margin consumer first | Dhan "risk of margin shortfall or payin obligation" |
| Delivery shortfall | Section 4.5 | Dhan physical-settlement policy |

Square-off **order** matters, just as it does for "Exit all": shorts come first,
so the hedge is never broken mid-liquidation.

**The RMS overrides arming.** An armed automated strategy cannot opt out of it:
a broker's risk desk does not ask the algorithm. Each square-off is journalled
against the strategy that owned the position, so that strategy's own journal
shows why the position closed. Its next run then sees the position gone and
re-decides, rather than trying to restore it.

## 5. Margin: real SPAN plus ELM

**New outbound host:** `nsearchives.nseindia.com`, approved 2026-11-24.
- It is named in one module only, `src/options/services/nse_span_client.py`, and
  is listed in `tests/test_outbound_hosts.py`.
- It is inbound public data, so `test_no_real_orders.py` is **unchanged**.
- The file is `archives/nsccl/span/nsccl.YYYYMMDD.s.zip`. It is about 10 MB, in
  SPAN XML, and HEAD returned 200 for 2026-11-20.
- NSE also publishes intraday SPAN snapshots, which is what brokers re-margin
  against during the day. Their file names are **unverified**. v1 loads the
  latest available end-of-day file and adds the intraday files once their names
  are confirmed.
- NSE archives sometimes refuse non-browser clients. That needs checking with a
  real GET during Phase 1. A failure there is the main risk to this whole
  section.

**The calculation, per portfolio, across ALL its NSE option positions,
whichever strategy holds them:**

1. **SPAN.** For each underlying, sum each position's 16-scenario risk array
   from the file and take the worst loss. Apply the inter-month (calendar)
   spread charge and the **short option minimum charge**.
   - On expiry day there is **no calendar-spread benefit** for the expiring
     contract (SEBI, Oct 2024).
2. **Exposure margin (ELM)** on short options, on the underlying's notional.
   - Index: 2%, or 3% when more than 10% OTM.
   - Stock: 3.5%, plus the deep-OTM rate.
   - The rates come from NSE's published ELM data, or from the YAML with a
     source and as-of date if NSE has no machine-readable file. Both routes are
     **to verify**.
3. **Expiry-day add-on:** +2% ELM on short **index** options expiring that day.
   That this applies to hedged shorts too is **unverified**.
4. **Delivery margin** (section 4.5) for stock options in the last 4 days.
5. **Premium.** A buy pays the premium in full, upfront (SEBI, from Feb 2025).
   A sell's premium received **does not reduce** the margin requirement. That
   is how brokers present it, but it is **to verify for Dhan**.
6. **Hedge benefit shown** = Σ(each leg margined alone) − portfolio margin, as
   in Dhan's "overall vs final".

**Parsing.** The parser is written by hand, from the SPAN 4.0 XML schema.

- `marketcalls/marginism` (MIT, pure Python) is used as a **reference and test
  oracle only**, not as a dependency, unless its fidelity checks out. That
  follows the same rule this repo applies to `dhanhq`: a guarantee is
  structural only if we own the code.

**Fallback.** If no SPAN file is newer than one session, the margin falls back
to the local scenario model (Black-76 over a SPAN-like grid). In that case the
page shows "SPAN unavailable — scenario estimate", and an alert fires. Nothing
falls back silently.

**Correctness check.** In Phase 2, **you** put 5–10 baskets into Dhan's own
basket screen (a naked short, a vertical spread, a strangle, an iron condor, a
calendar spread, and a stock-option short) and record Dhan's numbers. The tests
assert our figure is within a stated tolerance of each one. This is the only
honest way to claim "aligned to Dhan"; without it the claim is unverified.

## 6. Charges: `conf/charges/nse-equity-options.yaml`

| Item | Rate |
|---|---|
| Brokerage | ₹20 per executed order (Dhan). Also ₹20 for an exercised or assigned contract. |
| STT, sell side | **0.15%** of premium (Budget 2026, from 2026-04-01) |
| STT on exercise | **0.15%** of intrinsic value |
| NSE transaction charge | **0.03553%** of premium (NSE/FA/73061, from 2026-03-01) |
| SEBI fee | ₹10 per crore |
| Stamp duty | 0.003%, buy side |
| GST | 18% on brokerage, exchange and SEBI charges |
| Physical delivery | 0.10% of contract value (Dhan), on top of the equity delivery card |

Every rate comes from the research and must be checked against a **primary
source** when the card is written. The NSE transaction charge was seen only on a
mirror of the circular.

## 6a. UI: the mock gives the layout and flow, and the theme stays the app's own

`mocks/options-trader/index.html` was reviewed and accepted on 2026-11-24. What
it settles is the **screens, the flow and the behaviour**. Its colours and CSS
are throwaway. When the screens are built, they follow the app's existing
theme (`frontend/CLAUDE.md` §1):

- **MUI components themed by `src/theme/index.js`.** Colours come only from
  `theme.palette.*` and `theme.market.*`: up and down, ITM and ATM tints. There
  are no hex values in components. A missing token is added to `tokens.js`.
- **Light and dark both work.** The mock was light-only. Both modes are
  checked on every screen.
- **Reuse before writing anything new:**
  - `PortfolioSelector` and `ActivePortfolioContext` for the header picker;
  - `ChargesBreakdown` for charges;
  - `DepthLadder` and `OrderTicket` patterns for leg entry;
  - the existing `OptionChainPage` table conventions for the chain;
  - `AlertsPanel` for RMS events.
- **Live data comes from the one shared WebSocket**
  (`MarketFeedContext`, §2), never a second socket.
- **Honesty rules apply (§3).** Every margin figure says "estimate". A fallback
  to the scenario model is shown, not hidden.
- **The payoff chart** uses the charting the app already has: lightweight-charts
  or plain SVG driven by theme tokens. No new chart library.

## 7. Phases

Each phase is shippable on its own: the full suite passes, the module stays
usable, and it ends with a live run on a spare port before it is called done. A
phase ends in one commit, or a short series, on its own branch.

**What happens to margin between phases.** In P1, single-leg shorts use the
existing flat margin model, labelled. It does not yet resemble Dhan's figure, so
**P1 is for trying the flow, not for judging margin.** P2 replaces the model.

### Phase 0: settle the unknowns (about half a day, mostly live calls)

Each check below is done for real, and its result is written into section 9.

| Check | How |
|---|---|
| Does `/optionchain` serve stock underlyings (RELIANCE), and with which `UnderlyingSeg`? | One live call per kind: index and stock. |
| Will NSE serve the SPAN zip to a non-browser client? | A real GET of `nsccl.<last trading day>.s.zip`, then unzip and peek at the XML root. **If this fails, P2's design changes**, so it is checked first. |
| Is there an NSE source for the ELM rates? | A machine-readable file if one exists; otherwise a circular, cited in the YAML. |
| Lot sizes and expiries in a **fresh** master | Re-download the master. The cached copy is from 2026-09-23. |
| Charges from primary sources | The STT notification, the NSE circular for the transaction charge, and Dhan's pricing page. |
| Dhan's own policy numbers | Read Dhan's risk policy and FAQ pages directly: the expiry-day square-off time, the 15:18 cut-off, the 80% threshold, and whether premium received offsets margin. |

**Done when:** every row is verified or marked unverified with a reason.
**Exit gate:** you see the results before P1 starts.

### Phase 1: trade single options on any symbol (about 4–5 days)

**Backend**
- `conf/strategies/nse-options-trader.yaml`: a universe of NSE indices plus F&O
  stocks, derived from the master. NSE hours. No `automation` block. Shipped off.
- Instrument ingestion of OPTIDX and OPTSTK for the nearest N expiries, plus spot
  rows. It reuses `InstrumentSet` and the one-ingestion-rule-per-symbol check.
- A new **on-demand subscription policy**: open positions, plus ATM±N strikes of
  the viewed chain. There is a register and release API, and a budget against
  the 5000-instrument cap that is shown on the health page.
- Greeks and chain polling follow the viewed (underlying, expiry), at 1 request
  per 3 s per pair.
- `product` on orders, Intraday or Carry-forward, as a migration (SQLite and
  MySQL DDL) and a schema field. It is stored only in P1; the RMS acts on it
  in P3.
- `conf/charges/nse-equity-options.yaml`, with source URLs and as-of dates.
- Two packages, following backend CLAUDE.md §1 and the split in section 3:
  - `src/options/` is the engine. In P1 it holds the subscription and chain
    services.
  - `src/options_trader/` is the module: its routes, its controllers and the
    page's API. It imports the engine; the engine never imports it.
- **The engine boundary is asserted from the start.** A test fails if anything
  under `src/options/` imports `src/options_trader/`, or names
  `nse-options-trader`. Without that test, the shared layer stays shared only
  by convention.

**Frontend**
- The `/options` page: underlying search, expiry chips, and the live chain. In
  P1, B and S open a **single-leg** order ticket; the basket comes in P2.
- A sidebar entry gated on the strategy and capability, and the active
  portfolio picker. Light and dark mode.

**Tests**
- Chain resolution per underlying and expiry.
- Lot size comes from the master.
- The subscription budget is respected and released on leaving the page.
- Two portfolios trading the same contract do not mix.
- An order for this strategy is charged on the new card.
- `test_no_real_orders.py` and `test_outbound_hosts.py` pass unchanged.

**You can verify:** enable the module, create two portfolios, open
NIFTY / RELIANCE chains, and buy and sell single options in each portfolio
during market hours. P&L Reports and Trade Notes work.

### Phase 2: baskets and real SPAN margin (about 6–8 days, the largest)

**Backend**
- `src/options/services/nse_span_client.py` is the only module naming
  `nsearchives.nseindia.com`. It is added to `test_outbound_hosts.py`.
  - It runs a nightly download after the file is published, then retries.
  - The file is stored on disk, parsed into tables of risk arrays per contract,
    and its status is shown on the health page.
- A SPAN calculator over the portfolio's legs per underlying:
  - the 16 scenarios;
  - the inter-month spread charge;
  - the short-option minimum;
  - the expiry-day removal of calendar benefit.
- ELM, and the expiry-day 2% add-on.
- `BalanceService` gains a **per-(portfolio, strategy) margin model**, dispatched
  on `margin.model`. MCX keeps `percent_of_notional`, byte-for-byte unchanged.
- The scenario model is the fallback, with a visible state and an alert.
- An `option_baskets` table. Legs are sent buys first, each margin-checked
  against the post-earlier-leg book. Per-leg outcomes are kept.
- The **Hedge Break** check on closing orders. This is the one deliberate
  exception to "a close is never refused for funds", and it has its own test.
- **Exit all**, which covers shorts first.
- A margin preview endpoint that returns overall, final and hedge benefit,
  without placing anything.
- **The engine's public calls are usable from code, not only from HTTP:**
  `submit_paper_basket`, `OptionMarginService.preview`, `exit_group` and
  `pick_contract`, the last with its liquidity refusals.
  - The page's controllers are thin callers of them.
  - A test drives a basket for a **second, test-only strategy key** in a
    portfolio that also holds Options Trader legs. It shows that SPAN nets
    across both, and that each leg keeps its own `strategy_key`.
  - This test is what proves an automated module could use the engine.

**Frontend**
- The basket panel (legs, lots, MKT/LMT, product, margin box, charges), the
  payoff chart, the confirmation dialog, and the per-leg result screen.
- Positions grouped by underlying and expiry, with the SPAN and ELM split,
  Exit all, and the Hedge Break dialog.
- The funds card: margin breakdown and SPAN file status.

**Tests**
- A bull spread's margin is at most its max loss.
- A naked short carries the full SPAN plus ELM.
- A strangle is margined on the worse side.
- A calendar spread loses its benefit on expiry day.
- Buys are sent before sells.
- A short whose hedge was rejected is margined naked.
- Hedge Break refuses, and Exit all succeeds.
- The fallback is labelled.
- The SPAN parser is checked against a fixture taken from a real file.

**Needs you:** 5–10 baskets entered in Dhan's basket screen, with Dhan's figures
recorded. They become fixtures, and the tolerance is agreed then.
**Exit gate:** our margin is within that tolerance of Dhan on every fixture, or
the gap is written into section 9 with its cause.

### Phase 3: the risk desk (about 3 days)

- A new `options-rms` task in the `bracket_monitor` shape. It runs off the tick
  path in its own session, and is registered in `TASK_DESCRIPTIONS` and
  `expected_task_names`.
- The rules:
  - Intraday square-off at 15:25.
  - Fresh Intraday orders refused after the cut-off.
  - MTM loss of 80% or more → square off the portfolio's NSE option
    positions, whichever strategy opened them.
- Its scope is **every** NSE option position, not one strategy's. A square-off
  is journalled against the strategy that owned the leg, so an automated
  module's journal will show "closed by RMS: <rule>".
  - Margin shortfall → cut the largest consumer first, checked after every new
    SPAN load and on a short interval.
- Every action is a normal paper order that crosses the spread, a journal row
  with its reason, and an alert through the existing outbox (deduplicated).
- The RMS timeline on the page. The rules also appear in the alert catalogue.

**Tests:** each trigger fires once and does not repeat; shorts are covered
first; a `None` price never triggers; nothing acts while the market is closed;
the 15:25 square-off is idempotent across a restart.

**You can verify:** an Intraday position open at 15:25 is closed, and the
alert arrives on Telegram.

### Phase 4: expiry and physical delivery (about 4 days)

- An expiry job on the strategy clock, run after the close on expiry day. It
  covers **every** NSE option position, whichever strategy holds it.
- **A module can hook into expiry.** An automated strategy can register a
  "before expiry" callback, so it can roll or close on its own terms, for
  example two sessions before expiry. Settlement remains the backstop for
  anything still open.
- **Index options:** settle at intrinsic value against the closing index value,
  with STT on exercise and ₹20 per exercised contract. OTM contracts expire
  at zero.
- **Stock options:**
  - The delivery-margin ramp from E-4 (10/25/45/70/100%) enters blocked margin,
    so the P3 shortfall rule acts on it.
  - At expiry, an ITM contract becomes an equity buy or sell in the same
    portfolio, under the delivery card plus 0.10%.
  - A sell obligation with no shares held is squared off on expiry day
    beforehand, never delivered short.
- Settlements are ledger entries plus closing fills, so `pnl_service`'s replay
  agrees with the position book. That agreement is asserted.
- The upcoming-expiries panel and the delivery tracker on the page.

**Tests:**
- Intrinsic value against a known close.
- OTM legs expire at zero.
- The ramp percentages by day count, with the trading-day arithmetic for
  holidays and Tuesdays.
- An ITM long call creates shares and debits cash.
- An ITM short call with no holding is squared off rather than settled.
- The report and the position book agree.

**You can verify:** on the first real expiry, run the settlement and check one
index book and one stock book against intrinsic values computed by hand.

### Phase 5: polish and fidelity (optional, after use)

- Intraday SPAN snapshots, once the file names are verified.
- Strategy templates (bull spread, strangle, iron condor, straddle).
- Limit-order baskets in the UI.
- Freeze quantity and price-band rejection (D6), if wanted.
- BSE SENSEX and BANKEX.

### Order and size

P0 → P1 → P2 → P3 → P4, in sequence. Each phase builds on the one before: the
RMS needs real margin, and expiry needs the RMS for shortfalls. In total that
is **roughly 3–4 weeks of build** before P5. That now includes about one extra
day for the engine/module split, the boundary test and the second-strategy
test. The estimates are rough, and P2 is the one most likely to grow,
depending on how messy the SPAN XML turns out to be.

## 7a. Later: automated option strategies on the same engine (not in P0–P5)

Once P4 ships, an automated strategy that trades options ("VCP via calls",
"BTST via options", "Swing via options", or a new one) is **a new strategy
module, not a rebuild**. Each one is its own piece of work with its own
decisions, and the existing equity modules are **not** modified.

**What such a module provides**

- A YAML with an `automation` block, so it can be armed. It ships OFF and
  unarmed, like the other automated modules, and declares
  `margin.model: nse_span`.
- **Its signal:** reused from the equity module's services where they are
  importable (VCP's detector, Swing's ranking, BTST's scan), and never
  copied by hand.
- **Contract selection** through `pick_contract`, with its rules as YAML
  parameters: expiry (for example, the monthly with at least 15 days left),
  strike (for example, delta 0.5), and liquidity limits.
- **Sizing:** by premium at risk for long options, or by margin for short
  options.
- **Stops on the underlying, exits on the option.** This is the pattern
  `chart_trading` already uses: it watches the future and closes the option.
- **A roll policy** through the expiry hook in section 4.5, for holds that cross
  an expiry.
- **Its own journal, health tab and alerts**, the same shape the existing
  automated modules have.

**The gate before anything is armed: a backtest on real option prices**

- An edge measured on shares does not carry over automatically. Theta, IV
  changes, spreads and charges all change it. Earlier experiments here showed
  this: the NIFTY 10:15 breakout and the 5-15-50 EMA credit spread both lost on
  real option bars.
- So each module gets an experiment under `experiments/`, run on real option
  prices, with a holdout period, before it is enabled. That is the same
  discipline every strategy here has followed.
- **If an equity strategy does not survive translation to options, it stays an
  equity strategy.**

**What the engine guarantees these modules, from P1–P4**

- The same SPAN margin as the page, netted across the whole portfolio.
- The same pessimistic fills.
- The same RMS. Arming cannot opt out of it.
- The same expiry settlement and physical delivery.
- Per-leg journalling under the module's own `strategy_key`.

## 8. Decisions (2026-11-24)

| # | Decision | Answer |
|---|---|---|
| D0 | Overall goal | **As close to a real broker (Dhan) as possible.** |
| D1 | Margin | **Real NSE SPAN plus ELM.** New host `nsearchives.nseindia.com` approved. The scenario model is a labelled fallback only. |
| D2 | Universe | NSE indices and all F&O stocks. BSE is out of v1. |
| D3 | Basket | **Mirror Dhan: leg by leg, buys first, per-leg rejects, hedge-break on exit.** This replaces revision 1's "refuse whole basket". |
| D4 | Stock option expiry | **Full physical settlement** with the delivery-margin ramp and RMS square-off on shortfall. This replaces revision 1's cash-settle. |
| D5 | RMS rules | **Intraday 15:25, MTM ≥ 80% and margin shortfall:** all three. |
| D6 | Freeze quantity and price band | **Built in Phase 5 (approved 2026-11-25).** Orders above NSE's freeze quantity, and limit prices outside the day's range, are refused as Dhan refuses them. See section 15. |
| D7 | Reuse by automated strategies | **Build a shared engine** (`src/options/`) for SPAN margin, baskets, exit and hedge-break, the contract picker, RMS and expiry, covering every NSE option position. The Options Trader page is the first module on it. Automated option modules come later (section 7a), each one gated on a backtest on real option prices. |

## 9. Known gaps against Dhan, and what is unverified

- **Fills** stay pessimistic (backend CLAUDE.md section 4). A real order may get
  a better fill than the simulator gives.
- **Freeze quantity and price band** are enforced from Phase 5 (D6), with the
  two softenings in section 15.
- **Intraday SPAN** is v1-later. Until then, margin is fixed at the end-of-day
  file's parameters plus live prices.
- **Intraday product margin** is the same SPAN + ELM as carry-forward here.
  Whether Dhan offers extra intraday leverage on option selling is
  unverified.

## 10. Phase 0 results (2026-11-24, live checks)

Every row was checked for real: a live call, a downloaded file, or a primary
document read. Sources are in the rate card or below.

| Question | Answer | How it was checked |
|---|---|---|
| NSE SPAN download | **Works only with a browser User-Agent.** Without one, the connection is dropped (curl reports 000). With one: `nsccl.20261123.s.zip`, 8.0 MB, 200. The same day's file was not up at 21:21 IST (404). The XML is standard SPAN 4.00: 229 option portfolios, 118,069 option risk arrays of 16 scenarios each. P2's design holds. The client must send a browser-like UA. | Live GET plus unzip, 2026-11-24 |
| `/optionchain` for stocks | **Yes.** RELIANCE works with `UnderlyingScrip` 2885 (its NSE_EQ id) under `NSE_EQ` or `NSE_FNO`, giving 113 strikes with IV and greeks. | Live call |
| `/optionchain` for indices | Needs the **IDX_I id** (NIFTY 13, BANKNIFTY 25, FINNIFTY 27, NIFTYNXT50 38, MIDCPNIFTY 442). The master's `UNDERLYING_SECURITY_ID` on option rows (NIFTY 26000) is refused with "813 Invalid SecurityId". | Live call |
| Lot sizes | Unchanged: NIFTY 65, BANKNIFTY 30, FINNIFTY 60, MIDCPNIFTY 120, NIFTYNXT50 25. RELIANCE is 500. Read from the master at runtime anyway. | Fresh master |
| NIFTY expiry weekday | Weekly **Tuesday** (01, 08, 15, 22 Dec); monthly 29-Dec. Stock monthlies 29-Dec and 25-Jan. Read from the master, never assumed. | Fresh master |
| Option rows | 12,882 OPTIDX and 74,026 OPTSTK across 218 F&O stocks. A 70-day window ingests 69,117 rows (with equities) in 14.7 s. | Live refresh on a copy |
| Id collisions | 4 NSE_FNO ids also appear as segment-G gold bonds (not ingested). The ingester refuses a real collision. | Master scan |
| STT | **0.15%** sell side on premium, and **0.15%** on exercise, from 2026-04-01. | NSE/FATAX/73524 |
| NSE txn charge | **0.03553%** of premium, from 2026-03-01. | NSE/FA/73061 |
| ELM rates | Index short options 2%, 3% if more than 10% OTM, +2% on expiry day, 5% long-dated. Stock short options 3.5%, 5.25% if more than 30% OTM. | NCL/CMPT/73997 s.10.4 |
| ELM daily file | `F_AEL_OPT_CONTRACTS_DDMMYYYY.CSV` exists (NCL/CMPT/74569, live 2026-07-07), but its **public URL was not found**. P2 uses the circular's rates from the YAML. | Circulars; URL probes 404 |
| Dhan intraday square-off | **15:25** for NSE/BSE derivatives. Orders in the 2 minutes before may be rejected. The 15:18 fresh-order block is **not** on Dhan's page. | dhan.co/risk-management-policy |
| Dhan MTM rule | Squares off where "MTM crosses **80%** and above". | Same page |
| Dhan physical settlement | **Different from the plan's assumption.** No fresh stock-option position of any product on expiry day. Delivery needs **100% of contract value before 10:00 on expiry day**, and on a shortfall Dhan "reserves the right to square-off". **No E-4 ramp appears on Dhan's pages** (that ramp is the NSE's, which other brokers pass through). | Dhan RMS page plus Dhan FAQ (still says Thursday) |
| Premium credit | "Any option Receivable will not be consider against initial margin shortfall (Span + Exposure)." Whether premium counts as available cash is not stated. | Dhan RMS page |

**Consequence for Phase 4, needing your call before then.** Dhan's rule is
"100% before 10:00 on expiry day, no fresh positions that day". The plan's
10/25/45/70% ramp is NSE's own and is what some other brokers apply. Phase 4
should model Dhan's rule (decision D0). The NSE ramp may still apply on top at
the exchange level; that is unverified.

### Still unverified
- Dhan's exact square-off time on expiry day for physical settlement.
- Whether premium received counts towards available funds at Dhan.
- Whether the expiry-day 2% ELM applies to hedged shorts.
- The STT base on exercise (intrinsic value, by secondary sources).
- Stamp duty and GST against their own notifications (broker-primary only).

## 11. Phase 2 as built (2026-11-24)

- **SPAN in practice.** The app downloads `nsccl.YYYYMMDD.s.zip`, which needs a
  browser User-Agent. On 24-Nov the newest file was 23-Nov's, because 24-Nov's
  was still 404 at 22:57 IST. It parses in 1.5 s (119,188 contracts, 247
  underlyings) and is cached as a pickle. Parsing peaks at 147 MB.
- **Margin formula.**
  - Per underlying: margin = max(worst of the 16 scenarios + calendar spread
    charge, SOM) + exposure + the expiry-day add-on.
  - On expiry day the expiring legs are scanned separately, so they get no
    calendar benefit.
  - **Blocked = margin − net option value.** NSE credits NOV to net worth
    rather than inside SPAN. The result is that a long blocks only its
    premium, and a short's premium stays unspendable, matching Dhan's
    "receivable not considered" rule.
- **Figures on the 2026-11-23 file** (NIFTY 01-Dec, 1 lot of 65):

| Position | Margin (SPAN + exposure) | Blocked |
|---|---|---|
| Short 25350 CE | ₹1,70,613 | ₹1,87,744 |
| Long 25350 CE | ₹17,131 (= premium) | ₹0 |
| Bear call 25350/25550 | ₹40,010 | ₹45,584 |
| Short strangle 25800 CE / 24900 PE | ₹1,80,618 | ₹1,92,422 |
| Iron condor | ₹73,943 | ₹78,652 |
| RELIANCE short 1350 PE (lot 500) | ₹96,592 | ₹1,16,092 |

  SPAN on the strangle (₹1,17,790) is below the call alone (₹1,24,290),
  because the short put gains in the rally scenario. That is correct SPAN
  behaviour.

- **Still needed for the P2 exit gate:** your 5–10 baskets from Dhan's own
  basket screen, priced on the same day, so these can be compared. Until then
  "aligned to Dhan" is unverified.
- **Known simplification:** risk arrays are end-of-day; live prices move only
  the NOV and the exposure notional. Intraday SPAN snapshots are Phase 5.

## 12. Calibrated to Dhan (2026-11-24): the P2 exit gate, closed on measured data

**How it was measured.** Dhan's public calculator
(dhan.co/calculators/margin-calculator) needs no login. It was queried for one
lot SOLD of 23 contracts (NIFTY weekly and monthly, ITM through deep OTM,
calls and puts; BANKNIFTY; RELIANCE; HDFCBANK) and for three NIFTY baskets.
Zerodha's public SPAN calculator was queried as a second broker.

**What Dhan does.** Dhan uses NSE's own SPAN with no hidden add-on:
- risk arrays from the **session's beginning-of-day file**, `nsccl.D.i1`;
- options and the exposure base valued at **that file's prices**;
- blocked = scan risk − net option value + exposure (2% index, 3.5% stock);
- a basket's final margin adds **premium paid**, and premium received is not
  counted.

24-Nov was a holiday, so Dhan was still on 23-Nov's `i01`, built from NIFTY's
Friday close of 25,800.6. Zerodha had already moved to 25-Nov's `i1`, and
matched our scan + premium to the rupee (₹1,55,675). Our first comparison
looked 25% off only because the engine read the end-of-day file at live prices.

**The result, now in the app** (live preview on the verification copy):

| | App | Dhan |
|---|---|---|
| Naked 25350 CE | ₹2,19,035 | ₹2,18,792 (0.11%) |
| Naked 25700 CE | ₹1,96,244 | ₹1,96,279 (0.02%) |
| Bear call 25350/25550 final | ₹58,270 | ₹57,885 (0.67%) |
| Bear call hedge benefit | ₹1,72,465.15 | ₹1,72,465.15 (exact) |
| 25700B + 25550B + 25350S final | ₹55,495 | ₹55,111 (0.70%) |
| Same basket, hedge benefit | ₹1,83,602.25 | ₹1,83,602.25 (exact) |

Across all 23 single shorts the mean error is **0.64%**: under 0.3% within 1%
of spot, and up to 2.97% deep OTM, where Dhan values the premium slightly off
the file. All of it is pinned in `tests/test_options_dhan_parity.py`, on a
fixture cut from NSE's real `i01` file.

**What changed.**
- `options.span_snapshot` is `beginning_of_day` by default; `latest` is the
  Zerodha-like alternative.
- The margin card has a `broker_parity` block: `valuation: span_file` and
  `count_premium_received: false`.
- Previews report broker-style SPAN (scan less net option value).
- Basket "overall margin" = each short alone + premium paid.

**Still to watch:**
- If Dhan changes when it rolls to the next `i1`, the parity test is where it
  will show.
- The exposure-margin base Zerodha uses (₹23,997.7/unit on 24-Nov) is not
  explained and is not needed for Dhan parity.

## 13. Phase 3 as built (2026-11-24): the risk desk

`src/options/services/risk_desk.py` runs as the `options-rms` task. It has the
`bracket_monitor` shape: its own session, off the tick path, registered in
`TASK_DESCRIPTIONS` and `expected_task_names`. It serves **every**
SPAN-margined option position, whichever strategy holds it, and arming cannot
opt a strategy out of it. Its times and threshold are in the margin card's
`risk_desk` block, from Dhan's risk policy.

| Rule | Trigger | Action |
|---|---|---|
| Intraday square-off | 15:25 IST | Close every INTRADAY option position, shorts first |
| Entry cut-off | 15:23 until the close | Refuse a NEW Intraday position (Dhan: orders in the 2 minutes before square-off may be rejected) |
| MTM loss | Unrealised option loss ≥ 80% of the book's blocked margin | Close the portfolio's option book, shorts first |
| Margin shortfall | Available below zero (for example, a wider SPAN file overnight) | Close the largest-margin underlying, then re-check next pass |

- **Every action is an ordinary paper order.** Each goes through the single
  exit path (`BasketService.exit_matching`), with the reason written on the
  order.
- **Every action is recorded and alerted.** It gets a row in
  `options_risk_actions` and an alert (catalogue entry `options-rms`) tagged
  with the owning strategy.
- **Failed exits are retried, not hammered.** An exit that could not fill is
  marked FAILED/PARTIAL, alerted as CRITICAL, and retried after 60 s rather
  than on every pass.
- **It is careful about data and time.** An unmarked position never triggers
  the MTM rule, and nothing acts outside market hours.

**Unverified:** what Dhan's "MTM crosses 80%" is a percentage of. It is read
here as 80% of the blocked option margin.

**Verified on the database copy:** the task ran and was reported running by
System Health, and the page panel rendered. A live square-off needs market
hours; the 11 tests in `tests/test_options_risk_desk.py` drive every rule with
a fixed IST clock on the real-file SPAN fixture.

## 14. Phase 4 as built (2026-11-24): expiry, on Dhan's rule

Decision on 2026-11-24: follow **Dhan's own expiry rule**, not NSE's E-4
margin ramp. Its values are in the margin card's `expiry_settlement` block.
The logic is in `src/options/services/expiry_service.py`, which is engine
code and names no strategy. It is run by the risk desk's task.

| When | Stock options (physically settled) | Index options (cash-settled) |
|---|---|---|
| Expiry day, all session | No FRESH position of any product. Closing is allowed, including a plain SELL against a long. | Trade as normal |
| Expiry day, from 10:00 | ITM and would BUY shares (long call, short put): 100% of contract value blocked. Obligations that cannot be met are closed by the risk desk (`PHYSICAL_DELIVERY`): a SELL of shares not held in that portfolio, or a BUY the portfolio cannot fund. | — |
| After 15:45 (or any later pass) | ITM: the option closes at **0**, and the shares trade at the **strike** on the `nse-equity-delivery` card. OTM: the option closes at 0. | ITM: closed at intrinsic value against the official close. OTM: closed at 0. |

- **Charges on exercise.**
  - ₹20 exercise/assignment fee, plus GST on it.
  - STT on exercise, from the strategy's own card: 0.15% of intrinsic value, long side only.
  - 0.10% physical delivery charge on the contract value of contracts settled by delivery.
- **Why the option closes at 0 when the shares are delivered.** Closing it at
  intrinsic and also trading the shares at the strike would count the gain
  twice. Closing at 0 puts the gain on the share position instead, and the
  total P&L comes out the same.
- **A sell obligation with no shares at settlement is cash-settled at
  intrinsic, and its order says so.** This happens only if the 10:00 rule
  could not act, for example because the desk was down. NSE would auction the
  shortfall at a penalty, and this simulator cannot price that.
- **The close is fetched, never guessed.** It is the underlying's daily bar
  from `/charts/historical` (already allowlisted; IDX_I id for an index,
  NSE_EQ id for a stock).
  - If the bar is not published yet, or the call fails, settlement WAITS.
  - The wait is journalled once a day, and the next pass tries again, even
    after a restart the next morning.
- **Settlement fills are ordinary orders.**
  - `OrderService.record_off_market_fill` writes them with `order_type`
    EXPIRY or DELIVERY, at a stated price.
  - So positions, the cash ledger and P&L treat them like any other trade.
- **Upcoming expiries are on the page.** The risk-desk card lists every
  position expiring within 7 days and what expiry would do to it at today's
  price.
- **Fixed in passing.** The 15:23 Intraday cut-off refused a plain SELL that
  closed an Intraday long. Both "new position only" rules now look at the
  open position, not the client's `isCloseOrder` flag.

**Unverified:**
- The time of Dhan's own expiry-day square-off of undeliverable positions.
  10:00 here is the time its margin rule starts, not a documented square-off
  time.
- Whether Dhan charges the ₹20 fee per order or per contract. It is applied
  once per settled position.
- How the first real settlement looks. That can only be checked on a real
  expiry date, against a real Dhan contract note.

**Tested:** 15 tests in `tests/test_options_expiry.py`, run with a fixed IST
clock and a fake closing-price provider.

## 15. Phase 5 as built (2026-11-25)

Built: the four NSE items. **BSE is deferred** (decision 2026-11-25): its SPAN
comes from ICCL, a new outbound host, and gets its own decision once that
file and host have been checked.

### D6: freeze quantity and price range

- **Source.** NSE's daily F&O contract file, `NSE_FO_contract_DDMMYYYY.csv.gz`.
  - It comes from the already-approved host, and is fetched in
    `nse_span_client.py`, still the only module naming that host.
  - NSE stopped publishing `qtyfreeze` on 2024-04-15 (circular
    NSE/FAOP/61157). The old URL now redirects to that circular, which names
    this file as the replacement.
  - The file's `FinInstrmId` is Dhan's security ID (verified on 47810,
    47941 and 144259), so no mapping is needed.
  - The SPAN refresher fetches it on the same pass, and it is held in
    `src/options/services/contract_limits.py`.
- **Freeze quantity.** An order above the freeze limit is refused, and the
  refusal says how to split it.
  - The limit is the file's `MaxTradQty` minus 1. NSE lists 1801 for NIFTY,
    whose published freeze limit is 1,800, so an order must be BELOW the
    listed value.
  - This applies to every order, a close included: the exchange does not ask
    why an order is large.
  - Exits the application makes itself (risk desk, Exit all) are split into
    whole-lot orders of at most the limit. A single order would be rejected
    and leave the position open.
- **Price range.** A LIMIT price outside `MinPric`–`MaxPric` is refused.
  - It is applied only when the file is for today's session.
  - It is not applied when the live last price is already outside the range:
    NSE widens option ranges intraday, and the file cannot show that.
- **Before Place.** The basket preview shows any refusal per leg, before
  anything is placed.
- **Switches.** Both rules can be turned off in the margin card's
  `order_limits` block.

### Intraday SPAN snapshots

- **File names and times verified 2026-11-25** from the archive's
  Last-Modified headers for the 23-Nov session (IST):

  | File | Published |
  |---|---|
  | i1 | 20:58 the previous Friday |
  | i2 | 11:08 |
  | i3 | 12:37 |
  | i4 | 14:08 |
  | i5 | 15:46 |
  | s | 17:12 |

- **Faster pickup.** The refresher now wakes just after each of those times
  (`options.span_snapshot_times`) as well as every 30 minutes, so a new
  snapshot is in force within minutes, not up to half an hour late.
- **Re-margining.** Under `span_snapshot: latest` the book is re-margined on
  each snapshot. The risk desk reads the file in force on every pass, so a
  snapshot that raises margin past the cash leads to the shortfall rule.
- **Labelled on the page.** The page names the snapshot in force ("intraday
  snapshot 1 (about 11:00)") and whether it is Dhan's beginning-of-day rule.
- **The default stays `beginning_of_day`.** Whether Dhan re-margins
  intraday is **not measured**: the calibration ran on a holiday, when there
  were no intraday files. To settle it, on a trading day after 11:10, compare
  Dhan's calculator for a few naked shorts against i1 and against i2. Switch
  the default only if i2 matches.

### Templates

- Ten templates on the Options page: bull/bear call and put spreads (debit
  and credit), long/short straddle and strangle, iron condor, iron butterfly.
- Each is built from the chain's ATM strike with a chosen width.
- A template REPLACES the basket and places nothing. The legs go through the
  same preview, payoff and confirm step as legs added by hand.
- Pure frontend: `frontend/src/options/templates.js`.

### Limit-order baskets

- Limit legs were already accepted from Phase 2. What was missing was what
  happens after placing.
  - **Resting baskets** now appear on the page with each leg's live order
    status.
  - **Cancel resting legs** withdraws only the legs still open. Filled legs
    stay filled, as a broker's basket cancel behaves.
  - A basket's status is now re-derived from its orders, not frozen at
    placement.
- **Switching a leg to LMT** starts it at the current price.

**Tests:**
- `tests/test_options_order_limits.py`: 11 tests, including a real cut of
  NSE's contract file.
- 4 new tests in `tests/test_span_store_selection.py`.

### Live session, 2026-11-25 (paper trades on an isolated copy)

Run during market hours on a copy of the database, on port 24611, with the
live Dhan feed. It held one extra feed connection of Dhan's five; the
schedulers, token renewal and Telegram were off in the copy.

**Dhan re-margins on the beginning-of-day file, not on intraday snapshots.**
NSE published i2 at 11:08. NIFTY had gapped from 25,350 (the i1 file's price)
to about 25,920, which makes the two files very different. One lot sold, on
10 NIFTY 01-Dec contracts and 2 RELIANCE 29-Dec contracts:

| Dhan's calculator at | vs our i1 | vs our i2 |
|---|---|---|
| 11:10 | 0.68% mean error (worst 1.25%) | 18.4% mean error |
| 11:17 | identical to 11:10, to the rupee | — |

| 12:47 (i3 published 12:37) | identical to 11:10, to the rupee; 0.73% mean vs i1 | 17.8% mean error vs i3 |

Dhan's figure did not move once in 97 minutes while NSE published two new
snapshots, so it margins on the session's beginning-of-day file for the whole
session. The `beginning_of_day` default is **confirmed**.

**What worked:**
- Market fills came one tick worse than the live ask (ATM call 282.65
  against an ask of 282.60; RELIANCE call 51.35 against 51.30), as the
  pessimistic simulator intends.
- A 25950/26150 bear call spread filled in full, long leg first. Blocked
  margin plus premium paid reconciled to the preview's 41,621.
- A basket whose short was refused kept its filled hedge; nothing was rolled
  back, as at a broker.
- Exit all covered the short first, then sold four longs, and blocked margin
  went to zero.
- The freeze limit refused 28 lots of NIFTY and named 27.

**Found and fixed in the same session:**
1. **The price range never applied during the session.** NSE publishes the
   file named for session D in D's evening, and its range is for the next
   session. The first version waited for a file dated today. Fixed: the
   newest file dated before today, at most 4 days old.
2. **On a big move, the range check stood aside entirely.** After the gap-up
   the 26300 CE traded at 110 against a published range of 0.05–70.15, and a
   buy limit at 5,000 was accepted. Fixed: the range is scaled by last price
   / base price (here to 0.20–275.10). This is an approximation: NSE does not
   publish how it widens ranges.
3. **Resting limit legs blocked no margin.** The plan said they would, as at
   Dhan. Fixed:
   - a resting sell blocks what it would add if filled;
   - a resting buy blocks its premium at the limit price;
   - a resting order never frees margin;
   - at its own fill, its reservation is not counted against it.
4. **A basket whose legs were all cancelled read REJECTED.** It now reads
   CANCELLED.

**Intraday and Carry-forward lines (fixed the same day, on request).** A
contract held as both products is now TWO lines, as at Dhan: each has its own
average and P&L, and the 15:25 square-off closes only the Intraday one.

- **Lookups.** `PositionRepository.get_open_for_security` takes the product
  when the caller names one. Strategies without products pass none and are
  unchanged.
- **Margin.** It still nets the two lines per contract, because the exchange
  margins the client's NET position.
- **Orders without a product.**
  - With one open line, the order takes that line.
  - With two, it is refused and asks which.
  - A close naming a line that does not exist is refused, rather than
    opening a position on the other product.
- **Closes and exits** (position close, Exit all, the risk desk) always name
  the line they close.
- **Reports.** The P&L replay keys by product too, so the report and the
  positions agree.
- **Verified live at 11:36:**
  - an Intraday long and a Carry-forward short in the NIFTY 25900 CE became
    two lines, and margin netted to zero;
  - closing the Intraday line left the short, blocking 1,53,260 (Dhan's
    figure for that contract: 1,54,345).

**Unverified:** that Dhan's own RMS nets the two products in one contract for
margin, as the exchange does.
