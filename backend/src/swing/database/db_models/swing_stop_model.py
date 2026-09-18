"""The chandelier trailing stop, and the state it needs to survive a restart.

P14 sets a stop at `entry - 3.5 x ATR14` on the day of entry. P15 raises it on
every daily close to `max(stop, highest_close_since_entry - 3.5 x ATR14_today)`
and **never lowers it**. Specification section 14.4 lists exactly what has to be
persisted for that to survive a process restart: the position, the ATR at
entry, the highest close since entry, and the current stop. This table is that
list.

**Why a table rather than recomputing from bars.** The highest close since
entry is a function of the entry DATE, and the stop is a running maximum -- both
are path-dependent. Recomputing them would give the right answer only while
`daily_bars` still holds every session since entry and none of them has been
restated. A restatement after a corporate action would silently move a stop
that has already been acted on, which is the one number in this strategy that
must never move backwards.

**Why the stop is not on `positions`.** A position is generic: every strategy
in this application has them and none of the others has a chandelier stop. The
trail is a property of the RULE, not of owning shares, and the rule is what
this package is.

One ACTIVE row per (portfolio, security). The rotation holds a name or it does
not; there is no partial position to stop out, and `SwingStopRepository`
enforces the single active row rather than leaving it to a convention.
"""
from sqlalchemy import (
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
)

from src.database.base import Money, PreciseDateTime, TimestampedModel

# What this stop is doing now.
STOP_ACTIVE = "ACTIVE"          # watching, and may fire
# Fired, and an exit order was placed. It stays in this state rather than going
# straight to CLOSED because the exit is an ordinary paper order that can
# partially fill, and the row is the record of what fired and at what price.
STOP_TRIGGERED = "TRIGGERED"
# The position left for some other reason -- a rotation exit, a regime exit, an
# operator closing it by hand. The stop never fired and the row says so.
STOP_CLOSED = "CLOSED"
STOP_STATUSES = (STOP_ACTIVE, STOP_TRIGGERED, STOP_CLOSED)

# Why the row stopped being ACTIVE, for the exit mix in the performance report
# (specification section 9.1: 348 trail stops against 324 rotations).
EXIT_TRAIL = "TRAIL_STOP"
EXIT_ROTATION = "ROTATION"
EXIT_REGIME = "REGIME"
EXIT_MANUAL = "MANUAL"


class SwingStop(TimestampedModel):
    """One holding's chandelier stop, with everything it was computed from."""

    __tablename__ = "swing_stops"

    strategy_key = Column(String(64), nullable=False, index=True)
    portfolio_id = Column(
        Integer, ForeignKey("portfolios.id"), nullable=False, index=True
    )

    symbol = Column(String(32), nullable=False, index=True)
    security_id = Column(String(32), nullable=False, index=True)

    # The order that opened the position, so a stop can be traced to its entry.
    entry_order_id = Column(
        Integer, ForeignKey("orders.id"), nullable=True, index=True
    )
    # The SESSION of entry -- the regime index's own bar date -- not the
    # wall-clock date. "Highest close since entry" counts from here.
    entry_session = Column(Date, nullable=False)
    entry_price = Column(Money, nullable=False)
    quantity = Column(Integer, nullable=False)

    # P14's inputs, kept so the initial stop can be checked rather than
    # believed: entry - atr_multiple x entry_atr.
    entry_atr = Column(Money, nullable=False)
    atr_multiple = Column(Money, nullable=False)

    # P15's running state. `highest_close` only ever rises; `stop_price` only
    # ever rises. `last_ratcheted_session` makes the nightly ratchet idempotent
    # -- a restart at 18:20 must not re-apply 18:15's work.
    highest_close = Column(Money, nullable=False)
    stop_price = Column(Money, nullable=False)
    last_atr = Column(Money, nullable=True)
    last_ratcheted_session = Column(Date, nullable=True)

    status = Column(String(16), nullable=False, default=STOP_ACTIVE, index=True)
    # TRAIL_STOP / ROTATION / REGIME / MANUAL. Null while ACTIVE.
    exit_kind = Column(String(16), nullable=True, index=True)
    triggered_at = Column(PreciseDateTime, nullable=True)
    # The price that crossed the stop, which is not the stop and not the fill.
    trigger_price = Column(Money, nullable=True)
    exit_order_id = Column(
        Integer, ForeignKey("orders.id"), nullable=True, index=True
    )
    closed_at = Column(PreciseDateTime, nullable=True)
    note = Column(String(500), nullable=True)

    __table_args__ = (
        # The monitor's read: every active stop, and one holding's stop.
        Index("ix_swing_stops_active", "strategy_key", "status"),
        Index(
            "ix_swing_stops_portfolio_security",
            "portfolio_id",
            "security_id",
            "status",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<SwingStop({self.symbol} {self.status} stop={self.stop_price} "
            f"high={self.highest_close})>"
        )
