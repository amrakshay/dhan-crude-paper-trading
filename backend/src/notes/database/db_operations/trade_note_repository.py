"""Trade note persistence."""
from typing import List, Optional, Sequence

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.base_repository import BaseRepository
from src.notes.database.db_models.trade_note_model import TradeNote


class TradeNoteRepository(BaseRepository[TradeNote]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, TradeNote)

    async def search(
        self,
        query: Optional[str] = None,
        order_id: Optional[int] = None,
        position_id: Optional[int] = None,
        security_id: Optional[str] = None,
        page: int = 0,
        size: int = 50,
    ) -> tuple[List[TradeNote], int]:
        statement = select(TradeNote)
        conditions = []
        if query:
            # Case-insensitive substring search across the note text and the
            # contract it refers to, so "6800 CALL" finds notes on that strike.
            pattern = f"%{query}%"
            conditions.append(
                or_(
                    TradeNote.note_text.ilike(pattern),
                    TradeNote.trading_symbol.ilike(pattern),
                )
            )
        if order_id is not None:
            conditions.append(TradeNote.order_id == order_id)
        if position_id is not None:
            conditions.append(TradeNote.position_id == position_id)
        if security_id:
            conditions.append(TradeNote.security_id == security_id)
        if conditions:
            statement = statement.where(and_(*conditions))

        count_result = await self.session.execute(
            select(func.count()).select_from(statement.subquery())
        )
        total = count_result.scalar_one()

        result = await self.session.execute(
            statement.order_by(TradeNote.noted_at.desc()).offset(page * size).limit(size)
        )
        return list(result.scalars().all()), total

    async def list_for_orders(self, order_ids: Sequence[int]) -> List[TradeNote]:
        if not order_ids:
            return []
        result = await self.session.execute(
            select(TradeNote).where(TradeNote.order_id.in_(list(order_ids)))
        )
        return list(result.scalars().all())
