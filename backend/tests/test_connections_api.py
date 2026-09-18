"""The Connections page: its cards, its gates, and its two test buttons.

Named for the behaviour rather than the method, per backend/CLAUDE.md §11.
"""
import httpx
import pytest

from src.connections.services import providers
from src.connections.services.telegram_client import (
    FAIL_BAD_TOKEN,
    FAIL_CANNOT_POST,
    FAIL_FLOOD_CONTROL,
    FAIL_NOT_A_MEMBER,
    FAIL_NO_SUCH_CHAT,
    FAIL_WEBHOOK_SET,
    TelegramError,
    _classify,
)

BOT_TOKEN = "123456789:AAH-sentinel-bot-token-DO-NOT-LOG-9f2c1a"
CHAT_ID = "-1001234567890"


# --- the page ---------------------------------------------------------------
async def test_a_provider_with_no_row_is_still_a_card(auth_client):
    """"Not configured" is a state somebody has to be able to see.

    A page that only lists what already exists gives an operator no way to set
    up the thing that does not.
    """
    response = await auth_client.get("/api/connections")

    assert response.status_code == 200, response.text
    cards = {card["provider"]: card for card in response.json()["connections"]}
    assert set(cards) == {providers.PROVIDER_DHAN, providers.PROVIDER_TELEGRAM}
    assert cards["telegram"]["status"] == "NOT_CONFIGURED"
    assert cards["telegram"]["configured"] is False


async def test_a_connection_that_was_never_checked_is_not_shown_as_working(
    auth_client,
):
    """NEVER_CHECKED is a third state, and must not be green.

    `last_check_ok` is nullable precisely so that "we have no idea" and "it is
    broken" do not share one red pill -- and so that "configured" does not
    quietly read as "working".
    """
    saved = await auth_client.put(
        "/api/connections/telegram",
        json={"settings": {"bot_token": BOT_TOKEN, "chat_id": CHAT_ID}},
    )
    assert saved.status_code == 200, saved.text

    card = saved.json()
    assert card["configured"] is True
    assert card["lastCheckOk"] is None
    assert card["lastCheckedAt"] is None
    assert card["status"] == "NEVER_CHECKED"
    assert "never checked" in card["statusDetail"].lower()


async def test_a_half_configured_telegram_connection_is_not_configured(auth_client):
    """A bot token with no channel cannot send, so it is not configured."""
    saved = await auth_client.put(
        "/api/connections/telegram", json={"settings": {"bot_token": BOT_TOKEN}}
    )

    assert saved.status_code == 200, saved.text
    assert saved.json()["configured"] is False
    assert saved.json()["status"] == "NOT_CONFIGURED"


async def test_the_commands_chip_is_absent_until_commands_are_switched_on(
    auth_client,
):
    """A chip that claims a capability nobody enabled is the page lying."""
    off = await auth_client.put(
        "/api/connections/telegram",
        json={"settings": {"bot_token": BOT_TOKEN, "chat_id": CHAT_ID}},
    )
    assert "COMMANDS" not in off.json()["capabilities"]
    assert "ALERTS" in off.json()["capabilities"]

    on = await auth_client.put(
        "/api/connections/telegram",
        json={"settings": {"commands_enabled": "true"}},
    )
    assert "COMMANDS" in on.json()["capabilities"]


# --- secrets ----------------------------------------------------------------
async def test_the_bot_token_never_comes_back_to_the_browser(auth_client):
    saved = await auth_client.put(
        "/api/connections/telegram",
        json={"settings": {"bot_token": BOT_TOKEN, "chat_id": CHAT_ID}},
    )
    read = await auth_client.get("/api/connections/telegram")

    for response in (saved, read):
        assert BOT_TOKEN not in response.text
    token_field = read.json()["settings"]["bot_token"]
    assert token_field["present"] is True
    assert token_field["masked"]
    assert BOT_TOKEN not in token_field["masked"]


