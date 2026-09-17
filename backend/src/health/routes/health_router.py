"""System health endpoints.

**ROLE_ACCOUNT_ADMIN only.** This page exposes configuration paths, credential
metadata, connected sockets and recent log records. `conf/role-pages.json` also
keeps `/health` out of a ROLE_USER's sidebar, but that is presentation --
`require_admin` here is what refuses the request, the same arrangement as
Settings (backend/CLAUDE.md section 10).

These sit under `/api/healthcheck` next to the existing liveness probe, and
both paths are in `LogRequestsMiddleware.IGNORED_PATHS`, so a page polling every
few seconds does not fill `access.log` with entries about the page that reports
on `access.log`.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import SessionPrincipal, require_admin
from src.core.singleton_utils import SingletonDepends
from src.database.session import get_async_session
from src.health.api_schemas.health_schemas import (
    ProblemsResponse,
    SystemHealthResponse,
)
from src.health.controllers.health_controller import HealthController

health_router = APIRouter(prefix="/healthcheck", tags=["Health"])


async def get_health_controller(
    session: AsyncSession = Depends(get_async_session),
) -> HealthController:
    return SingletonDepends(HealthController, called_inside_fastapi_depends=True)(session)


@health_router.get("/system", response_model=SystemHealthResponse)
async def get_system_health(
    problems: int = Query(50, ge=0, le=250, description="How many buffered log records to return"),
    controller: HealthController = Depends(get_health_controller),
    _: SessionPrincipal = Depends(require_admin),
) -> SystemHealthResponse:
    """Everything this process knows about itself, in one snapshot.

    No secret is ever returned: the Dhan access token appears as a mask and its
    decoded expiry, the database URL is redacted, and log records come from a
    buffer that has already been through the log redactor.
    """
    return await controller.get_system_health(problem_limit=problems)


@health_router.get("/problems", response_model=ProblemsResponse)
async def get_problems(
    limit: int = Query(50, ge=1, le=250),
    controller: HealthController = Depends(get_health_controller),
    _: SessionPrincipal = Depends(require_admin),
) -> ProblemsResponse:
    """Recent WARNING and ERROR records, newest first.

    In-memory and process-scoped: empty after a restart. `logs/app.log` remains
    the durable record.
    """
    return await controller.get_problems(limit=limit)
