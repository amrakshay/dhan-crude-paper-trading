"""User API payloads.

**No schema here has a password_hash field.** The hash never leaves the server,
and the way to guarantee that is for the response model to have nowhere to put
it -- `tests/test_users_api.py` asserts it never appears in any response body.
"""
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.constants import UserRole, UserStatus


class UserResponse(BaseModel):
    """A user as the browser sees one. Explicit fields, never `from_attributes`
    over the whole model, so a new column cannot leak by being added."""

    id: int
    email: str
    first_name: str = Field(alias="firstName")
    last_name: str = Field(alias="lastName")
    full_name: str = Field(alias="fullName")
    role: str
    status: str
    must_change_password: bool = Field(alias="mustChangePassword")
    is_seed_user: bool = Field(alias="isSeedUser")
    last_login_at: Optional[str] = Field(None, alias="lastLoginAt")
    created_at: Optional[str] = Field(None, alias="createdAt")
    # Why the UI greys out delete/role/status for this row. Sent so the reason
    # can be shown rather than left as an inexplicably disabled button.
    can_be_deleted: bool = Field(True, alias="canBeDeleted")
    can_be_edited: bool = Field(True, alias="canBeEdited")
    immutable_reason: Optional[str] = Field(None, alias="immutableReason")

    model_config = ConfigDict(populate_by_name=True)


class UserListResponse(BaseModel):
    users: List[UserResponse]
    total: int


class CreateUserRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    first_name: str = Field(..., alias="firstName", min_length=1, max_length=100)
    last_name: str = Field(..., alias="lastName", min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=256)
    role: str = Field(...)
    status: str = Field(UserStatus.ACTIVE.value)
    must_change_password: bool = Field(False, alias="mustChangePassword")

    model_config = ConfigDict(populate_by_name=True)


class UpdateUserRequest(BaseModel):
    """Everything an admin may change about someone else.

    `email` is accepted only so that sending an unchanged one back (a whole-
    object PUT) is not an error. Any *different* value is rejected -- see
    UserController.update.
    """

    email: Optional[str] = None
    first_name: Optional[str] = Field(None, alias="firstName", max_length=100)
    last_name: Optional[str] = Field(None, alias="lastName", max_length=100)
    role: Optional[str] = None
    status: Optional[str] = None
    # Optional: an admin resetting someone's password to a temporary one.
    password: Optional[str] = Field(None, max_length=256)
    must_change_password: Optional[bool] = Field(None, alias="mustChangePassword")

    model_config = ConfigDict(populate_by_name=True)


class UpdateProfileRequest(BaseModel):
    """The profile page. Name only -- not role, not status, not email."""

    email: Optional[str] = None
    first_name: Optional[str] = Field(None, alias="firstName", max_length=100)
    last_name: Optional[str] = Field(None, alias="lastName", max_length=100)

    model_config = ConfigDict(populate_by_name=True)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., alias="currentPassword", max_length=256)
    new_password: str = Field(..., alias="newPassword", max_length=256)

    model_config = ConfigDict(populate_by_name=True)


class ChangePasswordResponse(BaseModel):
    changed: bool = True
    message: str = "Password changed"
    user: UserResponse


class DeleteUserResponse(BaseModel):
    deleted: bool = True
    message: str = "User deleted"


class RolePagesResponse(BaseModel):
    """Drives the sidebar and the client-side routes.

    Advisory for the UI only: the API enforces the same rules itself.
    """

    role: str
    pages: List[str]
    all_roles: List[dict] = Field(default_factory=list, alias="allRoles")

    model_config = ConfigDict(populate_by_name=True)


ROLE_VALUES = [role.value for role in UserRole]
STATUS_VALUES = [status.value for status in UserStatus]
