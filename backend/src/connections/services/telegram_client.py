"""The Telegram Bot API client, and the only module allowed to name its host.

FIRST OUTBOUND PATH IN THIS APPLICATION. Everything else that leaves this
process is INBOUND market data -- the app fetches prices and sends nothing
anywhere. This sends. It is therefore contained the same way
`dhan_token_client.py` contains `/RenewToken`: one base URL, named here and
nowhere else, with `tests/test_outbound_hosts.py` asserting nothing else names
it.

TELEGRAM IS NOT A BROKER AND MUST NOT BECOME ONE. Root `CLAUDE.md` section 1 is
untouched: this application cannot place a real order, so neither can a message
sent to it. There is no broker surface for a command to reach.

**THE BOT TOKEN IS IN THE URL PATH.** Every call is
`https://api.telegram.org/bot<TOKEN>/METHOD`, and httpx logs the full request
URL at INFO -- that is happening in `logs/app.log` right now for Dhan's chart
endpoint. A naive implementation therefore writes the bot token into the log on
every single call. Two defences, and both are needed:

  1. `ConnectionStore` registers the token with `log_redaction` the moment it
     is read or saved, so httpx's own line is scrubbed by the formatter the
     root logger already uses.
  2. Nothing here interpolates an httpx exception's MESSAGE into a log line or
     into an error string -- only its type. An exception can carry the request,
     and the request carries the token. Same rule `dhan_token_client.py`
     follows, and for the same reason.

`tests/test_no_secrets_in_logs.py` asserts both, pointed at the endpoints.

**VALIDATION HAS MORE THAN TWO OUTCOMES.** `getMe` proves the token. `getChat`
proves the channel -- but it fails differently when the bot is not a member of
the channel, which is the single most common setup mistake, than when the
channel does not exist. Those need different fixes, so they are different
failures here rather than one "invalid".
"""
import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from src import config_utils
from src.logging_config import get_logger

logger = get_logger("connections.telegram")

# The ONE place this host is named. Asserted by
# tests/test_outbound_hosts.py::test_only_the_telegram_client_names_the_bot_api.
TELEGRAM_API_BASE = "https://api.telegram.org"

# Methods this client may call. A closed set, read in one glance -- the same
# shape as the Dhan clients' endpoint lists, and for the same reason: a regex
# that accepts a family of methods is not the guarantee a set is.
METHOD_GET_ME = "getMe"
METHOD_GET_CHAT = "getChat"
METHOD_SEND_MESSAGE = "sendMessage"
METHOD_GET_UPDATES = "getUpdates"
ALLOWED_METHODS = frozenset(
    {METHOD_GET_ME, METHOD_GET_CHAT, METHOD_SEND_MESSAGE, METHOD_GET_UPDATES}
)


# --- failure kinds ---------------------------------------------------------
# Five distinguishable problems with five different fixes. "Failed to send" is
# not an acceptable message for any of them.
FAIL_NO_TOKEN = "NO_TOKEN"
FAIL_BAD_TOKEN = "BAD_TOKEN"
FAIL_NO_SUCH_CHAT = "NO_SUCH_CHAT"
FAIL_NOT_A_MEMBER = "NOT_A_MEMBER"
FAIL_CANNOT_POST = "CANNOT_POST"
FAIL_FLOOD_CONTROL = "FLOOD_CONTROL"
FAIL_WEBHOOK_SET = "WEBHOOK_SET"
FAIL_CONFLICT = "CONFLICT"
FAIL_NETWORK = "NETWORK"
FAIL_UNEXPECTED = "UNEXPECTED"

# Whether trying the same call again could plausibly work. A bad token cannot
# fix itself; flood control and a network blip can.
TRANSIENT_FAILURES = frozenset({FAIL_FLOOD_CONTROL, FAIL_NETWORK, FAIL_CONFLICT})


class TelegramError(Exception):
    """A Telegram call failed, with WHICH failure it was.

    The `kind` is what lets the UI print a sentence naming the fix rather than
    "failed". `retry_after` is populated only for flood control, where Telegram
    documents it on `ResponseParameters`.
    """

    def __init__(
        self,
        kind: str,
        message: str,
        retry_after: Optional[int] = None,
        status_code: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retry_after = retry_after
        self.status_code = status_code

    @property
    def transient(self) -> bool:
        return self.kind in TRANSIENT_FAILURES


@dataclass
class BotIdentity:
    """What `getMe` says about the bot behind the configured token."""

    id: int
    username: str
    first_name: str
    # Documented on `User` for a bot returned by getMe. FALSE means privacy
    # mode is ON, and in a GROUP the bot then sees only commands and replies.
    # Reported as a fact rather than left for the operator to guess at.
    can_read_all_group_messages: bool = False
    can_join_groups: bool = True

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "firstName": self.first_name,
            "canReadAllGroupMessages": self.can_read_all_group_messages,
            "canJoinGroups": self.can_join_groups,
        }


