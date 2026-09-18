"""User orchestration: turn service results and exceptions into HTTP."""
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import SessionPrincipal
from src.core.time_utils import to_ist
from src.logging_config import get_logger
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
from src.users.database.db_models.user_model import User
from src.users.database.db_operations.user_repository import (
    UserRepository,
    normalise_email,
)
from src.users.services.role_service import pages_for_role, role_catalogue
from src.users.services.user_service import UserService, UserValidationError

logger = get_logger("users.controller")


def _iso(value) -> Optional[str]:
    return to_ist(value).isoformat() if value else None


def to_response(user: User, acting_user_id: Optional[int] = None) -> UserResponse:
    """Shape a user for the wire.

    Built field by field rather than from the ORM object wholesale: that is
    what makes it impossible for `password_hash` to be returned by accident.
    """
    immutable_reason = None
    can_be_deleted = True
    can_be_edited = True

    if user.is_seed_user:
        can_be_deleted = False
        immutable_reason = (
            "The default administrator cannot be deleted, demoted or "
            "deactivated -- doing so could leave nobody able to administer the "
            "application."
        )
    elif acting_user_id is not None and user.id == acting_user_id:
        can_be_deleted = False
        immutable_reason = "You cannot delete your own account."

    return UserResponse(
        id=user.id,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        full_name=user.full_name,
        role=user.role,
        status=user.status,
        must_change_password=bool(user.must_change_password),
        is_seed_user=bool(user.is_seed_user),
        telegram_user_id=user.telegram_user_id,
        last_login_at=_iso(user.last_login_at),
        created_at=_iso(user.created_at),
        can_be_deleted=can_be_deleted,
        can_be_edited=can_be_edited,
        immutable_reason=immutable_reason,
    )


class UserController:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repository = UserRepository(session)
        self.service = UserService(self.repository)

    # --- reading -----------------------------------------------------------
    async def list_users(self, principal: SessionPrincipal) -> UserListResponse:
        """Every user. Readable by any role -- only changing is restricted."""
        users = await self.service.list_users()
        return UserListResponse(
            users=[to_response(user, principal.user_id) for user in users],
            total=len(users),
        )

    async def get_user(self, user_id: int, principal: SessionPrincipal) -> UserResponse:
        try:
            user = await self.service.get_user(user_id)
        except UserValidationError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return to_response(user, principal.user_id)

    async def me(self, principal: SessionPrincipal) -> UserResponse:
        user = await self.service.get_user(principal.user_id)
        return to_response(user, principal.user_id)

    def role_pages(self, principal: SessionPrincipal) -> RolePagesResponse:
        return RolePagesResponse(
            role=principal.role,
            pages=pages_for_role(principal.role),
            all_roles=role_catalogue(),
        )

    # --- writing -----------------------------------------------------------
    async def create_user(
        self, request: CreateUserRequest, principal: SessionPrincipal
    ) -> UserResponse:
        try:
            user = await self.service.create_user(
                email=request.email,
                first_name=request.first_name,
                last_name=request.last_name,
                password=request.password,
                role=request.role,
                status=request.status,
                must_change_password=request.must_change_password,
            )
        except UserValidationError as exc:
            logger.warning(
                "User creation refused for %r (requested by %s): %s",
                request.email, principal.email, exc,
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        user_id = user.id
        await self.session.commit()
        self.session.expire_all()
        refreshed = await self.repository.get_by_id(user_id)
        return to_response(refreshed, principal.user_id)

    async def update_user(
        self, user_id: int, request: UpdateUserRequest, principal: SessionPrincipal
    ) -> UserResponse:
        existing = await self.repository.get_by_id(user_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"User {user_id} not found")

        self._reject_email_change(request.email, existing, principal)

        try:
            await self.service.update_user(
                user_id=user_id,
                acting_user_id=principal.user_id,
                first_name=request.first_name,
                last_name=request.last_name,
                role=request.role,
                status=request.status,
                password=request.password,
                must_change_password=request.must_change_password,
                telegram_user_id=request.telegram_user_id,
                clear_telegram_user_id=request.clear_telegram_user_id,
            )
        except UserValidationError as exc:
            logger.warning(
                "Update refused for user %s (requested by %s): %s",
                user_id, principal.email, exc,
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        await self.session.commit()
        self.session.expire_all()
        refreshed = await self.repository.get_by_id(user_id)
        return to_response(refreshed, principal.user_id)

    async def update_profile(
        self, request: UpdateProfileRequest, principal: SessionPrincipal
    ) -> UserResponse:
        existing = await self.repository.get_by_id(principal.user_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Your account no longer exists")

        self._reject_email_change(request.email, existing, principal)

        try:
            await self.service.update_own_profile(
                user_id=principal.user_id,
                first_name=request.first_name,
                last_name=request.last_name,
            )
        except UserValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        await self.session.commit()
        self.session.expire_all()
        refreshed = await self.repository.get_by_id(principal.user_id)
        return to_response(refreshed, principal.user_id)

    async def change_password(
        self, request: ChangePasswordRequest, principal: SessionPrincipal
    ) -> ChangePasswordResponse:
        try:
            await self.service.change_own_password(
                user_id=principal.user_id,
                current_password=request.current_password,
                new_password=request.new_password,
            )
        except UserValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        await self.session.commit()
        self.session.expire_all()
        refreshed = await self.repository.get_by_id(principal.user_id)
        return ChangePasswordResponse(
            changed=True,
            message="Password changed",
            user=to_response(refreshed, principal.user_id),
        )

    async def delete_user(
        self, user_id: int, principal: SessionPrincipal
    ) -> DeleteUserResponse:
        try:
            await self.service.delete_user(user_id, acting_user_id=principal.user_id)
        except UserValidationError as exc:
            status_code = 404 if "not found" in str(exc).lower() else 400
            logger.warning(
                "Delete refused for user %s (requested by %s): %s",
                user_id, principal.email, exc,
            )
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

        await self.session.commit()
        return DeleteUserResponse(deleted=True, message="User deleted")

    # --- rules -------------------------------------------------------------
    @staticmethod
    def _reject_email_change(
        submitted: Optional[str], existing: User, principal: SessionPrincipal
    ) -> None:
        """Email is the login identifier and the thing every audit line refers
        to. Changing it would silently reassign that history, so it is refused
        here -- server-side, not merely disabled in the form.

        An unchanged value is allowed through so a whole-object PUT works.
        """
        if submitted is None:
            return
        if normalise_email(submitted) == normalise_email(existing.email):
            return
        logger.warning(
            "Refused an email change on user %s (%s -> %r), requested by %s",
            existing.id, existing.email, submitted, principal.email,
        )
        raise HTTPException(
            status_code=400,
            detail=(
                "Email cannot be changed. It is the login identifier and is "
                "referenced by existing records. Create a new user instead."
            ),
        )
