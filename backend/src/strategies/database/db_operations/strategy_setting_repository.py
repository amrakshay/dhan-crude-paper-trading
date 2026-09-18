"""Strategy setting persistence. Rows in, rows out; validation belongs above."""
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.base_repository import BaseRepository
from src.strategies.database.db_models.strategy_setting_model import StrategySetting


class StrategySettingRepository(BaseRepository[StrategySetting]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, StrategySetting)

    async def list_all(self) -> List[StrategySetting]:
        result = await self.session.execute(select(StrategySetting))
        return list(result.scalars().all())

    async def states(self) -> Dict[str, Dict[str, str]]:
        """{strategy_key: {setting_key: value}}, exactly as stored.

        Validated by the registry and the owning module, not here: deciding
        that a row names nothing real, or that "25:00" is not a time, is not a
        repository's job.
        """
        states: Dict[str, Dict[str, str]] = {}
        for row in await self.list_all():
            states.setdefault(row.strategy_key, {})[row.setting_key] = row.value
        return states

    async def set_value(
        self,
        strategy_key: str,
        setting_key: str,
        value: str,
        updated_by_user_id: Optional[int] = None,
    ) -> StrategySetting:
        result = await self.session.execute(
            select(StrategySetting).where(
                StrategySetting.strategy_key == str(strategy_key),
                StrategySetting.setting_key == str(setting_key),
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = StrategySetting(
                strategy_key=str(strategy_key),
                setting_key=str(setting_key),
                value=str(value),
                updated_by_user_id=updated_by_user_id,
            )
            self.session.add(row)
        else:
            row.value = str(value)
            row.updated_by_user_id = updated_by_user_id
        await self.session.flush()
        return row

    async def clear(self, strategy_key: str, setting_key: str) -> bool:
        """Back to the YAML's value. Deleting the row IS the reset -- there is
        no "unset" value to store, and a row saying "use the default" would be
        a second way to express the absence of one."""
        result = await self.session.execute(
            select(StrategySetting).where(
                StrategySetting.strategy_key == str(strategy_key),
                StrategySetting.setting_key == str(setting_key),
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True