@dataclass
class ChatIdentity:
    """What `getChat` says about the configured destination."""

    id: int
    type: str
    title: str = ""
    username: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "username": self.username,
        }


@dataclass
class TelegramCounters:
    """Counters for the health surfaces. No token, no chat content."""

    sent: int = 0
    failures: int = 0
    updates_received: int = 0
    last_failure_kind: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "sent": self.sent,
            "failures": self.failures,
            "updatesReceived": self.updates_received,
            "lastFailureKind": self.last_failure_kind,
        }


def _classify(status_code: int, payload: Dict[str, Any]) -> TelegramError:
    """Turn a Telegram error body into the failure it actually is.

    Telegram answers `{"ok": false, "error_code": N, "description": "..."}`,
    optionally with `parameters.retry_after`. The description is the only thing
    distinguishing "the bot is not in that channel" from "that channel does not
    exist", and those have different fixes, so it is read rather than
    discarded.
    """
    description = str(payload.get("description") or "").strip()
    lowered = description.lower()
    parameters = payload.get("parameters") or {}
    retry_after = parameters.get("retry_after")
    try:
        retry_after = int(retry_after) if retry_after is not None else None
    except (TypeError, ValueError):
        retry_after = None

    if status_code == 401 or "unauthorized" in lowered:
        return TelegramError(
            FAIL_BAD_TOKEN,
            "Telegram rejected the bot token. Check it against @BotFather \u2014 "
            "it is the whole string, including the numeric prefix and the colon.",
            status_code=status_code,
        )
    if status_code == 429 or "too many requests" in lowered:
        return TelegramError(
            FAIL_FLOOD_CONTROL,
            (
                f"Telegram is rate limiting this bot; it asked to wait "
                f"{retry_after} seconds."
                if retry_after
                else "Telegram is rate limiting this bot. Wait and try again."
            ),
            retry_after=retry_after,
            status_code=status_code,
        )
    if "webhook is active" in lowered or "webhook" in lowered and "getupdates" in lowered:
        return TelegramError(
            FAIL_WEBHOOK_SET,
            "This bot has an outgoing webhook configured, and Telegram does not "
            "serve getUpdates while one is set. Delete the webhook (deleteWebhook "
            "in @BotFather's API) to receive commands here.",
            status_code=status_code,
        )
    if status_code == 409 or "terminated by other" in lowered:
        return TelegramError(
            FAIL_CONFLICT,
            "Another process is already reading this bot's updates. Telegram "
            "serves one getUpdates consumer at a time.",
            status_code=status_code,
        )
    if "chat not found" in lowered or "chat_id is empty" in lowered:
        return TelegramError(
            FAIL_NO_SUCH_CHAT,
            "Telegram has no chat with that id. For a channel the id is a "
            "negative number beginning -100; a @name works only for a PUBLIC "
            "channel.",
            status_code=status_code,
        )
    if (
        "not a member" in lowered
        or "bot was kicked" in lowered
        or "bot is not a member" in lowered
        or "member list is inaccessible" in lowered
    ):
        return TelegramError(
            FAIL_NOT_A_MEMBER,
            "The token is good, but this bot is not a member of that channel. "
            "Add it to the channel and make it an administrator.",
            status_code=status_code,
        )
    if (
        "not enough rights" in lowered
        or "have no rights" in lowered
        or "need administrator rights" in lowered
        or "can't write" in lowered
        or "chat_write_forbidden" in lowered
    ):
        return TelegramError(
            FAIL_CANNOT_POST,
            "The bot is in that channel but is not allowed to post. Give it the "
            "'Post Messages' administrator right.",
            status_code=status_code,
        )
    if status_code == 403:
        return TelegramError(
            FAIL_NOT_A_MEMBER,
            f"Telegram refused the request: {description or 'forbidden'}. The "
            "usual cause is the bot not being a member of that channel.",
            status_code=status_code,
        )
    return TelegramError(
        FAIL_UNEXPECTED,
        f"Telegram returned {status_code}: {description or 'no description'}",
        status_code=status_code,
    )


