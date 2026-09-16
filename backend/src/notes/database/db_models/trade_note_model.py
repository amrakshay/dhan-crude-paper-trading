"""Free-text trade notes.

A first-class entity, not a column on orders: notes are written after a trade
completes, edited later, and surfaced from both order history and the P&L view.
"""
from sqlalchemy import Column, ForeignKey, Index, Integer, String, Text

from src.database.base import PreciseDateTime, TimestampedModel


class TradeNote(TimestampedModel):
    __tablename__ = "trade_notes"

    # A note hangs off an order, a position, or just a contract. At least one
    # of these is set; the service layer enforces that.
    order_id = Column(
        Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=True, index=True
    )
    position_id = Column(
        Integer, ForeignKey("positions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    security_id = Column(String(32), nullable=True, index=True)
    trading_symbol = Column(String(128), nullable=True)

    note_text = Column(Text, nullable=False)
    noted_at = Column(PreciseDateTime, nullable=False, index=True)
    edited_at = Column(PreciseDateTime, nullable=True)

    __table_args__ = (
        Index("ix_trade_notes_security_noted", "security_id", "noted_at"),
    )
