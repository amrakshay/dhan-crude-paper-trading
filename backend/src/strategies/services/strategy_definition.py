"""What a strategy module IS.

A strategy is the bundle of everything specific to trading one thing:

    underlying + exchange segment + instrument types
  + contract specification
  + market hours
  + subscription policy
  + charge rate card
  + margin model
  + the generic capabilities it supports

Everything else in this application is generic and knows nothing about
CRUDEOIL. The definitions are read from ``<CONFIG_PATH>/strategies/*.yaml`` --
one file per strategy -- and are immutable once loaded.

**A definition says nothing about whether the strategy is running.** That is
runtime state in the database, owned by the Strategies & Features page. Keeping
the two apart is what makes "off" a decision an operator takes rather than a
redeploy.
"""
import csv
import os
from dataclasses import dataclass, field
from datetime import time
from decimal import Decimal
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from src import config_utils


class StrategyConfigError(Exception):
    """A strategy YAML is missing something it cannot run without."""


# The generic capability catalogue. A strategy may support any subset of these;
# it may not invent one, because a capability is a feature of THIS codebase
# (pages, pollers, background work) and not of the strategy.
# The live quote screen for ONE instrument: its depth ladder, its chart and
# the one-click ticket beside them. It shows a strategy's FRONT CONTRACT, so
# only a strategy that has one can support it -- the NSE rotation holds a
# basket of equities and has no single instrument to put on that screen,
# which is why it was wrong for the page to appear whenever ANY strategy was
# running. Added 2026-09-18.
CAPABILITY_LIVE_PRICE = "live-price"
CAPABILITY_OPTION_CHAIN = "option-chain"
CAPABILITY_PRICE_CHART = "price-chart"
CAPABILITY_CHART_TRADING = "chart-trading"
CAPABILITY_GREEKS = "greeks"
CAPABILITY_TRADE_NOTES = "trade-notes"
CAPABILITY_PNL_REPORTS = "pnl-reports"

KNOWN_CAPABILITIES: Tuple[str, ...] = (
    CAPABILITY_LIVE_PRICE,
    CAPABILITY_OPTION_CHAIN,
    CAPABILITY_PRICE_CHART,
    CAPABILITY_CHART_TRADING,
    CAPABILITY_GREEKS,
    CAPABILITY_TRADE_NOTES,
    CAPABILITY_PNL_REPORTS,
)

CAPABILITY_LABELS: Dict[str, str] = {
    CAPABILITY_LIVE_PRICE: "Live price screen",
    CAPABILITY_OPTION_CHAIN: "Option chain",
    CAPABILITY_PRICE_CHART: "Price chart",
    CAPABILITY_CHART_TRADING: "Chart trading",
    CAPABILITY_GREEKS: "Greeks",
    CAPABILITY_TRADE_NOTES: "Trade notes",
    CAPABILITY_PNL_REPORTS: "P&L reports",
}

CAPABILITY_DESCRIPTIONS: Dict[str, str] = {
    CAPABILITY_LIVE_PRICE: (
        "The live quote screen for a strategy's front contract: depth, chart "
        "and ticket. Only a strategy with a single front contract can offer it."
    ),
    CAPABILITY_OPTION_CHAIN: "The paired-strike chain page.",
    CAPABILITY_PRICE_CHART: "Candle history from Dhan's chart endpoints.",
    CAPABILITY_CHART_TRADING: "One-click entry from the futures chart.",
    CAPABILITY_GREEKS: "Option-chain REST poll for delta, IV and the rest.",
    CAPABILITY_TRADE_NOTES: "Journal entries attached to trades.",
    CAPABILITY_PNL_REPORTS: "Realised P&L slices and the equity curve.",
}

# ---------------------------------------------------------------------------
# POLICIES: whether a strategy's own rules are ENFORCED.
#
# Read root CLAUDE.md section 3a before adding one. The rule there -- the YAML
# says what a strategy IS, the database says whether it is ON, and nothing
# about a strategy's rates, specs or margin model is editable from the UI --
# survives this, and these fit inside it, because what becomes editable is not
# a PARAMETER of a rule. It is whether a rule is OBEYED, which is the same kind
# of fact as enabled and armed: runtime state an operator flips, overlaid on a
# default the YAML declares.
#
# The moment a momentum floor, an ATR multiple or a lookback becomes editable
# from a page, the YAML stops being greppable against the specification's own
# table and section 3a stops being true. That is its own piece of work with its
# own decision, not an extension of this list.
#
# The framework knows only the NAMES, so a stored row for something that no
# longer exists can be ignored on load -- the same property `KNOWN_CAPABILITIES`
# gives capabilities and `MANAGED_KEYS` gives the settings table. What each one
# MEANS, and what its default is, belongs to the strategy's own module: see
# `src/swing/services/gate_policy.py`.
#
# They are offered only for a module that declares an `automation` block, the
# same way the arming switch is. A discretionary module has a person in front of
# every order and needs no policy switch.
#
# `regime.enforce` is deliberately SHARED between the two automated modules.
# Both mean the identical thing by it -- the regime index's close against its
# own 200-session SMA -- and what differs is only how much it does: it is
# load-bearing for the rotation and near-optional for BTST, where only 259 of
# 3,016 signals ever fire below the SMA. A second name for the same rule would
# make the switch look like two different facts.
POLICY_REGIME_ENFORCE = "regime.enforce"
POLICY_REGIME_ENFORCE_ENTRY_RETURN = "regime.enforce_entry_return"
POLICY_OFF_GATE_ENABLED = "off_gate.enabled"
POLICY_FNO_EXCLUDE = "fno.exclude"

KNOWN_POLICIES: Tuple[str, ...] = (
    POLICY_REGIME_ENFORCE,
    POLICY_REGIME_ENFORCE_ENTRY_RETURN,
    POLICY_OFF_GATE_ENABLED,
    POLICY_FNO_EXCLUDE,
)

