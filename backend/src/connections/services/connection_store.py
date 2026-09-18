"""Reading and writing a connection's settings, safely.

Everything above this module deals in plain dictionaries of key -> value. This
is the one place that knows a value might be a secret, and it is where the
three rules that protect one are applied together:

- a secret is stored in `encrypted_value` (Fernet, `crypto_service`) and never
  in `value` -- the repository enforces the column invariant, this decides
  which column a given key belongs in;
- a secret is registered with `log_redaction` the moment it is READ or SAVED,
  so a careless log line anywhere (notably httpx, which logs the full request
  URL at INFO, and a Telegram bot token lives in the URL PATH) is scrubbed;
- a secret is never returned to a caller that is building a browser response --
  `masked()` is what the API gets.

A row naming an unknown provider is ignored, which is why `load()` takes a
`Connection` and asks `providers.get_provider` rather than trusting the column.
"""
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src import log_redaction
from src.connections.database.db_models.connection_model import Connection
from src.connections.database.db_operations.connection_repository import (
    ConnectionRepository,
    ConnectionSettingRepository,
)
from src.connections.services import providers
from src.logging_config import get_logger
from src.settings.services import crypto_service

logger = get_logger("connections.store")


class ConnectionStoreError(Exception):
    """A connection could not be read or written as asked."""


