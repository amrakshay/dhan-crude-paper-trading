"""The decision journal, and the overnight book.

Three tables, all **append-only except the one that has to change**, and the
exception is stated rather than assumed.

**Why these are not `swing_sessions` and `swing_decisions`.** That journal's
columns are a breadth-ramped rotation's record -- `gate_on`, `breadth_above`,
`breadth_liquid`, `slots`, `index_sma`, `ranking_json`. This strategy has no
breadth and no ramp, and wants things that one has no room for: each
candidate's volume ratio against its own 20-day average, its close location
within the day's range, how far it is above its 55-day high, and afterwards the
realised overnight gap. Bending the rotation's tables to fit would make both
harder to read and would put a migration on a live, armed strategy.

`btst_sessions` -- one row per run, carrying the INPUTS the decision was made
from. "Nothing qualified" is useless in six months; "499 in the universe, 289
after excluding F&O names, 284 with enough history, 31 above their 55-day high,
6 of those on 2x volume, 2 closing strong, 1 through every filter" can be
audited against the data that produced it. That funnel IS the record.

`btst_decisions` -- one row per action AND per non-action, so "why was X not
bought" has an answer that does not require recomputing anything from bars or
from a book that has since moved on. This is the table that carries the live
inputs, because they are gone a second later: an intraday high, a low and a
cumulative volume at 15:20 exist nowhere else once the session closes.

`btst_holdings` -- what is held overnight, and whether it got out. THIS IS THE
ONE THAT IS UPDATED, and it is the reason the module has a third table at all.
Specification section 10.1: holding to the next close instead of the next open
takes the win rate from 71.4% to 49.0% and the net edge from +0.317% to
+0.128%. The exit is not a step in a process, it is the strategy -- so "is
anything still open that should not be", answered from a row rather than
inferred from the absence of one, is what the alarm and the Health tab's first
verdict both read.
"""
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)

from src.database.base import Money, PreciseDateTime, TimestampedModel


# What kind of run produced this record. PER MODULE, not shared with the
# rotation's NIGHTLY/REBALANCE: the strings are stored, and two strategies
# whose jobs do different things must not share a vocabulary that makes their
# journals look comparable when they are not.
RUN_SCAN = "SCAN"        # ~15:20: read the live session, decide, and buy
RUN_EXIT = "EXIT"        # at the open: sell everything, unconditionally
RUN_MANUAL = "MANUAL"    # an operator asked for a scan without placing orders
RUN_KINDS = (RUN_SCAN, RUN_EXIT, RUN_MANUAL)

# How the run ended.
STATUS_RUNNING = "RUNNING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
# The market was shut, so there was nothing to read. Distinct from COMPLETED
# because a run that decided nothing and a run that could not run are different
# facts, and the missed-run detector reads them differently.
STATUS_SKIPPED = "SKIPPED"

# What was decided about one symbol.
ACTION_BOUGHT = "BOUGHT"
ACTION_SOLD = "SOLD"
ACTION_SKIPPED = "SKIPPED"            # a candidate that qualified and was not bought
ACTION_NOT_ENTERED = "NOT_ENTERED"    # entries were blocked outright
ACTION_HELD = "HELD"                  # still open after the exit ran: a problem
ACTIONS = (
    ACTION_BOUGHT,
    ACTION_SOLD,
    ACTION_SKIPPED,
    ACTION_NOT_ENTERED,
    ACTION_HELD,
)

# What happened to an overnight holding.
EXIT_PENDING = "PENDING"
# Sold by the exit job, in the window the rule asks for.
EXIT_DONE = "EXITED"
# Sold, but not when it should have been -- the process was down at the open,
# or the order could not be placed until later. A DIFFERENT STATUS, not a
# footnote: section 10.1 is the whole reason, and a late exit that recorded
# itself as a normal one would quietly turn this strategy into a worse one
# while every report went on looking fine.
EXIT_LATE = "EXITED_LATE"
# The exit was attempted and refused or failed. Still open.
EXIT_FAILED = "FAILED"
EXIT_STATUSES = (EXIT_PENDING, EXIT_DONE, EXIT_LATE, EXIT_FAILED)


