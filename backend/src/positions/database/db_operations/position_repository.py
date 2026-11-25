"""Position persistence."""
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.base_repository import BaseRepository
from src.positions.database.db_models.position_model import Position


class PositionRepository(BaseRepository[Position]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, Position)

    async def get_open_for_security(
        self, portfolio_id: int, security_id: str, product: Optional[str] = None
    ) -> Optional[Position]:
        """At most one open position per contract PER PORTFOLIO.

        The portfolio is part of the key, not a filter that was forgotten. Two
        portfolios holding the same strike are two separate books: without the
        portfolio here, a buy in one would average into the other's position
        and a close in one would close the other. That is the single change in
        this feature that would corrupt data silently rather than fail.

        PRODUCT is part of the key when the caller names one. Dhan keeps an
        Intraday and a Carry-forward position in the same contract as two
        lines, each with its own average and P&L, and squares off only the
        Intraday one at 15:25 (OPTIONS_TRADER_PLAN.md D5; found missing in the
        live session of 2026-11-25). Strategies that have no products pass
        none and keep the one-line-per-contract behaviour unchanged.
        """
        conditions = [
            Position.portfolio_id == int(portfolio_id),
            Position.security_id == security_id,
            Position.is_open.is_(True),
        ]
        if product is not None:
            conditions.append(Position.product == product)
        result = await self.session.execute(
            select(Position).where(*conditions).order_by(Position.opened_at.desc())
        )
        return result.scalars().first()

    async def list_open_lines(self, portfolio_id: int, security_id: str) -> List[Position]:
        """Every open line for a contract in a portfolio, one per product."""
        result = await self.session.execute(
            select(Position).where(
                Position.portfolio_id == int(portfolio_id),
                Position.security_id == str(security_id),
                Position.is_open.is_(True),
            )
        )
        return [one for one in result.scalars().all() if int(one.net_quantity or 0)]

    async def list_open(
        self, portfolio_id: Optional[int] = None
    ) -> List[Position]:
        query = select(Position).where(Position.is_open.is_(True))
        if portfolio_id is not None:
            query = query.where(Position.portfolio_id == int(portfolio_id))
        result = await self.session.execute(query.order_by(Position.opened_at.desc()))
        return list(result.scalars().all())

    async def list_all(
        self,
        include_closed: bool = True,
        strategy_key: Optional[str] = None,
        portfolio_id: Optional[int] = None,
    ) -> List[Position]:
        query = select(Position)
        if not include_closed:
            query = query.where(Position.is_open.is_(True))
        if strategy_key:
            query = query.where(Position.strategy_key == strategy_key)
        if portfolio_id is not None:
            query = query.where(Position.portfolio_id == int(portfolio_id))
        result = await self.session.execute(query.order_by(Position.opened_at.desc()))
        return list(result.scalars().all())

    async def list_closed(self) -> List[Position]:
        result = await self.session.execute(
            select(Position)
            .where(Position.is_open.is_(False))
            .order_by(Position.closed_at.desc())
        )
        return list(result.scalars().all())
