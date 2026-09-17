"""Trades opened from the price chart, and their bracket levels.

A chart trade is a directional bet expressed on the FUTURE's chart but held as
an OPTION position: a Buy click buys the nearest ATM call, a Sell click the
nearest ATM put. This row is what ties the two together, so the chart can show
its own position and its own P&L without guessing which of the book's positions
came from it.

The stop-loss and take-profit levels are prices of the UNDERLYING FUTURE, not
of the option -- they are levels read off the chart the user is looking at.
bracket_monitor watches the future and closes the option position at market
when one is crossed. Both are nullable: a chart trade opens with no levels at
all and stays that way until lines are dragged onto the chart.
"""
from sqlalchemy import Column, Date, Index, Integer, String

from src.database.base import Money, PreciseDateTime, TimestampedModel


class ChartTrade(TimestampedModel):
    __tablename__ = "chart_trades"

    # Which strategy module this belongs to. STORED, not derived from the
    # instrument at read time: instrument rows are upserted and deactivated
    # across expiries, so a historical trade would lose the join the moment its
    # contract expired, and its P&L would silently leave every filtered view.
    strategy_key = Column(String(64), nullable=False, index=True)

    # The contract whose chart was traded (the near-month future).
    underlying_security_id = Column(String(32), nullable=False, index=True)
    underlying_symbol = Column(String(128), nullable=False)

    # The option actually bought.
    option_security_id = Column(String(32), nullable=False, index=True)
    option_symbol = Column(String(128), nullable=False)
    option_type = Column(String(2), nullable=False)          # CE | PE
    strike_price = Column(Money, nullable=False)
    expiry_date = Column(Date, nullable=True, index=True)
    lots = Column(Integer, nullable=False, default=1)

    # BUY (bought a call) or SELL (bought a put). Never a short option leg:
    # a Sell click buys a put, it does not write one.
    chart_side = Column(String(8), nullable=False)

    entry_order_id = Column(Integer, nullable=True, index=True)
    exit_order_id = Column(Integer, nullable=True)

    # Levels on the FUTURE, not the option. Null until dragged in.
    stop_loss_level = Column(Money, nullable=True)
    take_profit_level = Column(Money, nullable=True)

    status = Column(String(16), nullable=False, default="OPEN", index=True)
    exit_reason = Column(String(16), nullable=True)          # MANUAL | STOP_LOSS | TAKE_PROFIT
    triggered_level = Column(Money, nullable=True)
    underlying_at_entry = Column(Money, nullable=True)
    underlying_at_exit = Column(Money, nullable=True)

    opened_at = Column(PreciseDateTime, nullable=False)
    closed_at = Column(PreciseDateTime, nullable=True)

    __table_args__ = (
        Index("ix_chart_trades_underlying_status", "underlying_security_id", "status"),
    )
