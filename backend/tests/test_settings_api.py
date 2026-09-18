"""Settings API: precedence over .env, the synthetic-feed rule, and secrecy."""
import os
import time

import jwt
import pytest

from src import config_utils
from src.database.session import get_session_factory
from src.settings.database.db_operations.app_setting_repository import (
    AppSettingRepository,
)
from src.settings.services.settings_service import (
    KEY_ACCESS_TOKEN,
    KEY_CLIENT_ID,
    KEY_SYNTHETIC_FEED,
    SettingsService,
)


def _token(hours: int = 20, client_id: str = "1100123456") -> str:
    return jwt.encode(
        {"dhanClientId": client_id, "exp": int(time.time()) + hours * 3600},
        "dhan-key",
        algorithm="HS256",
    )


@pytest.fixture
async def service(db_session):
    return SettingsService(AppSettingRepository(db_session))


# --- auth ------------------------------------------------------------------
async def test_settings_endpoints_require_authentication(api_client):
    assert (await api_client.get("/api/settings")).status_code == 401
    assert (await api_client.put("/api/settings", json={})).status_code == 401
    assert (await api_client.post("/api/settings/validate", json={})).status_code == 401


# --- the synthetic-feed rule ----------------------------------------------
async def test_credentials_are_optional_while_the_synthetic_feed_is_on(auth_client):
    response = await auth_client.put("/api/settings", json={"syntheticFeed": True})

    assert response.status_code == 200
    assert response.json()["settings"]["syntheticFeed"] is True


async def test_live_mode_requires_both_credentials(auth_client):
    response = await auth_client.put("/api/settings", json={"syntheticFeed": False})

    assert response.status_code == 400
    assert "required" in response.json()["detail"].lower()


async def test_live_mode_is_rejected_with_only_a_client_id(auth_client):
    response = await auth_client.put(
        "/api/settings", json={"syntheticFeed": False, "clientId": "1100123456"}
    )
    assert response.status_code == 400


async def test_live_mode_is_accepted_with_both_credentials(auth_client):
    response = await auth_client.put(
        "/api/settings",
        json={
            "syntheticFeed": False,
            "clientId": "1100123456",
            "accessToken": _token(),
        },
    )

    assert response.status_code == 200
    assert response.json()["settings"]["syntheticFeed"] is False


async def test_live_mode_can_reuse_an_already_stored_token(auth_client):
    """Turning the live feed on must not force the token to be pasted again."""
    await auth_client.put(
        "/api/settings",
        json={"syntheticFeed": True, "clientId": "1100123456", "accessToken": _token()},
    )

    response = await auth_client.put(
        "/api/settings", json={"syntheticFeed": False, "clientId": "1100123456"}
    )

    assert response.status_code == 200


# --- secrecy ---------------------------------------------------------------
async def test_the_access_token_is_never_returned_to_the_browser(auth_client):
    token = _token()
    await auth_client.put(
        "/api/settings",
        json={"syntheticFeed": True, "clientId": "1100123456", "accessToken": token},
    )

    body = (await auth_client.get("/api/settings")).text

    assert token not in body
    assert "accessToken" not in body or token not in body


async def test_the_response_carries_a_mask_and_the_decoded_expiry(auth_client):
    await auth_client.put(
        "/api/settings",
        json={"syntheticFeed": True, "clientId": "1100123456", "accessToken": _token(20)},
    )

    token_info = (await auth_client.get("/api/settings")).json()["token"]

    assert token_info["present"] is True
    assert token_info["masked"].startswith("eyJh")
    assert token_info["isJwt"] is True
    assert token_info["expired"] is False
    assert 19 * 3600 < token_info["secondsRemaining"] <= 20 * 3600
    assert token_info["dhanClientId"] == "1100123456"


async def _dhan_setting(session, key: str):
    """One setting of the `dhan` CONNECTION.

    The credentials moved out of `app_settings` into the `connections` tables
    on 2026-09-18. The INVARIANT did not move: a secret lives in
    `encrypted_value` and nowhere else, enforced in the repository. These two
    tests assert that invariant in its new home rather than asserting the old
    address.
    """
    from src.connections.database.db_operations.connection_repository import (
        ConnectionRepository,
        ConnectionSettingRepository,
    )

    connection = await ConnectionRepository(session).get_by_provider("dhan")
    if connection is None:
        return None
    return await ConnectionSettingRepository(session).get_by_key(connection.id, key)


async def test_the_token_is_stored_encrypted_not_in_the_plaintext_column(
    auth_client, db_session
):
    token = _token()
    await auth_client.put(
        "/api/settings",
        json={"syntheticFeed": True, "clientId": "1100123456", "accessToken": token},
    )

    session = get_session_factory()()
    try:
        stored = await _dhan_setting(session, "access_token")
        assert stored is not None
        assert stored.is_encrypted is True
        assert stored.value is None, "a secret must never land in the plaintext column"
        assert stored.encrypted_value and token not in stored.encrypted_value
        # And it must NOT have been left behind in app_settings, or two copies
        # of one token would drift apart the first time one was renewed.
        assert await AppSettingRepository(session).get_by_key(KEY_ACCESS_TOKEN) is None
    finally:
        await session.close()


