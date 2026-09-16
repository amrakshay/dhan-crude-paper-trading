"""Single-credential authentication.

There is no user table, no registration and no password reset: this is a
single-user local tool. One username/password pair comes from the environment
(via conf/*.yaml `auth.*`), and a signed JWT is handed back in an httpOnly
cookie.
"""
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Optional

import jwt

from src import config_utils
from src.core.time_utils import UTC, utc_now
from src.logging_config import get_logger

logger = get_logger("auth.service")

_EPHEMERAL_SECRET: Optional[str] = None


class AuthError(Exception):
    """Raised when credentials are wrong or a token is unusable."""


class AuthService:
    @staticmethod
    def _configured_username() -> str:
        return config_utils.get_property_value("auth.username", "trader")

    @staticmethod
    def _configured_password() -> str:
        return config_utils.get_property_value("auth.password", "") or ""

    @staticmethod
    def jwt_secret() -> str:
        """Signing key. Falls back to a per-process random key so the app still
        runs unconfigured -- at the cost of invalidating sessions on restart."""
        global _EPHEMERAL_SECRET
        configured = config_utils.get_property_value("auth.jwt_secret", "") or ""
        if configured:
            return configured
        if _EPHEMERAL_SECRET is None:
            _EPHEMERAL_SECRET = secrets.token_urlsafe(48)
            logger.warning(
                "APP_JWT_SECRET is not set. Using a random per-process secret; "
                "every restart will log you out. Set APP_JWT_SECRET in .env."
            )
        return _EPHEMERAL_SECRET

    @staticmethod
    def algorithm() -> str:
        return config_utils.get_property_value("auth.jwt_algorithm", "HS256")

    @staticmethod
    def session_hours() -> int:
        return config_utils.get_property_value_int("auth.session_hours", 12)

    @staticmethod
    def cookie_name() -> str:
        return config_utils.get_property_value("auth.cookie_name", "dcpt_session")

    @classmethod
    def is_configured(cls) -> bool:
        return bool(cls._configured_password())

    @classmethod
    def authenticate(cls, username: str, password: str) -> str:
        """Validate credentials and return a signed session token."""
        expected_password = cls._configured_password()
        if not expected_password:
            raise AuthError(
                "No password is configured. Set APP_PASSWORD in .env and restart."
            )

        # compare_digest on both fields so a wrong username and a wrong password
        # take the same time.
        username_ok = hmac.compare_digest(username, cls._configured_username())
        password_ok = hmac.compare_digest(password, expected_password)
        if not (username_ok and password_ok):
            # Logged once, by the controller -- it has the request context and
            # is the layer that turns this into a 401.
            raise AuthError("Invalid username or password")

        return cls.issue_token(username)

    @classmethod
    def issue_token(cls, username: str) -> str:
        issued_at = utc_now().replace(tzinfo=UTC)
        expires_at = issued_at + timedelta(hours=cls.session_hours())
        payload = {
            "sub": username,
            "iat": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
        }
        return jwt.encode(payload, cls.jwt_secret(), algorithm=cls.algorithm())

    @classmethod
    def decode_token(cls, token: str) -> dict:
        try:
            return jwt.decode(token, cls.jwt_secret(), algorithms=[cls.algorithm()])
        except jwt.ExpiredSignatureError as exc:
            logger.debug("Rejected an expired session token")
            raise AuthError("Session expired") from exc
        except jwt.InvalidTokenError as exc:
            logger.debug("Rejected an invalid session token: %s", exc)
            raise AuthError("Invalid session") from exc

    @classmethod
    def token_expiry(cls, token: str) -> Optional[datetime]:
        payload = cls.decode_token(token)
        exp = payload.get("exp")
        return datetime.fromtimestamp(exp, tz=UTC) if exp else None
