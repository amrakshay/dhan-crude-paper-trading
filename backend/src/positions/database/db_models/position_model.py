"""Open and closed paper positions."""
from sqlalchemy import Boolean, Column, Date, Index, Integer, String

from src.database.base import Money, PreciseDateTime, TimestampedModel


class Position(TimestampedModel):
    """A position in one contract.

    `net_quantity` is signed: positive is long, negative is short. A position
    row is closed (is_open=False) once net_quantity reaches zero; opening the
    same contract again creates a new row, so the history of each round trip
    stays separable.
    """

    __tablename__ = "positions"

    security_id = Column(String(32), nullable=False, index=True)
    trading_symbol = Column(String(128), nullable=False)
    expiry_date = Column(Date, nullable=True, index=True)
    strike_price = Column(Money, nullable=True, index=True)
    option_type = Column(String(2), nullable=True)
    lot_size = Column(Integer, nullable=False)

    net_quantity = Column(Integer, nullable=False, default=0)
    average_price = Column(Money, nullable=False, default=0)

    realized_pnl = Column(Money, nullable=False, default=0)      # gross of charges
    total_charges = Column(Money, nullable=False, default=0)
    # Running totals used to compute average price and realised P&L correctly
    # across partial closes and reversals.
    buy_quantity = Column(Integer, nullable=False, default=0)
    sell_quantity = Column(Integer, nullable=False, default=0)
    buy_value = Column(Money, nullable=False, default=0)
    sell_value = Column(Money, nullable=False, default=0)

    is_open = Column(Boolean, nullable=False, default=True, index=True)
    opened_at = Column(PreciseDateTime, nullable=False)
    closed_at = Column(PreciseDateTime, nullable=True)

    __table_args__ = (
        Index("ix_positions_security_open", "security_id", "is_open"),
    )