class BtstSession(TimestampedModel):
    """One run of the strategy, with everything it saw."""

    __tablename__ = "btst_sessions"

    strategy_key = Column(String(64), nullable=False, index=True)
    portfolio_id = Column(
        Integer, ForeignKey("portfolios.id"), nullable=False, index=True
    )

    # The SESSION being decided. For a scan that is today -- it reads today's
    # own forming session, which is the whole difference from the rotation. For
    # an exit it is the session the positions were OPENED in, so the two halves
    # of one trade share a date and can be read as a pair.
    session_date = Column(Date, nullable=False, index=True)
    run_kind = Column(String(16), nullable=False, index=True)
    status = Column(String(16), nullable=False, index=True)

    started_at = Column(PreciseDateTime, nullable=False, index=True)
    completed_at = Column(PreciseDateTime, nullable=True)

    # --- the regime gate's inputs (B9) ------------------------------------
    index_symbol = Column(String(32), nullable=True)
    index_close = Column(Money, nullable=True)
    index_sma = Column(Money, nullable=True)
    gate_on = Column(Boolean, nullable=True)

    # --- the funnel -------------------------------------------------------
    # Every stage as a number, because at roughly 0.54 signals a session the
    # normal outcome is that nothing qualifies, and "nothing qualified" has to
    # be distinguishable from "nothing was looked at". The per-filter counts
    # are in `filter_counts_json`; these are the ones every run has.
    universe_size = Column(Integer, nullable=True)
    tradable_count = Column(Integer, nullable=True)      # after the F&O policy
    symbols_with_bars = Column(Integer, nullable=True)   # enough history
    symbols_with_quotes = Column(Integer, nullable=True) # a live book to read
    candidate_count = Column(Integer, nullable=True)     # through every filter
    slots = Column(Integer, nullable=True)
    picked_count = Column(Integer, nullable=True)

    # Read whole, never queried into. A column per filter would be a migration
    # every time the specification gained one.
    filter_counts_json = Column(Text, nullable=True)
    ranking_json = Column(Text, nullable=True)
    parameters_json = Column(Text, nullable=True)

    # --- the policy this run was taken under ------------------------------
    regime_enforced = Column(Boolean, nullable=True)
    fno_excluded = Column(Boolean, nullable=True)

    # Why the run ended as it did. Always populated for SKIPPED and FAILED.
    message = Column(String(500), nullable=True)

    __table_args__ = (
        Index("ix_btst_sessions_lookup", "strategy_key", "session_date", "run_kind"),
    )

    def __repr__(self) -> str:
        return (
            f"<BtstSession({self.strategy_key} {self.session_date} "
            f"{self.run_kind} {self.status})>"
        )


class BtstDecision(TimestampedModel):
    """One decision about one symbol: taken, or deliberately not taken."""

    __tablename__ = "btst_decisions"

    session_id = Column(
        Integer, ForeignKey("btst_sessions.id"), nullable=False, index=True
    )
    symbol = Column(String(32), nullable=False, index=True)
    security_id = Column(String(32), nullable=True, index=True)
    action = Column(String(16), nullable=False, index=True)

    rank = Column(Integer, nullable=True)

    # --- WHAT THE LIVE BOOK SAID, at the moment of the decision -----------
    #
    # These are the columns `swing_sessions` has no room for and the reason
    # this module has its own journal. Every one of them is gone a second
    # later: a session's running high, low and cumulative volume exist in the
    # feed and nowhere else, and once the session closes they are only
    # recoverable as a finished bar, which is a different number.
    price = Column(Money, nullable=True)           # the price it was measured at
    session_high = Column(Money, nullable=True)    # hi_sofar
    session_low = Column(Money, nullable=True)     # lo_sofar
    session_volume = Column(Integer, nullable=True)  # vol_sofar, shares
    vol_ratio = Column(Money, nullable=True)       # B5: vol_sofar / advq
    clv = Column(Money, nullable=True)             # B6
    breakout_high = Column(Money, nullable=True)   # B4: hi55
    momentum = Column(Money, nullable=True)        # B8: mom126
    sma = Column(Money, nullable=True)             # B7
    turnover_20d = Column(Money, nullable=True)    # B2: adv20

    quantity = Column(Integer, nullable=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True, index=True)

    # System-written, and the whole point of the table:
    # "Bought: rank 1 of 3, 4.2x volume, CLV 0.91, 1.3% above its 55-day high",
    # "Skipped: slots full", "Not entered: the regime gate is OFF".
    reason = Column(String(500), nullable=False)

    # --- the regime, and the policy this decision was taken under ----------
    # Stamped on every row, action and non-action alike, so a performance
    # report can be split by the rules that were in force without a two-hop
    # join. Nullable means NOT RECORDED, which is neither false nor "enforced".
    regime_gate_on = Column(Boolean, nullable=True)
    regime_enforced = Column(Boolean, nullable=True)
    fno_excluded = Column(Boolean, nullable=True)
    # Whether THIS name has listed derivatives, stored per decision rather than
    # looked up later: F&O eligibility is derived from Dhan's master on every
    # refresh and moves, so a name's status today does not describe the day it
    # was traded.
    fno_eligible = Column(Boolean, nullable=True)

    __table_args__ = (
        Index("ix_btst_decisions_session_symbol", "session_id", "symbol"),
    )

    def __repr__(self) -> str:
        return f"<BtstDecision({self.symbol} {self.action}: {self.reason[:40]})>"


