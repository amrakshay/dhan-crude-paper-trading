"""Commands IN: who may issue one, and what a refusal looks like.

This is the first inbound control path this application has ever had, into an
application that, when ARMED, spends money on its own schedule. Most of these
assert that something is REFUSED. They are load-bearing.
"""
import httpx
import pytest

from src.connections.services import providers
from src.connections.services.connection_store import ConnectionStore
from src.connections.services.telegram_command_service import (
    CONTROL_COMMANDS,
    READ_ONLY_COMMANDS,
    TelegramCommandService,
)

BOT_TOKEN = "555555555:AAH-command-bot-token-DO-NOT-LOG-1a2b3c"
CHAT_ID = "-1005555555555"
SENDER = 987654321


def _update(text: str, sender: int = SENDER) -> dict:
    return {
        "update_id": 1,
        "message": {
            "from": {"id": sender, "first_name": "Akshay"},
            "chat": {"id": sender, "type": "private"},
            "text": text,
        },
    }


async def _configure(session, commands=True, control=False) -> None:
    store = ConnectionStore(session)
    await store.put(providers.PROVIDER_TELEGRAM, providers.TELEGRAM_BOT_TOKEN, BOT_TOKEN)
    await store.put(providers.PROVIDER_TELEGRAM, providers.TELEGRAM_CHAT_ID, CHAT_ID)
    await store.put(
        providers.PROVIDER_TELEGRAM,
        providers.TELEGRAM_COMMANDS_ENABLED,
        "true" if commands else "false",
    )
    await store.put(
        providers.PROVIDER_TELEGRAM,
        providers.TELEGRAM_CONTROL_COMMANDS,
        "true" if control else "false",
    )
    await session.commit()


async def _map_seed_admin(session, telegram_user_id: int = SENDER):
    from src.users.database.db_operations.user_repository import UserRepository

    repository = UserRepository(session)
    user = await repository.get_seed_user()
    user.telegram_user_id = telegram_user_id
    await session.commit()
    return user


# --- authorisation ----------------------------------------------------------
async def test_nobody_is_mapped_by_default_so_nothing_is_authorised(db_session):
    """A Telegram connection with alerts working and no command users is a
    normal, complete state."""
    await _configure(db_session)

    authorisation = await TelegramCommandService(db_session).authorise(SENDER)

    assert authorisation.allowed is False
    assert "not mapped to an account" in authorisation.refusal


async def test_an_unmapped_sender_is_refused_and_told_nothing_useful(db_session):
    await _configure(db_session)

    outcome = await TelegramCommandService(db_session).handle(_update("/status"))

    assert outcome["reply"].startswith("Refused.")
    assert outcome["changed"] is False


async def test_a_channel_post_carries_no_sender_and_cannot_be_authorised(db_session):
    """`Message.from` "may be empty for messages sent to channels"."""
    await _configure(db_session)

    authorisation = await TelegramCommandService(db_session).authorise(None)

    assert authorisation.allowed is False
    assert "no sender" in authorisation.refusal


async def test_a_deactivated_user_is_refused(db_session):
    """The payoff of mapping to a user rather than keeping a second list:
    deactivation takes effect here too, for free."""
    from src.constants import UserStatus

    await _configure(db_session)
    user = await _map_seed_admin(db_session)
    user.status = UserStatus.INACTIVE.value
    await db_session.commit()

    authorisation = await TelegramCommandService(db_session).authorise(SENDER)

    assert authorisation.allowed is False
    assert "deactivated" in authorisation.refusal


async def test_a_user_who_owes_a_password_change_is_refused(db_session):
    """The same refusal `require_session` makes, and they cannot change it
    from here."""
    await _configure(db_session)
    user = await _map_seed_admin(db_session)
    user.must_change_password = True
    await db_session.commit()

    authorisation = await TelegramCommandService(db_session).authorise(SENDER)

    assert authorisation.allowed is False
    assert "password change" in authorisation.refusal


