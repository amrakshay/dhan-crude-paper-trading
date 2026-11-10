"""NSE VCP Swing: what was seen, what was decided, and what is held.

Five tables, the module's own, for the same reason BTST has its own: the
rotation's journal is a breadth-ramped ranking's record, and this strategy has
no ranking and no breadth. It has BASES -- a footprint, a pivot, a stop, a
contraction count -- and positions that change phase when they break out.
Nothing existing is altered, so the two armed strategies are untouched.

`vcp_sessions`   one row per run (NIGHTLY scan, MORNING orders), with the
                 counts it saw. Append-only, and the idempotence key.
`vcp_signals`    every base the detector ACCEPTED, tradable or not. This is the
                 de-duplication memory as well as the journal: a later reading
                 of a base already here is the same trade, not a new one.
                 Append-only except `status`, which moves CANDIDATE -> TAKEN /
                 SKIPPED once the morning run has acted on it.
`vcp_decisions`  one row per action and per deliberate non-action, per
                 portfolio, so "why was X not bought" has an answer.
`vcp_positions`  the stop and the phase of every position. UPDATED, because it
                 is live state (has it broken out, is an exit due), the same
                 exception `btst_holdings` and `swing_stops` make.
`vcp_scan_state` how far each symbol has been scanned, so a missed night is
                 caught up rather than skipped -- and so the first run replays
                 recent history instead of mistaking every base already in
                 progress for a new one.
"""
from sqlalchemy import (
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from src.database.base import Money, PreciseDateTime, TimestampedModel

RUN_NIGHTLY = "NIGHTLY"   # after the bars: scan, flag exits, journal
RUN_MORNING = "MORNING"   # at the open: sell what is flagged, buy candidates
RUN_KINDS = (RUN_NIGHTLY, RUN_MORNING)

STATUS_RUNNING = "RUNNING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
STATUS_SKIPPED = "SKIPPED"

# vcp_signals.status
SIGNAL_CANDIDATE = "CANDIDATE"    # passed every gate; the morning run may buy it
SIGNAL_REJECTED = "REJECTED"      # a gate refused it (2T, stop too tight, ...)
SIGNAL_REPLAYED = "REPLAYED"      # found while catching up on old bars: memory only
SIGNAL_IGNORED = "IGNORED"        # accepted, but already through the pivot or under
                                  # the stop: memory only, as in the backtest
SIGNAL_TAKEN = "TAKEN"            # bought in at least one portfolio
SIGNAL_NOT_TAKEN = "NOT_TAKEN"    # the morning run looked and bought it nowhere
SIGNAL_EXPIRED = "EXPIRED"        # never acted on before a newer session arrived

# vcp_decisions.action
ACTION_BOUGHT = "BOUGHT"
ACTION_SOLD = "SOLD"
ACTION_SKIPPED = "SKIPPED"
ACTION_FLAGGED = "FLAGGED"        # nightly: an exit is due at the next open
ACTION_HELD = "HELD"              # an exit that could not be completed

# vcp_positions.phase
PHASE_BASE = "BASE"               # stop = the final contraction's low
PHASE_BROKEN_OUT = "BROKEN_OUT"   # the 50 EMA trail is armed, the low is the floor

# vcp_positions.status
POSITION_OPEN = "OPEN"
POSITION_EXITED = "EXITED"
POSITION_NOT_HELD = "NOT_HELD"    # nothing left in the book to sell

# vcp_positions.exit_kind / exit_due
EXIT_STOP = "STOP"
EXIT_TRAIL = "TRAIL"
EXIT_CAP = "CAP"


class VcpSession(TimestampedModel):
    __tablename__ = "vcp_sessions"

    strategy_key = Column(String(64), nullable=False, index=True)
    # NULL for the NIGHTLY scan, which is a fact about the market rather than
    # about one book; set for a MORNING run, which trades one portfolio.
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=True, index=True)
    session_date = Column(Date, nullable=False, index=True)
    run_kind = Column(String(16), nullable=False, index=True)
    status = Column(String(16), nullable=False, index=True)
    started_at = Column(PreciseDateTime, nullable=False, index=True)
    completed_at = Column(PreciseDateTime, nullable=True)

    universe_size = Column(Integer, nullable=True)
    scanned_count = Column(Integer, nullable=True)     # symbols with enough bars
    detections_count = Column(Integer, nullable=True)  # accepted bases this session
    candidate_count = Column(Integer, nullable=True)
    bought_count = Column(Integer, nullable=True)
    sold_count = Column(Integer, nullable=True)
    flagged_count = Column(Integer, nullable=True)
    parameters_json = Column(Text, nullable=True)
    message = Column(String(500), nullable=True)

    __table_args__ = (
        Index("ix_vcp_sessions_lookup", "strategy_key", "session_date", "run_kind"),
    )


