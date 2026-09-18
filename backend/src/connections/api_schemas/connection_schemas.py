"""Connections API payloads.

**No secret is ever returned.** A secret setting comes back as
`{present, masked, decryptFailed}` -- the same shape the Settings page's access
token has always used -- and an OMITTED secret on a save means "keep what is
stored", never "clear it". Clearing is an explicit empty string.

Every payload here is deliberately loose about the per-provider `settings` and
`detail` objects (`Dict[str, Any]`) rather than modelling Dhan's and Telegram's
fields as separate schemas. A third provider is then a `ProviderSpec` and a
card, not a new pair of request and response models.
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ConnectionCard(BaseModel):
    provider: str
    label: str
    name: str
    subtitle: str
    capabilities: List[str] = []
    enabled: bool = False
    configured: bool = False
    status: str
    status_detail: str = Field("", alias="statusDetail")
    # Decision 3: LAST KNOWN, with its age. Null means never checked, which is
    # not the same as checked and failed.
    last_checked_at: Optional[str] = Field(None, alias="lastCheckedAt")
    last_check_ok: Optional[bool] = Field(None, alias="lastCheckOk")
    last_check_detail: Optional[str] = Field(None, alias="lastCheckDetail")
    metric_label: str = Field("", alias="metricLabel")
    metric_value: Optional[Any] = Field(None, alias="metricValue")
    detail: Dict[str, Any] = {}
    settings: Dict[str, Any] = {}

    model_config = ConfigDict(populate_by_name=True)


class ConnectionListResponse(BaseModel):
    connections: List[ConnectionCard]

    model_config = ConfigDict(populate_by_name=True)


class SaveConnectionRequest(BaseModel):
    """Whatever changed. An omitted secret keeps the stored one."""

    name: Optional[str] = Field(None, max_length=80)
    enabled: Optional[bool] = None
    settings: Dict[str, Any] = {}

    model_config = ConfigDict(populate_by_name=True)


class ValidateConnectionRequest(BaseModel):
    """Values to test. Omit them all to check whatever is in force."""

    settings: Dict[str, Any] = {}

    model_config = ConfigDict(populate_by_name=True)


class ValidateConnectionResponse(BaseModel):
    valid: bool
    message: str
    checked_at: str = Field(alias="checkedAt")
    # WHICH failure it was, so the page can name the fix rather than saying
    # "failed". Bad token, bot not in the channel and no such channel need
    # three different things done about them.
    failure_kind: Optional[str] = Field(None, alias="failureKind")
    detail: Dict[str, Any] = {}

    model_config = ConfigDict(populate_by_name=True)


class TestMessageResponse(BaseModel):
    sent: bool
    message: str
    at: str
    chat_id: Optional[str] = Field(None, alias="chatId")
    chat_title: Optional[str] = Field(None, alias="chatTitle")
    failure_kind: Optional[str] = Field(None, alias="failureKind")

    model_config = ConfigDict(populate_by_name=True)


class ListenRequest(BaseModel):
    seconds: int = Field(60, ge=5, le=120)

    model_config = ConfigDict(populate_by_name=True)


class ListenResponse(BaseModel):
    """What the listening window heard.

    `update.senderId` is the point of the whole button: it is how an operator
    discovers their own numeric Telegram user id, which `users.telegram_user_id`
    needs and which Telegram offers no friendly way to find. Nothing arriving
    is a RESULT, not an error, and `message` says so with the reasons in the
    order they are likely.
    """

    received: bool
    message: str
    at: str
    window_seconds: int = Field(60, alias="windowSeconds")
    failure_kind: Optional[str] = Field(None, alias="failureKind")
    update: Optional[Dict[str, Any]] = None
    borrowed_running_poller: bool = Field(False, alias="borrowedRunningPoller")

    model_config = ConfigDict(populate_by_name=True)


class AlertRow(BaseModel):
    id: int
    kind: str
    severity: str
    title: str
    body: str
    status: str
    attempts: int = 0
    suppressed_count: int = Field(0, alias="suppressedCount")
    last_error: Optional[str] = Field(None, alias="lastError")
    created_at: Optional[str] = Field(None, alias="createdAt")
    sent_at: Optional[str] = Field(None, alias="sentAt")

    model_config = ConfigDict(populate_by_name=True)


class AlertListResponse(BaseModel):
    alerts: List[AlertRow]
    counts: Dict[str, int] = {}
    dispatcher: Dict[str, Any] = {}
    watcher: Dict[str, Any] = {}

    model_config = ConfigDict(populate_by_name=True)
