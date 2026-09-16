"""Instrument master rows, sourced from Dhan's detailed scrip master CSV."""
from sqlalchemy import Boolean, Column, Date, Index, Integer, String

from src.database.base import Money, PreciseDateTime, TimestampedModel


class Instrument(TimestampedModel):
    """One tradable contract: a CRUDEOIL future or one option strike.

    `security_id` is Dhan's identifier and is the join key used everywhere else
    in this application. It is NOT stable across expiries -- the master is
    re-fetched at runtime and rows are upserted on every refresh.
    """

    __tablename__ = "instruments"

    security_id = Column(String(32), nullable=False, unique=True, index=True)
    exchange_id = Column(String(8), nullable=False)             # MCX
    exchange_segment = Column(String(16), nullable=False)       # MCX_COMM
    segment_code = Column(Integer, nullable=False)              # 5 for MCX_COMM
    instrument_type = Column(String(16), nullable=False, index=True)  # OPTFUT / FUTCOM
    underlying_symbol = Column(String(32), nullable=False, index=True)
    underlying_scrip = Column(Integer, nullable=True)

    trading_symbol = Column(String(128), nullable=False)
    display_name = Column(String(160), nullable=True)

    expiry_date = Column(Date, nullable=True, index=True)
    strike_price = Column(Money, nullable=True)
    option_type = Column(String(2), nullable=True)              # CE / PE / NULL

    lot_size = Column(Integer, nullable=False, default=1)
    tick_size = Column(Money, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True, index=True)
    refreshed_at = Column(PreciseDateTime, nullable=True)

    __table_args__ = (
        Index("ix_instruments_chain", "underlying_symbol", "expiry_date", "strike_price"),
        Index("ix_instruments_type_expiry", "instrument_type", "expiry_date"),
    )

    def __repr__(self) -> str:
        return (
            f"<Instrument(security_id={self.security_id}, "
            f"symbol={self.trading_symbol}, expiry={self.expiry_date}, "
            f"strike={self.strike_price}, type={self.option_type})>"
        )
