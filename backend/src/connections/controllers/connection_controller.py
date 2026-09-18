"""Connections orchestration. Services raise; this turns that into HTTP."""
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.connections.api_schemas.connection_schemas import (
    AlertListResponse,
    AlertRow,
    ConnectionCard,
    ConnectionListResponse,
    ListenResponse,
    SaveConnectionRequest,
    TestMessageResponse,
    ValidateConnectionRequest,
    ValidateConnectionResponse,
)
from src.connections.database.db_operations.alert_repository import AlertRepository
from src.connections.services import providers
from src.connections.services.connection_service import (
    ConnectionService,
    _iso_ist,
)
from src.connections.services.connection_store import ConnectionStoreError
from src.core.time_utils import utc_now
from src.logging_config import get_logger

logger = get_logger("connections.controller")


class ConnectionController:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.service = ConnectionService(session)

    async def list_connections(self) -> ConnectionListResponse:
        page = await self.service.page()
        return ConnectionListResponse(
            connections=[ConnectionCard(**card) for card in page["connections"]]
        )

    async def get_connection(self, provider: str) -> ConnectionCard:
        try:
            return ConnectionCard(**await self.service.detail(provider))
        except ConnectionStoreError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def save(
        self, provider: str, request: SaveConnectionRequest, user_id: Optional[int]
    ) -> ConnectionCard:
        try:
            saved = await self.service.save(
                provider,
                settings=request.settings,
                enabled=request.enabled,
                name=request.name,
                user_id=user_id,
            )
        except ConnectionStoreError as exc:
            logger.warning("Connection save refused for %s: %s", provider, exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # Dhan's credentials are what the live feed runs on, so a save has to
        # reach the running process rather than waiting for a restart -- the
        # same thing the Settings page has always done.
        #
        # `apply_to_config()` FIRST, then the feed. That is the same ordering
        # the lifespan uses and for the same reason: the feed client is built
        # from the config, so reconfiguring before the overlay would rebuild it
        # on the values the save has just replaced.
        if provider == providers.PROVIDER_DHAN:
            await self._apply_to_running_config()
            await self._reconfigure_feed()
        return ConnectionCard(**saved)

    async def validate(
        self, provider: str, request: ValidateConnectionRequest
    ) -> ValidateConnectionResponse:
        spec = providers.get_provider(provider)
        if spec is None:
            raise HTTPException(
                status_code=404, detail=f"{provider!r} is not a connection here."
            )
        if spec.key == providers.PROVIDER_DHAN:
            return await self._validate_dhan(request)
        return await self._validate_telegram(request)

    async def _validate_dhan(
        self, request: ValidateConnectionRequest
    ) -> ValidateConnectionResponse:
        """Dhan's check is the option chain expiry list, unchanged.

        That is the lightest authenticated call available and is already on
        this application's market-data allowlist, so validating a connection
        can never touch a trading endpoint.
        """
        from src.settings.database.db_operations.app_setting_repository import (
            AppSettingRepository,
        )
        from src.settings.services.settings_service import SettingsService

        settings = request.settings or {}
        service = SettingsService(AppSettingRepository(self.session))
        result = await service.validate_credentials(
            client_id=settings.get(providers.DHAN_CLIENT_ID) or None,
            access_token=settings.get(providers.DHAN_ACCESS_TOKEN) or None,
        )
        await self._record_check(
            providers.PROVIDER_DHAN, bool(result["valid"]), result["message"]
        )
        return ValidateConnectionResponse(
            valid=bool(result["valid"]),
            message=result["message"],
            checkedAt=result["checkedAt"],
            failureKind=None if result["valid"] else "DHAN_REFUSED",
            detail={
                "token": result.get("token"),
                "expiries": result.get("expiries", []),
                "detail": result.get("detail"),
            },
        )

    async def _validate_telegram(
        self, request: ValidateConnectionRequest
    ) -> ValidateConnectionResponse:
        from src.connections.services.telegram_service import TelegramService

        settings = request.settings or {}
        result = await TelegramService(self.session).validate(
            bot_token=settings.get(providers.TELEGRAM_BOT_TOKEN) or None,
            chat_id=settings.get(providers.TELEGRAM_CHAT_ID) or None,
        )
        await self._record_check(
            providers.PROVIDER_TELEGRAM, bool(result["valid"]), result["message"]
        )
        return ValidateConnectionResponse(
            valid=bool(result["valid"]),
            message=result["message"],
            checkedAt=result["checkedAt"],
            failureKind=result.get("failureKind"),
            detail={
                "tokenValid": result.get("tokenValid"),
                "chatValid": result.get("chatValid"),
                "bot": result.get("bot"),
                "chat": result.get("chat"),
            },
        )

    async def send_test_message(self) -> TestMessageResponse:
        from src.connections.services.telegram_service import TelegramService

        result = await TelegramService(self.session).send_test_message()
        await self.session.commit()
        return TestMessageResponse(
            sent=bool(result["sent"]),
            message=result["message"],
            at=result["at"],
            chatId=result.get("chatId"),
            chatTitle=result.get("chatTitle"),
            failureKind=result.get("failureKind"),
        )

    async def listen_for_test(self, seconds: int) -> ListenResponse:
        from src.connections.services.telegram_service import TelegramService

        result = await TelegramService(self.session).listen_for_test(seconds)
        return ListenResponse(
            received=bool(result["received"]),
            message=result["message"],
            at=result["at"],
            windowSeconds=result["windowSeconds"],
            failureKind=result.get("failureKind"),
            update=result.get("update"),
            borrowedRunningPoller=bool(result.get("borrowedRunningPoller")),
        )

    async def alerts(self, limit: int = 50) -> AlertListResponse:
        """What it told somebody, and whether it arrived.

        This is the reason the outbox is a table. A log line saying
        `sendMessage returned 200` cannot answer "did the 18:15 alert arrive",
        and a suppressed row cannot be distinguished from one that was never
        raised.
        """
        from src.connections.services.alert_dispatcher import get_alert_dispatcher
        from src.connections.services.alert_watcher import get_alert_watcher

        repository = AlertRepository(self.session)
        rows = await repository.recent(limit=limit)
        return AlertListResponse(
            alerts=[
                AlertRow(
                    id=row.id,
                    kind=row.kind,
                    severity=row.severity,
                    title=row.title,
                    body=row.body,
                    status=row.status,
                    attempts=int(row.attempts or 0),
                    suppressedCount=int(row.suppressed_count or 0),
                    lastError=row.last_error,
                    createdAt=_iso_ist(row.created_at),
                    sentAt=_iso_ist(row.sent_at),
                )
                for row in rows
            ],
            counts=await repository.counts_by_status(),
            dispatcher=get_alert_dispatcher().status(),
            watcher=get_alert_watcher().status(),
        )

    # --- helpers -----------------------------------------------------------
    async def _record_check(self, provider: str, ok: bool, detail: str) -> None:
        connection = await self.service.store.get(provider)
        if connection is None:
            return
        await self.service.store.connections.record_check(connection, ok, detail)
        await self.session.commit()

    async def _apply_to_running_config(self) -> None:
        """Overlay the saved credentials onto the in-memory config.

        `SettingsService.apply_to_config()` is still the ONE place this
        happens -- the startup call in `main.py` is untouched and still pinned
        by `tests/test_startup_applies_stored_settings.py`. This is the
        after-a-save half, which the Settings page has always done and which
        moved here with the fields.
        """
        from src.settings.database.db_operations.app_setting_repository import (
            AppSettingRepository,
        )
        from src.settings.services.settings_service import SettingsService

        try:
            await SettingsService(
                AppSettingRepository(self.session)
            ).apply_to_config()
        except Exception:  # noqa: BLE001
            logger.exception(
                "The credentials were saved but could not be applied to the "
                "running configuration; restart the server to pick them up"
            )

    @staticmethod
    async def _reconfigure_feed() -> None:
        from src.market.services.feed_manager import get_feed_manager

        try:
            await get_feed_manager().reconfigure()
        except Exception:  # noqa: BLE001
            logger.exception(
                "Dhan credentials were saved, but the market feed could not be "
                "restarted automatically. Restart the server to apply them."
            )