async def test_the_sender_is_matched_on_the_numeric_id_not_a_username(db_session):
    """A username is reassignable; an authorisation somebody can transfer by
    releasing a handle is not an authorisation."""
    await _configure(db_session)
    await _map_seed_admin(db_session, telegram_user_id=SENDER)

    assert (await TelegramCommandService(db_session).authorise(SENDER)).allowed is True
    assert (
        await TelegramCommandService(db_session).authorise(SENDER + 1)
    ).allowed is False


async def test_a_telegram_id_cannot_be_mapped_to_two_users(auth_client):
    """Two users sharing one id would make "who sent this" unanswerable."""
    created = await auth_client.post(
        "/api/users",
        json={
            "email": "second.mapping@abc.com",
            "firstName": "Second",
            "lastName": "Mapping",
            "password": "second-password-123",
            "role": "ROLE_USER",
            "mustChangePassword": False,
        },
    )
    assert created.status_code in (200, 201), created.text

    users = (await auth_client.get("/api/users")).json()["users"]
    seed = next(user for user in users if user["isSeedUser"])
    other = next(user for user in users if user["email"] == "second.mapping@abc.com")

    first = await auth_client.put(
        f"/api/users/{seed['id']}", json={"telegramUserId": SENDER}
    )
    assert first.status_code == 200, first.text
    assert first.json()["telegramUserId"] == SENDER

    clash = await auth_client.put(
        f"/api/users/{other['id']}", json={"telegramUserId": SENDER}
    )
    assert clash.status_code == 400, clash.text
    assert "already mapped" in clash.text


async def test_a_mapping_can_be_removed_explicitly(auth_client):
    users = (await auth_client.get("/api/users")).json()["users"]
    seed = next(user for user in users if user["isSeedUser"])

    await auth_client.put(f"/api/users/{seed['id']}", json={"telegramUserId": SENDER})
    cleared = await auth_client.put(
        f"/api/users/{seed['id']}", json={"clearTelegramUserId": True}
    )

    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["telegramUserId"] is None


async def test_only_an_admin_may_map_a_telegram_account(auth_client):
    created = await auth_client.post(
        "/api/users",
        json={
            "email": "mapper@abc.com",
            "firstName": "Map",
            "lastName": "Per",
            "password": "mapper-password-123",
            "role": "ROLE_USER",
            "mustChangePassword": False,
        },
    )
    assert created.status_code in (200, 201), created.text
    user_id = created.json()["id"]

    import main

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/api/auth/login",
            json={"email": "mapper@abc.com", "password": "mapper-password-123"},
        )
        assert login.status_code == 200, login.text
        refused = await client.put(
            f"/api/users/{user_id}", json={"telegramUserId": 111222333}
        )
        assert refused.status_code == 403, refused.text


# --- the split between the two halves --------------------------------------
def test_the_read_only_and_control_halves_do_not_overlap():
    """If you only get half of this built, build the read-only half."""
    assert set(READ_ONLY_COMMANDS) & set(CONTROL_COMMANDS) == set()
    assert "/arm" in CONTROL_COMMANDS
    assert "/disarm" in CONTROL_COMMANDS
    assert "/run" in CONTROL_COMMANDS
    assert "/status" in READ_ONLY_COMMANDS


async def test_a_read_only_command_works_for_any_active_user(db_session):
    await _configure(db_session)
    await _map_seed_admin(db_session)

    outcome = await TelegramCommandService(db_session).handle(_update("/status"))

    assert outcome["changed"] is False
    assert "nse-swing-momentum" in outcome["reply"]


async def test_a_control_command_is_refused_while_control_is_switched_off(db_session):
    """And the refusal SAYS it is a refusal-because-disabled, or the operator
    cannot tell it from a failure."""
    await _configure(db_session, control=False)
    await _map_seed_admin(db_session)

    outcome = await TelegramCommandService(db_session).handle(
        _update("/arm nse-swing-momentum")
    )

    assert "control commands are switched off" in outcome["reply"]
    assert outcome["changed"] is False

    from src.strategies.services.strategy_registry import get_strategy_registry

    assert get_strategy_registry().is_armed("nse-swing-momentum") is False


