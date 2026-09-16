"""Settings orchestration."""
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.logging_config import get_logger
from src.settings.api_schemas.settings_schemas import (
    SaveSettingsRequest,
    SaveSettingsResponse,
    SettingsResponse,
    ValidateCredentialsRequest,
    ValidateCredentialsResponse,
)
from src.settings.database.db_operations.app_setting_repository import (
    AppSettingRepository,
)
from src.settings.services.settings_service import (
    SettingsService,
    SettingsValidationError,
)

logger = get_logger("settings.controller")


class SettingsController:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.service = SettingsService(AppSettingRepository(session))

    async def get_settings(self) -> SettingsResponse:
        return SettingsResponse(**await self.service.effective())

    async def save(self, request: SaveSettingsRequest) -> SaveSettingsResponse:
        try:
            await self.service.save(
                client_id=request.client_id,
                access_token=request.access_token,
                synthetic_feed=request.synthetic_feed,
                clear_access_token=request.clear_access_token,
            )
        except SettingsValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # Credentials and the synthetic toggle only take effect once the feed is
        # rebuilt, so do it here rather than making the operator restart.
        feed_restarted = False
        feed_state = None
        message = "Settings saved."
        try:
            from src.market.services.feed_manager import get_feed_manager

            status = await get_feed_manager().reconfigure()
            feed_restarted = True
            feed_state = (status.get("feed") or {}).get("state")
            message = f"Settings saved and the market feed was restarted ({feed_state})."
        except Exception as exc:
            logger.exception("Feed reconfigure after settings save failed")
            message = (
                "Settings saved, but the market feed could not be restarted "
                f"automatically ({exc}). Restart the server to apply them."
            )

        return SaveSettingsResponse(
            saved=True,
            settings=SettingsResponse(**await self.service.effective()),
            feedRestarted=feed_restarted,
            feedState=feed_state,
            message=message,
        )

    async def validate(
        self, request: ValidateCredentialsRequest
    ) -> ValidateCredentialsResponse:
        result = await self.service.validate_credentials(
            client_id=request.client_id, access_token=request.access_token
        )
        return ValidateCredentialsResponse(
            valid=result["valid"],
            message=result["message"],
            checkedAt=result["checkedAt"],
            token=result["token"],
            expiries=result.get("expiries", []),
            detail=result.get("detail"),
        )
