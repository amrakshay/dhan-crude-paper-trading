"""Everything above the Telegram wire: validation, the two test buttons, sending.

`telegram_client` is the wire. This is what the page and the dispatcher talk
to, and it is where the difference between "prove the token", "prove the
channel" and "prove a message actually arrives" is drawn -- because those fail
for completely different reasons and have completely different fixes.
"""
import asyncio
import socket
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src import log_redaction
from src.connections.services import providers
from src.connections.services.connection_store import ConnectionStore
from src.connections.services.telegram_client import (
    FAIL_BAD_TOKEN,
    FAIL_NO_TOKEN,
    TelegramClient,
    TelegramCounters,
    TelegramError,
)
from src.core.time_utils import ist_now, to_ist, utc_now
from src.logging_config import get_logger

logger = get_logger("connections.telegram_service")

# Long enough that somebody can reach for their phone, unlock it, find the bot
# and type; short enough that nobody thinks the page has hung.
LISTEN_WINDOW_SECONDS = 60

# Counters shared across the process, so the health surfaces see one number
# rather than one per request-scoped client.
_COUNTERS = TelegramCounters()


def counters() -> TelegramCounters:
    return _COUNTERS


def installation_name() -> str:
    """Which copy of this application sent a message.

    Somebody running a staging copy and a real one needs to tell the two apart
    at a glance, and a bare "test" tells them nothing.
    """
    try:
        return socket.gethostname()
    except Exception:  # noqa: BLE001
        return "unknown host"


@dataclass
class TelegramConfig:
    """The configured bot, as the rest of this package needs it."""

    bot_token: str = ""
    chat_id: str = ""
    chat_title: str = ""
    commands_enabled: bool = False
    control_commands_enabled: bool = False
    enabled: bool = True
    connection_id: Optional[int] = None

    @property
    def can_send(self) -> bool:
        return bool(self.enabled and self.bot_token and self.chat_id)


