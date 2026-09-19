"""IPO dashboard endpoints.

**ACCESS IS SPLIT, AND THE SPLIT IS ENFORCED HERE.** Reading all three tabs is
open to any signed-in user; the three actions -- Applied, Accepted UPI Mandate,
Reject -- are admin-only. `conf/role-pages.json` grants `/ipo` to both roles and
the UI hides the buttons for a ROLE_USER, but that is presentation: hiding a
control is not access control. `require_admin` on the action endpoints is what
actually refuses, and `tests/test_ipo_api.py` asserts a ROLE_USER calling them
directly gets a 403.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import SessionPrincipal, require_admin, require_session
from src.core.singleton_utils import SingletonDepends
from src.database.session import get_async_session
from src.ipo.api_schemas.ipo_schemas import (
    IpoResponse,
    IpoStatusResponse,
    IpoTabResponse,
    SetActionRequest,
)
from src.ipo.controllers.ipo_controller import (
    IpoController,
    TAB_CLOSING_NEXT,
    TAB_CLOSING_TODAY,
    TAB_LISTED,
)

ipo_router = APIRouter(prefix="/ipo", tags=["IPO Dashboard"])


async def get_ipo_controller(
    session: AsyncSession = Depends(get_async_session),
) -> IpoController:
    return SingletonDepends(IpoController, called_inside_fastapi_depends=True)(session)


@ipo_router.get("/closing-today", response_model=IpoTabResponse)
async def closing_today(
    controller: IpoController = Depends(get_ipo_controller),
    _: SessionPrincipal = Depends(require_session),
) -> IpoTabResponse:
    """Mainboard IPOs whose subscription closes today (IST)."""
    return await controller.tab(TAB_CLOSING_TODAY)


@ipo_router.get("/closing-next", response_model=IpoTabResponse)
async def closing_next(
    controller: IpoController = Depends(get_ipo_controller),
    _: SessionPrincipal = Depends(require_session),
) -> IpoTabResponse:
    """Mainboard IPOs closing on the next day the exchange is open.

    Weekends are skipped; there is no holiday list, and the response says so.
    """
    return await controller.tab(TAB_CLOSING_NEXT)


@ipo_router.get("/listed", response_model=IpoTabResponse)
async def listed(
    controller: IpoController = Depends(get_ipo_controller),
    _: SessionPrincipal = Depends(require_session),
) -> IpoTabResponse:
    """IPOs that have listed, newest first. Forward-only; nothing backfilled."""
    return await controller.tab(TAB_LISTED)


@ipo_router.get("/status", response_model=IpoStatusResponse)
async def status(
    controller: IpoController = Depends(get_ipo_controller),
    _: SessionPrincipal = Depends(require_session),
) -> IpoStatusResponse:
    """What the IPO clock is doing, and when it last refreshed."""
    return await controller.status()


@ipo_router.post("/{ipo_id}/action", response_model=IpoResponse)
async def set_action(
    ipo_id: int,
    request: SetActionRequest,
    controller: IpoController = Depends(get_ipo_controller),
    principal: SessionPrincipal = Depends(require_admin),
) -> IpoResponse:
    """Mark an IPO Applied / Accepted / Rejected, or unmark it.

    ADMIN ONLY, enforced here rather than by the sidebar. Append-only: the
    action log keeps every change with its timestamp and the user who made it.
    """
    return await controller.set_action(ipo_id, request, principal.user_id)


@ipo_router.post("/refresh", response_model=IpoTabResponse)
async def refresh_now(
    controller: IpoController = Depends(get_ipo_controller),
    _: SessionPrincipal = Depends(require_admin),
) -> IpoTabResponse:
    """Fetch the board now. Does not consume a scheduled slot."""
    return await controller.refresh_now()
