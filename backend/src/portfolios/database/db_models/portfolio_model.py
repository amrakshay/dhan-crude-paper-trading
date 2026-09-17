"""Portfolios, the strategies attached to them, and their cash ledger.

A portfolio behaves like one demat account: it holds money, you deposit into it
and withdraw from it, it is attached to one or many strategies, and every
order, position and chart trade belongs to exactly one of them. The same
strategy may run in several portfolios at once and their books must not mix --
which is a property of the LOOKUPS as much as of these tables, see
`PositionRepository.get_open_for_security`.

**The balance is not here.** There is no `balance` column and there must never
be one. Cash is derived by replaying `cash_ledger`, the same way realised P&L
is derived by replaying fills (backend/CLAUDE.md section 6) and for the same
reason: a stored total drifts from the entries that produced it, cannot be
sliced by day or by strategy, and gives two answers to one question.

**The ledger is append-only.** A correction is a new entry, never an edit and
never a delete -- the same discipline as order events, where every transition
is a row rather than a mutation.

**Margin is not a ledger entry.** What a short position ties up is derived from
the open position itself, so there is no block/release bookkeeping that can get
out of step with the position it is meant to describe.
"""
from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)

from src.database.base import Money, PreciseDateTime, TimestampedModel

STATUS_ACTIVE = "ACTIVE"
STATUS_ARCHIVED = "ARCHIVED"

# Ledger entry types. Signed amounts: positive is money in, negative out.
ENTRY_DEPOSIT = "DEPOSIT"
ENTRY_WITHDRAWAL = "WITHDRAWAL"
ENTRY_TRADE_DEBIT = "TRADE_DEBIT"
ENTRY_TRADE_CREDIT = "TRADE_CREDIT"
ENTRY_CHARGES = "CHARGES"

ENTRY_TYPES = (
    ENTRY_DEPOSIT,
    ENTRY_WITHDRAWAL,
    ENTRY_TRADE_DEBIT,
    ENTRY_TRADE_CREDIT,
    ENTRY_CHARGES,
)


class Portfolio(TimestampedModel):
    """One book of paper money."""

    __tablename__ = "portfolios"

    name = Column(String(80), nullable=False, unique=True, index=True)
    description = Column(String(255), nullable=True)
    # ACTIVE | ARCHIVED. A portfolio with history is archived, never deleted,
    # so P&L can never silently change under a report that used to include it.
    status = Column(String(16), nullable=False, default=STATUS_ACTIVE, index=True)


class PortfolioStrategy(TimestampedModel):
    """Which strategies a portfolio may trade.

    Many-to-many on purpose: a portfolio can run several strategies, and one
    strategy can run in several portfolios at once.
    """

    __tablename__ = "portfolio_strategies"

    portfolio_id = Column(
        Integer,
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    strategy_key = Column(String(64), nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint("portfolio_id", "strategy_key", name="uq_portfolio_strategy"),
    )


class CashLedgerEntry(TimestampedModel):
    """One movement of money. Append-only; the balance is their sum."""

    __tablename__ = "cash_ledger"

    portfolio_id = Column(
        Integer,
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entry_type = Column(String(24), nullable=False, index=True)
    # SIGNED: positive is money into the portfolio, negative is money out.
    # Storing the sign rather than a direction flag means the balance is a SUM
    # and cannot be got wrong by forgetting to negate one entry type.
    amount = Column(Money, nullable=False, default=0)
    # The order that caused this entry, for trade and charge entries. Null for
    # deposits and withdrawals, which have no order behind them.
    order_id = Column(
        Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True
    )
    note = Column(String(255), nullable=True)
    created_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    entry_at = Column(PreciseDateTime, nullable=False, index=True)

    __table_args__ = (
        Index("ix_cash_ledger_portfolio_at", "portfolio_id", "entry_at"),
    )
