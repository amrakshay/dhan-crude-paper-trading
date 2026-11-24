"""Options Trader page endpoints.

Reads are open to any signed-in user, like the chain and the positions pages:
trading is not admin-gated in this application. Orders are placed through
`POST /api/orders` with `product`, never from here.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import SessionPrincipal, require_session
from src.database.session import get_async_session
from src.options_trader.api_schemas.options_trader_schemas import (
    ChainResponse,
    UnderlyingListResponse,
)
from src.options_trader.controllers.options_trader_controller import (
    OptionsTraderController,
)

options_trader_router = APIRouter(prefix="/options-trader", tags=["Options Trader"])


async def get_controller(
    session: AsyncSession = Depends(get_async_session),
) -> OptionsTraderController:
    return OptionsTraderController(session)


@options_trader_router.get("/underlyings", response_model=UnderlyingListResponse)
async def underlyings(
    controller: OptionsTraderController = Depends(get_controller),
    _: SessionPrincipal = Depends(require_session),
) -> UnderlyingListResponse:
    """Every NSE index and F&O stock with active option contracts."""
    return await controller.underlyings()


@options_trader_router.get("/chain", response_model=ChainResponse)
async def chain(
    underlying: str = Query(..., description="NIFTY, BANKNIFTY, RELIANCE, ..."),
    expiry: Optional[date] = Query(None, description="YYYY-MM-DD; nearest if omitted"),
    controller: OptionsTraderController = Depends(get_controller),
    _: SessionPrincipal = Depends(require_session),
) -> ChainResponse:
    """One chain, and PINS its ATM strike window to the live feed.

    Poll it while the page is open; the pin lapses after the module's
    `options.view_ttl_seconds` without a request, or on DELETE /view.
    """
    return await controller.chain(underlying, expiry)


@options_trader_router.delete("/view", status_code=204)
async def release_view(
    controller: OptionsTraderController = Depends(get_controller),
    _: SessionPrincipal = Depends(require_session),
) -> Response:
    """The page closed: release the pinned strikes now."""
    await controller.release()
    return Response(status_code=204)
