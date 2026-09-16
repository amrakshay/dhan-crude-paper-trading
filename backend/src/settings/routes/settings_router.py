"""Settings endpoints.

Values saved here are stored in the database and take priority over `.env`.
The Dhan access token is encrypted at rest and is never returned to the browser.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import require_session
from src.core.singleton_utils import SingletonDepends
from src.database.session import get_async_session
from src.settings.api_schemas.settings_schemas import (
    SaveSettingsRequest,
    SaveSettingsResponse,
    SettingsResponse,
    ValidateCredentialsRequest,
    ValidateCredentialsResponse,
)
from src.settings.controllers.settings_controller import SettingsController

settings_router = APIRouter(prefix="/settings", tags=["Settings"])


async def get_settings_controller(
    session: AsyncSession = Depends(get_async_session),
) -> SettingsController:
    return SingletonDepends(SettingsController, called_inside_fastapi_depends=True)(session)


@settings_router.get("", response_model=SettingsResponse)
async def get_settings(
    controller: SettingsController = Depends(get_settings_controller),
    _: str = Depends(require_session),
) -> SettingsResponse:
    """Current effective settings, and whether each came from the database or .env.

    The access token is reported as a mask plus its decoded expiry — never in
    full.
    """
    return await controller.get_settings()


@settings_router.put("", response_model=SaveSettingsResponse)
async def save_settings(
    request: SaveSettingsRequest,
    controller: SettingsController = Depends(get_settings_controller),
    _: str = Depends(require_session),
) -> SaveSettingsResponse:
    """Save settings and restart the market feed so they take effect.

    An omitted or empty `accessToken` leaves the stored token untouched.
    """
    return await controller.save(request)


@settings_router.post("/validate", response_model=ValidateCredentialsResponse)
async def validate_credentials(
    request: ValidateCredentialsRequest,
    controller: SettingsController = Depends(get_settings_controller),
    _: str = Depends(require_session),
) -> ValidateCredentialsResponse:
    """Check credentials against Dhan without saving them.

    Issues one market-data request (the option chain expiry list) — the lightest
    authenticated call available, and already on this application's market-data
    allowlist.
    """
    return await controller.validate(request)
