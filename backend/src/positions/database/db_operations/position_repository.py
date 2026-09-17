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
        self, portfolio_id: int, security_id: str
    ) -> Optional[Position]:
        """At most one open position per contract PER PORTFOLIO.

        The portfolio is part of the key, not a filter that was forgotten. Two
        portfolios holding the same strike are two separate books: without the
        portfolio here, a buy in one would average into the other's position
        and a close in one would close the other. That is the single change in
        this feature that would corrupt data silently rather than fail.
        """
        result = await self.session.execute(
            select(Position)
            .where(
                Position.portfolio_id == int(portfolio_id),
                Position.security_id == security_id,
                Position.is_open.is_(True),
            )
            .order_by(Position.opened_at.desc())
        )
        return result.scalars().first()

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
