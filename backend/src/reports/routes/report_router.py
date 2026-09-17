"""P&L report endpoints."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import SessionPrincipal, require_session
from src.core.singleton_utils import SingletonDepends
from src.core.time_utils import ist_now
from src.database.session import get_async_session
from src.reports.api_schemas.report_schemas import PnlReportResponse
from src.reports.controllers.report_controller import ReportController

report_router = APIRouter(prefix="/reports", tags=["Reports"])


async def get_report_controller(
    session: AsyncSession = Depends(get_async_session),
) -> ReportController:
    return SingletonDepends(ReportController, called_inside_fastapi_depends=True)(session)


@report_router.get("/pnl", response_model=PnlReportResponse)
async def get_pnl_report(
    security_id: Optional[str] = Query(None, alias="securityId"),
    strategy_key: Optional[str] = Query(
        None,
        alias="strategyKey",
        description=(
            "Filter by strategy module. Omit for ALL strategies, including "
            "disabled ones -- their history and their totals do not move when "
            "a toggle does."
        ),
    ),
    portfolio_id: Optional[int] = Query(
        None,
        alias="portfolioId",
        description=(
            "Filter by portfolio. OMIT for the all-portfolios view: the header "
            "scope is a convenience, not a filter you can be trapped in."
        ),
    ),
    placed_from: Optional[datetime] = Query(None, alias="from"),
    placed_to: Optional[datetime] = Query(None, alias="to"),
    controller: ReportController = Depends(get_report_controller),
    _: SessionPrincipal = Depends(require_session),
) -> PnlReportResponse:
    """Realised and unrealised P&L, by day / expiry / strike, gross vs net.

    Realised P&L is derived by replaying every fill through the same
    weighted-average rules the live positions use, so the two can never
    disagree.
    """
    return await controller.build_report(
        security_id, placed_from, placed_to, strategy_key, portfolio_id
    )


@report_router.get("/pnl/export.csv")
async def export_pnl_csv(
    security_id: Optional[str] = Query(None, alias="securityId"),
    strategy_key: Optional[str] = Query(
        None,
        alias="strategyKey",
        description=(
            "Filter by strategy module. Omit for ALL strategies, including "
            "disabled ones -- their history and their totals do not move when "
            "a toggle does."
        ),
    ),
    portfolio_id: Optional[int] = Query(
        None,
        alias="portfolioId",
        description=(
            "Filter by portfolio. OMIT for the all-portfolios view: the header "
            "scope is a convenience, not a filter you can be trapped in."
        ),
    ),
    placed_from: Optional[datetime] = Query(None, alias="from"),
    placed_to: Optional[datetime] = Query(None, alias="to"),
    controller: ReportController = Depends(get_report_controller),
    _: SessionPrincipal = Depends(require_session),
) -> Response:
    """Every realisation event as CSV."""
    content = await controller.export_realisations_csv(
        security_id, placed_from, placed_to, strategy_key, portfolio_id
    )
    filename = f"pnl-{ist_now().strftime('%Y%m%d-%H%M')}.csv"
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@report_router.get("/orders/export.csv")
async def export_orders_csv(
    security_id: Optional[str] = Query(None, alias="securityId"),
    strategy_key: Optional[str] = Query(
        None,
        alias="strategyKey",
        description=(
            "Filter by strategy module. Omit for ALL strategies, including "
            "disabled ones -- their history and their totals do not move when "
            "a toggle does."
        ),
    ),
    portfolio_id: Optional[int] = Query(
        None,
        alias="portfolioId",
        description=(
            "Filter by portfolio. OMIT for the all-portfolios view: the header "
            "scope is a convenience, not a filter you can be trapped in."
        ),
    ),
    placed_from: Optional[datetime] = Query(None, alias="from"),
    placed_to: Optional[datetime] = Query(None, alias="to"),
    controller: ReportController = Depends(get_report_controller),
    _: SessionPrincipal = Depends(require_session),
) -> Response:
    """Every order with its full charges breakdown as CSV."""
    content = await controller.export_orders_csv(
        security_id, placed_from, placed_to, strategy_key, portfolio_id
    )
    filename = f"orders-{ist_now().strftime('%Y%m%d-%H%M')}.csv"
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