async def test_the_bot_token_is_stored_encrypted_not_in_the_plaintext_column(
    auth_client, db_session
):
    """The invariant that has kept a secret out of a plaintext column here."""
    from src.database.session import get_session_factory

    await auth_client.put(
        "/api/connections/telegram",
        json={"settings": {"bot_token": BOT_TOKEN, "chat_id": CHAT_ID}},
    )

    session = get_session_factory()()
    try:
        from src.connections.database.db_operations.connection_repository import (
            ConnectionRepository,
            ConnectionSettingRepository,
        )

        connection = await ConnectionRepository(session).get_by_provider("telegram")
        stored = await ConnectionSettingRepository(session).get_by_key(
            connection.id, "bot_token"
        )
        assert stored.is_encrypted is True
        assert stored.value is None, "a secret must never land in the plaintext column"
        assert stored.encrypted_value and BOT_TOKEN not in stored.encrypted_value

        chat = await ConnectionSettingRepository(session).get_by_key(
            connection.id, "chat_id"
        )
        assert chat.value == CHAT_ID
        assert chat.is_encrypted is False
        assert chat.encrypted_value is None
    finally:
        await session.close()


async def test_an_omitted_bot_token_leaves_the_stored_one_untouched(auth_client):
    """The same rule the Dhan token already follows: empty means "keep it"."""
    await auth_client.put(
        "/api/connections/telegram",
        json={"settings": {"bot_token": BOT_TOKEN, "chat_id": CHAT_ID}},
    )

    await auth_client.put(
        "/api/connections/telegram", json={"settings": {"chat_id": "-100999"}}
    )

    read = await auth_client.get("/api/connections/telegram")
    assert read.json()["settings"]["bot_token"]["present"] is True
    assert read.json()["settings"]["chat_id"] == "-100999"


async def test_a_row_naming_an_unknown_provider_is_ignored(db_session):
    """A stray row must never start influencing configuration."""
    from src.connections.database.db_operations.connection_repository import (
        ConnectionRepository,
    )
    from src.connections.services.connection_service import ConnectionService
    from src.connections.services.connection_store import ConnectionStore

    await ConnectionRepository(db_session).create_connection(
        provider="some-provider-from-the-future", name="Mystery"
    )
    await db_session.commit()

    store = ConnectionStore(db_session)
    assert await store.get("some-provider-from-the-future") is None
    assert await store.values("some-provider-from-the-future") == {}

    page = await ConnectionService(db_session).page()
    assert {card["provider"] for card in page["connections"]} == {
        providers.PROVIDER_DHAN,
        providers.PROVIDER_TELEGRAM,
    }


async def test_a_setting_a_provider_does_not_declare_is_refused(db_session):
    from src.connections.services.connection_store import (
        ConnectionStore,
        ConnectionStoreError,
    )

    with pytest.raises(ConnectionStoreError):
        await ConnectionStore(db_session).put("telegram", "webhook_url", "x")


# --- the credentials that moved --------------------------------------------
async def test_the_dhan_credentials_are_reachable_from_connections(auth_client):
    """They moved here; the Settings page keeps the synthetic switch."""
    await auth_client.put(
        "/api/settings",
        json={"syntheticFeed": True, "clientId": "1100123456", "accessToken": ""},
    )

    card = await auth_client.get("/api/connections/dhan")

    assert card.status_code == 200, card.text
    assert card.json()["detail"]["clientId"] == "1100123456"
    assert card.json()["capabilities"] == ["MARKET_DATA"]


async def test_saving_dhan_credentials_on_connections_reaches_the_running_config(
    auth_client,
):
    """The startup overlay is untouched; a save still applies immediately."""
    from src import config_utils

    await auth_client.put(
        "/api/connections/dhan",
        json={"settings": {"client_id": "1100777777"}},
    )

    assert config_utils.get_property_value("dhan.client_id") == "1100777777"


