"""Reads the schema revision the database is actually at.

The health page reports which Alembic revision this database is on, which is
the difference between "the code is deployed" and "the code is deployed and
migrated". That is a query, so it lives in a repository rather than in a
service (backend/CLAUDE.md section 1) even though the health feature owns no
tables and ships no migration.
"""
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.logging_config import get_logger

logger = get_logger("health.schema")


class SchemaRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def current_revision(self) -> Optional[str]:
        """The `alembic_version` row, or None.

        None is a legitimate answer, not an error: a database built by
        `DatabaseManager.create_tables()` (which is how the tests build one)
        has the full schema and no `alembic_version` table at all.
        """
        try:
            result = await self.session.execute(text("SELECT version_num FROM alembic_version"))
            return result.scalar_one_or_none()
        except Exception as exc:
            logger.debug("Could not read alembic_version: %s", exc)
            return None
