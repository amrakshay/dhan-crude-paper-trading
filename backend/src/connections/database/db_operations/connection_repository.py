"""Connection persistence.

The repository owns the `value` XOR `encrypted_value` invariant, exactly as
`AppSettingRepository` does for `app_settings`. Nothing above this layer can
write a secret into the plaintext column, because nothing above this layer
writes the columns.
"""
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.connections.database.db_models.connection_model import (
    Connection,
    ConnectionSetting,
)
from src.core.base_repository import BaseRepository
from src.core.time_utils import utc_now


class ConnectionRepository(BaseRepository[Connection]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, Connection)

    async def list_all(self) -> List[Connection]:
        result = await self.session.execute(
            select(Connection).order_by(Connection.provider, Connection.name)
        )
        return list(result.scalars().all())

    async def get_by_provider(self, provider: str) -> Optional[Connection]:
        """The first row for a provider, by name.

        Every provider is a singleton today (see `ProviderSpec.singleton`), so
        "the Dhan connection" is a meaningful phrase. The schema allows more,
        and this deliberately picks deterministically rather than assuming
        exactly one exists.
        """
        result = await self.session.execute(
            select(Connection)
            .where(Connection.provider == provider)
            .order_by(Connection.id)
        )
        return result.scalars().first()

    async def create_connection(
        self,
        provider: str,
        name: str,
        enabled: bool = True,
        updated_by_user_id: Optional[int] = None,
    ) -> Connection:
        connection = Connection(
            provider=provider,
            name=name,
            enabled=enabled,
            updated_by_user_id=updated_by_user_id,
        )
        self.session.add(connection)
        await self.session.flush()
        return connection

    async def record_check(
        self,
        connection: Connection,
        ok: bool,
        detail: Optional[str],
        checked_at=None,
    ) -> Connection:
        """Store the outcome of a check, with when it happened.

        `last_check_ok` stays nullable everywhere else: a connection that has
        never been checked is a third state, and only an actual check may move
        it off None.
        """
        connection.last_checked_at = checked_at or utc_now()
        connection.last_check_ok = bool(ok)
        connection.last_check_detail = (detail or "")[:500] or None
        await self.session.flush()
        return connection


class ConnectionSettingRepository(BaseRepository[ConnectionSetting]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, ConnectionSetting)

    async def get_by_key(
        self, connection_id: int, key: str
    ) -> Optional[ConnectionSetting]:
        result = await self.session.execute(
            select(ConnectionSetting).where(
                ConnectionSetting.connection_id == connection_id,
                ConnectionSetting.key == key,
            )
        )
        return result.scalar_one_or_none()

    async def get_all_for(self, connection_id: int) -> Dict[str, ConnectionSetting]:
        result = await self.session.execute(
            select(ConnectionSetting).where(
                ConnectionSetting.connection_id == connection_id
            )
        )
        return {setting.key: setting for setting in result.scalars().all()}

    async def upsert(
        self,
        connection_id: int,
        key: str,
        value: Optional[str] = None,
        encrypted_value: Optional[str] = None,
        is_encrypted: bool = False,
    ) -> ConnectionSetting:
        setting = await self.get_by_key(connection_id, key)
        if setting is None:
            setting = ConnectionSetting(connection_id=connection_id, key=key)
            self.session.add(setting)

        # A value lives in exactly ONE column, never both. This is the
        # invariant that keeps a secret out of the plaintext column.
        setting.value = None if is_encrypted else value
        setting.encrypted_value = encrypted_value if is_encrypted else None
        setting.is_encrypted = bool(is_encrypted)
        setting.set_at = utc_now()
        await self.session.flush()
        return setting

    async def delete_by_key(self, connection_id: int, key: str) -> bool:
        setting = await self.get_by_key(connection_id, key)
        if setting is None:
            return False
        await self.session.delete(setting)
        await self.session.flush()
        return True
