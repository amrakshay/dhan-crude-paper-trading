"""Auth orchestration: turn service results into HTTP concerns."""
from fastapi import HTTPException, Response, status

from src import config_utils
from src.auth.api_schemas.auth_schemas import LoginRequest, LogoutResponse, SessionResponse
from src.auth.services.auth_service import AuthError, AuthService
from src.logging_config import get_logger

logger = get_logger("auth.controller")


class AuthController:
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

    def login(self, login_data: LoginRequest, response: Response) -> SessionResponse:
        try:
            token = AuthService.authenticate(login_data.username, login_data.password)
        except AuthError as exc:
            logger.warning("Failed login attempt for username=%r", login_data.username)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
            ) from exc

        self._set_cookie(response, token)
        expiry = AuthService.token_expiry(token)
        logger.info("Login succeeded for %s", login_data.username)
        return SessionResponse(
            username=login_data.username,
            expires_at=expiry.isoformat() if expiry else None,
        )

    def logout(self, response: Response) -> LogoutResponse:
        response.delete_cookie(key=AuthService.cookie_name(), path="/")
        return LogoutResponse()

    def current_session(self, username: str, token: str) -> SessionResponse:
        expiry = None
        try:
            expiry = AuthService.token_expiry(token)
        except AuthError:
            pass
        return SessionResponse(
            username=username, expires_at=expiry.isoformat() if expiry else None
        )
