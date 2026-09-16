"""Auth request/response payloads."""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=256)


class SessionResponse(BaseModel):
    username: str
    authenticated: bool = True
    expires_at: Optional[str] = Field(None, alias="expiresAt")

    model_config = ConfigDict(populate_by_name=True)


class LogoutResponse(BaseModel):
    message: str = "Logged out"
