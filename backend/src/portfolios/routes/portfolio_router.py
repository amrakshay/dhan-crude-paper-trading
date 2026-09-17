"""Portfolio endpoints.

PAPER RUPEES ONLY. A portfolio is a local table of money this application
invented; nothing here reaches a broker for a balance, a funds limit or a
holding, and no name in this package resembles one that does.

**Admins create and fund; anyone trades.** Creating, funding, withdrawing,
archiving and deleting are gated with `require_admin` here AND hidden from the
sidebar by conf/role-pages.json -- the route dependency is the gate, the
sidebar is presentation (backend/CLAUDE.md section 10).
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import SessionPrincipal, require_admin, require_session
from src.core.singleton_utils import SingletonDepends
from src.database.session import get_async_session
from src.portfolios.api_schemas.portfolio_schemas import (
    CreatePortfolioRequest,
    LedgerResponse,
    MoneyRequest,
    PortfolioListResponse,
    PortfolioResponse,
    UpdatePortfolioRequest,
)
from src.portfolios.controllers.portfolio_controller import PortfolioController

portfolio_router = APIRouter(prefix="/portfolios", tags=["Portfolios"])


async def get_portfolio_controller(
    session: AsyncSession = Depends(get_async_session),
) -> PortfolioController:
    return SingletonDepends(PortfolioController, called_inside_fastapi_depends=True)(
        session
    )


@portfolio_router.get("", response_model=PortfolioListResponse)
async def list_portfolios(
    include_archived: bool = Query(False, alias="includeArchived"),
    controller: PortfolioController = Depends(get_portfolio_controller),
    _: SessionPrincipal = Depends(require_session),
) -> PortfolioListResponse:
    """Every portfolio with its cash, blocked margin, available and equity.

    The four numbers are separate on purpose: one "balance" hides what a short
    position ties up. Blocked margin is an ESTIMATE and says so in the payload.
    """
    return await controller.list_portfolios(include_archived=include_archived)


@portfolio_router.get("/{portfolio_id}", response_model=PortfolioResponse)
async def get_portfolio(
    portfolio_id: int,
    controller: PortfolioController = Depends(get_portfolio_controller),
    _: SessionPrincipal = Depends(require_session),
) -> PortfolioResponse:
    return await controller.get_portfolio(portfolio_id)


@portfolio_router.get("/{portfolio_id}/ledger", response_model=LedgerResponse)
async def get_ledger(
    portfolio_id: int,
    page: int = Query(0, ge=0),
    size: int = Query(100, gt=0, le=500),
    controller: PortfolioController = Depends(get_portfolio_controller),
    _: SessionPrincipal = Depends(require_session),
) -> LedgerResponse:
    """The append-only cash ledger, newest first. The balance is its sum."""
    return await controller.get_ledger(portfolio_id, page=page, size=size)


@portfolio_router.post("", response_model=PortfolioResponse, status_code=201)
async def create_portfolio(
    request: CreatePortfolioRequest,
    controller: PortfolioController = Depends(get_portfolio_controller),
    principal: SessionPrincipal = Depends(require_admin),
) -> PortfolioResponse:
    """Create a portfolio. Account admins only."""
    return await controller.create(request, user_id=principal.user_id)


@portfolio_router.put("/{portfolio_id}", response_model=PortfolioResponse)
async def update_portfolio(
    portfolio_id: int,
    request: UpdatePortfolioRequest,
    controller: PortfolioController = Depends(get_portfolio_controller),
    _: SessionPrincipal = Depends(require_admin),
) -> PortfolioResponse:
    return await controller.update(portfolio_id, request)


@portfolio_router.post("/{portfolio_id}/deposit", response_model=LedgerResponse)
async def deposit(
    portfolio_id: int,
    request: MoneyRequest,
    controller: PortfolioController = Depends(get_portfolio_controller),
    principal: SessionPrincipal = Depends(require_admin),
) -> LedgerResponse:
    """Add money. Recorded as a ledger entry, never as a balance update."""
    return await controller.deposit(portfolio_id, request, user_id=principal.user_id)


@portfolio_router.post("/{portfolio_id}/withdraw", response_model=LedgerResponse)
async def withdraw(
    portfolio_id: int,
    request: MoneyRequest,
    controller: PortfolioController = Depends(get_portfolio_controller),
    principal: SessionPrincipal = Depends(require_admin),
) -> LedgerResponse:
    """Take money out.

    Refused if it would take AVAILABLE below zero -- which includes reaching
    money blocked against open short positions, even though that figure is an
    estimate.
    """
    return await controller.withdraw(portfolio_id, request, user_id=principal.user_id)


@portfolio_router.post("/{portfolio_id}/archive", response_model=PortfolioResponse)
async def archive_portfolio(
    portfolio_id: int,
    controller: PortfolioController = Depends(get_portfolio_controller),
    _: SessionPrincipal = Depends(require_admin),
) -> PortfolioResponse:
    """Hide it from the pickers. Its history stays intact and readable."""
    return await controller.archive(portfolio_id)


@portfolio_router.post("/{portfolio_id}/restore", response_model=PortfolioResponse)
async def restore_portfolio(
    portfolio_id: int,
    controller: PortfolioController = Depends(get_portfolio_controller),
    _: SessionPrincipal = Depends(require_admin),
) -> PortfolioResponse:
    return await controller.restore(portfolio_id)


@portfolio_router.delete("/{portfolio_id}", status_code=204)
async def delete_portfolio(
    portfolio_id: int,
    controller: PortfolioController = Depends(get_portfolio_controller),
    _: SessionPrincipal = Depends(require_admin),
) -> Response:
    """Only a portfolio that has never been used.

    Anything with an order, a position, a chart trade or a ledger entry is
    refused: deleting it would silently move every total that included it.
    Archive it instead.
    """
    await controller.delete(portfolio_id)
    return Response(status_code=204)