async def test_going_live_without_credentials_is_still_refused(auth_client):
    """The refusal SURVIVED the move, and now points at Connections.

    Live mode with no credentials means the feed errors out, and this
    application must never invent prices in its place.
    """
    refused = await auth_client.put(
        "/api/settings", json={"syntheticFeed": False, "clientId": ""}
    )

    assert refused.status_code == 400, refused.text
    assert "Connections page" in refused.text


# --- validation reads differently for each failure -------------------------
def test_the_three_telegram_failures_read_differently():
    """Bad token, bot not in the channel, and no such channel need three fixes.

    Collapsing them into "invalid" is what makes an operator spend an hour on
    the wrong one.
    """
    bad_token = _classify(401, {"description": "Unauthorized"})
    not_a_member = _classify(
        403, {"description": "Forbidden: bot is not a member of the channel chat"}
    )
    no_chat = _classify(400, {"description": "Bad Request: chat not found"})
    cannot_post = _classify(
        400,
        {"description": "Bad Request: not enough rights to send text messages to the chat"},
    )
    flood = _classify(
        429, {"description": "Too Many Requests", "parameters": {"retry_after": 17}}
    )

    assert bad_token.kind == FAIL_BAD_TOKEN
    assert not_a_member.kind == FAIL_NOT_A_MEMBER
    assert no_chat.kind == FAIL_NO_SUCH_CHAT
    assert cannot_post.kind == FAIL_CANNOT_POST
    assert flood.kind == FAIL_FLOOD_CONTROL
    assert flood.retry_after == 17

    messages = {
        bad_token.message,
        not_a_member.message,
        no_chat.message,
        cannot_post.message,
        flood.message,
    }
    assert len(messages) == 5, "five problems must read as five messages"


def test_a_webhook_is_reported_rather_than_timed_out():
    """`getUpdates` does not work while a webhook is set. Say so.

    Timing out and blaming the operator is the worst version of this.
    """
    error = _classify(
        409, {"description": "Conflict: can't use getUpdates method while webhook is active"}
    )
    assert error.kind == FAIL_WEBHOOK_SET
    assert "webhook" in error.message.lower()


def test_only_flood_control_and_network_failures_are_worth_retrying():
    assert TelegramError(FAIL_FLOOD_CONTROL, "x").transient is True
    assert TelegramError(FAIL_BAD_TOKEN, "x").transient is False
    assert TelegramError(FAIL_NOT_A_MEMBER, "x").transient is False


# --- the two test buttons ---------------------------------------------------
def test_the_test_message_identifies_the_installation_and_says_it_is_a_test():
    """A bare "test" tells somebody with two copies of this nothing."""
    from src.connections.services.telegram_service import (
        TelegramService,
        installation_name,
    )

    body = TelegramService.test_message_body()

    assert "Dhan Paper Trading" in body
    assert "not an alert" in body
    assert installation_name() in body
    assert "IST" in body


def test_nothing_arriving_is_a_result_with_its_reasons_in_order():
    """A spinner that gives up silently is the worst version of this button."""
    from src.connections.services.telegram_service import TelegramService

    message = TelegramService.nothing_arrived_message()

    assert "channel" in message.lower()
    assert "never started a chat" in message.lower()
    assert "privacy mode" in message.lower()
    assert "webhook" in message.lower()
    # The likeliest cause has to come first, or the list is a wall.
    assert message.index("channel") < message.index("webhook")


def test_a_channel_post_is_reported_as_carrying_no_sender():
    """`Message.from` "may be empty for messages sent to channels".

    So posting in the channel discovers the chat id and teaches the operator
    nothing about their own user id, which is the thing they came for.
    """
    from src.connections.services.telegram_service import TelegramService

    described = TelegramService.describe_update(
        {
            "update_id": 5,
            "channel_post": {
                "chat": {"id": -1001, "type": "channel", "title": "NSE Swing Alerts"},
                "text": "hello",
            },
        }
    )

    assert described["senderId"] is None
    assert described["isChannelPost"] is True
    assert described["chatId"] == "-1001"


