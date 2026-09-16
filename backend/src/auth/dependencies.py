"""Session and role dependencies that gate every protected endpoint.

`require_session` now returns a `SessionPrincipal` rather than a username
string, and **re-reads the user from the database on every request**. That
costs one indexed lookup per call and buys three things a token alone cannot:

* deactivating a user ends their session on the *next request*, rather than
  letting it run to token expiry;
* demoting a user takes effect immediately, instead of when their token
  happens to expire;
* deleting a user invalidates their session.

For a single-operator tool the lookup is free in practice, and a stale
authorisation decision is the kind of bug nobody notices until it matters.
"""
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from fastapi import Cookie, Depends, HTTPException, Query, WebSocket, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.services.auth_service import AuthError, AuthService
from src.constants import UserRole
from src.database.session import get_async_session
from src.logging_config import get_logger

# See the note in auth_service.py: the users package is imported inside
# functions here, because its routers gate on these dependencies.
if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.users.database.db_models.user_model import User

logger = get_logger("auth.dependencies")

# Sent as the `code` on the 403 a user gets while their password is unchanged,
# so the browser can route to the change-password screen instead of showing a
# generic "forbidden".
PASSWORD_CHANGE_REQUIRED = "PASSWORD_CHANGE_REQUIRED"


@dataclass(frozen=True)
class SessionPrincipal:
    """Who is making this request. Built fresh from the database each time."""

    user_id: int
    email: str
    role: str
    first_name: str
    last_name: str
    must_change_password: bool
    is_seed_user: bool

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ACCOUNT_ADMIN.value

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def __str__(self) -> str:
        # Used in log lines, so it reads as an identity rather than a dataclass.
        return f"{self.email} ({self.role})"

    @classmethod
    def from_user(cls, user: "User") -> "SessionPrincipal":
        return cls(
            user_id=user.id,
            email=user.email,
            role=user.role,
            first_name=user.first_name,
            last_name=user.last_name,
            must_change_password=bool(user.must_change_password),
            is_seed_user=bool(user.is_seed_user),
        )


def _unauthorised(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


async def resolve_principal(token: Optional[str], session: AsyncSession) -> SessionPrincipal:
    """Validate a token and load the live user behind it.

    Shared by the HTTP and WebSocket paths so the two can never drift -- there
    were previously two hand-written copies of this logic.
    """
    from src.users.database.db_operations.user_repository import UserRepository

    if not token:
        raise _unauthorised("Not authenticated")

    try:
        payload = AuthService.decode_token(token)
    except AuthError as exc:
        raise _unauthorised(str(exc)) from exc

    user_id = payload.get("uid")
    if user_id is None:
        # A token issued before the users module existed.
        raise _unauthorised("Session is no longer valid; sign in again")

    user = await UserRepository(session).get_by_id(int(user_id))
    if user is None:
        logger.warning(
            "Session token for deleted user id=%s rejected", user_id
        )
        raise _unauthorised("This account no longer exists")

    if not user.is_active:
        logger.warning(
            "Session token for deactivated user %s rejected", user.email
        )
        raise _unauthorised("This account has been deactivated")

    return SessionPrincipal.from_user(user)


async def require_session_allow_password_change(
    session_cookie: str = Cookie(default=None, alias="dcpt_session"),
    session: AsyncSession = Depends(get_async_session),
) -> SessionPrincipal:
    """Authenticated, but tolerant of an unchanged password.

    Only the handful of endpoints a user must reach *in order to* change it
    depend on this: /auth/me, /auth/logout and the change-password call itself.
    """
    return await resolve_principal(session_cookie, session)


async def require_session(
    principal: SessionPrincipal = Depends(require_session_allow_password_change),
) -> SessionPrincipal:
    """Authenticated, active, and not owing a password change.

    A user created with `must_change_password` can reach nothing else until it
    is cleared -- enforced here rather than by the UI, so it holds against a
    direct API call too.
    """
    if principal.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "You must change your password before using the application."
            ),
            headers={"X-Auth-Action": PASSWORD_CHANGE_REQUIRED},
        )
    return principal


def require_role(*roles: str):
    """Dependency factory: allow only these roles.

    Returned as a dependency rather than checked inside each controller so the
    rule is visible in the route signature and in the generated OpenAPI.
    """
    allowed = {role.value if hasattr(role, "value") else str(role) for role in roles}

    async def _dependency(
        principal: SessionPrincipal = Depends(require_session),
    ) -> SessionPrincipal:
        if principal.role not in allowed:
            logger.warning(
                "Refused %s to %s: requires one of %s",
                principal.email, principal.role, sorted(allowed),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Your role does not have access to this. "
                    f"Required: {', '.join(sorted(allowed))}."
                ),
            )
        return principal

    return _dependency


# The common case, named so route signatures read as the rule they express.
require_admin = require_role(UserRole.ACCOUNT_ADMIN.value)


async def require_websocket_session(
    websocket: WebSocket,
    token: str = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
) -> SessionPrincipal:
    """WebSocket variant.

    Browsers do send cookies on same-origin WebSocket handshakes, so the cookie
    is tried first; `?token=` is the fallback for cross-origin dev (Vite on
    :5173 talking to the API on :8000).
    """
    candidate = websocket.cookies.get(AuthService.cookie_name()) or token
    return await resolve_principal(candidate, session)


AuthenticatedUser = Depends(require_session)
