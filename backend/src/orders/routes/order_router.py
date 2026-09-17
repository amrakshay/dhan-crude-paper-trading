"""Paper order endpoints.

PAPER RUPEES ONLY -- these write to the local database and match against the
in-memory book. No broker is contacted.
"""
from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import SessionPrincipal, require_session
from src.core.singleton_utils import SingletonDepends
from src.database.session import get_async_session
from src.orders.api_schemas.order_schemas import (
    OrderListResponse,
    OrderResponse,
    PlaceOrderRequest,
    PreviewOrderRequest,
    PreviewOrderResponse,
)
from src.orders.controllers.order_controller import OrderController

order_router = APIRouter(prefix="/orders", tags=["Orders"])


async def get_order_controller(
    session: AsyncSession = Depends(get_async_session),
) -> OrderController:
    return SingletonDepends(OrderController, called_inside_fastapi_depends=True)(session)


@order_router.post("/preview", response_model=PreviewOrderResponse)
async def preview_order(
    request: PreviewOrderRequest,
    controller: OrderController = Depends(get_order_controller),
    _: SessionPrincipal = Depends(require_session),
) -> PreviewOrderResponse:
    """Simulate an order WITHOUT placing it.

    Returns the indicative fill (walking the real book), the full charges
    breakdown and the net debit/credit -- everything the trader must see before
    confirming.
    """
    return await controller.preview_order(request)


@order_router.post("", response_model=OrderResponse, status_code=201)
async def submit_paper_order(
    request: PlaceOrderRequest,
    controller: OrderController = Depends(get_order_controller),
    _: SessionPrincipal = Depends(require_session),
) -> OrderResponse:
    """Place a paper order and match it against the live book."""
    return await controller.submit_paper_order(request)


@order_router.get("", response_model=OrderListResponse)
async def list_orders(
    status: Optional[List[str]] = Query(None, description="Filter by order status"),
    strategy_key: Optional[str] = Query(
        None,
        alias="strategyKey",
        description=(
            "Filter by strategy module. Omit for every strategy -- the history "
            "of a DISABLED strategy is still returned, because turning a "
            "strategy off must not hide trades that already happened."
        ),
    ),
    portfolio_id: Optional[int] = Query(
        None,
        alias="portfolioId",
        description=(
            "Filter by portfolio. Omit for every portfolio -- history is never "
            "hidden by a scope."
        ),
    ),
    security_id: Optional[str] = Query(None, alias="securityId"),
    expiry: Optional[date] = Query(None),
    placed_from: Optional[datetime] = Query(None, alias="placedFrom"),
    placed_to: Optional[datetime] = Query(None, alias="placedTo"),
    search: Optional[str] = Query(None, description="Substring match on trading symbol"),
    page: int = Query(0, ge=0),
    size: int = Query(50, gt=0, le=500),
    controller: OrderController = Depends(get_order_controller),
    _: SessionPrincipal = Depends(require_session),
) -> OrderListResponse:
    """Order history with full state transitions, fills and charges."""
    return await controller.list_orders(
        statuses=status,
        strategy_key=strategy_key,
        portfolio_id=portfolio_id,
        security_id=security_id,
        expiry_date=expiry,
        placed_from=placed_from,
        placed_to=placed_to,
        search=search,
        page=page,
        size=size,
    )


@order_router.get("/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: int,
    controller: OrderController = Depends(get_order_controller),
    _: SessionPrincipal = Depends(require_session),
) -> OrderResponse:
    """One order with every state transition, fill and charge line."""
    return await controller.get_order(order_id)


@order_router.post("/{order_id}/cancel", response_model=OrderResponse)
async def cancel_paper_order(
    order_id: int,
    controller: OrderController = Depends(get_order_controller),
    _: SessionPrincipal = Depends(require_session),
) -> OrderResponse:
    """Cancel a resting or partially filled order."""
    return await controller.cancel_paper_order(order_id)
