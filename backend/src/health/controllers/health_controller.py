"""Health orchestration.

Thin by design: the aggregation is in `health_service`, and the two database
reads this needs (the schema revision and the instrument master's freshness)
go through repositories.
"""
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.time_utils import as_utc_aware
from src.health.api_schemas.health_schemas import (
    ProblemsResponse,
    SystemHealthResponse,
)
from src.health.database.db_operations.schema_repository import SchemaRepository
from src.health.services import health_service
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.instruments.services.instrument_master_service import InstrumentMasterService
from src.logging_config import get_logger

logger = get_logger("health.controller")

# How many buffered log records the page gets by default. The buffer holds
# more; the page is a glance, not a log viewer.
DEFAULT_PROBLEM_LIMIT = 50


class HealthController:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.schema_repository = SchemaRepository(session)
        self.instrument_repository = InstrumentRepository(session)
        self.master_service = InstrumentMasterService(self.instrument_repository)

    async def _instrument_status(self) -> Optional[Dict[str, Any]]:
        """Instrument master count and age, or None if it cannot be read.

        A failure here must not fail the whole page: the point of a health
        endpoint is to still answer when part of the system is unwell.
        """
        try:
            count = await self.instrument_repository.count_all()
            last_refreshed = await self.instrument_repository.last_refreshed_at()
            return {
                "instrumentCount": count,
                "lastRefreshedAt": (
                    as_utc_aware(last_refreshed).isoformat() if last_refreshed else None
                ),
                "cacheFresh": self.master_service.is_cache_fresh(),
            }
        except Exception as exc:
            logger.warning("Could not read instrument master status: %s", exc)
            return None

    async def _stored_settings(self) -> Optional[Dict[str, Any]]:
        """What the Settings page saved, for comparison against what is in effect.

        The decrypted token is in here, so it is compared and discarded -- only
        key names ever reach the payload. See health_service._stored_settings_health.
        """
        try:
            from src.settings.database.db_operations.app_setting_repository import (
                AppSettingRepository,
            )
            from src.settings.services.settings_service import SettingsService

            return await SettingsService(AppSettingRepository(self.session)).load_stored()
        except Exception as exc:
            logger.warning("Could not read stored settings: %s", exc)
            return None

    async def get_system_health(
        self, problem_limit: int = DEFAULT_PROBLEM_LIMIT
    ) -> SystemHealthResponse:
        health = health_service.build_health(
            schema_revision=await self.schema_repository.current_revision(),
            instrument_status=await self._instrument_status(),
            stored_settings=await self._stored_settings(),
            problem_limit=problem_limit,
        )
        health["summary"] = health_service.summarise(health)
        return SystemHealthResponse(**health)

    async def get_problems(self, limit: int = DEFAULT_PROBLEM_LIMIT) -> ProblemsResponse:
        return ProblemsResponse(**health_service.problems_health(limit))