def _truthy(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in ("true", "1", "yes", "y", "on")


def _registered(token: Optional[str]) -> str:
    """Register a bot token with the log redactor before it is ever used.

    `ConnectionStore` registers a token it READS or SAVES, which covers the
    configured bot. It does NOT cover a token an operator has typed into the
    form and not yet saved -- and pressing Validate with an unsaved token is
    the very first thing anybody does.

    That path leaks, because httpx logs the full request URL at INFO and a bot
    token is in the PATH. Found on 2026-09-18 by grepping app.log after driving
    the page for real, not by reading the code:

        HTTP Request: POST https://api.telegram.org/bot<TOKEN>/getMe "401"

    So every token this service is handed is registered here, wherever it came
    from. Registration is permanent for the process, which is the right trade:
    the cost of scrubbing a mistyped token is nil, and the cost of not
    scrubbing a real one is a credential in a log file.
    """
    log_redaction.register_secret(token)
    return token or ""


class TelegramService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.store = ConnectionStore(session)

    async def config(self) -> TelegramConfig:
        connection = await self.store.get(providers.PROVIDER_TELEGRAM)
        if connection is None:
            return TelegramConfig()
        values = await self.store.values_for(connection)
        return TelegramConfig(
            bot_token=values.get(providers.TELEGRAM_BOT_TOKEN, ""),
            chat_id=values.get(providers.TELEGRAM_CHAT_ID, ""),
            chat_title=values.get(providers.TELEGRAM_CHAT_TITLE, ""),
            commands_enabled=_truthy(values.get(providers.TELEGRAM_COMMANDS_ENABLED)),
            control_commands_enabled=_truthy(
                values.get(providers.TELEGRAM_CONTROL_COMMANDS)
            ),
            enabled=bool(connection.enabled),
            connection_id=connection.id,
        )

    async def client(self, bot_token: Optional[str] = None) -> TelegramClient:
        token = (bot_token or "").strip()
        if not token:
            token = (await self.config()).bot_token
        return TelegramClient(_registered(token), counters=_COUNTERS)

    # --- validation --------------------------------------------------------
    async def validate(
        self, bot_token: Optional[str] = None, chat_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Check the bot and the channel, and say WHICH of them is wrong.

        Three outcomes, not two. `getMe` proves the token; `getChat` proves the
        channel, and it fails differently when the bot is not a member of that
        channel -- the single most common setup mistake -- than when the channel
        does not exist. Those have different fixes, so they read differently
        here. **Nothing is posted**: a page that sends a message every time
        somebody opens it is not validation, it is spam.
        """
        configured = await self.config()
        token = (bot_token or "").strip() or configured.bot_token
        destination = (chat_id or "").strip() or configured.chat_id

        result: Dict[str, Any] = {
            "valid": False,
            "checkedAt": utc_now().isoformat(),
            "tokenValid": False,
            "chatValid": False,
            "failureKind": None,
            "message": "",
            "bot": None,
            "chat": None,
        }

        if not token:
            result["failureKind"] = FAIL_NO_TOKEN
            result["message"] = (
                "No bot token yet. Message @BotFather on Telegram, send "
                "/newbot, and paste the token it gives you."
            )
            return result

        client = TelegramClient(_registered(token), counters=_COUNTERS)
        try:
            bot = await client.get_me()
        except TelegramError as error:
            result["failureKind"] = error.kind
            result["message"] = error.message
            return result

        result["tokenValid"] = True
        result["bot"] = bot.as_dict()

        if not destination:
            result["message"] = (
                f"The bot token is good (@{bot.username}), but no channel is "
                f"set yet. Use 'Listen for a test message' to discover the chat "
                f"id without hunting for it."
            )
            return result

        try:
            chat = await client.get_chat(destination)
        except TelegramError as error:
            result["failureKind"] = error.kind
            result["message"] = (
                f"The bot token is good (@{bot.username}), but the channel is "
                f"not reachable: {error.message}"
            )
            return result

        result["chatValid"] = True
        result["valid"] = True
        result["chat"] = chat.as_dict()
        label = chat.title or chat.username or str(chat.id)
        result["message"] = (
            f"@{bot.username} can reach {label}. Nothing was posted \u2014 use "
            f"'Send test message' to prove that separately."
        )
        return result

    # --- send test message -------------------------------------------------
    @staticmethod
    def test_message_body() -> str:
        """A message that identifies ITSELF, not just its sender.

        Which installation sent it, when, and that it is a manual test rather
        than an alert. Somebody with a staging copy and a real one needs to
        tell them apart at a glance.
        """
        return (
            "Test message from Dhan Paper Trading\n"
            "Sent by hand from the Connections page \u2014 this is not an alert.\n"
            f"Host: {installation_name()}  ·  "
            f"{ist_now().strftime('%H:%M IST, %d %b %Y')}\n"
            "If you can read this, alerts will reach this channel."
        )

    async def send_test_message(self) -> Dict[str, Any]:
        """Prove the outbound half in one press: token, channel and permission."""
        configured = await self.config()
        result: Dict[str, Any] = {
            "sent": False,
            "at": utc_now().isoformat(),
            "chatId": configured.chat_id,
            "chatTitle": configured.chat_title,
            "failureKind": None,
            "message": "",
        }

        if not configured.bot_token:
            result["failureKind"] = FAIL_NO_TOKEN
            result["message"] = "Save a bot token first; there is nothing to send with."
            return result
        if not configured.chat_id:
            result["failureKind"] = FAIL_NO_TOKEN
            result["message"] = (
                "Save a channel id first. 'Listen for a test message' will "
                "discover one for you."
            )
            return result

        client = TelegramClient(_registered(configured.bot_token), counters=_COUNTERS)
        try:
            await client.send_message(configured.chat_id, self.test_message_body())
        except TelegramError as error:
            result["failureKind"] = error.kind
            result["message"] = error.message
            await self._record_check(False, error.message)
            return result

        label = configured.chat_title or configured.chat_id
        result["sent"] = True
        result["message"] = (
            f"Posted to {label} at {ist_now().strftime('%H:%M IST')}. "
            f"Check the channel."
        )
        await self._record_check(True, f"Test message posted to {label}")
        return result

    async def send(self, text: str) -> None:
        """Deliver one alert. Raises `TelegramError` for the dispatcher to read."""
        configured = await self.config()
        if not configured.can_send:
            raise TelegramError(
                FAIL_NO_TOKEN,
                "The Telegram connection is not configured or is switched off.",
            )
        client = TelegramClient(_registered(configured.bot_token), counters=_COUNTERS)
        await client.send_message(configured.chat_id, text)

    # --- listen for a test message ----------------------------------------
    async def listen_for_test(
        self, seconds: int = LISTEN_WINDOW_SECONDS
    ) -> Dict[str, Any]:
        """Open a listening window and report the first update that arrives.

        THIS IS THE SETUP TOOL, not merely a check. `users.telegram_user_id`
        needs the sender's NUMERIC id, and Telegram offers no friendly way to
        find your own -- this is how the operator discovers it.

        **It borrows the running poller's stream rather than opening a second
        one.** Telegram is widely reported to serve one `getUpdates` consumer
        per bot and to answer a second with HTTP 409; that behaviour is NOT in
        the official documentation and is recorded as unverified in the
        README's Known gaps. Borrowing is correct either way, and costs
        nothing if the report turns out to be wrong.

        It works with commands switched OFF, because this is how you switch
        them on in the first place.
        """
        from src.connections.services.telegram_command_service import (
            get_telegram_poller,
        )

        configured = await self.config()
        result: Dict[str, Any] = {
            "received": False,
            "windowSeconds": int(seconds),
            "at": utc_now().isoformat(),
            "failureKind": None,
            "message": "",
            "update": None,
            "borrowedRunningPoller": False,
        }

        if not configured.bot_token:
            result["failureKind"] = FAIL_NO_TOKEN
            result["message"] = "Save a bot token first; there is nothing to listen with."
            return result

        poller = get_telegram_poller()
        update: Optional[Dict[str, Any]] = None

        # `polling`, not `running`: the task is alive even while commands are
        # switched off, and then it consumes nothing. Borrowing a stream nobody
        # is reading would make this button wait its whole window and report
        # "nothing arrived" for a message that did in fact arrive.
        if poller.polling:
            result["borrowedRunningPoller"] = True
            update = await poller.wait_for_update(seconds)
        else:
            client = TelegramClient(_registered(configured.bot_token), counters=_COUNTERS)
            try:
                update = await self._poll_directly(client, seconds)
            except TelegramError as error:
                result["failureKind"] = error.kind
                result["message"] = error.message
                return result

        if update is None:
            result["message"] = self.nothing_arrived_message()
            return result

        result["received"] = True
        result["update"] = self.describe_update(update)
        described = result["update"]
        if described.get("senderId") is None:
            # A CHANNEL POST CARRIES NO USER. The Bot API documents
            # `Message.from` as "may be empty for messages sent to channels",
            # so posting in the channel discovers the chat id and teaches the
            # operator nothing about their own user id -- which is the thing
            # they came for.
            result["message"] = (
                "Something arrived, but it was a channel post, which carries no "
                "sender. That gives you the chat id and NOT your user id. To "
                "discover your user id, send a DIRECT MESSAGE to the bot "
                "instead."
            )
        else:
            result["message"] = (
                f"Received from {described.get('senderName') or 'an unnamed user'}. "
                f"Their Telegram user id is {described['senderId']}."
            )
        return result

    async def _poll_directly(
        self, client: TelegramClient, seconds: int
    ) -> Optional[Dict[str, Any]]:
        """Poll on our own when no background poller is running.

        The offset is deliberately not advanced past what is read: this window
        is a diagnostic, and consuming an update the command poller has not
        seen would make the test change what the application does.
        """
        deadline = asyncio.get_event_loop().time() + seconds
        while asyncio.get_event_loop().time() < deadline:
            remaining = max(1, int(deadline - asyncio.get_event_loop().time()))
            updates = await client.get_updates(
                offset=None, timeout_seconds=min(remaining, 20)
            )
            if updates:
                return updates[-1]
        return None

    @staticmethod
    def nothing_arrived_message() -> str:
        """Nothing arriving is a RESULT, not an error.

        A spinner that gives up silently is the worst version of this button,
        so the reasons are listed in the order they are likely.
        """
        return (
            "Nothing arrived in the listening window. In order of likelihood: "
            "(1) you posted in the channel instead of messaging the bot "
            "directly -- a channel post carries no user; (2) you have never "
            "started a chat with this bot, and a bot cannot message a person "
            "first, so press Start in the bot's chat once; (3) privacy mode is "
            "on, which hides ordinary group messages from the bot; (4) an "
            "outgoing webhook is configured, and Telegram does not serve "
            "getUpdates while one is set."
        )

    @staticmethod
    def describe_update(update: Dict[str, Any]) -> Dict[str, Any]:
        """What the operator needs off an update, and nothing else.

        The sender's numeric id is the point of the whole button; the chat id
        fills in the channel field without hunting for it; the text confirms it
        is the message they just sent rather than a stale one.
        """
        message = (
            update.get("message")
            or update.get("channel_post")
            or update.get("edited_message")
            or {}
        )
        sender = message.get("from") or {}
        chat = message.get("chat") or {}
        name = " ".join(
            part for part in (sender.get("first_name"), sender.get("last_name")) if part
        ).strip()
        return {
            "updateId": update.get("update_id"),
            "senderId": sender.get("id"),
            "senderName": name or None,
            "senderUsername": sender.get("username"),
            "chatId": str(chat.get("id")) if chat.get("id") is not None else None,
            "chatType": chat.get("type"),
            "chatTitle": chat.get("title") or chat.get("username") or None,
            "text": message.get("text"),
            "isChannelPost": "channel_post" in update,
        }

    # --- bookkeeping -------------------------------------------------------
    async def _record_check(self, ok: bool, detail: str) -> None:
        connection = await self.store.get(providers.PROVIDER_TELEGRAM)
        if connection is None:
            return
        await self.store.connections.record_check(connection, ok, detail)