class TelegramClient:
    """One bot token, four methods, nothing stored.

    The token is passed in rather than read from configuration, because this
    client is used both for the CONFIGURED bot and for a token the operator has
    typed into the form but not saved. Contrast `DhanTokenClient`, which reads
    its credential from the live config precisely so no caller has to hold one.
    """

    def __init__(self, bot_token: str, counters: Optional[TelegramCounters] = None):
        self._token = (bot_token or "").strip()
        self.counters = counters or TelegramCounters()

    @property
    def configured(self) -> bool:
        return bool(self._token)

    @staticmethod
    def _timeout() -> int:
        return config_utils.get_property_value_int(
            "connections.telegram_timeout_seconds", 20
        )

    def _url(self, method: str) -> str:
        """The request URL. THE TOKEN IS IN THE PATH -- see the module docstring."""
        if method not in ALLOWED_METHODS:
            raise TelegramError(
                FAIL_UNEXPECTED, f"{method} is not a method this client may call."
            )
        return f"{TELEGRAM_API_BASE}/bot{self._token}/{method}"

    async def _call(
        self,
        method: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        if not self._token:
            raise TelegramError(
                FAIL_NO_TOKEN,
                "No Telegram bot token is configured. Create a bot with "
                "@BotFather and paste its token on the Connections page.",
            )

        try:
            async with httpx.AsyncClient(timeout=timeout or self._timeout()) as client:
                response = await client.post(
                    self._url(method), json=payload or {}, headers={"Accept": "application/json"}
                )
        except Exception as error:  # noqa: BLE001
            self.counters.failures += 1
            self.counters.last_failure_kind = FAIL_NETWORK
            # The exception's MESSAGE is deliberately not reported: an httpx
            # error can carry the request URL, and the bot token is IN that
            # URL. Only the type.
            logger.warning(
                "Telegram %s could not be completed: %s", method, type(error).__name__
            )
            raise TelegramError(
                FAIL_NETWORK,
                f"Could not reach Telegram ({type(error).__name__}). Check this "
                f"machine's outbound network access.",
            ) from None

        try:
            body = response.json()
        except Exception:  # noqa: BLE001
            self.counters.failures += 1
            self.counters.last_failure_kind = FAIL_UNEXPECTED
            raise TelegramError(
                FAIL_UNEXPECTED,
                f"Telegram returned {response.status_code} with a body that is "
                f"not JSON.",
                status_code=response.status_code,
            ) from None

        if not isinstance(body, dict) or not body.get("ok"):
            error = _classify(response.status_code, body if isinstance(body, dict) else {})
            self.counters.failures += 1
            self.counters.last_failure_kind = error.kind
            logger.warning("Telegram %s failed: %s", method, error.kind)
            raise error

        return body.get("result")

    # --- the four calls ----------------------------------------------------
    async def get_me(self) -> BotIdentity:
        """Validate the bot token, and learn whether privacy mode is on."""
        result = await self._call(METHOD_GET_ME) or {}
        return BotIdentity(
            id=int(result.get("id") or 0),
            username=str(result.get("username") or ""),
            first_name=str(result.get("first_name") or ""),
            can_read_all_group_messages=bool(
                result.get("can_read_all_group_messages")
            ),
            can_join_groups=bool(result.get("can_join_groups", True)),
        )

    async def get_chat(self, chat_id: str) -> ChatIdentity:
        """Validate the destination. Fails DIFFERENTLY when the bot is not in it."""
        result = await self._call(METHOD_GET_CHAT, {"chat_id": chat_id}) or {}
        return ChatIdentity(
            id=int(result.get("id") or 0),
            type=str(result.get("type") or ""),
            title=str(result.get("title") or ""),
            username=str(result.get("username") or ""),
        )

    async def send_message(
        self, chat_id: str, text: str, disable_notification: bool = False
    ) -> Dict[str, Any]:
        result = await self._call(
            METHOD_SEND_MESSAGE,
            {
                "chat_id": chat_id,
                "text": text,
                "disable_notification": bool(disable_notification),
                # Plain text. Markdown would make an unescaped symbol in a
                # trading symbol or an error message fail the send, which is
                # the worst time to discover formatting.
                "disable_web_page_preview": True,
            },
        )
        self.counters.sent += 1
        return result or {}

    async def get_updates(
        self, offset: Optional[int] = None, timeout_seconds: int = 25
    ) -> List[Dict[str, Any]]:
        """Long-poll for updates.

        LONG-POLLING RATHER THAN A WEBHOOK. A webhook needs this application
        reachable from the internet, which it is not and should not need to be.
        Long-polling is one more background task in the shape this codebase
        already uses five times.
        """
        payload: Dict[str, Any] = {"timeout": int(timeout_seconds)}
        if offset is not None:
            payload["offset"] = int(offset)
        # The HTTP timeout must outlast the long poll, or every poll is a
        # client-side timeout that looks like a network failure.
        result = await self._call(
            METHOD_GET_UPDATES, payload, timeout=timeout_seconds + 10
        )
        updates = list(result or [])
        self.counters.updates_received += len(updates)
        return updates
