"""NSE VCP Swing: the decision journal, the open positions, today's candidates.

Read-only and open to any signed-in user, like the rotation's and BTST's
journals: a journal is history. Nothing here triggers a run or places an order;
the clock and the Strategies page's enable/arm switches are the only controls.
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import SessionPrincipal, require_session
from src.core.singleton_utils import SingletonDepends
from src.database.session import get_async_session
from src.vcp.controllers.vcp_controller import VcpController

vcp_router = APIRouter(prefix="/vcp", tags=["VCP Swing"])


async def get_vcp_controller(
    session: AsyncSession = Depends(get_async_session),
) -> VcpController:
    return SingletonDepends(VcpController, called_inside_fastapi_depends=True)(session)


@vcp_router.get("/overview")
async def overview(
    portfolio_id: Optional[int] = Query(default=None, alias="portfolioId"),
    controller: VcpController = Depends(get_vcp_controller),
    _: SessionPrincipal = Depends(require_session),
) -> Dict[str, Any]:
    """Status, today's candidates, positions, decisions and the run journal."""
    return await controller.overview(portfolio_id)
