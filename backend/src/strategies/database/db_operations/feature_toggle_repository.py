"""Feature toggle persistence."""
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.base_repository import BaseRepository
from src.strategies.database.db_models.feature_toggle_model import (
    SCOPE_AUTOMATION,
    SCOPE_CAPABILITY,
    SCOPE_POLICY,
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
            SCOPE_AUTOMATION: {},
            SCOPE_POLICY: {},
        }
        for row in rows:
            states.setdefault(row.scope, {})[row.toggle_key] = bool(row.enabled)
        return states

    async def audit_for(
        self, scope: str, toggle_keys: Sequence[str]
    ) -> Dict[str, Dict[str, Any]]:
        """When each of those toggles was last written, and by whom.

        There is no HISTORY here and this deliberately does not pretend
        otherwise: the row carries the CURRENT value with the last person to
        set it. "The gate was relaxed at 15:19 and re-enforced at 16:40" is not
        a question this table can answer, and the health page says so in those
        words rather than implying a trail it does not have.
        """
        if not toggle_keys:
            return {}
        result = await self.session.execute(
            select(FeatureToggle).where(
                FeatureToggle.scope == scope,
                FeatureToggle.toggle_key.in_([str(key) for key in toggle_keys]),
            )
        )
        return {
            row.toggle_key: {
                "updated_at": row.updated_at,
                "updated_by_user_id": row.updated_by_user_id,
            }
            for row in result.scalars().all()
        }

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
