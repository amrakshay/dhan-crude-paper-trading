"""Auth request/response payloads.

Login is by **email**, not username: the users table is the only identity
source and email is its login identifier.
"""
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=256)

    model_config = ConfigDict(populate_by_name=True)


class SessionResponse(BaseModel):
    """Who is signed in, and what their session may reach.

    `mustChangePassword` and `pages` are here so the browser can route
    correctly on first paint without a second round trip.
    """

    email: str
    authenticated: bool = True
    user_id: Optional[int] = Field(None, alias="userId")
    first_name: str = Field("", alias="firstName")
    last_name: str = Field("", alias="lastName")
    full_name: str = Field("", alias="fullName")
    role: str = ""
    status: str = ""
    must_change_password: bool = Field(False, alias="mustChangePassword")
    is_seed_user: bool = Field(False, alias="isSeedUser")
    pages: List[str] = Field(default_factory=list)
    expires_at: Optional[str] = Field(None, alias="expiresAt")

    model_config = ConfigDict(populate_by_name=True)


class LogoutResponse(BaseModel):
    message: str = "Logged out"
