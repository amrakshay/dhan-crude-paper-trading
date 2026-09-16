"""Position persistence."""
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.base_repository import BaseRepository
from src.positions.database.db_models.position_model import Position


class PositionRepository(BaseRepository[Position]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, Position)

    async def get_open_for_security(self, security_id: str) -> Optional[Position]:
        """At most one open position per contract; the service enforces it."""
        result = await self.session.execute(
            select(Position)
            .where(Position.security_id == security_id, Position.is_open.is_(True))
            .order_by(Position.opened_at.desc())
        )
        return result.scalars().first()

    async def list_open(self) -> List[Position]:
        result = await self.session.execute(
            select(Position)
            .where(Position.is_open.is_(True))
            .order_by(Position.opened_at.desc())
        )
        return list(result.scalars().all())

    async def list_all(self, include_closed: bool = True) -> List[Position]:
        query = select(Position)
        if not include_closed:
            query = query.where(Position.is_open.is_(True))
        result = await self.session.execute(query.order_by(Position.opened_at.desc()))
        return list(result.scalars().all())

    async def list_closed(self) -> List[Position]:
        result = await self.session.execute(
            select(Position)
            .where(Position.is_open.is_(False))
            .order_by(Position.closed_at.desc())
        )
        return list(result.scalars().all())
