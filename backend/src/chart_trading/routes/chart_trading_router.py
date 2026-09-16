"""Chart trading endpoints.

PAPER RUPEES ONLY. A click here writes rows to the local database and matches
against the in-memory book, exactly like the order ticket. No broker is
contacted, and a Sell buys a put -- it never writes an option.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import SessionPrincipal, require_session
from src.chart_trading.api_schemas.chart_trading_schemas import (
    ChartClickRequest,
    ChartClickResponse,
    ChartLevelsRequest,
    ChartPreviewResponse,
    ChartStateResponse,
)
from src.chart_trading.controllers.chart_trading_controller import (
    ChartTradingController,
)
from src.core.singleton_utils import SingletonDepends
from src.database.session import get_async_session

chart_trading_router = APIRouter(prefix="/chart-trading", tags=["Chart Trading"])


async def get_chart_trading_controller(
    session: AsyncSession = Depends(get_async_session),
) -> ChartTradingController:
    return SingletonDepends(ChartTradingController, called_inside_fastapi_depends=True)(
        session
    )


@chart_trading_router.get("/state", response_model=ChartStateResponse)
async def get_chart_state(
    security_id: str = Query(..., alias="securityId", description="The charted contract"),
    controller: ChartTradingController = Depends(get_chart_trading_controller),
    _: SessionPrincipal = Depends(require_session),
) -> ChartStateResponse:
    """The chart's own open position, its bracket levels and its live P&L."""
    return await controller.get_state(security_id)


@chart_trading_router.get("/preview", response_model=ChartPreviewResponse)
async def preview_chart_trade(
    security_id: str = Query(..., alias="securityId"),
    expiry: Optional[date] = Query(None, description="Defaults to the nearest expiry"),
    lots: Optional[int] = Query(None, ge=1),
    controller: ChartTradingController = Depends(get_chart_trading_controller),
    _: SessionPrincipal = Depends(require_session),
) -> ChartPreviewResponse:
    """What a Buy and a Sell would each buy right now, and what each would cost.

    The chart shows this BEFORE the click, which is how one-click entry still
    puts the estimated charges and net debit on screen ahead of the order.
    """
    return await controller.preview(security_id, expiry, lots)


@chart_trading_router.post("/click", response_model=ChartClickResponse, status_code=201)
async def click_chart(
    request: ChartClickRequest,
    controller: ChartTradingController = Depends(get_chart_trading_controller),
    _: SessionPrincipal = Depends(require_session),
) -> ChartClickResponse:
    """One click on the chart.

    BUY buys the nearest ATM call, SELL the nearest ATM put. The two net: the
    opposite click closes an open chart trade instead of stacking a second leg
    on top of it.
    """
    return await controller.click(request)


@chart_trading_router.patch("/trades/{trade_id}/levels", response_model=ChartStateResponse)
async def set_chart_levels(
    trade_id: int,
    request: ChartLevelsRequest,
    controller: ChartTradingController = Depends(get_chart_trading_controller),
    _: SessionPrincipal = Depends(require_session),
) -> ChartStateResponse:
    """Arm, move or clear the dragged stop-loss / take-profit lines.

    Levels are prices of the UNDERLYING FUTURE. A level on the wrong side of
    the market is refused rather than armed, because it would fire the instant
    it was set.
    """
    return await controller.set_levels(trade_id, request)


@chart_trading_router.post("/trades/{trade_id}/close", response_model=ChartStateResponse)
async def close_chart_trade(
    trade_id: int,
    controller: ChartTradingController = Depends(get_chart_trading_controller),
    _: SessionPrincipal = Depends(require_session),
) -> ChartStateResponse:
    """Sell the option back at market and flatten the chart trade."""
    return await controller.close(trade_id)
