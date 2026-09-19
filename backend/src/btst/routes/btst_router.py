"""BTST Overnight endpoints.

PAPER RUPEES ONLY, like everything else here. The two `runs` endpoints trigger
the same code the scheduler runs, under the same gates: a switched-off strategy
refuses, and an UNARMED one decides, journals and places nothing.

Reading is open to any signed-in user -- the page is a record of what the
system decided, and a journal is history. TRIGGERING a run is admin-only,
because a scan spends the portfolio's money. `/health` is admin-only too: it
serves live machinery state and WARNING+ log records, which is the exposure
`/api/healthcheck/*` is gated for.
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import SessionPrincipal, require_admin, require_session
from src.btst.api_schemas.btst_schemas import RunRequest
from src.btst.controllers.btst_controller import BtstController
from src.core.singleton_utils import SingletonDepends
from src.database.session import get_async_session

btst_router = APIRouter(prefix="/btst", tags=["BTST Overnight"])


async def get_btst_controller(
    session: AsyncSession = Depends(get_async_session),
) -> BtstController:
    return SingletonDepends(BtstController, called_inside_fastapi_depends=True)(
        session
    )


def _default_key() -> str:
    return BtstController.default_strategy_key()


@btst_router.get("/status")
async def get_status(
    strategy_key: Optional[str] = Query(None, alias="strategyKey"),
    portfolio_id: Optional[int] = Query(None, alias="portfolioId"),
    controller: BtstController = Depends(get_btst_controller),
    _: SessionPrincipal = Depends(require_session),
) -> Dict[str, Any]:
    """What it holds overnight, what it last decided, and what it will do next.

    Every undefined figure comes back as null rather than zero: an overnight gap
    on a position that has not been sold is not a gap of 0.00%.
    """
    return await controller.status(strategy_key or _default_key(), portfolio_id)


@btst_router.get("/signals")
async def get_signals(
    strategy_key: Optional[str] = Query(None, alias="strategyKey"),
    portfolio_id: Optional[int] = Query(None, alias="portfolioId"),
    controller: BtstController = Depends(get_btst_controller),
    _: SessionPrincipal = Depends(require_session),
) -> Dict[str, Any]:
    """TODAY'S SCAN, as it stands right now.

    The tab the rotation has no equivalent of, because its decision happens
    overnight in one shot and this one forms over the afternoon. It computes
    the identical funnel the scheduled scan computes, journals nothing and
    places nothing.
    """
    return await controller.signals(strategy_key or _default_key(), portfolio_id)


@btst_router.get("/history")
async def get_history(
    strategy_key: Optional[str] = Query(None, alias="strategyKey"),
    limit: int = Query(30, ge=1, le=200),
    controller: BtstController = Depends(get_btst_controller),
    _: SessionPrincipal = Depends(require_session),
) -> Dict[str, Any]:
    """The decision journal: every run, and every decision and non-action."""
    return await controller.history(strategy_key or _default_key(), limit)


@btst_router.get("/performance")
async def get_performance(
    strategy_key: Optional[str] = Query(None, alias="strategyKey"),
    controller: BtstController = Depends(get_btst_controller),
    _: SessionPrincipal = Depends(require_session),
) -> Dict[str, Any]:
    """What the overnight gaps have actually been, beside what was backtested."""
    return await controller.performance(strategy_key or _default_key())


@btst_router.get("/configuration")
async def get_configuration(
    strategy_key: Optional[str] = Query(None, alias="strategyKey"),
    controller: BtstController = Depends(get_btst_controller),
    _: SessionPrincipal = Depends(require_session),
) -> Dict[str, Any]:
    """The rule switches and the two clock times, with their defaults beside
    them -- and what is NOT editable, and why."""
    return controller.configuration(strategy_key or _default_key())


@btst_router.get("/explain")
async def get_explain(
    strategy_key: Optional[str] = Query(None, alias="strategyKey"),
    controller: BtstController = Depends(get_btst_controller),
    _: SessionPrincipal = Depends(require_session),
) -> Dict[str, Any]:
    """B1-B16, read from the strategy's own YAML. The page restates no number."""
    return controller.explain(strategy_key or _default_key())


@btst_router.get("/health")
async def get_health(
    strategy_key: Optional[str] = Query(None, alias="strategyKey"),
    portfolio_id: Optional[int] = Query(None, alias="portfolioId"),
    controller: BtstController = Depends(get_btst_controller),
    _: SessionPrincipal = Depends(require_admin),
) -> Dict[str, Any]:
    """Is this strategy healthy, and did the exit run?

    **ROLE_ACCOUNT_ADMIN only**, unlike every other read on this router, for
    the same reason the rotation's health tab is: it serves live machinery
    state and WARNING+ log records, which can carry anything a developer
    interpolated.
    """
    return await controller.health(strategy_key or _default_key(), portfolio_id)


@btst_router.post("/runs/scan")
async def run_scan(
    request: RunRequest,
    controller: BtstController = Depends(get_btst_controller),
    _: SessionPrincipal = Depends(require_admin),
) -> Dict[str, Any]:
    """Run the afternoon scan now.

    ADMIN ONLY: with the strategy armed and `placeOrders` true this SPENDS
    MONEY. `placeOrders: false` computes and journals the identical decision
    and places nothing, which is what an unarmed scheduled run does.
    """
    return await controller.run_scan(
        request.strategy_key or _default_key(),
        request.portfolio_id,
        request.force,
        request.place_orders,
    )


@btst_router.post("/runs/exit")
async def run_exit(
    request: RunRequest,
    controller: BtstController = Depends(get_btst_controller),
    _: SessionPrincipal = Depends(require_admin),
) -> Dict[str, Any]:
    """Sell every overnight position now.

    ADMIN ONLY. This is the manual form of the job that IS this strategy's
    edge, and it is deliberately available by hand: if the scheduled exit did
    not run, the right response is to run it -- late, and recorded as late --
    rather than to wait.
    """
    return await controller.run_exit(
        request.strategy_key or _default_key(), request.portfolio_id
    )
