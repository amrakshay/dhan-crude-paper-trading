"""Open and closed paper positions."""
from sqlalchemy import Boolean, Column, Date, ForeignKey, Index, Integer, String

from src.database.base import Money, PreciseDateTime, TimestampedModel


class Position(TimestampedModel):
    """A position in one contract.

    `net_quantity` is signed: positive is long, negative is short. A position
    row is closed (is_open=False) once net_quantity reaches zero; opening the
    same contract again creates a new row, so the history of each round trip
    stays separable.
    """

    __tablename__ = "positions"

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
        # PER PORTFOLIO. The index used to be (security_id, is_open), matching a
        # lookup that found "the" open position for a contract. With portfolios
        # that lookup would net two books into one row: a buy in portfolio B
        # would average into portfolio A's position and a close in one would
        # close the other.
        Index("ix_positions_portfolio_security_open", "portfolio_id", "security_id", "is_open"),
    )
