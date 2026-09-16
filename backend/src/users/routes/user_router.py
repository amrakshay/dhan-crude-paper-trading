"""User management endpoints.

Read is open to any authenticated role; every write needs
`ROLE_ACCOUNT_ADMIN`. The profile endpoints are the exception -- they act on
the caller's own row and need no role at all.

The role gate is on the route, not in the controller, so the rule is visible in
the signature and in the generated OpenAPI. **Hiding a nav item is not access
control**: these dependencies are what actually stop a ROLE_USER, whatever the
sidebar shows.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import (
    SessionPrincipal,
    require_admin,
    require_session,
    require_session_allow_password_change,
)
from src.core.singleton_utils import SingletonDepends
from src.database.session import get_async_session
from src.users.api_schemas.user_schemas import (
    ChangePasswordRequest,
    ChangePasswordResponse,
    CreateUserRequest,
    DeleteUserResponse,
    RolePagesResponse,
    UpdateProfileRequest,
    UpdateUserRequest,
    UserListResponse,
    UserResponse,
)
from src.users.controllers.user_controller import UserController

user_router = APIRouter(prefix="/users", tags=["Users"])


async def get_user_controller(
    session: AsyncSession = Depends(get_async_session),
) -> UserController:
    return SingletonDepends(UserController, called_inside_fastapi_depends=True)(session)


# --- own account ------------------------------------------------------------
# Declared before /{user_id} so "me" and "role-pages" are not swallowed by the
# path parameter.


@user_router.get("/me", response_model=UserResponse)
async def get_own_profile(
    controller: UserController = Depends(get_user_controller),
    principal: SessionPrincipal = Depends(require_session_allow_password_change),
) -> UserResponse:
    """Your own details. Reachable while a password change is still owed, so
    the change-password screen can show who is signed in."""
    return await controller.me(principal)


@user_router.put("/me", response_model=UserResponse)
async def update_own_profile(
    request: UpdateProfileRequest,
    controller: UserController = Depends(get_user_controller),
    principal: SessionPrincipal = Depends(require_session),
) -> UserResponse:
    """Update your own name. Email can never change; role and status are not
    yours to set."""
    return await controller.update_profile(request, principal)


@user_router.post("/me/password", response_model=ChangePasswordResponse)
async def change_own_password(
    request: ChangePasswordRequest,
    controller: UserController = Depends(get_user_controller),
    principal: SessionPrincipal = Depends(require_session_allow_password_change),
) -> ChangePasswordResponse:
    """Change your own password, proving you know the current one.

    Uses the permissive session dependency on purpose: this is the one thing a
    user owing a password change must be able to reach.
    """
    return await controller.change_password(request, principal)


@user_router.get("/role-pages", response_model=RolePagesResponse)
async def get_role_pages(
    controller: UserController = Depends(get_user_controller),
    principal: SessionPrincipal = Depends(require_session),
) -> RolePagesResponse:
    """Which pages your role may see, from conf/role-pages.json.

    Drives the sidebar and the client-side routes. Advisory only -- the API
    enforces the same rules itself.
    """
    return controller.role_pages(principal)


# --- administration ---------------------------------------------------------


@user_router.get("", response_model=UserListResponse)
async def list_users(
    controller: UserController = Depends(get_user_controller),
    principal: SessionPrincipal = Depends(require_session),
) -> UserListResponse:
    """Every user. Any role may look; only an admin may change."""
    return await controller.list_users(principal)


@user_router.post("", response_model=UserResponse, status_code=201)
async def create_user(
    request: CreateUserRequest,
    controller: UserController = Depends(get_user_controller),
    principal: SessionPrincipal = Depends(require_admin),
) -> UserResponse:
    """Create a user. Admin only."""
    return await controller.create_user(request, principal)


@user_router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    controller: UserController = Depends(get_user_controller),
    principal: SessionPrincipal = Depends(require_session),
) -> UserResponse:
    return await controller.get_user(user_id, principal)


@user_router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    request: UpdateUserRequest,
    controller: UserController = Depends(get_user_controller),
    principal: SessionPrincipal = Depends(require_admin),
) -> UserResponse:
    """Update a user. Admin only. Email is never updatable.

    The seeded administrator's role and status are refused as well as its
    deletion -- demoting an undeletable admin would leave no way back.
    """
    return await controller.update_user(user_id, request, principal)


@user_router.delete("/{user_id}", response_model=DeleteUserResponse)
async def delete_user(
    user_id: int,
    controller: UserController = Depends(get_user_controller),
    principal: SessionPrincipal = Depends(require_admin),
) -> DeleteUserResponse:
    """Delete a user. Admin only.

    Refused for the seeded administrator, for your own account, and for the
    last active administrator.
    """
    return await controller.delete_user(user_id, principal)
