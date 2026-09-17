"""Portfolio, attachment and ledger persistence.

Repositories own the queries (backend/CLAUDE.md section 1), including the two
that decide whether a portfolio may be deleted and what its cash is -- both of
which are answered from the data rather than from a name or a stored total.
"""
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.chart_trading.database.db_models.chart_trade_model import ChartTrade
from src.core.base_repository import BaseRepository
from src.orders.database.db_models.order_model import Order
from src.portfolios.database.db_models.portfolio_model import (
    STATUS_ACTIVE,
    CashLedgerEntry,
    Portfolio,
    PortfolioStrategy,
)
from src.positions.database.db_models.position_model import Position

ZERO = Decimal("0")


class PortfolioRepository(BaseRepository[Portfolio]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, Portfolio)

    async def get_by_name(self, name: str) -> Optional[Portfolio]:
        result = await self.session.execute(
            select(Portfolio).where(Portfolio.name == str(name))
        )
        return result.scalar_one_or_none()

    async def list_portfolios(self, include_archived: bool = False) -> List[Portfolio]:
        query = select(Portfolio)
        if not include_archived:
            query = query.where(Portfolio.status == STATUS_ACTIVE)
        result = await self.session.execute(query.order_by(Portfolio.name.asc()))
        return list(result.scalars().all())

    # --- attachments -------------------------------------------------------
    async def strategies_for(self, portfolio_id: int) -> List[str]:
        result = await self.session.execute(
            select(PortfolioStrategy.strategy_key)
            .where(PortfolioStrategy.portfolio_id == int(portfolio_id))
            .order_by(PortfolioStrategy.strategy_key.asc())
        )
        return [row[0] for row in result.all()]

    async def strategies_for_many(
        self, portfolio_ids: Sequence[int]
    ) -> Dict[int, List[str]]:
        if not portfolio_ids:
            return {}
        result = await self.session.execute(
            select(PortfolioStrategy.portfolio_id, PortfolioStrategy.strategy_key)
            .where(PortfolioStrategy.portfolio_id.in_([int(i) for i in portfolio_ids]))
            .order_by(PortfolioStrategy.strategy_key.asc())
        )
        mapping: Dict[int, List[str]] = {int(i): [] for i in portfolio_ids}
        for portfolio_id, strategy_key in result.all():
            mapping.setdefault(int(portfolio_id), []).append(strategy_key)
        return mapping

    async def portfolios_running(self, strategy_key: str) -> List[Portfolio]:
        """Which portfolios have this strategy attached."""
        result = await self.session.execute(
            select(Portfolio)
            .join(PortfolioStrategy, PortfolioStrategy.portfolio_id == Portfolio.id)
            .where(PortfolioStrategy.strategy_key == str(strategy_key))
            .order_by(Portfolio.name.asc())
        )
        return list(result.scalars().all())

    async def set_strategies(self, portfolio_id: int, strategy_keys: Sequence[str]) -> None:
        existing = set(await self.strategies_for(portfolio_id))
        wanted = {str(key) for key in strategy_keys}

        for key in wanted - existing:
            self.session.add(
                PortfolioStrategy(portfolio_id=int(portfolio_id), strategy_key=key)
            )
        for key in existing - wanted:
            rows = await self.session.execute(
                select(PortfolioStrategy).where(
                    PortfolioStrategy.portfolio_id == int(portfolio_id),
                    PortfolioStrategy.strategy_key == key,
                )
            )
            for row in rows.scalars().all():
                await self.session.delete(row)
        await self.session.flush()

    # --- history -----------------------------------------------------------
    async def has_history(self, portfolio_id: int) -> bool:
        """Anything at all that would change if this portfolio disappeared.

        Checked on DATA rather than by comparing names or trusting a flag: a
        portfolio that has ever traded or ever held money can be archived but
        never deleted, because deleting it would silently move every total that
        included it.
        """
        portfolio_id = int(portfolio_id)
        for model in (Order, Position, ChartTrade, CashLedgerEntry):
            result = await self.session.execute(
                select(func.count())
                .select_from(model)
                .where(model.portfolio_id == portfolio_id)
            )
            if int(result.scalar_one() or 0) > 0:
                return True
        return False

    async def open_position_count(self, portfolio_id: int) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(Position)
            .where(
                Position.portfolio_id == int(portfolio_id),
                Position.is_open.is_(True),
            )
        )
        return int(result.scalar_one() or 0)


class CashLedgerRepository(BaseRepository[CashLedgerEntry]):
    """Append-only. There is deliberately no update and no delete here."""

    def __init__(self, session: AsyncSession):
        super().__init__(session, CashLedgerEntry)

    async def add_entry(
        self,
        portfolio_id: int,
        entry_type: str,
        amount: Decimal,
        entry_at: datetime,
        order_id: Optional[int] = None,
        note: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
    ) -> CashLedgerEntry:
        entry = CashLedgerEntry(
            portfolio_id=int(portfolio_id),
            entry_type=str(entry_type),
            amount=Decimal(str(amount)),
            order_id=order_id,
            note=note,
            created_by_user_id=created_by_user_id,
            entry_at=entry_at,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def balance(self, portfolio_id: int) -> Decimal:
        """Cash, by replaying the ledger. Never a stored running total."""
        result = await self.session.execute(
            select(func.sum(CashLedgerEntry.amount)).where(
                CashLedgerEntry.portfolio_id == int(portfolio_id)
            )
        )
        total = result.scalar_one_or_none()
        return Decimal(str(total)) if total is not None else ZERO

    async def balances(self, portfolio_ids: Sequence[int]) -> Dict[int, Decimal]:
        """One query for many portfolios, for the Portfolios page."""
        if not portfolio_ids:
            return {}
        result = await self.session.execute(
            select(CashLedgerEntry.portfolio_id, func.sum(CashLedgerEntry.amount))
            .where(CashLedgerEntry.portfolio_id.in_([int(i) for i in portfolio_ids]))
            .group_by(CashLedgerEntry.portfolio_id)
        )
        totals = {int(i): ZERO for i in portfolio_ids}
        for portfolio_id, total in result.all():
            totals[int(portfolio_id)] = (
                Decimal(str(total)) if total is not None else ZERO
            )
        return totals

    async def list_entries(
        self,
        portfolio_id: int,
        page: int = 0,
        size: int = 100,
    ) -> tuple[List[CashLedgerEntry], int]:
        """Newest first, as the UI shows them."""
        base = select(CashLedgerEntry).where(
            CashLedgerEntry.portfolio_id == int(portfolio_id)
        )
        count_result = await self.session.execute(
            select(func.count()).select_from(base.subquery())
        )
        total = int(count_result.scalar_one() or 0)

        result = await self.session.execute(
            base.order_by(CashLedgerEntry.entry_at.desc(), CashLedgerEntry.id.desc())
            .offset(page * size)
            .limit(size)
        )
        return list(result.scalars().all()), total

    async def charged_for_order(self, order_id: int) -> Decimal:
        """How much has already been charged to the ledger for one order.

        Charges are recomputed on the cumulative executed quantity rather than
        accumulated per fill (backend/CLAUDE.md section 5), so the ledger has to
        record the DELTA against this -- otherwise a multi-fill order is charged
        twice.
        """
        result = await self.session.execute(
            select(func.sum(CashLedgerEntry.amount)).where(
                CashLedgerEntry.order_id == int(order_id),
                CashLedgerEntry.entry_type == "CHARGES",
            )
        )
        total = result.scalar_one_or_none()
        # Charge entries are negative; report the positive magnitude charged.
        return -Decimal(str(total)) if total is not None else ZERO