POLICY_LABELS: Dict[str, str] = {
    POLICY_REGIME_ENFORCE: "Enforce the regime gate",
    POLICY_REGIME_ENFORCE_ENTRY_RETURN: "Enforce the entry-return filter",
    POLICY_OFF_GATE_ENABLED: "Trade the off-gate variant",
    POLICY_FNO_EXCLUDE: "Exclude F&O-eligible names",
}

# What ON and OFF should be CALLED for each switch. Two of these are about
# whether a rule is obeyed; the third is about whether a variant is being
# traded, and calling that one "enforced" would be a word that does not mean
# what it says.
POLICY_STATE_LABELS: Dict[str, tuple] = {
    POLICY_REGIME_ENFORCE: ("ENFORCED", "NOT ENFORCED"),
    POLICY_REGIME_ENFORCE_ENTRY_RETURN: ("ENFORCED", "NOT ENFORCED"),
    POLICY_OFF_GATE_ENABLED: ("ENABLED", "DISABLED"),
    POLICY_FNO_EXCLUDE: ("EXCLUDED", "INCLUDED"),
}

POLICY_DESCRIPTIONS: Dict[str, str] = {
    POLICY_REGIME_ENFORCE: (
        "P8 and P17. On: the book goes to cash when the index loses its "
        "200-session SMA, and no entry is taken below it. Off: the gate is "
        "still computed and still recorded on every session and every trade, "
        "and it stops nothing."
    ),
    POLICY_REGIME_ENFORCE_ENTRY_RETURN: (
        "P9. On: a negative 63-session index return blocks NEW entries. Off: "
        "it does not. It never forces an exit either way."
    ),
    POLICY_OFF_GATE_ENABLED: (
        "The V3b variant: hold a smaller book at its own momentum floor while "
        "the gate is off, instead of holding cash. The owner's own research "
        "tested thirteen such variants and not one beat holding cash."
    ),
    POLICY_FNO_EXCLUDE: (
        "On: only names WITHOUT listed derivatives are traded. For the 210 "
        "F&O names continuous trading ends at 15:15, so a 15:20 order lands "
        "in a closing auction this simulator cannot model -- and the non-F&O "
        "half carries the larger edge anyway (+0.722% against +0.512% gross "
        "per signal). Off: the whole universe is scanned, at roughly twice the "
        "signal frequency and with F&O orders refused after 15:15."
    ),
}


# ---------------------------------------------------------------------------
# SETTINGS: runtime VALUES, as against policies, which are runtime booleans.
#
# The same line applies and it is worth restating, because this is the list
# somebody will be tempted to extend. These two are WHEN THE MACHINE WAKES UP.
# Moving the order placement from 09:16 to 09:30 does not change what the
# strategy is; it changes when this installation acts on it.
#
# P1-P19 are not that and do not belong here: the momentum floor, the ATR
# multiple, the breadth ramp, the rank cut-off, every lookback -- and the
# rebalance CADENCE, which is P18 and was measured both ways (daily 23.4% CAGR
# at -22.2% drawdown against weekly's 19.9% and -18.3%). Choosing between those
# is picking a different strategy, not configuring this one. The moment one
# appears here, the YAML stops being greppable against the specification's own
# table and root CLAUDE.md section 3a stops being true.
#
# As with policies, the framework knows only the NAMES. What a setting MEANS,
# what its default is and what values are legal belong to the strategy's own
# module -- see `src/swing/services/schedule_settings.py`.
SETTING_NIGHTLY_AT = "schedule.nightly_at"
SETTING_REBALANCE_AT = "schedule.rebalance_at"
# BTST's two. Named separately from the rotation's rather than reused, because
# they are constrained in OPPOSITE directions and a shared name would make one
# validator have to ask which strategy it was looking at. The rotation's
# analysis may not run inside the session -- it would store a forming bar as a
# finished one; BTST's scan may only run inside it, because reading the forming
# session is the entire mechanism.
SETTING_SCAN_AT = "schedule.scan_at"
SETTING_EXIT_AT = "schedule.exit_at"

KNOWN_SETTINGS: Tuple[str, ...] = (
    SETTING_NIGHTLY_AT,
    SETTING_REBALANCE_AT,
    SETTING_SCAN_AT,
    SETTING_EXIT_AT,
)

SETTING_LABELS: Dict[str, str] = {
    SETTING_NIGHTLY_AT: "Analysis of stocks",
    SETTING_REBALANCE_AT: "Order placement time",
    SETTING_SCAN_AT: "Afternoon scan",
    SETTING_EXIT_AT: "Morning exit",
}

SETTING_DESCRIPTIONS: Dict[str, str] = {
    SETTING_NIGHTLY_AT: (
        "IST. When it refreshes prices from Dhan, re-ranks the universe, moves "
        "every trailing stop on to the session's close and writes the decision "
        "record. It never places an order, so it may run at any hour -- except "
        "during the session itself, which would store a half-finished bar as a "
        "finished one."
    ),
    SETTING_REBALANCE_AT: (
        "IST. When it sells what the rule says to sell and buys what it says to "
        "buy. It must be inside continuous trading: an order sent outside the "
        "session is refused, so a time outside it would mean a strategy that "
        "decides and never trades."
    ),
    SETTING_SCAN_AT: (
        "IST. When it reads the session so far -- each name's high, low and "
        "cumulative volume -- decides which qualify, and buys them. It must be "
        "INSIDE the session, because the forming session is what it measures. "
        "Moving it earlier measures less of the day and moving it later leaves "
        "less room to fill."
    ),
    SETTING_EXIT_AT: (
        "IST. When it sells everything it is holding, unconditionally. This is "
        "the whole edge: the same signals held to the next CLOSE instead of "
        "the next OPEN take the win rate from 71.4% to 49.0%. It must be "
        "inside continuous trading and early in it."
    ),
}


# Which page a capability grants, where it grants one. The trading pages that
# show HISTORY (/orders, /positions) are deliberately absent: a strategy going
# off must never hide trades that already happened.
CAPABILITY_PAGES: Dict[str, str] = {
    CAPABILITY_LIVE_PRICE: "/live",
    CAPABILITY_OPTION_CHAIN: "/chain",
    CAPABILITY_TRADE_NOTES: "/notes",
    CAPABILITY_PNL_REPORTS: "/reports",
}

