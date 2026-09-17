"""Chart trading orchestration: domain errors in, HTTP out."""
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.chart_trading.api_schemas.chart_trading_schemas import (
    ChartClickRequest,
    ChartClickResponse,
    ChartLevelsRequest,
    ChartPreviewResponse,
    ChartStateResponse,
)
from src.chart_trading.database.db_operations.chart_trade_repository import (
    ChartTradeRepository,
)
from src.chart_trading.services.chart_trading_service import (
    ChartTradingError,
    ChartTradingService,
)
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.logging_config import get_logger
from src.orders.database.db_operations.order_repository import OrderRepository
from src.orders.services.order_service import OrderValidationError
from src.positions.database.db_operations.position_repository import PositionRepository

logger = get_logger("chart_trading.controller")


class ChartTradingController:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repository = ChartTradeRepository(session)
        self.service = ChartTradingService(
            chart_trades=self.repository,
            instruments=InstrumentRepository(session),
            orders=OrderRepository(session),
            positions=PositionRepository(session),
        )

    async def _state(
        self, underlying_security_id: str, portfolio_id: Optional[int] = None
    ) -> ChartStateResponse:
        return ChartStateResponse(
            **await self.service.get_state(underlying_security_id, portfolio_id)
        )

    async def get_state(
        self, security_id: str, portfolio_id: Optional[int] = None
    ) -> ChartStateResponse:
        try:
            return await self._state(security_id, portfolio_id)
        except ChartTradingError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    async def preview(
        self, security_id: str, expiry=None, lots: Optional[int] = None
    ) -> ChartPreviewResponse:
        try:
            return ChartPreviewResponse(
                **await self.service.preview(security_id, expiry, lots)
            )
        except ChartTradingError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    async def click(self, request: ChartClickRequest) -> ChartClickResponse:
        try:
            result = await self.service.click(
                underlying_security_id=request.security_id,
                side=request.side,
                expiry=request.expiry,
                lots=request.lots,
                portfolio_id=request.portfolio_id,
            )
            # The order service writes through this session; the background
            # matcher and bracket monitor open their own, so nothing they do
            # is visible until this commits (backend/CLAUDE.md section 2).
            await self.session.commit()
            self.session.expire_all()
        except (ChartTradingError, OrderValidationError) as error:
            logger.warning(
                "Chart %s on %s rejected: %s", request.side, request.security_id, error
            )
            raise HTTPException(status_code=400, detail=str(error)) from error

        return ChartClickResponse(
            action=result["action"],
            state=await self._state(request.security_id, request.portfolio_id),
        )

    async def set_levels(
        self, trade_id: int, request: ChartLevelsRequest
    ) -> ChartStateResponse:
        try:
            trade = await self.service.set_levels(
                trade_id,
                stop_loss=request.stop_loss,
                take_profit=request.take_profit,
                clear_stop_loss=request.clear_stop_loss,
                clear_take_profit=request.clear_take_profit,
            )
            underlying_security_id = trade.underlying_security_id
            portfolio_id = trade.portfolio_id
            await self.session.commit()
            self.session.expire_all()
        except ChartTradingError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return await self._state(underlying_security_id, portfolio_id)

    async def close(self, trade_id: int) -> ChartStateResponse:
        try:
            trade = await self.service.close(trade_id)
            underlying_security_id = trade.underlying_security_id
            portfolio_id = trade.portfolio_id
            await self.session.commit()
            self.session.expire_all()
        except (ChartTradingError, OrderValidationError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return await self._state(underlying_security_id)
