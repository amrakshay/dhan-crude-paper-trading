"""Instrument master endpoints."""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import SessionPrincipal, require_session
from src.core.singleton_utils import SingletonDepends
from src.database.session import get_async_session
from src.instruments.api_schemas.instrument_schemas import (
    ChainResponse,
    ExpiryListResponse,
    InstrumentResponse,
    InstrumentStatusResponse,
    RefreshResponse,
)
from src.instruments.controllers.instrument_controller import InstrumentController

instrument_router = APIRouter(prefix="/instruments", tags=["Instruments"])


async def get_instrument_controller(
    session: AsyncSession = Depends(get_async_session),
) -> InstrumentController:
    return SingletonDepends(InstrumentController, called_inside_fastapi_depends=True)(session)


@instrument_router.post("/refresh", response_model=RefreshResponse)
async def refresh_instrument_master(
    force: bool = Query(False, description="Re-download even if the cache is fresh"),
    controller: InstrumentController = Depends(get_instrument_controller),
    _: SessionPrincipal = Depends(require_session),
) -> RefreshResponse:
    """Re-fetch Dhan's instrument master and upsert the CRUDEOIL universe.

    Security IDs change every expiry, so this is the operation that keeps the
    chain pointing at contracts that actually exist.
    """
    return await controller.refresh(force=force)


@instrument_router.get("/status", response_model=InstrumentStatusResponse)
async def get_instrument_status(
    controller: InstrumentController = Depends(get_instrument_controller),
    _: SessionPrincipal = Depends(require_session),
) -> InstrumentStatusResponse:
    """How many contracts are loaded, when, and what the front month is."""
    return await controller.get_status()


@instrument_router.get("/expiries", response_model=ExpiryListResponse)
async def list_expiries(
    controller: InstrumentController = Depends(get_instrument_controller),
    _: SessionPrincipal = Depends(require_session),
) -> ExpiryListResponse:
    """Option and futures expiries, plus the mapping between them."""
    return await controller.list_expiries()


@instrument_router.get("/futures/near", response_model=InstrumentResponse)
async def get_near_future(
    controller: InstrumentController = Depends(get_instrument_controller),
    _: SessionPrincipal = Depends(require_session),
) -> InstrumentResponse:
    """The front-month CRUDEOIL future."""
    return await controller.get_near_future()


@instrument_router.get("/chain", response_model=ChainResponse)
async def get_chain(
    expiry: Optional[date] = Query(None, description="Defaults to the nearest expiry"),
    controller: InstrumentController = Depends(get_instrument_controller),
    _: SessionPrincipal = Depends(require_session),
) -> ChainResponse:
    """Full strike ladder for one expiry, with its underlying future."""
    return await controller.get_chain(expiry)
