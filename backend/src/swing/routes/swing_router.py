"""Swing Momentum endpoints.

PAPER RUPEES ONLY, like everything else here. The two `runs` endpoints trigger
the same code the scheduler runs, under the same gates: a switched-off strategy
refuses, and an UNARMED one decides, journals and places nothing.

Reading is open to any signed-in user -- the page is a record of what the
system decided. TRIGGERING a run is admin-only, because a rebalance spends the
portfolio's money.
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import SessionPrincipal, require_admin, require_session
from src.core.singleton_utils import SingletonDepends
from src.database.session import get_async_session
from src.swing.api_schemas.swing_schemas import RunRequest
from src.swing.controllers.swing_controller import SwingController

swing_router = APIRouter(prefix="/swing", tags=["Swing Momentum"])

DEFAULT_STRATEGY = "nse-swing-momentum"


async def get_swing_controller(
    session: AsyncSession = Depends(get_async_session),
) -> SwingController:
    return SingletonDepends(SwingController, called_inside_fastapi_depends=True)(
        session
    )


@swing_router.get("/strategies")
async def list_automated_strategies(
    controller: SwingController = Depends(get_swing_controller),
    _: SessionPrincipal = Depends(require_session),
) -> Dict[str, Any]:
    """Which modules decide on a schedule, and whether each may trade."""
    return {"strategies": controller.automated_strategies()}


@swing_router.get("/status")
async def get_status(
    strategy_key: str = Query(DEFAULT_STRATEGY, alias="strategyKey"),
    portfolio_id: Optional[int] = Query(None, alias="portfolioId"),
    controller: SwingController = Depends(get_swing_controller),
    _: SessionPrincipal = Depends(require_session),
) -> Dict[str, Any]:
    """The regime, the breadth, the slots, the arming state and the schedule.

    Every undefined figure comes back as null rather than zero: a breadth that
    could not be measured is not a breadth of 0%, and an equity figure withheld
    because a position has no mark is not a balance of nothing.
    """
    return await controller.status(strategy_key, portfolio_id)


@swing_router.get("/book")
async def get_book(
    portfolio_id: int = Query(..., alias="portfolioId"),
    strategy_key: str = Query(DEFAULT_STRATEGY, alias="strategyKey"),
    controller: SwingController = Depends(get_swing_controller),
    _: SessionPrincipal = Depends(require_session),
) -> Dict[str, Any]:
    """The open book: every position, its rank, its stop and its distance to it."""
    return await controller.book(strategy_key, portfolio_id)


@swing_router.get("/history")
async def get_history(
    strategy_key: str = Query(DEFAULT_STRATEGY, alias="strategyKey"),
    limit: int = Query(30, ge=1, le=200),
    run_kind: Optional[str] = Query(None, alias="runKind"),
    controller: SwingController = Depends(get_swing_controller),
    _: SessionPrincipal = Depends(require_session),
) -> Dict[str, Any]:
    """Recent runs, newest first. Append-only: nothing here was ever edited."""
    return await controller.history(strategy_key, limit, run_kind)


@swing_router.get("/sessions/{session_id}")
async def get_session(
    session_id: int,
    strategy_key: str = Query(DEFAULT_STRATEGY, alias="strategyKey"),
    controller: SwingController = Depends(get_swing_controller),
    _: SessionPrincipal = Depends(require_session),
) -> Dict[str, Any]:
    """One run, with every decision it took and every one it declined to take."""
    return await controller.session_detail(strategy_key, session_id)


@swing_router.get("/stops")
async def get_stops(
    strategy_key: str = Query(DEFAULT_STRATEGY, alias="strategyKey"),
    portfolio_id: Optional[int] = Query(None, alias="portfolioId"),
    limit: int = Query(100, ge=1, le=1000),
    controller: SwingController = Depends(get_swing_controller),
    _: SessionPrincipal = Depends(require_session),
) -> Dict[str, Any]:
    """Every chandelier stop, active and historical, with what set it."""
    return await controller.stops(strategy_key, portfolio_id, limit)


@swing_router.get("/performance")
async def get_performance(
    strategy_key: str = Query(DEFAULT_STRATEGY, alias="strategyKey"),
    portfolio_id: Optional[int] = Query(None, alias="portfolioId"),
    controller: SwingController = Depends(get_swing_controller),
    _: SessionPrincipal = Depends(require_session),
) -> Dict[str, Any]:
    """CAGR, drawdown, MAR, win rate, profit factor, hold, exit mix, concentration.

    From this book's own realised history, not from the backtest. CAGR, MAR and
    drawdown are WITHHELD when money moved into or out of the portfolio after
    the first trade -- a deposit is not a gain and a withdrawal is not a
    drawdown.
    """
    return await controller.performance(strategy_key, portfolio_id)


@swing_router.get("/explain")
async def explain(
    strategy_key: str = Query(DEFAULT_STRATEGY, alias="strategyKey"),
    controller: SwingController = Depends(get_swing_controller),
    _: SessionPrincipal = Depends(require_session),
) -> Dict[str, Any]:
    """Every number that defines this strategy, read from its own YAML.

    The "How it works" page renders this rather than restating the values in
    JavaScript: no number that affects a trade is written twice (root
    `CLAUDE.md` section 7). Each entry carries the specification's own
    parameter code so the page and the handoff can be read side by side.
    """
    return controller.explain(strategy_key)


@swing_router.post("/runs/nightly")
async def run_nightly(
    request: RunRequest,
    strategy_key: str = Query(DEFAULT_STRATEGY, alias="strategyKey"),
    controller: SwingController = Depends(get_swing_controller),
    _: SessionPrincipal = Depends(require_admin),
) -> Dict[str, Any]:
    """Decide one session now. Places no orders under any circumstances."""
    return await controller.run_nightly(
        strategy_key, request.portfolio_id, request.force
    )


@swing_router.post("/runs/rebalance")
async def run_rebalance(
    request: RunRequest,
    strategy_key: str = Query(DEFAULT_STRATEGY, alias="strategyKey"),
    controller: SwingController = Depends(get_swing_controller),
    _: SessionPrincipal = Depends(require_admin),
) -> Dict[str, Any]:
    """Run the rebalance now: sells first, then buys, through the paper path.

    Only an ARMED strategy places anything. An unarmed one produces the
    identical decision record with no orders, which is what makes arming a
    safeguard rather than a mode.
    """
    return await controller.run_rebalance(
        strategy_key, request.portfolio_id, request.force
    )