# Of those, the ones that need something LIVE to show. The chain is a live
# quote screen and has nothing to display without a subscribed instrument.
#
# The others -- Reports and Trade Notes -- are history, and history does not go
# away when a strategy is switched off (decision 3). They follow their own
# capability toggle and nothing else: turning P&L reports off hides Reports for
# everyone, but turning the last STRATEGY off must not, or a trader loses the
# record of what that strategy did.
CAPABILITY_LIVE_PAGES: FrozenSet[str] = frozenset(
    {CAPABILITY_LIVE_PRICE, CAPABILITY_OPTION_CHAIN}
)

# Pages granted by a strategy being live, WHATEVER that strategy is.
#
# Empty since 2026-09-18, and deliberately kept rather than deleted. `/live`
# used to be here, which meant the Crude Oil screen appeared whenever ANY
# strategy was running -- so switching MCX crude off and leaving only the NSE
# rotation on left a page in the sidebar showing a front contract that no
# running strategy had. A page belongs to whatever can actually fill it, and
# the mechanism for that already existed: a capability the strategy declares.
#
# The tuple stays because the distinction it draws is still real. A page that
# needs *something* live but is not specific to one strategy's instruments
# belongs here; a page that needs one strategy's own contract belongs in
# CAPABILITY_PAGES. Nothing is in the first category today.
STRATEGY_LIVE_PAGES: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ClosingAuction:
    """An exchange session that is NOT continuous trading, at the end of a day.

    NSE's Closing Auction Session went live on 3 August 2026: for F&O-eligible
    names, continuous cash trading now ends at `continuous_close` (15:15) and a
    call auction runs to `auction_close` (15:35), while the rest of the market
    trades continuously to the ordinary close. Two things follow for anything
    that fires a stop:

      * an order sent between `continuous_close` and the close for one of those
        names cannot execute in continuous trading -- it lands in the auction,
        at a price the auction sets and that no depth book here publishes; and
      * this simulator has no model of a call auction at all. Filling such an
        order against the last continuous book would flatter it.

    `applies_to` names the property that decides which instruments are in
    scope. Only "fno_eligible" is understood, and it is a column on
    `instruments`, derived from the master's own FUTSTK rows.

    The times are configuration, not constants in Python, for the same reason
    every charge rate is (root CLAUDE.md section 7): they change what a trade
    does, so they belong in the strategy module with the rest of its session.
    """

    applies_to: str
    continuous_close: time
    auction_close: time

    APPLIES_FNO_ELIGIBLE = "fno_eligible"


@dataclass(frozen=True)
class MarketHours:
    timezone: str
    open: time
    close: time
    close_us_dst: Optional[time]
    trading_days: Tuple[int, ...]
    # None for an exchange with no auction session -- MCX has none.
    closing_auction: Optional[ClosingAuction] = None


@dataclass(frozen=True)
class SubscriptionPolicy:
    """What a strategy contributes to the ONE process-wide feed connection.

    `kind` is what tells the feed manager which shape to resolve:

      option_chain -- the near future plus an ATM strike window across the
                      nearest expiries. What MCX crude does.
      positions    -- whatever is currently HELD, plus the instruments the
                      strategy names outright (its regime index, and the
                      candidates a rebalance is about to trade). A cash-equity
                      rotation has no chain to centre, and subscribing its
                      whole universe would spend the connection's budget on
                      500 symbols whose decisions are taken from daily bars
                      fetched over REST.
      universe     -- every tradable name the strategy claims, but only
                      between `window_opens_at` and `window_closes_at`, and
                      `positions` the rest of the day.

    THE THIRD KIND INVERTS THE SECOND'S REASONING, and both are right about
    their own strategy. A rotation decides overnight on daily bars, so a live
    universe subscription is 490 instruments nobody reads. BTST decides at
    15:20 on the session's own high, low and cumulative volume for every
    candidate at once -- for which the feed is not an optimisation, it is the
    only mechanism that fits in the window (specification section 8 measures
    REST-polling ~480 symbols at ~5 minutes).

    The window is what keeps the cost bounded. Dhan's Quote/Full packet carries
    the session AGGREGATE rather than a delta, so a subscription opened in the
    afternoon should still report the whole session -- which would mean paying
    for ~289 extra instruments for forty minutes a day instead of six hours.
    That claim is UNVERIFIED against a live feed; see
    `scripts/verify_feed_session_fields.py`. If it is wrong, the fix is to move
    `window_opens_at` to the open, which is configuration rather than code.
    """

    kind: str
    strike_window: int = 0
    expiries_to_subscribe: int = 0
    resubscribe_move_strikes: int = 3
    # UNIVERSE only. None on the other two kinds, which are all-day.
    window_opens_at: Optional[time] = None
    window_closes_at: Optional[time] = None

    OPTION_CHAIN = "option_chain"
    POSITIONS = "positions"
    UNIVERSE = "universe"

    KINDS = (OPTION_CHAIN, POSITIONS, UNIVERSE)

    def window_is_open(self, at: time) -> bool:
        """Is the universe due on the feed at this moment?

        False for any kind that has no window, so a caller need not ask what
        kind it is holding. A window with only one end configured is treated as
        unbounded at the other, which is the reading that keeps a half-filled
        block from silently subscribing nothing.
        """
        if self.kind != self.UNIVERSE:
            return False
        if self.window_opens_at is not None and at < self.window_opens_at:
            return False
        if self.window_closes_at is not None and at > self.window_closes_at:
            return False
        return True