async def test_the_client_id_is_stored_as_plain_text(auth_client, db_session):
    await auth_client.put(
        "/api/settings", json={"syntheticFeed": True, "clientId": "1100123456"}
    )

    session = get_session_factory()()
    try:
        stored = await _dhan_setting(session, "client_id")
        assert stored is not None
        assert stored.value == "1100123456"
        assert stored.is_encrypted is False
        assert await AppSettingRepository(session).get_by_key(KEY_CLIENT_ID) is None
    finally:
        await session.close()


# --- update semantics ------------------------------------------------------
async def test_an_omitted_token_leaves_the_stored_one_untouched(auth_client):
    original = _token(20)
    await auth_client.put(
        "/api/settings",
        json={"syntheticFeed": True, "clientId": "1100123456", "accessToken": original},
    )
    before = (await auth_client.get("/api/settings")).json()["token"]["masked"]

    await auth_client.put(
        "/api/settings", json={"syntheticFeed": True, "clientId": "9999999999"}
    )
    after = (await auth_client.get("/api/settings")).json()

    assert after["token"]["masked"] == before
    assert after["clientId"] == "9999999999"


async def test_a_token_can_be_explicitly_cleared(auth_client):
    await auth_client.put(
        "/api/settings",
        json={"syntheticFeed": True, "clientId": "1100123456", "accessToken": _token()},
    )

    await auth_client.put(
        "/api/settings",
        json={"syntheticFeed": True, "clientId": "1100123456", "clearAccessToken": True},
    )

    assert (await auth_client.get("/api/settings")).json()["token"]["present"] is False


# --- precedence over .env --------------------------------------------------
async def test_saved_settings_override_the_env_fallback(service, monkeypatch):
    monkeypatch.setenv("DHAN_CLIENT_ID", "env-client-id")
    monkeypatch.setenv("DHAN_SYNTHETIC_FEED", "true")

    # Turning the synthetic feed off requires a token, so supply one.
    await service.save(
        client_id="db-client-id", access_token=_token(), synthetic_feed=False
    )
    effective = await service.effective()

    assert effective["clientId"] == "db-client-id"
    assert effective["clientIdSource"] == "database"
    assert effective["syntheticFeed"] is False
    assert effective["syntheticFeedSource"] == "database"


async def test_env_is_used_when_nothing_is_saved(service, monkeypatch):
    monkeypatch.setenv("DHAN_CLIENT_ID", "env-client-id")

    effective = await service.effective()

    assert effective["clientId"] == "env-client-id"
    assert effective["clientIdSource"] == "env"


async def test_applying_settings_overlays_the_live_config(service):
    """This is what makes every existing config_utils caller see the UI value."""
    await service.save(
        client_id="1100123456", access_token=_token(), synthetic_feed=False
    )

    assert config_utils.get_property_value("dhan.client_id") == "1100123456"
    assert config_utils.get_property_value_boolean("market_feed.synthetic_feed") is False
    assert config_utils.get_property_value("dhan.access_token")


async def test_settings_survive_a_reload_of_the_service(db_session):
    """A fresh service instance (as after a restart) must see stored values."""
    first = SettingsService(AppSettingRepository(db_session))
    await first.save(client_id="1100123456", access_token=_token(), synthetic_feed=False)

    second = SettingsService(AppSettingRepository(db_session))
    effective = await second.effective()

    assert effective["clientId"] == "1100123456"
    assert effective["syntheticFeed"] is False
    assert effective["token"]["present"] is True


# --- validation ------------------------------------------------------------
async def test_validation_rejects_an_expired_token_without_calling_dhan(auth_client):
    expired = jwt.encode({"exp": int(time.time()) - 60}, "k", algorithm="HS256")

    response = await auth_client.post(
        "/api/settings/validate",
        json={"clientId": "1100123456", "accessToken": expired},
    )
    body = response.json()

    assert body["valid"] is False
    assert "expired" in body["message"].lower()
    assert body["detail"] is None, "no network call should have been attempted"


async def test_validation_catches_a_client_id_that_disagrees_with_the_token(auth_client):
    response = await auth_client.post(
        "/api/settings/validate",
        json={"clientId": "9999999999", "accessToken": _token(client_id="1100123456")},
    )
    body = response.json()

    assert body["valid"] is False
    assert "1100123456" in body["message"]
    assert body["detail"] is None


async def test_validation_without_any_credentials_asks_for_them(auth_client):
    response = await auth_client.post("/api/settings/validate", json={})
    body = response.json()

    assert body["valid"] is False
    assert "client ID" in body["message"]
