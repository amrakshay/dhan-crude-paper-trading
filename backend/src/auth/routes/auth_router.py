"""Auth endpoints. One login screen's worth of surface area."""
from fastapi import APIRouter, Cookie, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.api_schemas.auth_schemas import LoginRequest, LogoutResponse, SessionResponse
from src.auth.controllers.auth_controller import AuthController
from src.auth.dependencies import (
    SessionPrincipal,
    require_session_allow_password_change,
)
from src.core.singleton_utils import SingletonDepends
from src.database.session import get_async_session

auth_router = APIRouter(prefix="/auth", tags=["Auth"])


async def get_auth_controller(
    session: AsyncSession = Depends(get_async_session),
) -> AuthController:
    return SingletonDepends(AuthController, called_inside_fastapi_depends=True)(session)


@auth_router.post("/login", response_model=SessionResponse)
async def login(
    login_data: LoginRequest,
    response: Response,
    controller: AuthController = Depends(get_auth_controller),
) -> SessionResponse:
    """Exchange an email and password for a session cookie.

    Authenticates against the users table. `APP_USERNAME` / `APP_PASSWORD` no
    longer authenticate anyone.
    """
    return await controller.login(login_data, response)


@auth_router.post("/logout", response_model=LogoutResponse)
async def logout(
    response: Response,
    controller: AuthController = Depends(get_auth_controller),
) -> LogoutResponse:
    """Clear the session cookie."""
    return controller.logout(response)


@auth_router.get("/me", response_model=SessionResponse)
async def current_session(
    principal: SessionPrincipal = Depends(require_session_allow_password_change),
    session_cookie: str = Cookie(default="", alias="dcpt_session"),
    controller: AuthController = Depends(get_auth_controller),
) -> SessionResponse:
    """Who am I, what may I see, and when does this session expire.

    Uses the permissive session dependency so the browser can still learn it
    owes a password change, rather than being locked out of finding out.
    """
    return controller.current_session(principal, session_cookie)