@dataclass(frozen=True)
class Universe:
    """A named list of symbols a strategy claims, loaded from a CSV.

    A strategy owning ONE underlying (MCX crude) has no universe and resolves
    ownership against `symbol`. A rotation owning 500 of the 9,884 NSE EQUITY
    rows in Dhan's master needs a list, and that list is configuration -- a
    file an operator refreshes quarterly from NSE's own archive -- rather than
    a scrape, so this application gains no new outbound host.
    """

    name: str
    file: str
    symbols: FrozenSet[str]
    # security_id as published in the universe file. The instrument master is
    # authoritative; this is kept only so a divergence can be reported rather
    # than discovered as a wrong price.
    declared_security_ids: Dict[str, str] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.symbols)


@dataclass(frozen=True)
class InstrumentSet:
    """One family of rows a strategy pulls out of Dhan's instrument master.

    MCX crude declares two implicitly (OPTFUT and FUTCOM, both MCX_COMM). An
    equity rotation declares two explicitly, in DIFFERENT segments: its
    universe of NSE_EQ EQUITY rows, and the single IDX_I INDEX row its regime
    gate is read from. That is why the segment lives here and not only on the
    strategy.
    """

    instrument_type: str
    exchange_segment: str
    exchange_segment_code: int
    # frozenset() means "every symbol in the strategy's universe".
    symbols: FrozenSet[str] = field(default_factory=frozenset)
    from_universe: bool = False
    # Where the contract size comes from. Dhan publishes LOT_SIZE=1.0 for every
    # MCX row, which is a defect and is substituted from contract_specs; for
    # NSE EQUITY 1.0 is CORRECT -- delivery trades one share -- and demanding a
    # contract_specs entry would mean 500 of them.
    lot_size_source: str = "config"
    # NSE publishes several rows under one UNDERLYING_SYMBOL: CHOLAFIN and
    # MOTHERSON each have an EQUITY row for the share and another for a listed
    # NCD (SERIES=D1), and ingesting the debenture instead of the share is a
    # silently wrong price. An empty tuple means "any series", which is what
    # MCX wants -- its rows carry SERIES=NA.
    series: Tuple[str, ...] = ()
    # Which master column becomes the row's `trading_symbol`. DISPLAY_NAME
    # identifies a derivatives CONTRACT ("CRUDEOIL 17 SEP 8350 CALL") and is
    # the right answer there; for a cash equity it is the company's long name
    # ("Bharat Heavy Electricals") while UNDERLYING_SYMBOL is the ticker a
    # trader actually reads and types. Both are kept -- this only decides
    # which one is the short one.
    trading_symbol_source: str = "display_name"

    TRADING_SYMBOL_FROM_DISPLAY_NAME = "display_name"
    TRADING_SYMBOL_FROM_UNDERLYING = "underlying_symbol"
    # A free-text tag a strategy uses to find one of its own sets again, e.g.
    # "regime_index". Never interpreted by the framework.
    role: Optional[str] = None

    LOT_SIZE_FROM_CONFIG = "config"
    LOT_SIZE_FROM_MASTER = "master"

    def trusts_master_lot_size(self) -> bool:
        return self.lot_size_source == self.LOT_SIZE_FROM_MASTER

    def ingestion_signature(self) -> Tuple[Any, ...]:
        """HOW this set turns a master row into an `instruments` row.

        Everything that changes the resulting row, and nothing that does not:
        `symbols` and `from_universe` decide WHICH rows are claimed, not what
        they become, and `role` is a tag the framework never interprets.

        It exists so that two strategies claiming the SAME name can be told
        apart from two strategies claiming it DIFFERENTLY. The NSE Swing
        Momentum rotation and BTST Overnight both trade the Nifty 500 out of
        `NSE_EQ`, with the same series filter, the same lot-size source and the
        same trading-symbol source -- so the row either of them would ingest is
        byte for byte the same row, and insisting one of them own it would mean
        two strategies could never share a universe. Two sets that disagree
        about the series or the lot size WOULD produce different rows, and that
        is still refused.
        """
        return (
            self.instrument_type,
            self.exchange_segment,
            self.exchange_segment_code,
            tuple(sorted(self.series)),
            self.lot_size_source,
            self.trading_symbol_source,
        )

    def accepts_series(self, series: Optional[str]) -> bool:
        if not self.series:
            return True
        return str(series or "").strip().upper() in self.series


@dataclass(frozen=True)
class ReferenceInstrument:
    """An instrument a strategy READS but never trades.

    The swing rotation's regime gate is read from the NIFTY 50 index. That is
    market data and nothing else: the gate is evaluated on the index's daily
    CLOSE, orders fill at the next session's open, and no decision anywhere
    needs a live index tick.

    It is deliberately NOT ingested into the `instruments` table, and this is
    not tidiness. **Dhan's security ids are unique per SEGMENT, not globally**
    -- verified against the master on 2026-09-18, where id 13 is NIFTY in
    IDX_I and ABB in NSE_EQ, with 44 such collisions between the INDEX and
    EQUITY row sets. `instruments.security_id` carries a global UNIQUE
    constraint and `MarketBook` keys its rows by security id alone, so
    ingesting the index would either fail the constraint or silently merge the
    index's prices with a stock that is itself in the Nifty 500. Keeping the
    reference instrument out of the table sidesteps both, and costs nothing:
    the charts client takes (security_id, exchange_segment, instrument) as
    arguments and needs no database row.
    """

    role: str
    symbol: str
    label: str
    security_id: str
    exchange_segment: str
    exchange_segment_code: int
    instrument: str


@dataclass(frozen=True)
class MarginModel:
    """An ESTIMATE of what a short position ties up. Never a broker figure.

    `model` is carried so a surface can say which approximation it is looking
    at, and so an unknown model fails loudly rather than silently blocking
    zero.
    """

    model: str
    short_option_percent_of_notional: Decimal

    PERCENT_OF_NOTIONAL = "percent_of_notional"


