"""The decision journal.

Order events already record what happened to an order. This records what the
STRATEGY decided, and why -- including the decisions that produced no trade,
which are most of them and are the ones nothing else would preserve.

Two tables, both **append-only**, the same way `order_events` and `cash_ledger`
are. A record is never edited. If the system reconsiders, that is a new record.

`swing_sessions` is one row per run, carrying the INPUTS the decision was made
from rather than only its conclusion. "Gate OFF" is useless in six months;
"NIFTY 23,270.6 against SMA200 24,501.7, needs +5.3%, breadth 215/448 = 48.0%,
4 slots" can be audited against the data that produced it.

`swing_decisions` is one row per action AND per non-action, so "why was X not
bought" has an answer that does not require recomputing anything from bars that
may since have been restated.
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


# What kind of run produced this record.
RUN_NIGHTLY = "NIGHTLY"          # after the close: recompute, update stops, record
RUN_REBALANCE = "REBALANCE"      # at the open: emit and execute the lists
RUN_MANUAL = "MANUAL"            # an operator asked for a recomputation
RUN_KINDS = (RUN_NIGHTLY, RUN_REBALANCE, RUN_MANUAL)

# How the run ended.
STATUS_RUNNING = "RUNNING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
# The market was shut, so there was nothing to decide. Distinct from COMPLETED
# because a run that decided nothing and a run that did not happen are
# different facts, and the missed-run detector reads them differently.
STATUS_SKIPPED = "SKIPPED"

# What was decided about one symbol.
ACTION_BOUGHT = "BOUGHT"
ACTION_SOLD = "SOLD"
ACTION_HELD = "HELD"
ACTION_SKIPPED = "SKIPPED"            # a candidate that was not bought
ACTION_NOT_ENTERED = "NOT_ENTERED"    # entries were blocked outright
ACTION_STOP_MOVED = "STOP_MOVED"
ACTIONS = (
    ACTION_BOUGHT,
    ACTION_SOLD,
    ACTION_HELD,
    ACTION_SKIPPED,
    ACTION_NOT_ENTERED,
    ACTION_STOP_MOVED,
)


class SwingSession(TimestampedModel):
    """One run of the strategy, with everything it saw."""

    __tablename__ = "swing_sessions"

    strategy_key = Column(String(64), nullable=False, index=True)
    portfolio_id = Column(
        Integer, ForeignKey("portfolios.id"), nullable=False, index=True
    )

    # The SESSION being decided -- the regime index's own bar date, which is
    # this application's trading calendar. Not the wall-clock date the job ran:
    # a job that runs at 18:15 on a holiday decides nothing about that day.
    session_date = Column(Date, nullable=False, index=True)
    run_kind = Column(String(16), nullable=False, index=True)
    status = Column(String(16), nullable=False, index=True)

    started_at = Column(PreciseDateTime, nullable=False, index=True)
    completed_at = Column(PreciseDateTime, nullable=True)

    # --- the regime gate's inputs (P8, P9) --------------------------------
    index_symbol = Column(String(32), nullable=True)
    index_close = Column(Money, nullable=True)
    index_sma = Column(Money, nullable=True)
    gate_on = Column(Boolean, nullable=True)
    index_return_over_window = Column(Money, nullable=True)
    entries_allowed = Column(Boolean, nullable=True)

    # --- breadth and slots (P10, P11) -------------------------------------
    # Numerator and denominator, not just the fraction: 215/448 can be checked
    # and 0.48 cannot.
    breadth_above = Column(Integer, nullable=True)
    breadth_liquid = Column(Integer, nullable=True)
    slots = Column(Integer, nullable=True)

    # --- how many names each filter left ----------------------------------
    universe_size = Column(Integer, nullable=True)
    symbols_with_bars = Column(Integer, nullable=True)
    candidate_count = Column(Integer, nullable=True)

    # The ranking as it stood, and the configuration in force. JSON because
    # both are read whole and never queried into; a column per parameter would
    # be a migration every time the specification gains one.
    ranking_json = Column(Text, nullable=True)
    parameters_json = Column(Text, nullable=True)
    filter_counts_json = Column(Text, nullable=True)

    # Why the run ended as it did. Always populated for SKIPPED and FAILED.
    message = Column(String(500), nullable=True)

    __table_args__ = (
        Index("ix_swing_sessions_lookup", "strategy_key", "session_date", "run_kind"),
    )

    def __repr__(self) -> str:
        return (
            f"<SwingSession({self.strategy_key} {self.session_date} "
            f"{self.run_kind} {self.status})>"
        )


class SwingDecision(TimestampedModel):
    """One decision about one symbol: taken, or deliberately not taken."""

    __tablename__ = "swing_decisions"

    session_id = Column(
        Integer, ForeignKey("swing_sessions.id"), nullable=False, index=True
    )
    symbol = Column(String(32), nullable=False, index=True)
    action = Column(String(16), nullable=False, index=True)

    # The rank that justified it. A rotation exit is "rank 19 > 15", and an
    # operator has to be able to see the 19.
    rank = Column(Integer, nullable=True)
    score = Column(Money, nullable=True)

    quantity = Column(Integer, nullable=True)
    reference_price = Column(Money, nullable=True)

    # The order this decision produced, where it produced one. Nullable
    # because most decisions are non-actions.
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True, index=True)

    # System-written, and the whole point of the table:
    # "Rotation: rank 19 > 15", "Skipped: insufficient cash",
    # "Not entered: the 63-day filter blocks new entries".
    reason = Column(String(500), nullable=False)

    # --- the regime, and the policy this decision was taken under ----------
    #
    # `orders -> swing_decisions -> swing_sessions` already answers "was the
    # gate off when this trade was opened?", and the journal is append-only so
    # that join cannot go stale. These columns are not there because the data
    # was missing -- they are there because `swing_decisions` is the row that is
    # ALWAYS written, for every action and every non-action, which makes it the
    # reliable carrier, and because a two-hop join is not something anybody
    # writes by hand when they want to split a performance report in two.
    #
    # Nullable throughout: every row written before 2026-09-18 has no policy
    # recorded, and a NULL saying "not recorded" is the honest answer. It is not
    # false and it is not "enforced".
    regime_gate_on = Column(Boolean, nullable=True)
    regime_index_close = Column(Money, nullable=True)
    regime_index_sma = Column(Money, nullable=True)
    regime_index_return = Column(Money, nullable=True)
    # Whether P8/P17 and P9 were being ENFORCED when this was decided -- not
    # what they are. See src/swing/services/gate_policy.py.
    regime_enforced = Column(Boolean, nullable=True)
    entry_return_enforced = Column(Boolean, nullable=True)
    # baseline | v3b-off-gate
    gate_variant = Column(String(16), nullable=True)

    __table_args__ = (
        Index("ix_swing_decisions_session_symbol", "session_id", "symbol"),
    )

    def __repr__(self) -> str:
        return f"<SwingDecision({self.symbol} {self.action}: {self.reason[:40]})>"
