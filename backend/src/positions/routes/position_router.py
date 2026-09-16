"""Position endpoints."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import require_session
from src.core.singleton_utils import SingletonDepends
from src.database.session import get_async_session
from src.orders.api_schemas.order_schemas import OrderResponse
from src.positions.api_schemas.position_schemas import (
    ClosePositionRequest,
    PositionListResponse,
)
from src.positions.controllers.position_controller import PositionController

position_router = APIRouter(prefix="/positions", tags=["Positions"])


async def get_position_controller(
    session: AsyncSession = Depends(get_async_session),
) -> PositionController:
    return SingletonDepends(PositionController, called_inside_fastapi_depends=True)(session)


@position_router.get("", response_model=PositionListResponse)
async def list_positions(
    include_closed: bool = Query(False, alias="includeClosed"),
    controller: PositionController = Depends(get_position_controller),
    _: str = Depends(require_session),
) -> PositionListResponse:
    """Open positions with live MTM, plus aggregate realised/unrealised P&L."""
    return await controller.list_positions(include_closed=include_closed)


@position_router.post("/{position_id}/close", response_model=OrderResponse)
async def close_position(
    position_id: int,
    request: ClosePositionRequest,
    controller: PositionController = Depends(get_position_controller),
    _: str = Depends(require_session),
) -> OrderResponse:
    """Close or partially close a position by placing an offsetting order."""
    return await controller.close_position(position_id, request)