@dataclass(frozen=True)
class AutomationPolicy:
    """Whether a strategy may act on its own, and what it starts allowed to do.

    ENABLED AND ARMED ARE TWO SWITCHES, and this is where the framework learns
    the difference. Enabling a strategy makes it compute, decide and write a
    decision record. ARMING is what lets it submit an order. They are separate
    because a strategy that trades on a schedule trades with nobody watching,
    and watching it decide for a while before it can spend anything is the
    cheapest possible safeguard.

    `automated` is false for a strategy whose YAML declares no `automation`
    block at all -- a discretionary module like MCX crude, where every order
    comes from a human and there is nothing to arm. Nothing in the application
    offers an arming control for one of those.

    `armed_by_default` is only the state a brand-new installation starts in.
    After that the live state is a row in `feature_toggles` under the
    AUTOMATION scope, exactly as `enabled_by_default` is.

    `max_lots_per_order` overrides `trading.max_lots_per_order` for this
    strategy. It exists because "lots" means two different things: an MCX
    option lot is 100 barrels and 100 of them is a fat finger, while an NSE
    delivery "lot" is one share and 100 of them is a third of one position.
    A single global cap cannot be right for both, and silently routing the
    share count through `quantity_override` to dodge the check would leave
    `order.lots` disagreeing with `order.quantity / order.lot_size`.
    """

    automated: bool
    armed_by_default: bool
    max_lots_per_order: Optional[int] = None


@dataclass(frozen=True)
class StrategyDefinition:
    key: str
    label: str
    description: str
    enabled_by_default: bool

    # underlying
    symbol: str
    underlying_scrip: Optional[int]
    exchange_segment: str
    exchange_segment_code: int
    exchange_id: str
    option_instrument_type: Optional[str]
    futures_instrument_type: Optional[str]

    contract_specs: Dict[str, Any]
    tick_size_divisor: int
    market_hours: MarketHours
    subscription: SubscriptionPolicy
    greeks_expiries_to_poll: int
    charges_rate_card: str
    margin: MarginModel
    capabilities: FrozenSet[str] = field(default_factory=frozenset)

    # Whether this module trades on a schedule, and whether it starts allowed
    # to. A strategy with no `automation` block is discretionary and is never
    # armed -- see AutomationPolicy.
    automation: AutomationPolicy = field(
        default_factory=lambda: AutomationPolicy(
            automated=False, armed_by_default=False
        )
    )

    # A strategy owning MANY underlyings names the list here. None means it
    # owns exactly one -- `symbol` -- which is what MCX crude does and what
    # every call site assumed before 2026-09-18.
    universe: Optional[Universe] = None
    # Which families of master rows this strategy claims. Synthesised from
    # option_instrument_type / futures_instrument_type when the YAML does not
    # declare them, so a single-underlying derivatives module needs no change.
    instrument_sets: Tuple[InstrumentSet, ...] = ()
    # Instruments this strategy reads but never trades, by role. See
    # ReferenceInstrument for why these are not rows in `instruments`.
    reference_instruments: Dict[str, ReferenceInstrument] = field(default_factory=dict)

    # Blocks of the YAML the FRAMEWORK does not interpret, handed to the
    # strategy's own module unchanged.
    #
    # This is how a strategy's rules live in configuration without the registry
    # having to learn them. The rotation's P1-P19 parameters are numbers that
    # change what it trades, and root CLAUDE.md section 7 is absolute that no
    # such number may be hardcoded in Python -- but they mean nothing to any
    # other strategy, and teaching `StrategyDefinition` about momentum
    # lookbacks would make the framework specific to one module.
    #
    # Nothing here is validated on load. The module that reads a block is the
    # only thing that knows what it should contain, and it validates it.
    module_config: Dict[str, Any] = field(default_factory=dict)

    # --- derived ----------------------------------------------------------
    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def lot_size(self, symbol: Optional[str] = None) -> Optional[int]:
        spec = self.contract_specs.get(symbol or self.symbol)
        if not isinstance(spec, dict):
            return None
        value = spec.get("lot_size")
        return int(value) if value is not None else None

    def symbols(self) -> FrozenSet[str]:
        """Every underlying symbol this strategy claims.

        One symbol for a derivatives module on a single underlying; the
        universe plus any instrument set's explicit symbols (the regime index)
        for a rotation.
        """
        collected: set = set()
        for instrument_set in self.instrument_sets:
            if instrument_set.from_universe and self.universe is not None:
                collected.update(self.universe.symbols)
            else:
                collected.update(instrument_set.symbols)
        return frozenset(collected) or frozenset({self.symbol})

    def segments(self) -> FrozenSet[str]:
        """Every exchange segment this strategy has instruments in.

        More than one is normal for a rotation: its stocks are NSE_EQ and the
        index its regime gate reads is IDX_I.
        """
        return frozenset(
            instrument_set.exchange_segment
            for instrument_set in self.instrument_sets
        ) or frozenset({self.exchange_segment})

    def module_section(self, name: str) -> Dict[str, Any]:
        """One uninterpreted block, or an empty mapping."""
        section = self.module_config.get(str(name))
        return dict(section) if isinstance(section, dict) else {}

    def reference_instrument(self, role: str) -> Optional[ReferenceInstrument]:
        return self.reference_instruments.get(str(role))

    def instrument_set_with_role(self, role: str) -> Optional[InstrumentSet]:
        for instrument_set in self.instrument_sets:
            if instrument_set.role == role:
                return instrument_set
        return None

    def owns_instrument(self, exchange_segment: str, underlying_symbol: str) -> bool:
        """Does this strategy trade that contract?

        Resolved from the instrument's own segment and underlying rather than
        from a stored key, and only ever called when a trade is written or a
        subscription is built -- never on the tick path.
        """
        segment = str(exchange_segment)
        symbol = str(underlying_symbol)
        for instrument_set in self.instrument_sets:
            if instrument_set.exchange_segment != segment:
                continue
            if instrument_set.from_universe:
                if self.universe is not None and symbol in self.universe.symbols:
                    return True
            elif symbol in instrument_set.symbols:
                return True
        return False

    def pages(self, enabled_capabilities: FrozenSet[str]) -> List[str]:
        """LIVE pages this strategy contributes, given the capabilities on.

        History pages are not here: they do not belong to a strategy and do not
        disappear with one. See `StrategyRegistry.feature_pages`.
        """
        granted = list(STRATEGY_LIVE_PAGES)
        for capability in sorted(
            self.capabilities & enabled_capabilities & CAPABILITY_LIVE_PAGES
        ):
            page = CAPABILITY_PAGES.get(capability)
            if page:
                granted.append(page)
        return granted


