"""Auth orchestration: turn service results into HTTP concerns."""
from typing import TYPE_CHECKING

from fastapi import HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src import config_utils
from src.auth.api_schemas.auth_schemas import LoginRequest, LogoutResponse, SessionResponse
from src.auth.dependencies import SessionPrincipal
from src.auth.services.auth_service import AccountInactiveError, AuthError, AuthService
from src.constants import UserStatus
from src.logging_config import get_logger

# See the note in auth_service.py -- the users package is imported inside
# functions to keep auth free of a module-scope dependency on it.
if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.users.database.db_models.user_model import User

logger = get_logger("auth.controller")


class AuthController:
    def __init__(self, session: AsyncSession):
        from src.users.database.db_operations.user_repository import UserRepository
        from src.users.services.user_service import UserService

        self.session = session
        self.repository = UserRepository(session)
        self.service = UserService(self.repository)

    def _set_cookie(self, response: Response, token: str) -> None:
        response.set_cookie(
            key=AuthService.cookie_name(),
            value=token,
            httponly=True,
            secure=config_utils.get_property_value_boolean("auth.cookie_secure", False),
            samesite=config_utils.get_property_value("auth.cookie_samesite", "lax"),
            max_age=AuthService.session_hours() * 3600,
            path="/",
        )

    async def login(self, login_data: LoginRequest, response: Response) -> SessionResponse:
        try:
            user = await AuthService.authenticate(
                self.repository, login_data.email, login_data.password
            )
        except AccountInactiveError as exc:
            # A deactivated account is refused even with the right password.
            logger.warning(
                "Login refused for deactivated account %r", login_data.email
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
            ) from exc
        except AuthError as exc:
            # The attempted email is logged; the password never is.
            logger.warning(
                "Failed login attempt for email=%r: %s", login_data.email, exc
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
            ) from exc

        token = AuthService.issue_token(user)
        await self.service.record_login(user)
        await self.session.commit()

        self._set_cookie(response, token)
        expiry = AuthService.token_expiry(token)
        logger.info(
            "Login succeeded for %s (%s); session valid for %sh%s",
            user.email, user.role, AuthService.session_hours(),
            " -- password change required before anything else is reachable"
            if user.must_change_password else "",
        )
        return self._session_response(user, expiry.isoformat() if expiry else None)

    def logout(self, response: Response) -> LogoutResponse:
        response.delete_cookie(key=AuthService.cookie_name(), path="/")
        logger.info("Session cookie cleared on logout")
        return LogoutResponse()

    def current_session(
        self, principal: SessionPrincipal, token: str
    ) -> SessionResponse:
        from src.users.services.role_service import pages_for_role

        expiry = None
        try:
            expiry = AuthService.token_expiry(token)
        except AuthError:
            pass
        return SessionResponse(
            email=principal.email,
            authenticated=True,
            user_id=principal.user_id,
            first_name=principal.first_name,
            last_name=principal.last_name,
            full_name=principal.full_name,
            role=principal.role,
            status=UserStatus.ACTIVE.value,
            must_change_password=principal.must_change_password,
            is_seed_user=principal.is_seed_user,
            pages=pages_for_role(principal.role),
            expires_at=expiry.isoformat() if expiry else None,
        )

    @staticmethod
    def _session_response(user: "User", expires_at) -> SessionResponse:
        from src.users.services.role_service import pages_for_role

        return SessionResponse(
            email=user.email,
            authenticated=True,
            user_id=user.id,
            first_name=user.first_name,
            last_name=user.last_name,
            full_name=user.full_name,
            role=user.role,
            status=user.status,
            must_change_password=bool(user.must_change_password),
            is_seed_user=bool(user.is_seed_user),
            pages=pages_for_role(user.role),
            expires_at=expires_at,
        )
