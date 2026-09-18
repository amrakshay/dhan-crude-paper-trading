"""Trailing-stop persistence.

Unlike the journal, this table is NOT append-only: a chandelier stop is a
running maximum and updating it is the whole point. What it does refuse is a
second ACTIVE row for one holding -- the rotation owns a name or it does not,
and two active stops on one position would fire twice.
"""
from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.base_repository import BaseRepository
from src.logging_config import get_logger
from src.swing.database.db_models.swing_stop_model import (
    STOP_ACTIVE,
    STOP_TRIGGERED,
    SwingStop,
)

logger = get_logger("swing.stops")


class SwingStopError(Exception):
    pass


class SwingStopRepository(BaseRepository[SwingStop]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, SwingStop)

    async def create(self, **fields: Any) -> SwingStop:
        """One ACTIVE stop per (portfolio, security), enforced here.

        Checked rather than left to a convention: a duplicate would fire twice,
        and the second firing would sell a position that no longer exists.
        """
        existing = await self.get_active(
            fields["portfolio_id"], fields["security_id"]
        )
        if existing is not None:
            raise SwingStopError(
                f"{fields.get('symbol')} already has an active trailing stop "
                f"(id {existing.id}) in portfolio {fields['portfolio_id']}. "
                f"Close it before opening another."
            )
        record = SwingStop(**fields)
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_by_id(self, stop_id: int) -> Optional[SwingStop]:
        result = await self.session.execute(
            select(SwingStop).where(SwingStop.id == int(stop_id))
        )
        return result.scalar_one_or_none()

    async def get_active(
        self, portfolio_id: int, security_id: str
    ) -> Optional[SwingStop]:
        result = await self.session.execute(
            select(SwingStop)
            .where(
                and_(
                    SwingStop.portfolio_id == int(portfolio_id),
                    SwingStop.security_id == str(security_id),
                    SwingStop.status == STOP_ACTIVE,
                )
            )
            .order_by(desc(SwingStop.id))
        )
        return result.scalars().first()

    async def list_active(
        self,
        strategy_key: Optional[str] = None,
        portfolio_id: Optional[int] = None,
    ) -> List[SwingStop]:
        query = select(SwingStop).where(SwingStop.status == STOP_ACTIVE)
        if strategy_key:
            query = query.where(SwingStop.strategy_key == strategy_key)
        if portfolio_id is not None:
            query = query.where(SwingStop.portfolio_id == int(portfolio_id))
        result = await self.session.execute(query.order_by(SwingStop.symbol.asc()))
        return list(result.scalars().all())

    async def list_triggered(
        self, strategy_key: Optional[str] = None
    ) -> List[SwingStop]:
        """Stops that fired but whose exit has not been placed yet.

        The Closing Auction Session is what makes this a real state rather than
        an instant: a stop that fires on an F&O-eligible name after 15:15
        cannot fill in continuous trading, so it is recorded as triggered and
        the exit waits for the next session's open.
        """
        query = select(SwingStop).where(
            and_(
                SwingStop.status == STOP_TRIGGERED,
                SwingStop.exit_order_id.is_(None),
            )
        )
        if strategy_key:
            query = query.where(SwingStop.strategy_key == strategy_key)
        result = await self.session.execute(query.order_by(SwingStop.id.asc()))
        return list(result.scalars().all())

    async def history(
        self,
        strategy_key: str,
        portfolio_id: Optional[int] = None,
        limit: int = 200,
    ) -> List[SwingStop]:
        query = select(SwingStop).where(SwingStop.strategy_key == strategy_key)
        if portfolio_id is not None:
            query = query.where(SwingStop.portfolio_id == int(portfolio_id))
        result = await self.session.execute(
            query.order_by(desc(SwingStop.id)).limit(int(limit))
        )
        return list(result.scalars().all())

    async def exit_kind_by_order_id(
        self, strategy_key: str
    ) -> Dict[int, str]:
        """{exit order id: TRAIL_STOP | ROTATION | REGIME | MANUAL}.

        The exit mix in the performance report is counted from this rather than
        by reading English out of a reason string.
        """
        result = await self.session.execute(
            select(SwingStop.exit_order_id, SwingStop.exit_kind).where(
                and_(
                    SwingStop.strategy_key == strategy_key,
                    SwingStop.exit_order_id.is_not(None),
                    SwingStop.exit_kind.is_not(None),
                )
            )
        )
        return {int(order_id): kind for order_id, kind in result.all() if order_id}

    async def ratcheted_on(
        self, strategy_key: str, session_date: date
    ) -> int:
        """How many active stops have already been ratcheted for a session."""
        rows = await self.list_active(strategy_key=strategy_key)
        return sum(1 for row in rows if row.last_ratcheted_session == session_date)