class VcpSignal(TimestampedModel):
    __tablename__ = "vcp_signals"

    strategy_key = Column(String(64), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("vcp_sessions.id"), nullable=True, index=True)
    symbol = Column(String(32), nullable=False, index=True)
    security_id = Column(String(32), nullable=True)
    seen_date = Column(Date, nullable=False, index=True)      # the bar it was visible on
    base_start_date = Column(Date, nullable=False)
    pivot_price = Column(Money, nullable=False)
    stop_price = Column(Money, nullable=False)
    close_price = Column(Money, nullable=False)               # on seen_date
    atr = Column(Money, nullable=True)
    stop_atr = Column(Money, nullable=True)                   # (close - stop) / ATR
    contractions = Column(Integer, nullable=True)
    variant = Column(String(32), nullable=True)               # "10W 12/5 3T"
    score = Column(Money, nullable=True)
    trend_template = Column(Integer, nullable=True)
    status = Column(String(16), nullable=False, index=True)
    reason = Column(String(500), nullable=False)

    __table_args__ = (
        Index("ix_vcp_signals_symbol_seen", "strategy_key", "symbol", "seen_date"),
    )


class VcpDecision(TimestampedModel):
    __tablename__ = "vcp_decisions"

    session_id = Column(Integer, ForeignKey("vcp_sessions.id"), nullable=False, index=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=True, index=True)
    signal_id = Column(Integer, ForeignKey("vcp_signals.id"), nullable=True, index=True)
    vcp_position_id = Column(Integer, ForeignKey("vcp_positions.id"), nullable=True, index=True)
    symbol = Column(String(32), nullable=False, index=True)
    security_id = Column(String(32), nullable=True)
    action = Column(String(16), nullable=False, index=True)
    quantity = Column(Integer, nullable=True)
    price = Column(Money, nullable=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True, index=True)
    reason = Column(String(500), nullable=False)


class VcpPosition(TimestampedModel):
    __tablename__ = "vcp_positions"

    strategy_key = Column(String(64), nullable=False, index=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=False, index=True)
    signal_id = Column(Integer, ForeignKey("vcp_signals.id"), nullable=True, index=True)
    symbol = Column(String(32), nullable=False, index=True)
    security_id = Column(String(32), nullable=False, index=True)

    signal_date = Column(Date, nullable=False)                # the base's seen_date
    entry_date = Column(Date, nullable=False, index=True)     # the session it was bought in
    entry_at = Column(PreciseDateTime, nullable=False)
    entry_order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    entry_price = Column(Money, nullable=True)
    quantity = Column(Integer, nullable=False)

    stop_price = Column(Money, nullable=False)                # never moves
    pivot_price = Column(Money, nullable=False)
    phase = Column(String(16), nullable=False, index=True)
    breakout_date = Column(Date, nullable=True)
    last_close = Column(Money, nullable=True)
    last_ema = Column(Money, nullable=True)
    last_bar_date = Column(Date, nullable=True)
    sessions_held = Column(Integer, nullable=True)

    exit_due = Column(String(16), nullable=True)              # TRAIL | CAP, sold at the next open
    exit_due_since = Column(Date, nullable=True)
    stop_triggered_at = Column(PreciseDateTime, nullable=True)

    status = Column(String(16), nullable=False, index=True)
    exit_kind = Column(String(16), nullable=True)
    exit_order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    exit_price = Column(Money, nullable=True)
    exited_at = Column(PreciseDateTime, nullable=True)
    exit_reason = Column(String(500), nullable=True)

    __table_args__ = (
        Index("ix_vcp_positions_open", "strategy_key", "portfolio_id", "status"),
    )


class VcpScanState(TimestampedModel):
    __tablename__ = "vcp_scan_state"

    strategy_key = Column(String(64), nullable=False)
    symbol = Column(String(32), nullable=False)
    scanned_through = Column(Date, nullable=False)

    __table_args__ = (
        UniqueConstraint("strategy_key", "symbol", name="uq_vcp_scan_state_symbol"),
    )