def _require(document: Dict[str, Any], key: str, source: str) -> Any:
    node: Any = document
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            raise StrategyConfigError(f"{source}: missing required key '{key}'")
        node = node[part]
    if node is None:
        raise StrategyConfigError(f"{source}: '{key}' is empty")
    return node


def _parse_hhmm(value: Any, source: str, key: str) -> time:
    text = str(value).strip()
    try:
        hour, minute = text.split(":")
        return time(int(hour), int(minute))
    except Exception as exc:  # noqa: BLE001 - config error, reported as one
        raise StrategyConfigError(
            f"{source}: '{key}' must be HH:MM, got {value!r}"
        ) from exc


# Top-level YAML keys the framework itself reads. Anything else is a block the
# strategy's own module owns, and travels to it through `module_config`.
FRAMEWORK_KEYS = frozenset(
    {
        "key",
        "label",
        "description",
        "enabled_by_default",
        "underlying",
        "universe",
        "instrument_sets",
        "reference_instruments",
        "contract_specs",
        "market_hours",
        "subscription",
        "greeks",
        "charges",
        "margin",
        "capabilities",
        # Interpreted by the framework since 2026-09-18: whether a module
        # trades unattended is a property every surface needs to know about
        # (the Strategies page, the health page, the scheduler), not something
        # only the owning module understands.
        "automation",
    }
)

UNIVERSES_DIRNAME = "universes"

# Column names in a universe CSV. The file is a straight copy of the research
# project's `universe_nifty500.csv`, kept in that shape so a refresh is a copy
# rather than a transformation.
UNIVERSE_COL_SYMBOL = "symbol"
UNIVERSE_COL_SECURITY_ID = "security_id"