class ConnectionStore:
    """Per-session access to connections and their settings."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.connections = ConnectionRepository(session)
        self.settings = ConnectionSettingRepository(session)

    # --- reading -----------------------------------------------------------
    async def get(self, provider: str) -> Optional[Connection]:
        """The connection row for a provider, or None.

        An unknown provider returns None rather than raising: this is called
        from the startup path, and a database carrying a row this build does
        not understand must still boot.
        """
        if not providers.is_known(provider):
            return None
        return await self.connections.get_by_provider(provider)

    async def values(self, provider: str) -> Dict[str, str]:
        """Decrypted settings for a provider. Missing keys are omitted.

        Only keys the provider DECLARES are returned, so a stray row cannot
        start influencing configuration -- the same guarantee `MANAGED_KEYS`
        gives `app_settings`.
        """
        spec = providers.get_provider(provider)
        if spec is None:
            return {}
        connection = await self.connections.get_by_provider(provider)
        if connection is None:
            return {}
        return await self.values_for(connection)

    async def values_for(self, connection: Connection) -> Dict[str, str]:
        spec = providers.get_provider(connection.provider)
        if spec is None:
            logger.debug(
                "Ignoring connection %s: provider %r is not one this build knows",
                connection.id, connection.provider,
            )
            return {}

        stored = await self.settings.get_all_for(connection.id)
        resolved: Dict[str, str] = {}
        for key in spec.known_keys:
            setting = stored.get(key)
            if setting is None:
                continue
            if setting.is_encrypted:
                plaintext = crypto_service.decrypt(setting.encrypted_value or "")
                if plaintext is None:
                    # Unreadable (the encryption secret was rotated). Reported
                    # rather than raised: startup must not be blocked by a
                    # setting nobody can read, and the operator is told to
                    # re-enter it.
                    logger.warning(
                        "Connection %s setting %r could not be decrypted -- most "
                        "likely APP_ENCRYPTION_KEY (or APP_JWT_SECRET) changed "
                        "since it was saved. Re-enter it on the Connections page.",
                        connection.name, key,
                    )
                    continue
                resolved[key] = plaintext
                # Whatever a future call site does with this value, it will not
                # reach a log line. httpx logs full request URLs at INFO and a
                # Telegram bot token travels in the PATH.
                log_redaction.register_secret(plaintext)
            else:
                resolved[key] = setting.value or ""
        return resolved

    async def decrypt_failed(self, provider: str, key: str) -> bool:
        connection = await self.get(provider)
        if connection is None:
            return False
        setting = await self.settings.get_by_key(connection.id, key)
        if setting is None or not setting.is_encrypted:
            return False
        return crypto_service.decrypt(setting.encrypted_value or "") is None

    # --- writing -----------------------------------------------------------
    async def ensure(
        self,
        provider: str,
        name: Optional[str] = None,
        updated_by_user_id: Optional[int] = None,
    ) -> Connection:
        """The connection row for a provider, created on first use."""
        spec = providers.get_provider(provider)
        if spec is None:
            raise ConnectionStoreError(
                f"{provider!r} is not a connection provider this build knows about."
            )
        connection = await self.connections.get_by_provider(provider)
        if connection is None:
            connection = await self.connections.create_connection(
                provider=spec.key,
                name=(name or spec.default_name or spec.label)[:80],
                enabled=True,
                updated_by_user_id=updated_by_user_id,
            )
            logger.info(
                "Created the %s connection %r", spec.label, connection.name
            )
        return connection

    async def put(
        self,
        provider: str,
        key: str,
        value: Optional[str],
        updated_by_user_id: Optional[int] = None,
    ) -> None:
        """Store one setting. An empty value DELETES the row.

        Deleting rather than storing "" keeps "not configured" and "configured
        to nothing" from being the same state, which matters for a chat id the
        page has to be able to say is absent.
        """
        spec = providers.get_provider(provider)
        if spec is None:
            raise ConnectionStoreError(
                f"{provider!r} is not a connection provider this build knows about."
            )
        if key not in spec.known_keys:
            raise ConnectionStoreError(
                f"{key!r} is not a setting of the {spec.label} connection."
            )

        connection = await self.ensure(provider, updated_by_user_id=updated_by_user_id)
        value = (value or "").strip()

        if not value:
            await self.settings.delete_by_key(connection.id, key)
            return

        if spec.is_secret(key):
            if not crypto_service.is_available():
                raise ConnectionStoreError(crypto_service.unavailable_reason())
            await self.settings.upsert(
                connection.id,
                key,
                encrypted_value=crypto_service.encrypt(value),
                is_encrypted=True,
            )
            log_redaction.register_secret(value)
            # The key is humanised ("access_token" -> "access token") because
            # these lines are what an operator greps app.log for. The VALUE is
            # a mask, never the secret.
            logger.info(
                "Stored the %s for the %s connection (%s)",
                key.replace("_", " "), spec.label, crypto_service.mask(value),
            )
        else:
            await self.settings.upsert(connection.id, key, value=value)
            logger.info(
                "Stored the %s for the %s connection",
                key.replace("_", " "), spec.label,
            )

        connection.updated_by_user_id = updated_by_user_id or connection.updated_by_user_id
        await self.session.flush()

    async def put_ciphertext(
        self, provider: str, key: str, ciphertext: str
    ) -> None:
        """Store an ALREADY-ENCRYPTED value without decrypting it first.

        Used by the migration path that moves the Dhan token out of
        `app_settings`: there is no reason to have that token in memory to move
        a row, and decrypting-then-re-encrypting would put it there.
        """
        spec = providers.get_provider(provider)
        if spec is None or not spec.is_secret(key):
            raise ConnectionStoreError(
                f"{key!r} is not an encrypted setting of {provider!r}."
            )
        connection = await self.ensure(provider)
        await self.settings.upsert(
            connection.id, key, encrypted_value=ciphertext, is_encrypted=True
        )

    # --- reporting ---------------------------------------------------------
    async def describe(self, connection: Connection) -> Dict[str, Any]:
        """The safe view of a connection's settings, for an API response.

        A secret is reported as `crypto_service.mask()` plus its presence.
        Nothing here can return one in full.
        """
        spec = providers.get_provider(connection.provider)
        if spec is None:
            return {}
        values = await self.values_for(connection)
        described: Dict[str, Any] = {}
        for key in spec.plain_keys:
            described[key] = values.get(key, "")
        for key in spec.secret_keys:
            present = key in values
            described[key] = {
                "present": present,
                "masked": crypto_service.mask(values[key]) if present else None,
                "decryptFailed": await self.decrypt_failed(spec.key, key),
            }
        return described