class BtstHolding(TimestampedModel):
    """One position held overnight, and whether it got out.

    **The one table here that is UPDATED rather than appended to**, and the
    exception is deliberate: this row IS the open question "did the exit run",
    and a question is answered by changing its answer, not by appending a
    second one. The append-only record of what happened is
    `btst_decisions`, which gets a SOLD row; this is the state the exit job and
    the alarm both read.

    Nothing in it is recomputable after the fact. The entry price is on the
    fill, but which SESSION a position belongs to, when its exit was due, and
    whether it was late are facts about this strategy's clock that no other
    table carries -- and `positions` cannot answer them, because a position
    that was exited late looks exactly like one that was exited on time.
    """

    __tablename__ = "btst_holdings"

    strategy_key = Column(String(64), nullable=False, index=True)
    portfolio_id = Column(
        Integer, ForeignKey("portfolios.id"), nullable=False, index=True
    )
    position_id = Column(
        Integer, ForeignKey("positions.id"), nullable=True, index=True
    )
    security_id = Column(String(32), nullable=False, index=True)
    symbol = Column(String(32), nullable=False, index=True)

    # --- the entry --------------------------------------------------------
    entry_session_date = Column(Date, nullable=False, index=True)
    entry_at = Column(PreciseDateTime, nullable=False)
    entry_order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    entry_price = Column(Money, nullable=True)
    quantity = Column(Integer, nullable=False)

    # The policy the position was OPENED under. A position keeps it: the same
    # rule the rotation applies through `swing_stops`, and for the same reason
    # -- re-enforcing a rule must not retrospectively re-describe a trade
    # taken while it was relaxed. A null reads as ENFORCED, so an unknown fails
    # towards the specification.
    entry_gate_on = Column(Boolean, nullable=True)
    entry_regime_enforced = Column(Boolean, nullable=True)

    # --- the exit ---------------------------------------------------------
    exit_status = Column(String(16), nullable=False, index=True)
    exit_order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    exit_price = Column(Money, nullable=True)
    exited_at = Column(PreciseDateTime, nullable=True)
    # How many minutes after the configured exit time it actually left. Stored
    # rather than derived, because the configured time is a runtime setting and
    # may have moved since.
    exit_delay_minutes = Column(Integer, nullable=True)
    exit_reason = Column(String(500), nullable=True)

    # (exit - entry) / entry, the thing the whole strategy is a bet on.
    # Computed once, at the exit, from the two prices actually paid -- not from
    # the bars, which would be the backtest's number rather than this book's.
    overnight_gap = Column(Money, nullable=True)

    __table_args__ = (
        Index(
            "ix_btst_holdings_open",
            "strategy_key", "portfolio_id", "exit_status",
        ),
    )

    @property
    def is_open(self) -> bool:
        return self.exit_status in (EXIT_PENDING, EXIT_FAILED)

    def __repr__(self) -> str:
        return (
            f"<BtstHolding({self.symbol} x{self.quantity} "
            f"{self.entry_session_date} {self.exit_status})>"
        )
