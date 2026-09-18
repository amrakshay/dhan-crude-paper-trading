"""Runtime settings: the synthetic-feed toggle, and the Dhan credentials' home.

**The Dhan credentials moved to `connections` on 2026-09-18.** `client_id` and
`access_token` are now rows of the `dhan` CONNECTION, not of `app_settings`;
the synthetic-feed switch stayed here, because it is a mode of THIS application
rather than a credential and would sit oddly on a card describing a connection
to somebody else.

**This module's public surface did not change**, and that is deliberate.
`apply_to_config()` is called from the lifespan BEFORE the feed starts and its
position is pinned by `tests/test_startup_applies_stored_settings.py`;
`token_refresh_service` renews a token by calling `save()`; every caller of
`resolve_credentials()` is unaffected. Moving the storage without moving the
seam is what keeps all three working, and keeps the startup overlay exactly
where it was.

**Precedence: values saved in the UI (database) override `.env`.** `.env` is the
fallback, which keeps a fresh checkout working before anyone opens the Settings
page.

Rather than changing every `config_utils` call site, the effective settings are
*overlaid onto the in-memory config* at startup and after each save. Everything
that already reads `dhan.client_id`, `dhan.access_token` or
`market_feed.synthetic_feed` therefore picks up the UI value with no further
change.

The access token is stored encrypted (see `crypto_service`); the client id is
stored as plaintext. The token is never returned to the browser -- only a mask,
and metadata derived from it.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import jwt

from src import config_utils, log_redaction
from src.core.time_utils import UTC
from src.logging_config import get_logger
from src.settings.database.db_operations.app_setting_repository import (
    AppSettingRepository,
)
from src.settings.services import crypto_service

logger = get_logger("settings.service")

# Only these keys are read back out of the database and applied to the config.
KEY_CLIENT_ID = "dhan.client_id"
KEY_ACCESS_TOKEN = "dhan.access_token"
KEY_SYNTHETIC_FEED = "market_feed.synthetic_feed"

MANAGED_KEYS = (KEY_CLIENT_ID, KEY_ACCESS_TOKEN, KEY_SYNTHETIC_FEED)
SECRET_KEYS = (KEY_ACCESS_TOKEN,)

# Which of those live in `app_settings` and which in the `dhan` connection.
# The config keys are unchanged either way: what moved is the row, not the name
# every `config_utils` caller reads.
APP_SETTING_KEYS = (KEY_SYNTHETIC_FEED,)
CONNECTION_KEYS = {
    KEY_CLIENT_ID: "client_id",
    KEY_ACCESS_TOKEN: "access_token",
}

# Warn when the stored Dhan token has less than this left. Dhan tokens are
# day-scoped, and a feed that dies mid-session because nobody noticed the
# expiry is exactly the failure the logs should have predicted.
TOKEN_EXPIRY_WARN_SECONDS = 2 * 60 * 60


class SettingsValidationError(Exception):
    pass


@dataclass
class TokenInfo:
    """What can be learned about an access token without contacting Dhan."""

    present: bool = False
    masked: Optional[str] = None
    is_jwt: bool = False
    expires_at: Optional[datetime] = None
    seconds_remaining: Optional[int] = None
    expired: bool = False
    dhan_client_id: Optional[str] = None
    decrypt_failed: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "present": self.present,
            "masked": self.masked,
            "isJwt": self.is_jwt,
            "expiresAt": self.expires_at.isoformat() if self.expires_at else None,
            "secondsRemaining": self.seconds_remaining,
            "expired": self.expired,
            "dhanClientId": self.dhan_client_id,
            "decryptFailed": self.decrypt_failed,
        }


def inspect_token(token: Optional[str]) -> TokenInfo:
    """Read a Dhan access token's expiry without verifying its signature.

    Dhan access tokens are JWTs and carry an `exp` claim, so the real expiry is
    available locally -- no need to assume "24 hours from whenever it was
    pasted". The signature is deliberately not verified: it was issued by Dhan
    with a key we do not hold, and we are reading metadata, not trusting it.
    """
    if not token:
        return TokenInfo(present=False)

    info = TokenInfo(present=True, masked=crypto_service.mask(token))
    try:
        claims = jwt.decode(
            token,
            options={"verify_signature": False, "verify_exp": False, "verify_aud": False},
        )
    except jwt.PyJWTError:
        # Not a JWT (or malformed). Still usable as an opaque token; we just
        # cannot say when it expires.
        return info

    info.is_jwt = True
    expiry = claims.get("exp")
    if expiry is not None:
        try:
            info.expires_at = datetime.fromtimestamp(int(expiry), tz=UTC)
            remaining = info.expires_at - datetime.now(timezone.utc)
            info.seconds_remaining = int(remaining.total_seconds())
            info.expired = info.seconds_remaining <= 0
        except (ValueError, OSError, OverflowError):
            pass

    for claim in ("dhanClientId", "dhan_client_id", "clientId", "sub"):
        if claims.get(claim):
            info.dhan_client_id = str(claims[claim])
            break

    return info


class SettingsService:
    def __init__(self, repository: AppSettingRepository):
        self.repository = repository

    # --- reading -----------------------------------------------------------
    def _connection_store(self):
        """The Dhan connection's settings, on this service's own session."""
        from src.connections.services.connection_store import ConnectionStore

        return ConnectionStore(self.repository.session)

    async def load_stored(self) -> Dict[str, Optional[str]]:
        """Decrypted settings from the database. Missing keys are omitted.

        Two sources now, one dictionary: the synthetic switch out of
        `app_settings`, the credentials out of the `dhan` connection. Callers
        see the same keys they always did.
        """
        resolved: Dict[str, Optional[str]] = {}

        from src.connections.services import providers as connection_providers

        try:
            values = await self._connection_store().values(
                connection_providers.PROVIDER_DHAN
            )
        except Exception:  # noqa: BLE001 - never block startup on a settings read
            logger.exception(
                "Could not read the Dhan connection; falling back to .env for "
                "the credentials"
            )
            values = {}
        for config_key, connection_key in CONNECTION_KEYS.items():
            if values.get(connection_key):
                resolved[config_key] = values[connection_key]

        stored = await self.repository.get_all()
        for key in APP_SETTING_KEYS:
            setting = stored.get(key)
            if setting is None:
                continue
            if setting.is_encrypted:
                plaintext = crypto_service.decrypt(setting.encrypted_value or "")
                if plaintext is None:
                    # Unreadable (key rotated). Fall through to .env rather
                    # than pretending the setting is absent AND unusable.
                    logger.warning(
                        "Stored setting %s could not be decrypted -- most likely "
                        "APP_ENCRYPTION_KEY (or APP_JWT_SECRET) changed since it "
                        "was saved. Falling back to .env; re-enter it on the "
                        "Settings page to fix this.",
                        key,
                    )
                    continue
                resolved[key] = plaintext
                # Never let this value reach a log line, whatever a future call
                # site does with it.
                log_redaction.register_secret(plaintext)
            else:
                resolved[key] = setting.value

        return resolved

    async def decrypt_failed_for(self, key: str) -> bool:
        if key in CONNECTION_KEYS:
            from src.connections.services import providers as connection_providers

            return await self._connection_store().decrypt_failed(
                connection_providers.PROVIDER_DHAN, CONNECTION_KEYS[key]
            )
        setting = await self.repository.get_by_key(key)
        if setting is None or not setting.is_encrypted:
            return False
        return crypto_service.decrypt(setting.encrypted_value or "") is None

    @staticmethod
    def env_fallbacks() -> Dict[str, Optional[str]]:
        """What `.env` alone would give. Used to show the fallback in the UI."""
        import os

        return {
            KEY_CLIENT_ID: os.environ.get("DHAN_CLIENT_ID") or None,
            KEY_ACCESS_TOKEN: os.environ.get("DHAN_ACCESS_TOKEN") or None,
            KEY_SYNTHETIC_FEED: os.environ.get("DHAN_SYNTHETIC_FEED") or None,
        }

    # --- applying ----------------------------------------------------------
    async def apply_to_config(self) -> Dict[str, str]:
        """Overlay stored settings onto the live in-memory config.

        This is what makes the database take priority over `.env` for every
        existing `config_utils` caller.
        """
        stored = await self.load_stored()
        config = config_utils.load_config()
        applied: Dict[str, str] = {}

        for key, value in stored.items():
            if value is None:
                continue
            section, _, leaf = key.partition(".")
            node = config.setdefault(section, {})
            if not isinstance(node, dict):
                continue
            node[leaf] = value
            applied[key] = "set" if key in SECRET_KEYS else str(value)
            logger.debug(
                "Applied setting %s = %s (database overrides .env)",
                key,
                crypto_service.mask(value) if key in SECRET_KEYS else value,
            )

        if applied:
            logger.info(
                "Applied %s setting(s) from the database over .env: %s",
                len(applied), ", ".join(sorted(applied)),
            )
        self._warn_if_token_expiring(stored.get(KEY_ACCESS_TOKEN))
        return applied

    @staticmethod
    def _warn_if_token_expiring(token: Optional[str]) -> None:
        """Say so while there is still time to replace the token."""
        if not token:
            return
        info = inspect_token(token)
        if info.seconds_remaining is None:
            return
        if info.expired:
            logger.warning(
                "The stored Dhan access token expired at %s. The live feed will "
                "be rejected until a new token is saved on the Settings page.",
                info.expires_at.isoformat() if info.expires_at else "an unknown time",
            )
        elif info.seconds_remaining < TOKEN_EXPIRY_WARN_SECONDS:
            hours, minutes = divmod(info.seconds_remaining // 60, 60)
            logger.warning(
                "The stored Dhan access token expires in %sh %sm (at %s). "
                "Generate a new one in the Dhan console before it lapses.",
                hours, minutes,
                info.expires_at.isoformat() if info.expires_at else "?",
            )

    # --- writing -----------------------------------------------------------
    async def save(
        self,
        client_id: Optional[str],
        access_token: Optional[str],
        synthetic_feed: bool,
        clear_access_token: bool = False,
    ) -> Dict[str, Any]:
        """Persist settings, then overlay them onto the running config.

        `access_token` of None or "" leaves the stored token untouched, so the
        UI can save other fields without ever round-tripping the secret through
        the browser. `clear_access_token` removes it explicitly.
        """
        synthetic_feed = bool(synthetic_feed)
        client_id = (client_id or "").strip()
        access_token = (access_token or "").strip()
        logger.debug(
            "Settings save requested: syntheticFeed=%s clientIdProvided=%s "
            "accessTokenProvided=%s clearAccessToken=%s",
            synthetic_feed, bool(client_id), bool(access_token), clear_access_token,
        )

        # Live credentials are only required when NOT running synthetically.
        if not synthetic_feed:
            effective_client_id = client_id or (await self.load_stored()).get(KEY_CLIENT_ID)
            effective_token = access_token
            if not effective_token and not clear_access_token:
                effective_token = (await self.load_stored()).get(KEY_ACCESS_TOKEN)
            if not effective_client_id or not effective_token:
                # The refusal SURVIVED the move to Connections, deliberately:
                # live mode with no credentials means the feed errors out, and
                # this application must never invent prices in its place. What
                # changed is where the operator is sent to fix it.
                raise SettingsValidationError(
                    "A Dhan client ID and access token are required when the "
                    "synthetic feed is off. They now live on the Connections "
                    "page -- set them there, or turn the synthetic feed on."
                )

        await self.repository.upsert(
            KEY_SYNTHETIC_FEED, value="true" if synthetic_feed else "false"
        )

        # The credentials are rows of the `dhan` CONNECTION now. The store owns
        # encryption, the value-XOR-encrypted_value invariant and registering
        # the token with the log redactor, exactly as this method used to.
        from src.connections.services import providers as connection_providers
        from src.connections.services.connection_store import ConnectionStoreError

        store = self._connection_store()
        try:
            if client_id:
                await store.put(
                    connection_providers.PROVIDER_DHAN,
                    connection_providers.DHAN_CLIENT_ID,
                    client_id,
                )
            if clear_access_token:
                logger.info(
                    "Clearing the stored Dhan access token at the operator's request"
                )
                await store.put(
                    connection_providers.PROVIDER_DHAN,
                    connection_providers.DHAN_ACCESS_TOKEN,
                    "",
                )
            elif access_token:
                await store.put(
                    connection_providers.PROVIDER_DHAN,
                    connection_providers.DHAN_ACCESS_TOKEN,
                    access_token,
                )
        except ConnectionStoreError as error:
            raise SettingsValidationError(str(error)) from error

        await self.repository.session.commit()
        applied = await self.apply_to_config()
        logger.info(
            "Settings saved (syntheticFeed=%s, clientId=%s, tokenChanged=%s)",
            synthetic_feed, bool(client_id), bool(access_token) or clear_access_token,
        )
        return applied

    # --- effective view ----------------------------------------------------
    async def effective(self) -> Dict[str, Any]:
        """What the application is actually using, and where it came from."""
        stored = await self.load_stored()
        env = self.env_fallbacks()

        def source(key: str) -> str:
            if key in stored and stored[key] not in (None, ""):
                return "database"
            if env.get(key):
                return "env"
            return "default"

        client_id = stored.get(KEY_CLIENT_ID) or env.get(KEY_CLIENT_ID) or ""
        token = stored.get(KEY_ACCESS_TOKEN) or env.get(KEY_ACCESS_TOKEN) or ""

        raw_synthetic = stored.get(KEY_SYNTHETIC_FEED)
        if raw_synthetic is None:
            raw_synthetic = env.get(KEY_SYNTHETIC_FEED)
        synthetic = (
            True
            if raw_synthetic is None
            else str(raw_synthetic).strip().lower() in ("true", "1", "yes", "y", "on")
        )

        token_info = inspect_token(token)
        token_info.decrypt_failed = await self.decrypt_failed_for(KEY_ACCESS_TOKEN)

        return {
            "clientId": client_id,
            "clientIdSource": source(KEY_CLIENT_ID),
            "syntheticFeed": synthetic,
            "syntheticFeedSource": source(KEY_SYNTHETIC_FEED),
            "accessTokenSource": source(KEY_ACCESS_TOKEN),
            "token": token_info.as_dict(),
            "encryptionAvailable": crypto_service.is_available(),
            "encryptionWarning": (
                None if crypto_service.is_available() else crypto_service.unavailable_reason()
            ),
        }

    # --- validation --------------------------------------------------------
    async def validate_credentials(
        self,
        client_id: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Check a credential pair against Dhan, without saving it.

        Validation issues a real market-data request -- the option chain expiry
        list for the configured underlying. That is the lightest authenticated
        call available and is already on this application's market-data
        allowlist, so validating can never touch a trading endpoint.

        When `client_id` / `access_token` are omitted the credentials currently
        in force are validated instead.
        """
        from src.market.services.dhan_option_chain_client import (
            DhanOptionChainClient,
            OptionChainError,
            OptionChainRateLimited,
        )

        if not client_id or not access_token:
            resolved_id, resolved_token = await self.resolve_credentials()
            client_id = client_id or resolved_id
            access_token = access_token or resolved_token

        # A token the operator has TYPED IN and not yet saved has never been
        # through the store, so it has never been registered with the log
        # redactor. Dhan sends it as a header rather than in the URL, so this
        # is not the leak the Telegram path had -- but registering it costs
        # nothing and closes the same class of gap for the same reason.
        log_redaction.register_secret(access_token)

        token_info = inspect_token(access_token)
        result: Dict[str, Any] = {
            "valid": False,
            "checkedAt": datetime.now(timezone.utc).isoformat(),
            "token": token_info.as_dict(),
            "message": "",
            "detail": None,
        }

        if not client_id or not access_token:
            result["message"] = "Enter both a client ID and an access token to validate."
            return result

        # A locally-detectable expiry is worth reporting before spending a
        # network round trip on a token that cannot possibly work.
        if token_info.expired:
            result["message"] = (
                "This access token has already expired. Generate a new one in "
                "the Dhan web console and paste it here."
            )
            return result

        if (
            token_info.dhan_client_id
            and client_id
            and token_info.dhan_client_id != client_id
        ):
            result["message"] = (
                f"The token was issued for client ID {token_info.dhan_client_id}, "
                f"but {client_id} was entered. Check the client ID."
            )
            return result

        # The probe is a cheap authenticated read against whichever underlying
        # is actually configured, so it stays correct for a strategy that is
        # not CRUDEOIL.
        from src.strategies.services.strategy_registry import get_strategy_registry

        registry = get_strategy_registry()
        running = registry.enabled()
        probe_strategy = running[0] if running else registry.default()
        underlying_scrip = probe_strategy.underlying_scrip
        underlying_segment = probe_strategy.exchange_segment
        client = DhanOptionChainClient(client_id=client_id, access_token=access_token)

        try:
            expiries = await client.fetch_expiry_list(underlying_scrip, underlying_segment)
        except OptionChainRateLimited:
            result["message"] = (
                "Dhan rate limited the check (one request per 3 seconds). "
                "Wait a moment and try again."
            )
            return result
        except OptionChainError as exc:
            detail = str(exc)
            result["detail"] = detail
            logger.warning(
                "Dhan credential validation failed for client id %s: %s",
                client_id, detail[:300],
            )
            if "401" in detail or "invalid" in detail.lower() or "unauthor" in detail.lower():
                result["message"] = (
                    "Dhan rejected these credentials. Check the client ID and "
                    "regenerate the access token."
                )
            elif "403" in detail:
                result["message"] = (
                    "Dhan accepted the token but refused the request. The Data "
                    "APIs subscription may not be active on this account."
                )
            else:
                result["message"] = f"Could not validate against Dhan: {detail[:200]}"
            return result
        except Exception as exc:  # network failures, DNS, timeouts
            logger.exception(
                "Could not reach Dhan while validating credentials for client id %s",
                client_id,
            )
            result["detail"] = str(exc)
            result["message"] = f"Could not reach Dhan: {exc}"
            return result

        result["valid"] = True
        result["expiries"] = expiries[:5]
        logger.info(
            "Dhan credentials validated for client id %s: %s expiries returned",
            client_id, len(expiries),
        )
        remaining = token_info.seconds_remaining
        if remaining is not None:
            hours, minutes = divmod(max(0, remaining) // 60, 60)
            result["message"] = (
                f"Credentials are valid. Dhan returned {len(expiries)} expiries. "
                f"This token expires in {hours}h {minutes}m."
            )
        else:
            result["message"] = (
                f"Credentials are valid. Dhan returned {len(expiries)} expiries."
            )
        return result

    async def resolve_credentials(self) -> tuple[str, str]:
        """The credentials in force (database first, then .env)."""
        stored = await self.load_stored()
        env = self.env_fallbacks()
        return (
            stored.get(KEY_CLIENT_ID) or env.get(KEY_CLIENT_ID) or "",
            stored.get(KEY_ACCESS_TOKEN) or env.get(KEY_ACCESS_TOKEN) or "",
        )
