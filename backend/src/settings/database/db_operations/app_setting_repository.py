"""Settings persistence."""
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.base_repository import BaseRepository
from src.core.time_utils import utc_now
from src.settings.database.db_models.app_setting_model import AppSetting


class AppSettingRepository(BaseRepository[AppSetting]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, AppSetting)

    async def get_by_key(self, key: str) -> Optional[AppSetting]:
        result = await self.session.execute(
            select(AppSetting).where(AppSetting.key == key)
        )
        return result.scalar_one_or_none()

    async def get_all(self) -> Dict[str, AppSetting]:
        result = await self.session.execute(select(AppSetting))
        return {setting.key: setting for setting in result.scalars().all()}

    async def upsert(
        self,
        key: str,
        value: Optional[str] = None,
        encrypted_value: Optional[str] = None,
        is_encrypted: bool = False,
    ) -> AppSetting:
        setting = await self.get_by_key(key)
        if setting is None:
            setting = AppSetting(key=key)
            self.session.add(setting)

        # A value lives in exactly one column, never both.
        setting.value = None if is_encrypted else value
        setting.encrypted_value = encrypted_value if is_encrypted else None
        setting.is_encrypted = is_encrypted
        setting.set_at = utc_now()
        await self.session.flush()
        return setting

    async def delete_by_key(self, key: str) -> bool:
        setting = await self.get_by_key(key)
        if setting is None:
            return False
        await self.session.delete(setting)
        await self.session.flush()
        return True