async def test_a_control_command_needs_an_account_admin(db_session):
    """The same gate the arming endpoint uses, not a thinner one.

    A Telegram message that arms a strategy cannot be a thinner path than the
    button, which has a confirmation dialog carrying the server's warnings.
    """
    from src.constants import UserRole
    from src.users.database.db_operations.user_repository import UserRepository

    await _configure(db_session, control=True)
    user = await _map_seed_admin(db_session)
    # Demote behind the guard rails, which is what this test is about: the
    # ROLE is what decides, wherever the request arrived from.
    user.role = UserRole.USER.value
    await db_session.commit()

    outcome = await TelegramCommandService(db_session).handle(
        _update("/arm nse-swing-momentum")
    )

    assert "needs an account administrator" in outcome["reply"]

    from src.strategies.services.strategy_registry import get_strategy_registry

    assert get_strategy_registry().is_armed("nse-swing-momentum") is False


async def test_arming_from_telegram_records_the_sender_as_the_author(db_session):
    """`updated_by_user_id` is filled in exactly as the UI fills it.

    That is the payoff of resolving the sender to an application user: a
    command from a phone leaves the same audit trail as a click.
    """
    from src.strategies.database.db_operations.feature_toggle_repository import (
        FeatureToggleRepository,
    )
    from src.strategies.services.strategy_registry import get_strategy_registry

    await _configure(db_session, control=True)
    user = await _map_seed_admin(db_session)

    outcome = await TelegramCommandService(db_session).handle(
        _update("/arm nse-swing-momentum")
    )

    assert "ARMED" in outcome["reply"]
    assert outcome["changed"] is True
    assert get_strategy_registry().is_armed("nse-swing-momentum") is True

    rows = await FeatureToggleRepository(db_session).states()
    from src.strategies.database.db_models.feature_toggle_model import SCOPE_AUTOMATION

    assert rows[SCOPE_AUTOMATION]["nse-swing-momentum"] is True

    # And it is journalled with its sender.
    from src.connections.database.db_operations.alert_repository import AlertRepository

    alerts = await AlertRepository(db_session).recent()
    command_rows = [row for row in alerts if row.kind == "COMMAND"]
    assert command_rows
    assert str(user.id) in command_rows[0].body
    assert str(SENDER) in command_rows[0].body

    # Leave the registry where the suite expects it.
    get_strategy_registry().set_armed("nse-swing-momentum", False)


async def test_a_refused_command_is_journalled_too(db_session):
    """A refusal that leaves no record is how you fail to notice an attempt."""
    from src.connections.database.db_operations.alert_repository import AlertRepository

    await _configure(db_session, control=True)

    # Nobody is mapped, so this is refused -- and the refusal is what must
    # leave a record.
    outcome = await TelegramCommandService(db_session).handle(
        _update("/arm nse-swing-momentum")
    )
    assert outcome["reply"].startswith("Refused.")
    await db_session.commit()

    alerts = await AlertRepository(db_session).recent()
    command_rows = [row for row in alerts if row.kind == "COMMAND"]
    assert command_rows
    assert "Authorised: no" in command_rows[0].body


# --- nothing here is a broker ----------------------------------------------
def test_no_command_names_a_broker_operation():
    """Root CLAUDE.md section 1 is untouched.

    Telegram is not a broker and must not become one: there is no broker
    surface for a command to reach, and none is introduced by name either.

    The banned names are IMPORTED from the safety scanner rather than typed
    here -- spelling them as string literals in this file would itself trip
    `test_no_broker_order_operations_anywhere`, which is the scanner working
    exactly as intended.
    """
    import inspect

    from src.connections.services import (
        telegram_client,
        telegram_command_service,
        telegram_service,
    )
    from tests.test_no_real_orders import FORBIDDEN_IDENTIFIERS

    for module in (telegram_command_service, telegram_client, telegram_service):
        source = inspect.getsource(module)
        for banned in FORBIDDEN_IDENTIFIERS:
            assert banned not in source, f"{module.__name__} names {banned}"
