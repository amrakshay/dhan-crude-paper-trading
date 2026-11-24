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
    BasketRequest,
    ChainResponse,
    ExitGroupRequest,
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


@options_trader_router.post("/basket/preview")
async def preview_basket(
    request: BasketRequest,
    controller: OptionsTraderController = Depends(get_controller),
    _: SessionPrincipal = Depends(require_session),
) -> dict:
    """Overall vs final margin and the hedge benefit. Places nothing."""
    return await controller.preview_basket(request)


@options_trader_router.post("/basket", status_code=201)
async def place_basket(
    request: BasketRequest,
    controller: OptionsTraderController = Depends(get_controller),
    _: SessionPrincipal = Depends(require_session),
) -> dict:
    """Send every leg as its own paper order, buys first. Returns each leg's outcome."""
    return await controller.place_basket(request)


@options_trader_router.get("/basket/{basket_id}")
async def basket(
    basket_id: int,
    controller: OptionsTraderController = Depends(get_controller),
    _: SessionPrincipal = Depends(require_session),
) -> dict:
    return await controller.basket(basket_id)


@options_trader_router.post("/exit-group")
async def exit_group(
    request: ExitGroupRequest,
    controller: OptionsTraderController = Depends(get_controller),
    _: SessionPrincipal = Depends(require_session),
) -> dict:
    """Exit every option on an underlying (and expiry), SHORTS FIRST."""
    return await controller.exit_group(request)


@options_trader_router.get("/positions")
async def positions(
    portfolio_id: int = Query(..., alias="portfolioId"),
    controller: OptionsTraderController = Depends(get_controller),
    _: SessionPrincipal = Depends(require_session),
) -> dict:
    """Open option positions grouped by underlying and expiry, with SPAN margin."""
    return await controller.positions(portfolio_id)


@options_trader_router.get("/span")
async def span_status(
    controller: OptionsTraderController = Depends(get_controller),
    _: SessionPrincipal = Depends(require_session),
) -> dict:
    """Which NSE SPAN file is in force, and why not if none is."""
    return controller.span_status()


@options_trader_router.get("/rms")
async def risk_desk(
    portfolio_id: int = Query(..., alias="portfolioId"),
    controller: OptionsTraderController = Depends(get_controller),
    _: SessionPrincipal = Depends(require_session),
) -> dict:
    """The risk desk's actions on this portfolio, newest first, and its rules."""
    return await controller.risk_desk(portfolio_id)