async def test_the_listen_test_reports_the_sender_id_that_a_mapping_needs(
    auth_client, monkeypatch
):
    """This is how an operator discovers their own numeric Telegram user id."""
    from src.connections.services import telegram_command_service

    await auth_client.put(
        "/api/connections/telegram",
        json={"settings": {"bot_token": BOT_TOKEN, "chat_id": CHAT_ID}},
    )

    class _Poller:
        running = True
        polling = True

        async def wait_for_update(self, seconds):
            return {
                "update_id": 9,
                "message": {
                    "from": {"id": 7654321, "first_name": "Akshay", "username": "ak"},
                    "chat": {"id": 7654321, "type": "private"},
                    "text": "/start",
                },
            }

    monkeypatch.setattr(
        telegram_command_service, "get_telegram_poller", lambda: _Poller()
    )

    response = await auth_client.post(
        "/api/connections/telegram/listen", json={"seconds": 5}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["received"] is True
    assert body["update"]["senderId"] == 7654321
    assert body["update"]["chatId"] == "7654321"
    assert body["borrowedRunningPoller"] is True
    assert "7654321" in body["message"]


async def test_discovering_an_id_grants_it_nothing(auth_client, monkeypatch):
    """Applying a discovery stays an explicit action on the users API."""
    from src.connections.services import telegram_command_service

    class _Poller:
        running = True
        polling = True

        async def wait_for_update(self, seconds):
            return {
                "update_id": 1,
                "message": {
                    "from": {"id": 4242424242},
                    "chat": {"id": 4242424242, "type": "private"},
                    "text": "hi",
                },
            }

        @staticmethod
        def status():
            return {"running": True}

    monkeypatch.setattr(
        telegram_command_service, "get_telegram_poller", lambda: _Poller()
    )
    await auth_client.put(
        "/api/connections/telegram",
        json={"settings": {"bot_token": BOT_TOKEN, "chat_id": CHAT_ID}},
    )
    await auth_client.post("/api/connections/telegram/listen", json={"seconds": 5})

    card = await auth_client.get("/api/connections/telegram")
    assert card.json()["detail"]["commandUsers"] == [], (
        "discovering an id must never map it"
    )


# --- gates ------------------------------------------------------------------
async def test_a_plain_user_cannot_reach_any_connections_endpoint(auth_client):
    """`require_admin` is what refuses; the sidebar is presentation."""
    created = await auth_client.post(
        "/api/users",
        json={
            "email": "connections-reader@abc.com",
            "firstName": "Connections",
            "lastName": "Reader",
            "password": "reader-password-123",
            "role": "ROLE_USER",
            "mustChangePassword": False,
        },
    )
    assert created.status_code in (200, 201), created.text

    import main

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/api/auth/login",
            json={
                "email": "connections-reader@abc.com",
                "password": "reader-password-123",
            },
        )
        assert login.status_code == 200, login.text

        for call in (
            client.get("/api/connections"),
            client.get("/api/connections/telegram"),
            client.get("/api/connections/alerts"),
            client.put("/api/connections/telegram", json={"settings": {}}),
            client.post("/api/connections/telegram/validate", json={"settings": {}}),
            client.post("/api/connections/telegram/test-message"),
            client.post("/api/connections/telegram/listen", json={"seconds": 5}),
        ):
            refused = await call
            assert refused.status_code == 403, refused.text


async def test_connections_is_an_admin_only_page(auth_client):
    """In role-pages.json, and NOT gated by a strategy toggle."""
    from src.strategies.services.strategy_registry import StrategyRegistry
    from src.users.services.role_service import pages_for_role

    assert "/connections" in pages_for_role("ROLE_ACCOUNT_ADMIN")
    assert "/connections" not in pages_for_role("ROLE_USER")
    assert "/connections" not in StrategyRegistry.gated_pages()


