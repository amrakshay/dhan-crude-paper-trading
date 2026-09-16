"""Authentication against the users table.

**`APP_USERNAME` / `APP_PASSWORD` no longer authenticate anyone.** The users
table is the only source of identity. That code path was removed rather than
left as a fallback: two authentication paths is exactly the kind of thing that
rots, and an env-var backdoor that outlives the table it was meant to replace
is worse than no backdoor at all.

Login is **by email**. The session JWT carries the user id and role alongside
the email, so an authorisation decision costs no extra lookup -- but the role
is re-read from the database on every request anyway (see `dependencies.py`),
because a token issued before a demotion must not keep admin rights.
"""
import secrets
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

import jwt

from src import config_utils
from src.core.time_utils import UTC, utc_now
from src.logging_config import get_logger

# auth and users depend on each other: users' routes gate on auth's
# dependencies, and auth authenticates against users' table. The cycle is
# broken by keeping every users import in THIS package function-scoped, the
# same way orders <-> positions is handled (see backend/CLAUDE.md section 1).
if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.users.database.db_models.user_model import User
    from src.users.database.db_operations.user_repository import UserRepository

logger = get_logger("auth.service")

_EPHEMERAL_SECRET: Optional[str] = None

CLAIM_USER_ID = "uid"
CLAIM_ROLE = "role"
CLAIM_MUST_CHANGE_PASSWORD = "mcp"


class AuthError(Exception):
    """Raised when credentials are wrong or a token is unusable."""


class AccountInactiveError(AuthError):
    """The credentials were right but the account is deactivated."""


class AuthService:
    # --- configuration -----------------------------------------------------
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

    # --- authentication ----------------------------------------------------
    @classmethod
    async def authenticate(
        cls, repository: "UserRepository", email: str, password: str
    ) -> "User":
        """Verify an email/password pair and return the user.

        Raises AuthError for an unknown email or a wrong password -- the same
        message for both, so the response cannot be used to enumerate accounts.
        """
        from src.users.services import password_service

        user = await repository.get_by_email(email)

        if user is None:
            # Still spend the hashing time, so a missing account and a wrong
            # password take comparably long and cannot be told apart by timing.
            password_service.verify_password(password, password_service.dummy_hash())
            raise AuthError("Invalid email or password")

        if not password_service.verify_password(password, user.password_hash):
            raise AuthError("Invalid email or password")

        if not user.is_active:
            # Said plainly: this one is not a guess about whether the account
            # exists, the caller has already proved they hold its password.
            raise AccountInactiveError(
                "This account is deactivated. Ask an administrator to "
                "reactivate it."
            )

        return user

    # --- tokens ------------------------------------------------------------
    @classmethod
    def issue_token(cls, user: "User") -> str:
        issued_at = utc_now().replace(tzinfo=UTC)
        expires_at = issued_at + timedelta(hours=cls.session_hours())
        payload = {
            "sub": user.email,
            CLAIM_USER_ID: user.id,
            CLAIM_ROLE: user.role,
            CLAIM_MUST_CHANGE_PASSWORD: bool(user.must_change_password),
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
