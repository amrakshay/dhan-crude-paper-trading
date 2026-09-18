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
    Boolean,
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
    """One holding's chandelier stop, with everything it was computed from.

    Since 2026-09-18 this is written for EVERY position the rotation opens,
    including one that has no stop yet, which makes it the rotation's
    per-position record as well as its stop record. `stop_price` is null in
    that case; the table's name is unchanged and its meaning is documented
    here rather than renamed, because the rows already written under the old
    meaning are the same rows.
    """

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
    #
    # `entry_atr` is nullable because a row is now written for EVERY position,
    # including one entered on a session where ATR14 could not be computed. A
    # null here and a null `stop_price` are the same fact seen twice: this
    # position has no stop yet, and the next nightly ratchet will set one.
    entry_atr = Column(Money, nullable=True)
    atr_multiple = Column(Money, nullable=False)

    # --- what the regime was doing when this position was opened -----------
    #
    # `entry_regime_enforced` is load-bearing, not reporting: `plan_sells`
    # reads it per position to decide whether P17's liquidation reaches it. A
    # position opened while the regime gate was being OBSERVED keeps that
    # policy -- turning enforcement back on stops new entries, it does not sell
    # a book opened under the other rule (it leaves by rotation or by its
    # trailing stop instead).
    #
    # `entry_gate_on` is the gate's own boolean at entry, for reading the
    # numbers back afterwards. They are different questions: the gate can be ON
    # while enforcement is off, and both are worth knowing.
    #
    # Nullable because rows written before 2026-09-18 recorded neither. A null
    # `entry_regime_enforced` is treated as ENFORCED by the planner -- the
    # specification's behaviour -- so an unknown fails towards liquidation
    # rather than towards an exemption nothing can justify.
    entry_gate_on = Column(Boolean, nullable=True)
    entry_regime_enforced = Column(Boolean, nullable=True)

    # P15's running state. `highest_close` only ever rises; `stop_price` only
    # ever rises. `last_ratcheted_session` makes the nightly ratchet idempotent
    # -- a restart at 18:20 must not re-apply 18:15's work.
    highest_close = Column(Money, nullable=False)
    # NULL means "this position has no stop yet", which is a real and visible
    # state rather than an absent row: the ATR was unavailable on the session of
    # entry, so P14 could not be applied, and `ratchet_one` sets the first stop
    # as soon as one can be computed. The monitor SKIPS a null stop rather than
    # comparing against it.
    #
    # The table was widened rather than renamed on 2026-09-18. It was already
    # very nearly the rotation's per-position record -- entry session, entry
    # price, quantity, entry order -- and the alternative, resolving the entry
    # policy from `swing_decisions` at read time, depends on every open position
    # having a BOUGHT row, which is true today and one manual trade away from
    # not being.
    stop_price = Column(Money, nullable=True)
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