def load_universe(name: str, filename: str, source: str) -> Universe:
    """Read a universe CSV from ``<CONFIG_PATH>/universes/``.

    A missing or empty file is fatal for the same reason a malformed strategy
    is: a rotation that silently loses half its universe does not fail, it
    just ranks a smaller pool and produces a different, plausible answer.
    """
    path = filename
    if not os.path.isabs(path):
        path = os.path.join(config_utils.get_config_path(), path)

    if not os.path.isfile(path):
        raise StrategyConfigError(
            f"{source}: universe file {path!r} does not exist. Every symbol "
            f"this strategy may trade comes from that file."
        )

    symbols: List[str] = []
    declared: Dict[str, str] = {}
    with open(path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or UNIVERSE_COL_SYMBOL not in reader.fieldnames:
            raise StrategyConfigError(
                f"{source}: universe file {path!r} has no {UNIVERSE_COL_SYMBOL!r} "
                f"column; got {reader.fieldnames}"
            )
        for record in reader:
            symbol = (record.get(UNIVERSE_COL_SYMBOL) or "").strip().upper()
            if not symbol:
                continue
            symbols.append(symbol)
            security_id = (record.get(UNIVERSE_COL_SECURITY_ID) or "").strip()
            if security_id:
                declared[symbol] = security_id

    if not symbols:
        raise StrategyConfigError(
            f"{source}: universe file {path!r} contains no symbols."
        )

    duplicates = sorted({s for s in symbols if symbols.count(s) > 1})
    if duplicates:
        raise StrategyConfigError(
            f"{source}: universe file {path!r} lists "
            f"{', '.join(duplicates[:5])} more than once."
        )

    return Universe(
        name=str(name),
        file=path,
        symbols=frozenset(symbols),
        declared_security_ids=declared,
    )


def _build_instrument_sets(
    document: Dict[str, Any],
    source: str,
    universe: Optional[Universe],
    default_segment: str,
    default_segment_code: int,
) -> Tuple[InstrumentSet, ...]:
    """The families of master rows this strategy claims.

    Declared explicitly under `instrument_sets:`, or synthesised from
    `underlying.option_instrument_type` / `futures_instrument_type` so a
    single-underlying derivatives module keeps working unchanged.
    """
    declared = document.get("instrument_sets")
    if not declared:
        underlying = document.get("underlying") or {}
        symbol = str(_require(document, "underlying.symbol", source))
        types = [
            underlying.get("option_instrument_type"),
            underlying.get("futures_instrument_type"),
        ]
        return tuple(
            InstrumentSet(
                instrument_type=str(instrument_type),
                exchange_segment=default_segment,
                exchange_segment_code=default_segment_code,
                symbols=frozenset({symbol}),
                from_universe=False,
                lot_size_source=InstrumentSet.LOT_SIZE_FROM_CONFIG,
            )
            for instrument_type in types
            if instrument_type
        )

    if not isinstance(declared, list):
        raise StrategyConfigError(f"{source}: 'instrument_sets' must be a list")

    built: List[InstrumentSet] = []
    for index, entry in enumerate(declared):
        where = f"{source}: instrument_sets[{index}]"
        if not isinstance(entry, dict):
            raise StrategyConfigError(f"{where} must be a mapping")
        instrument_type = str(entry.get("instrument_type") or "").strip()
        if not instrument_type:
            raise StrategyConfigError(f"{where}: 'instrument_type' is required")

        raw_symbols = entry.get("symbols")
        from_universe = isinstance(raw_symbols, str) and raw_symbols.strip() == "universe"
        if from_universe and universe is None:
            raise StrategyConfigError(
                f"{where}: symbols: universe, but no 'universe' block is declared."
            )
        if from_universe:
            symbols: FrozenSet[str] = frozenset()
        elif isinstance(raw_symbols, list):
            symbols = frozenset(str(one).strip().upper() for one in raw_symbols if str(one).strip())
            if not symbols:
                raise StrategyConfigError(f"{where}: 'symbols' list is empty")
        else:
            raise StrategyConfigError(
                f"{where}: 'symbols' must be a list, or the string 'universe'"
            )

        lot_size_source = str(
            entry.get("lot_size_source") or InstrumentSet.LOT_SIZE_FROM_CONFIG
        )
        if lot_size_source not in (
            InstrumentSet.LOT_SIZE_FROM_CONFIG,
            InstrumentSet.LOT_SIZE_FROM_MASTER,
        ):
            raise StrategyConfigError(
                f"{where}: lot_size_source {lot_size_source!r} is not "
                f"{InstrumentSet.LOT_SIZE_FROM_CONFIG!r} or "
                f"{InstrumentSet.LOT_SIZE_FROM_MASTER!r}."
            )

        built.append(
            InstrumentSet(
                instrument_type=instrument_type,
                exchange_segment=str(entry.get("exchange_segment") or default_segment),
                exchange_segment_code=int(
                    entry.get("exchange_segment_code", default_segment_code)
                ),
                symbols=symbols,
                from_universe=from_universe,
                lot_size_source=lot_size_source,
                trading_symbol_source=str(
                    entry.get("trading_symbol_source")
                    or InstrumentSet.TRADING_SYMBOL_FROM_DISPLAY_NAME
                ),
                series=tuple(
                    str(one).strip().upper()
                    for one in (entry.get("series") or [])
                    if str(one).strip()
                ),
                role=(str(entry["role"]) if entry.get("role") else None),
            )
        )
    return tuple(built)


def _build_reference_instruments(
    document: Dict[str, Any], source: str, default_segment: str, default_segment_code: int
) -> Dict[str, ReferenceInstrument]:
    declared = document.get("reference_instruments") or {}
    if not isinstance(declared, dict):
        raise StrategyConfigError(
            f"{source}: 'reference_instruments' must be a mapping of role -> instrument"
        )
    built: Dict[str, ReferenceInstrument] = {}
    for role, entry in declared.items():
        where = f"{source}: reference_instruments.{role}"
        if not isinstance(entry, dict):
            raise StrategyConfigError(f"{where} must be a mapping")
        for required in ("symbol", "security_id", "exchange_segment", "instrument"):
            if not entry.get(required):
                raise StrategyConfigError(f"{where}: '{required}' is required")
        built[str(role)] = ReferenceInstrument(
            role=str(role),
            symbol=str(entry["symbol"]),
            label=str(entry.get("label") or entry["symbol"]),
            security_id=str(entry["security_id"]),
            exchange_segment=str(entry["exchange_segment"]),
            exchange_segment_code=int(
                entry.get("exchange_segment_code", default_segment_code)
            ),
            instrument=str(entry["instrument"]),
        )
    return built


def _build_closing_auction(
    hours: Dict[str, Any], source: str
) -> Optional[ClosingAuction]:
    """The auction session, where the exchange runs one. See ClosingAuction."""
    block = hours.get("closing_auction")
    if not block:
        return None
    if not isinstance(block, dict):
        raise StrategyConfigError(
            f"{source}: market_hours.closing_auction must be a mapping."
        )
    applies_to = str(_require(block, "applies_to", source))
    if applies_to != ClosingAuction.APPLIES_FNO_ELIGIBLE:
        raise StrategyConfigError(
            f"{source}: market_hours.closing_auction.applies_to {applies_to!r} "
            f"is not understood. The only scope implemented is "
            f"{ClosingAuction.APPLIES_FNO_ELIGIBLE!r}, which reads the column "
            f"of that name on `instruments`."
        )
    return ClosingAuction(
        applies_to=applies_to,
        continuous_close=_parse_hhmm(
            _require(block, "continuous_close", source),
            source,
            "market_hours.closing_auction.continuous_close",
        ),
        auction_close=_parse_hhmm(
            _require(block, "auction_close", source),
            source,
            "market_hours.closing_auction.auction_close",
        ),
    )


def build_definition(document: Dict[str, Any], source: str) -> StrategyDefinition:
    """Turn one parsed YAML document into a definition, or explain why not.

    Every failure here is a configuration mistake an operator can fix, so each
    one names the file and the key rather than raising a KeyError from the
    middle of a dictionary walk.
    """
    key = str(_require(document, "key", source)).strip()
    if not key:
        raise StrategyConfigError(f"{source}: 'key' is empty")

    hours = _require(document, "market_hours", source)
    close_us_dst = hours.get("close_us_dst")
    # A cash-equity rotation has no chain to centre and no strikes to window,
    # so it declares no subscription block at all; its feed contribution is
    # whatever it holds. See SubscriptionPolicy.
    subscription = document.get("subscription") or {}
    specs = dict(document.get("contract_specs") or {})
    tick_divisor = int(specs.pop("tick_size_divisor", 1) or 1)

    universe_block = document.get("universe") or {}
    universe = (
        load_universe(
            name=str(universe_block.get("name") or key),
            filename=str(_require(document, "universe.file", source)),
            source=source,
        )
        if universe_block
        else None
    )

    default_segment = str(_require(document, "underlying.exchange_segment", source))
    default_segment_code = int(
        _require(document, "underlying.exchange_segment_code", source)
    )
    instrument_sets = _build_instrument_sets(
        document, source, universe, default_segment, default_segment_code
    )
    if not instrument_sets:
        raise StrategyConfigError(
            f"{source}: the strategy claims no instrument rows. Declare "
            f"'instrument_sets', or underlying.option_instrument_type / "
            f"underlying.futures_instrument_type."
        )

    subscription_kind = str(
        subscription.get("kind")
        or (
            SubscriptionPolicy.OPTION_CHAIN
            if subscription.get("strike_window") is not None
            else SubscriptionPolicy.POSITIONS
        )
    )
    if subscription_kind not in SubscriptionPolicy.KINDS:
        raise StrategyConfigError(
            f"{source}: subscription.kind {subscription_kind!r} is not one of "
            f"{', '.join(repr(one) for one in SubscriptionPolicy.KINDS)}."
        )
    window_opens_at = None
    window_closes_at = None
    if subscription_kind == SubscriptionPolicy.UNIVERSE:
        # REQUIRED, not defaulted. A universe subscription with no window is
        # the whole universe on the feed all session, which is precisely the
        # cost this kind exists to bound -- and it would be an expensive thing
        # to acquire by leaving a key out.
        window_opens_at = _parse_hhmm(
            _require(subscription, "window_opens_at", source),
            source,
            "subscription.window_opens_at",
        )
        window_closes_at = _parse_hhmm(
            _require(subscription, "window_closes_at", source),
            source,
            "subscription.window_closes_at",
        )
        if window_closes_at <= window_opens_at:
            raise StrategyConfigError(
                f"{source}: subscription.window_closes_at "
                f"({window_closes_at.strftime('%H:%M')}) must be after "
                f"window_opens_at ({window_opens_at.strftime('%H:%M')}); the "
                f"universe would never reach the feed."
            )
    if subscription_kind == SubscriptionPolicy.OPTION_CHAIN:
        _require(subscription, "strike_window", source)
        _require(subscription, "expiries_to_subscribe", source)
        # Dhan's UnderlyingScrip is the option chain's key. A cash strategy has
        # no chain to ask for, so it is required here and only here.
        _require(document, "underlying.underlying_scrip", source)
        _require(document, "underlying.option_instrument_type", source)
        _require(document, "underlying.futures_instrument_type", source)

    declared = [str(item) for item in (document.get("capabilities") or [])]
    unknown = [item for item in declared if item not in KNOWN_CAPABILITIES]
    if unknown:
        raise StrategyConfigError(
            f"{source}: unknown capabilit{'y' if len(unknown) == 1 else 'ies'} "
            f"{', '.join(sorted(unknown))}. Known: {', '.join(KNOWN_CAPABILITIES)}."
        )

    # A module with no `automation` block is discretionary: nothing schedules
    # it, there is no arming control for it, and `is_armed` is permanently
    # false. MCX crude is that case and must stay that way.
    automation_block = document.get("automation")
    if automation_block is not None and not isinstance(automation_block, dict):
        raise StrategyConfigError(
            f"{source}: 'automation' must be a mapping, got "
            f"{type(automation_block).__name__}."
        )
    max_lots = (automation_block or {}).get("max_lots_per_order")
    if max_lots is not None:
        try:
            max_lots = int(max_lots)
        except (TypeError, ValueError) as error:
            raise StrategyConfigError(
                f"{source}: automation.max_lots_per_order must be an integer, "
                f"got {max_lots!r}."
            ) from error
        if max_lots <= 0:
            raise StrategyConfigError(
                f"{source}: automation.max_lots_per_order must be positive."
            )
    automation = AutomationPolicy(
        automated=automation_block is not None,
        armed_by_default=bool((automation_block or {}).get("armed_by_default", False)),
        max_lots_per_order=max_lots,
    )

    margin = document.get("margin") or {}
    margin_model = str(margin.get("model") or MarginModel.PERCENT_OF_NOTIONAL)
    if margin_model != MarginModel.PERCENT_OF_NOTIONAL:
        raise StrategyConfigError(
            f"{source}: margin.model {margin_model!r} is not implemented. "
            f"Only {MarginModel.PERCENT_OF_NOTIONAL!r} is."
        )

    return StrategyDefinition(
        key=key,
        label=str(document.get("label") or key),
        description=str(document.get("description") or "").strip(),
        enabled_by_default=bool(document.get("enabled_by_default", True)),
        symbol=str(_require(document, "underlying.symbol", source)),
        underlying_scrip=(
            int((document.get("underlying") or {}).get("underlying_scrip"))
            if (document.get("underlying") or {}).get("underlying_scrip") is not None
            else None
        ),
        exchange_segment=default_segment,
        exchange_segment_code=default_segment_code,
        exchange_id=str(_require(document, "underlying.exchange_id", source)),
        option_instrument_type=(
            str((document.get("underlying") or {}).get("option_instrument_type"))
            if (document.get("underlying") or {}).get("option_instrument_type")
            else None
        ),
        futures_instrument_type=(
            str((document.get("underlying") or {}).get("futures_instrument_type"))
            if (document.get("underlying") or {}).get("futures_instrument_type")
            else None
        ),
        contract_specs=specs,
        tick_size_divisor=tick_divisor,
        market_hours=MarketHours(
            timezone=str(hours.get("timezone") or "Asia/Kolkata"),
            open=_parse_hhmm(_require(hours, "open", source), source, "market_hours.open"),
            close=_parse_hhmm(
                _require(hours, "close", source), source, "market_hours.close"
            ),
            close_us_dst=(
                _parse_hhmm(close_us_dst, source, "market_hours.close_us_dst")
                if close_us_dst
                else None
            ),
            trading_days=tuple(int(day) for day in (hours.get("trading_days") or [])),
            closing_auction=_build_closing_auction(hours, source),
        ),
        subscription=SubscriptionPolicy(
            kind=subscription_kind,
            strike_window=int(subscription.get("strike_window") or 0),
            expiries_to_subscribe=int(subscription.get("expiries_to_subscribe") or 0),
            resubscribe_move_strikes=int(
                subscription.get("resubscribe_move_strikes", 3)
            ),
            window_opens_at=window_opens_at,
            window_closes_at=window_closes_at,
        ),
        greeks_expiries_to_poll=int(
            (document.get("greeks") or {}).get("expiries_to_poll", 2)
        ),
        charges_rate_card=str(
            (document.get("charges") or {}).get("rate_card") or "charges"
        ),
        margin=MarginModel(
            model=margin_model,
            short_option_percent_of_notional=Decimal(
                str(margin.get("short_option_percent_of_notional", "0"))
            ),
        ),
        capabilities=frozenset(declared),
        automation=automation,
        universe=universe,
        instrument_sets=instrument_sets,
        reference_instruments=_build_reference_instruments(
            document, source, default_segment, default_segment_code
        ),
        module_config={
            key: value
            for key, value in document.items()
            if key not in FRAMEWORK_KEYS
        },
    )
