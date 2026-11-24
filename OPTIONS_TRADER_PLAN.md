# Options Trader: buy and sell options on any symbol, aligned to a real broker (Dhan)

Status: **plan, revision 2, 2026-11-24. Nothing is built.**

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
| D6 | Freeze quantity and price band | **Not selected.** Orders above NSE's freeze quantity or outside the day's band are **accepted**, where Dhan would reject them. This is a known gap; it is a small add-on if wanted. |
| D7 | Reuse by automated strategies | **Build a shared engine** (`src/options/`) for SPAN margin, baskets, exit and hedge-break, the contract picker, RMS and expiry, covering every NSE option position. The Options Trader page is the first module on it. Automated option modules come later (section 7a), each one gated on a backtest on real option prices. |

## 9. Known gaps against Dhan, and what is unverified

- **Fills** stay pessimistic (backend CLAUDE.md section 4). A real order may get
  a better fill than the simulator gives.
- **Freeze quantity and price band** are not enforced (D6).
- **Intraday SPAN** is v1-later. Until then, margin is fixed at the end-of-day
  file's parameters plus live prices.
- **Unverified, to check in Phase 1:**
  - whether `/optionchain` serves stock underlyings;
  - Dhan's expiry-day square-off time for physical settlement;
  - Dhan's fresh-order cut-off near 15:18;
  - the 80% MTM threshold;
  - whether premium received offsets margin at Dhan;
  - the ELM source;
  - whether the expiry-day 2% applies to hedged shorts;
  - the NIFTY weekly expiry weekday (the plan reads expiries from the master
    and assumes none);
  - any lot-size revision (the plan reads lot size from the master).
