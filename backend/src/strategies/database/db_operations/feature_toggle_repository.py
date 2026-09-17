"""Feature toggle persistence."""
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.base_repository import BaseRepository
from src.strategies.database.db_models.feature_toggle_model import (
    SCOPE_CAPABILITY,
    SCOPE_STRATEGY,
    FeatureToggle,
)


class FeatureToggleRepository(BaseRepository[FeatureToggle]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, FeatureToggle)

    async def list_all(self) -> List[FeatureToggle]:
        result = await self.session.execute(select(FeatureToggle))
        return list(result.scalars().all())

    async def states(self) -> Dict[str, Dict[str, bool]]:
        """{scope: {key: enabled}}, exactly as stored.

        Validated by the registry, not here: the repository's job is to return
        rows, and deciding that a row names nothing real is the registry's.
        """
        rows = await self.list_all()
        states: Dict[str, Dict[str, bool]] = {
            SCOPE_STRATEGY: {},
            SCOPE_CAPABILITY: {},
        }
        for row in rows:
            states.setdefault(row.scope, {})[row.toggle_key] = bool(row.enabled)
        return states

    async def set_state(
        self,
        scope: str,
        toggle_key: str,
        enabled: bool,
        updated_by_user_id: Optional[int] = None,
    ) -> FeatureToggle:
        result = await self.session.execute(
            select(FeatureToggle).where(
                FeatureToggle.scope == scope,
                FeatureToggle.toggle_key == str(toggle_key),
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = FeatureToggle(
                scope=scope,
                toggle_key=str(toggle_key),
                enabled=bool(enabled),
                updated_by_user_id=updated_by_user_id,
            )
            self.session.add(row)
        else:
            row.enabled = bool(enabled)
            row.updated_by_user_id = updated_by_user_id
        await self.session.flush()
        return row