async def test_the_listen_test_does_not_borrow_a_poller_that_is_consuming_nothing(
    auth_client, monkeypatch
):
    """The bug this property exists to prevent, asserted directly.

    The command poller task starts unconditionally and re-reads the connection
    each pass, so switching commands on takes effect without a restart -- which
    means that while commands are OFF the task is ALIVE and calling nothing.
    Borrowing that stream would make the listen test wait its whole window and
    report "nothing arrived" for a message that did in fact arrive.

    And the listen test has to work with commands switched off, because it is
    how you switch them on.
    """
    from src.connections.services import telegram_command_service

    await auth_client.put(
        "/api/connections/telegram",
        json={
            "settings": {
                "bot_token": BOT_TOKEN,
                "chat_id": CHAT_ID,
                "commands_enabled": "false",
            }
        },
    )

    borrowed = []

    class _IdlePoller:
        """Alive, but consuming nothing -- commands are switched off."""

        running = True
        polling = False

        async def wait_for_update(self, seconds):
            borrowed.append(seconds)
            return None

        @staticmethod
        def status():
            return {"running": True, "enabled": False}

    monkeypatch.setattr(
        telegram_command_service, "get_telegram_poller", lambda: _IdlePoller()
    )

    polled = []

    async def _poll_directly(self, client, seconds):
        polled.append(seconds)
        return {
            "update_id": 3,
            "message": {
                "from": {"id": 5150, "first_name": "Akshay"},
                "chat": {"id": 5150, "type": "private"},
                "text": "hello",
            },
        }

    from src.connections.services.telegram_service import TelegramService

    monkeypatch.setattr(TelegramService, "_poll_directly", _poll_directly)

    response = await auth_client.post(
        "/api/connections/telegram/listen", json={"seconds": 5}
    )

    assert response.status_code == 200, response.text
    assert borrowed == [], "it must not borrow a stream nobody is reading"
    assert polled == [5], "it must poll directly instead"
    assert response.json()["borrowedRunningPoller"] is False
    assert response.json()["update"]["senderId"] == 5150


def test_the_poller_distinguishes_being_alive_from_consuming():
    """`running` and `polling` are different questions."""
    from src.connections.services.telegram_command_service import (
        TelegramCommandPoller,
    )

    poller = TelegramCommandPoller()
    assert poller.running is False
    assert poller.polling is False

    poller.enabled = True
    assert poller.polling is False, "enabled but not running is not polling"


# --- the validate probe, and the misdiagnosis it used to make ---------------
def test_the_probe_skips_a_strategy_that_has_no_option_chain():
    """The bug that reported a WORKING token as rejected.

    `validate_credentials` picked the first ENABLED strategy and assumed it
    could be probed. `nse-swing-momentum` declares no `underlying_scrip` on
    purpose -- a rotation over five hundred equities has no single underlying --
    so from the day MCX crude was switched off the probe sent a null security
    id, Dhan answered `400 Invalid SecurityId`, and the page told the operator
    to regenerate a credential that was working.
    """
    from src.settings.services.settings_service import SettingsService
    from src.strategies.services.strategy_registry import get_strategy_registry

    registry = get_strategy_registry()
    # The exact live configuration that broke it: the equity rotation on, the
    # only strategy with an option chain off.
    registry.set_enabled("mcx-crude-options", False)
    registry.set_enabled("nse-swing-momentum", True)
    try:
        probe = SettingsService._probe_target()

        assert probe is not None, "a switched-off module's underlying is still probeable"
        definition, scrip, segment = probe
        assert definition.key == "mcx-crude-options"
        assert scrip is not None, "the probe must never send a null security id"
        assert segment == "MCX_COMM"
    finally:
        registry.set_enabled("mcx-crude-options", True)


