"""Chart trade persistence."""
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.chart_trading.database.db_models.chart_trade_model import ChartTrade
from src.core.base_repository import BaseRepository

STATUS_OPEN = "OPEN"
STATUS_CLOSED = "CLOSED"


class ChartTradeRepository(BaseRepository[ChartTrade]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, ChartTrade)

    async def get_open_for_underlying(self, underlying_security_id: str) -> Optional[ChartTrade]:
        """The one open chart trade on a contract.

        At most one exists because a Sell click closes an open call before it
        opens anything, and a Buy click closes an open put -- the service
        enforces that netting, this is just the read.
        """
        result = await self.session.execute(
            select(ChartTrade)
            .where(
                ChartTrade.underlying_security_id == str(underlying_security_id),
                ChartTrade.status == STATUS_OPEN,
            )
            .order_by(ChartTrade.opened_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_open(self) -> List[ChartTrade]:
        """Every armed chart trade, for the bracket monitor."""
        result = await self.session.execute(
            select(ChartTrade)
            .where(ChartTrade.status == STATUS_OPEN)
            .order_by(ChartTrade.opened_at.asc())
        )
        return list(result.scalars().all())

    async def list_recent(self, limit: int = 50) -> List[ChartTrade]:
        result = await self.session.execute(
            select(ChartTrade).order_by(ChartTrade.opened_at.desc()).limit(limit)
        )
        return list(result.scalars().all())
