"""Auth endpoints. One login screen's worth of surface area."""
from fastapi import APIRouter, Cookie, Depends, Response

from src.auth.api_schemas.auth_schemas import LoginRequest, LogoutResponse, SessionResponse
from src.auth.controllers.auth_controller import AuthController
from src.auth.dependencies import require_session
from src.core.singleton_utils import SingletonDepends

auth_router = APIRouter(prefix="/auth", tags=["Auth"])


def get_auth_controller() -> AuthController:
    return SingletonDepends(AuthController, called_inside_fastapi_depends=True)()


@auth_router.post("/login", response_model=SessionResponse)
async def login(
    login_data: LoginRequest,
    response: Response,
    controller: AuthController = Depends(get_auth_controller),
) -> SessionResponse:
    """Exchange the configured username/password for a session cookie."""
    return controller.login(login_data, response)


@auth_router.post("/logout", response_model=LogoutResponse)
async def logout(
    response: Response,
    controller: AuthController = Depends(get_auth_controller),
) -> LogoutResponse:
    """Clear the session cookie."""
    return controller.logout(response)


@auth_router.get("/me", response_model=SessionResponse)
async def current_session(
    username: str = Depends(require_session),
    session_cookie: str = Cookie(default="", alias="dcpt_session"),
    controller: AuthController = Depends(get_auth_controller),
) -> SessionResponse:
    """Who am I, and when does this session expire."""
    return controller.current_session(username, session_cookie)
