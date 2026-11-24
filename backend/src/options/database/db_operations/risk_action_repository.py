"""Queries for the risk desk's journal. Written once, never updated."""
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.options.database.db_models.risk_action_model import RiskAction


class RiskActionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, **fields) -> RiskAction:
        row = RiskAction(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def recent(self, portfolio_id: Optional[int] = None, limit: int = 50) -> List[RiskAction]:
        query = select(RiskAction).order_by(RiskAction.id.desc()).limit(int(limit))
        if portfolio_id is not None:
            query = query.where(RiskAction.portfolio_id == int(portfolio_id))
        result = await self.session.execute(query)
        return list(result.scalars().all())