def test_the_probe_prefers_an_enabled_strategy_that_can_be_probed():
    """Preference, not indifference: probe what is actually running when it can
    be probed at all."""
    from src.settings.services.settings_service import SettingsService
    from src.strategies.services.strategy_registry import get_strategy_registry

    registry = get_strategy_registry()
    registry.set_enabled("mcx-crude-options", True)

    definition, _scrip, _segment = SettingsService._probe_target()
    assert definition.key == "mcx-crude-options"


def test_a_malformed_probe_is_not_reported_as_a_rejected_credential(
    monkeypatch,
):
    """`Invalid SecurityId` is a REQUEST error, and Dhan only sends one after
    authenticating the caller. A rejected token is a 401.

    The old test was `"invalid" in detail`, which matches `Invalid SecurityId`
    -- so a working token was reported as rejected. Telling somebody to
    regenerate a credential that works is the worst answer this check can give.
    """
    from src.settings.services.settings_service import SettingsService

    authentication_failures = (
        'http 401 {"status":"failed"}',
        "unauthorized",
        "invalid token",
        "invalid access token",
        "token expired",
    )
    request_failures = (
        'http 400 {"data":{"813":"invalid securityid"},"status":"failed"}',
        'http 400 {"data":{"806":"invalid expiry date"}}',
        "http 500 internal server error",
    )

    for detail in authentication_failures:
        assert SettingsService._is_authentication_failure(detail), detail
    for detail in request_failures:
        assert not SettingsService._is_authentication_failure(detail), detail


async def test_a_400_from_dhan_says_the_credentials_WORK(auth_client, monkeypatch):
    """End to end: a structured 400 must not advise regenerating the token."""
    from src.market.services import dhan_option_chain_client as module

    async def _refuse(self, *args, **kwargs):
        raise module.OptionChainError(
            'Option chain request failed: HTTP 400 '
            '{"data":{"813":"Invalid SecurityId"},"status":"failed"}'
        )

    monkeypatch.setattr(module.DhanOptionChainClient, "fetch_expiry_list", _refuse)

    await auth_client.put(
        "/api/settings",
        json={"syntheticFeed": True, "clientId": "1100123456", "accessToken": _token()},
    )
    response = await auth_client.post(
        "/api/connections/dhan/validate", json={"settings": {}}
    )

    assert response.status_code == 200, response.text
    message = response.json()["message"]
    assert "regenerate" not in message.lower(), (
        "a 400 must never advise replacing a working token"
    )
    assert "rejected these credentials" not in message.lower()
    assert "accepted" in message.lower()


def _token(hours: int = 20) -> str:
    import time

    import jwt

    return jwt.encode(
        {"dhanClientId": "1100123456", "exp": int(time.time()) + hours * 3600},
        "not-the-real-signing-key",
        algorithm="HS256",
    )


async def test_validating_records_the_check_so_the_pill_stops_saying_never(
    auth_client, monkeypatch
):
    """The other half of the report: the tile still said "Never checked".

    A check RECORDS its outcome, so the pill and the age must move. A page that
    says "never checked" immediately after checking is contradicting itself
    about the one thing it exists to report.
    """
    from src.market.services import dhan_option_chain_client as module

    async def _ok(self, *args, **kwargs):
        return ["2026-10-15", "2026-11-17"]

    monkeypatch.setattr(module.DhanOptionChainClient, "fetch_expiry_list", _ok)

    await auth_client.put(
        "/api/settings",
        json={"syntheticFeed": True, "clientId": "1100123456", "accessToken": _token()},
    )
    before = await auth_client.get("/api/connections/dhan")
    assert before.json()["status"] == "NEVER_CHECKED"
    assert before.json()["lastCheckedAt"] is None

    validated = await auth_client.post(
        "/api/connections/dhan/validate", json={"settings": {}}
    )
    assert validated.json()["valid"] is True, validated.text

    after = await auth_client.get("/api/connections/dhan")
    assert after.json()["lastCheckedAt"] is not None
    assert after.json()["lastCheckOk"] is True
    assert after.json()["status"] == "CONNECTED"
