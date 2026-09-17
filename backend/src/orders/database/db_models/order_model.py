"""Paper orders, their state transitions, their fills and their charges.

Nothing in this module -- or anywhere downstream of it -- reaches a broker.
An "order" here is a row in SQLite/MySQL that is matched against a snapshot of
the live book by src.orders.services.fill_simulator.
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
from sqlalchemy.orm import relationship

from src.database.base import Money, PreciseDateTime, TimestampedModel


class Order(TimestampedModel):
    __tablename__ = "orders"

    client_order_id = Column(String(36), nullable=False, unique=True, index=True)

    # Which strategy module this belongs to. STORED, not derived from the
    # instrument at read time: instrument rows are upserted and deactivated
    # across expiries, so a historical trade would lose the join the moment its
    # contract expired, and its P&L would silently leave every filtered view.
    strategy_key = Column(String(64), nullable=False, index=True)

    # Which portfolio's money this used. Every trade belongs to exactly one,
    # and two portfolios holding the same contract are two separate books --
    # which is enforced by the LOOKUPS being keyed on (portfolio_id,
    # security_id), not just by this column existing.
    portfolio_id = Column(
        Integer, ForeignKey("portfolios.id"), nullable=False, index=True
    )

    # Contract snapshot, denormalised so history survives an instrument-master
    # refresh that drops the expired security_id.
    security_id = Column(String(32), nullable=False, index=True)
    trading_symbol = Column(String(128), nullable=False)
    expiry_date = Column(Date, nullable=True, index=True)
    strike_price = Column(Money, nullable=True, index=True)
    option_type = Column(String(2), nullable=True)
    lot_size = Column(Integer, nullable=False)

    side = Column(String(4), nullable=False, index=True)        # BUY / SELL
    order_type = Column(String(8), nullable=False)              # MARKET / LIMIT
    lots = Column(Integer, nullable=False)
    quantity = Column(Integer, nullable=False)                  # lots * lot_size
    limit_price = Column(Money, nullable=True)

    status = Column(String(24), nullable=False, index=True)
    filled_quantity = Column(Integer, nullable=False, default=0)
    average_fill_price = Column(Money, nullable=True)
    rejection_reason = Column(String(255), nullable=True)

    # Set to true when this order was created to close an existing position.
    is_close_order = Column(Boolean, nullable=False, default=False)

    placed_at = Column(PreciseDateTime, nullable=False, index=True)
    last_event_at = Column(PreciseDateTime, nullable=False)
    completed_at = Column(PreciseDateTime, nullable=True)

    events = relationship(
        "OrderEvent",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderEvent.event_at",
        lazy="selectin",
    )
    fills = relationship(
        "OrderFill",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderFill.fill_at",
        lazy="selectin",
    )
    charges = relationship(
        "OrderCharge",
        back_populates="order",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_orders_status_placed", "status", "placed_at"),
        Index("ix_orders_security_placed", "security_id", "placed_at"),
        Index("ix_orders_strategy_placed", "strategy_key", "placed_at"),
        Index("ix_orders_portfolio_placed", "portfolio_id", "placed_at"),
    )


class OrderEvent(TimestampedModel):
    """Every state transition, timestamped to the millisecond."""

    __tablename__ = "order_events"

    order_id = Column(
        Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type = Column(String(24), nullable=False)   # PLACED / FILL / CANCEL / ...
    status = Column(String(24), nullable=False)       # resulting order status
    event_at = Column(PreciseDateTime, nullable=False, index=True)
    price = Column(Money, nullable=True)
    quantity = Column(Integer, nullable=True)
    message = Column(String(255), nullable=True)

    order = relationship("Order", back_populates="events")


class OrderFill(TimestampedModel):
    """An individual execution. One order may produce several."""

    __tablename__ = "order_fills"

    order_id = Column(
        Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fill_at = Column(PreciseDateTime, nullable=False, index=True)
    price = Column(Money, nullable=False)
    quantity = Column(Integer, nullable=False)
    # Which depth level supplied the liquidity (1-5), and the modelled slippage
    # in ticks relative to the touch. Kept so fills can be audited later.
    book_level = Column(Integer, nullable=True)
    slippage_ticks = Column(Integer, nullable=False, default=0)
    reference_price = Column(Money, nullable=True)   # touch price before slippage

    order = relationship("Order", back_populates="fills")


class OrderCharge(TimestampedModel):
    """Full charges breakdown for one order, as computed at fill time."""

    __tablename__ = "order_charges"

    order_id = Column(
        Integer,
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    # turnover, total_charges and rates_version are real columns because they
    # are queried and aggregated. The individual taxes are NOT: which taxes
    # exist is a property of the rate card an order was charged under (MCX pays
    # CTT, an equity card would pay STT), and a column per tax is what would
    # make adding one a schema migration.
    turnover = Column(Money, nullable=False, default=0)
    total_charges = Column(Money, nullable=False, default=0)
    # Which rate-card version produced these numbers, so an old order can still
    # be explained after rates are updated in conf/charges/<card>.yaml.
    rates_version = Column(String(32), nullable=True)
    # The line items: [{name, label, amount, rawAmount, rate, base, formula,
    # note}]. Read back through src/charges/services/charge_persistence.py.
    breakdown_json = Column(Text, nullable=True)

    order = relationship("Order", back_populates="charges")
