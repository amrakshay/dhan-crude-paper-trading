"""Settings API payloads.

The access token is never returned to the browser. Responses carry only a mask
and metadata derived from it.
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class TokenInfoResponse(BaseModel):
    present: bool
    masked: Optional[str] = None
    is_jwt: bool = Field(False, alias="isJwt")
    expires_at: Optional[str] = Field(None, alias="expiresAt")
    seconds_remaining: Optional[int] = Field(None, alias="secondsRemaining")
    expired: bool = False
    dhan_client_id: Optional[str] = Field(None, alias="dhanClientId")
    decrypt_failed: bool = Field(False, alias="decryptFailed")

    model_config = ConfigDict(populate_by_name=True)


class SettingsResponse(BaseModel):
    client_id: str = Field("", alias="clientId")
    client_id_source: str = Field("default", alias="clientIdSource")
    synthetic_feed: bool = Field(True, alias="syntheticFeed")
    synthetic_feed_source: str = Field("default", alias="syntheticFeedSource")
    access_token_source: str = Field("default", alias="accessTokenSource")
    token: TokenInfoResponse
    encryption_available: bool = Field(True, alias="encryptionAvailable")
    encryption_warning: Optional[str] = Field(None, alias="encryptionWarning")

    model_config = ConfigDict(populate_by_name=True)


class SaveSettingsRequest(BaseModel):
    synthetic_feed: bool = Field(True, alias="syntheticFeed")
    client_id: Optional[str] = Field(None, alias="clientId")
    # Omit (or send "") to leave the stored token untouched -- the browser never
    # has to round-trip the secret just to change another field.
    access_token: Optional[str] = Field(None, alias="accessToken")
    clear_access_token: bool = Field(False, alias="clearAccessToken")

    model_config = ConfigDict(populate_by_name=True)


class SaveSettingsResponse(BaseModel):
    saved: bool = True
    settings: SettingsResponse
    feed_restarted: bool = Field(False, alias="feedRestarted")
    feed_state: Optional[str] = Field(None, alias="feedState")
    message: str = ""

    model_config = ConfigDict(populate_by_name=True)


class ValidateCredentialsRequest(BaseModel):
    """Credentials to test. Omit both to validate whatever is in force."""

    client_id: Optional[str] = Field(None, alias="clientId")
    access_token: Optional[str] = Field(None, alias="accessToken")

    model_config = ConfigDict(populate_by_name=True)


class ValidateCredentialsResponse(BaseModel):
    valid: bool
    message: str
    checked_at: str = Field(alias="checkedAt")
    token: TokenInfoResponse
    expiries: List[str] = []
    detail: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)
