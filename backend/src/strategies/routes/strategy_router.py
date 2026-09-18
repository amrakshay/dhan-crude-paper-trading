"""Strategies & Features endpoints.

Reading is open to any signed-in user -- the Positions and Orders pages need to
know which strategies exist to label a row. TOGGLING is admin-only, gated with
`require_admin` here as well as hidden from the sidebar by role-pages.json.
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import SessionPrincipal, require_admin, require_session
from src.core.singleton_utils import SingletonDepends
from src.database.session import get_async_session
from src.strategies.api_schemas.strategy_schemas import (
    ArmRequest,
    ArmResponse,
    StrategyListResponse,
    ToggleRequest,
    ToggleResponse,
)
from src.strategies.controllers.strategy_controller import StrategyController

strategy_router = APIRouter(prefix="/strategies", tags=["Strategies"])


async def get_strategy_controller(
    session: AsyncSession = Depends(get_async_session),
) -> StrategyController:
    return SingletonDepends(StrategyController, called_inside_fastapi_depends=True)(
        session
    )


@strategy_router.get("", response_model=StrategyListResponse)
async def list_strategies(
    controller: StrategyController = Depends(get_strategy_controller),
    _: SessionPrincipal = Depends(require_session),
) -> StrategyListResponse:
    """Every strategy module and capability, with what each is costing.

    The cost figures -- instruments subscribed, expiries polled, greeks
    interval, open positions, which portfolios run it -- are what make the
    toggle an informed decision. They are read from state the process already
    keeps, never measured on the tick path.
    """
    return await controller.list_strategies()


@strategy_router.get("/{strategy_key}/disable-warnings")
async def disable_warnings(
    strategy_key: str,
    controller: StrategyController = Depends(get_strategy_controller),
    _: SessionPrincipal = Depends(require_admin),
) -> Dict[str, Any]:
    """What switching this strategy off would cost, before it is switched off."""
    return await controller.disable_warnings(strategy_key)


@strategy_router.put("/{strategy_key}/enabled", response_model=ToggleResponse)
async def set_strategy_enabled(
    strategy_key: str,
    request: ToggleRequest,
    controller: StrategyController = Depends(get_strategy_controller),
    principal: SessionPrincipal = Depends(require_admin),
) -> ToggleResponse:
    """Switch a strategy on or off, applied to the running process immediately.

    Off means off: no instruments on the feed, no greeks poll against the Dhan
    token, no chart fetches, no background work, and its live pages gone from
    the sidebar. Its HISTORY stays exactly where it was.
    """
    return await controller.set_strategy_enabled(
        strategy_key, request.enabled, user_id=principal.user_id
    )


@strategy_router.get("/{strategy_key}/arm-warnings")
async def arm_warnings(
    strategy_key: str,
    controller: StrategyController = Depends(get_strategy_controller),
    _: SessionPrincipal = Depends(require_admin),
) -> Dict[str, Any]:
    """What arming this strategy would let loose, before it is armed."""
    return await controller.arm_warnings(strategy_key)


@strategy_router.put("/{strategy_key}/armed", response_model=ArmResponse)
async def set_strategy_armed(
    strategy_key: str,
    request: ArmRequest,
    controller: StrategyController = Depends(get_strategy_controller),
    principal: SessionPrincipal = Depends(require_admin),
) -> ArmResponse:
    """Let an automated strategy submit orders of its own accord, or stop it.

    Separate from the enabled switch, and separate on purpose: an enabled
    strategy computes, decides and writes a decision record every session; an
    ARMED one may also spend the portfolio's money without anyone clicking.
    Disarming never cancels or closes anything -- open positions keep their
    stops recomputed and recorded, and only the placing of new orders stops.

    A strategy whose module declares no automation block is refused with a 400:
    there is nothing to arm, because every order in it comes from a person.
    """
    return await controller.set_strategy_armed(
        strategy_key, request.armed, user_id=principal.user_id
    )


@strategy_router.put("/capabilities/{capability}/enabled", response_model=ToggleResponse)
async def set_capability_enabled(
    capability: str,
    request: ToggleRequest,
    controller: StrategyController = Depends(get_strategy_controller),
    principal: SessionPrincipal = Depends(require_admin),
) -> ToggleResponse:
    """Switch a capability on or off for every strategy at once."""
    return await controller.set_capability_enabled(
        capability, request.enabled, user_id=principal.user_id
    )
