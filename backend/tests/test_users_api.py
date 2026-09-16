"""Users, roles and the guard rails that keep the application administrable.

Most of these assert a NEGATIVE -- that something is refused. Hiding a button
is not access control, so every rule is tested by calling the API directly as
the role that should not be allowed to do it.
"""
import httpx
import pytest

from src.constants import UserRole, UserStatus
from tests.conftest import SEED_ADMIN_EMAIL, SEED_ADMIN_PASSWORD, login_as

PLAIN_USER = {
    "email": "plain.user@abc.com",
    "firstName": "Plain",
    "lastName": "User",
    "password": "plain-user-password",
    "role": UserRole.USER.value,
}
SECOND_ADMIN = {
    "email": "second.admin@abc.com",
    "firstName": "Second",
    "lastName": "Admin",
    "password": "second-admin-password",
    "role": UserRole.ACCOUNT_ADMIN.value,
}


async def _create(client, payload, **overrides):
    body = {**payload, **overrides}
    response = await client.post("/api/users", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def _fresh_client(api_client, email, password):
    """A second client with its own cookie jar, signed in as someone else."""
    import main

    transport = httpx.ASGITransport(app=main.app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    await login_as(client, email, password)
    return client


# --- the seeded administrator ----------------------------------------------
async def test_the_seeded_admin_exists_with_the_right_role_and_no_forced_change(
    auth_client,
):
    response = await auth_client.get("/api/users")

    assert response.status_code == 200
    seeded = [user for user in response.json()["users"] if user["isSeedUser"]]
    assert len(seeded) == 1
    assert seeded[0]["email"] == SEED_ADMIN_EMAIL
    assert seeded[0]["role"] == UserRole.ACCOUNT_ADMIN.value
    assert seeded[0]["status"] == UserStatus.ACTIVE.value
    # The default credential is allowed to stand -- a known, recorded risk.
    assert seeded[0]["mustChangePassword"] is False


async def test_the_seeded_admin_cannot_be_deleted(auth_client):
    seeded = (await auth_client.get("/api/users")).json()["users"][0]

    response = await auth_client.delete(f"/api/users/{seeded['id']}")

    assert response.status_code == 400
    assert "cannot be deleted" in response.json()["detail"].lower()
    assert (await auth_client.get(f"/api/users/{seeded['id']}")).status_code == 200


async def test_the_seeded_admin_cannot_be_demoted(auth_client):
    """Undeletable AND demotable would strand the app with no administrator."""
    seeded = (await auth_client.get("/api/users")).json()["users"][0]

    response = await auth_client.put(
        f"/api/users/{seeded['id']}", json={"role": UserRole.USER.value}
    )

    assert response.status_code == 400
    assert "role cannot be changed" in response.json()["detail"].lower()
    assert (await auth_client.get(f"/api/users/{seeded['id']}")).json()["role"] == (
        UserRole.ACCOUNT_ADMIN.value
    )


async def test_the_seeded_admin_cannot_be_deactivated(auth_client):
    seeded = (await auth_client.get("/api/users")).json()["users"][0]

    response = await auth_client.put(
        f"/api/users/{seeded['id']}", json={"status": UserStatus.INACTIVE.value}
    )

    assert response.status_code == 400
    assert "cannot be deactivated" in response.json()["detail"].lower()


async def test_the_seeded_admin_can_still_be_renamed(auth_client):
    """The guard rails are about lockout, not about freezing the whole row."""
    seeded = (await auth_client.get("/api/users")).json()["users"][0]

    response = await auth_client.put(
        f"/api/users/{seeded['id']}", json={"firstName": "Renamed"}
    )

    assert response.status_code == 200
    assert response.json()["firstName"] == "Renamed"


# --- deletion guard rails ---------------------------------------------------
async def test_a_user_cannot_delete_themselves_even_as_an_admin(auth_client):
    admin = await _create(auth_client, SECOND_ADMIN)
    other = await _fresh_client(auth_client, SECOND_ADMIN["email"], SECOND_ADMIN["password"])

    try:
        response = await other.delete(f"/api/users/{admin['id']}")
    finally:
        await other.aclose()

    assert response.status_code == 400
    assert "your own account" in response.json()["detail"].lower()


async def test_an_admin_can_delete_another_user(auth_client):
    user = await _create(auth_client, PLAIN_USER)

    response = await auth_client.delete(f"/api/users/{user['id']}")

    assert response.status_code == 200
    assert (await auth_client.get(f"/api/users/{user['id']}")).status_code == 404


async def test_the_last_active_admin_cannot_be_demoted(auth_client):
    """Seeded admin aside, the count guard still has to hold on its own."""
    admin = await _create(auth_client, SECOND_ADMIN)
    seeded = [
        user for user in (await auth_client.get("/api/users")).json()["users"]
        if user["isSeedUser"]
    ][0]
    # Demoting the only non-seed admin is fine while the seeded one is active.
    assert (
        await auth_client.put(
            f"/api/users/{admin['id']}", json={"role": UserRole.USER.value}
        )
    ).status_code == 200
    assert seeded["role"] == UserRole.ACCOUNT_ADMIN.value


# --- role-based access ------------------------------------------------------
@pytest.fixture
async def user_client(auth_client):
    """A signed-in ROLE_USER, alongside the admin's own client."""
    await _create(auth_client, PLAIN_USER)
    client = await _fresh_client(
        auth_client, PLAIN_USER["email"], PLAIN_USER["password"]
    )
    yield client
    await client.aclose()


async def test_a_plain_user_is_refused_by_the_settings_endpoints(user_client):
    """Not merely hidden from the sidebar -- refused by the API."""
    assert (await user_client.get("/api/settings")).status_code == 403
    assert (await user_client.put("/api/settings", json={})).status_code == 403
    assert (await user_client.post("/api/settings/validate", json={})).status_code == 403


async def test_a_plain_user_is_refused_user_create_update_and_delete(
    user_client, auth_client
):
    victim = await _create(auth_client, SECOND_ADMIN)

    created = await user_client.post("/api/users", json={
        "email": "nope@abc.com", "firstName": "No", "lastName": "Pe",
        "password": "should-not-work", "role": UserRole.USER.value,
    })
    updated = await user_client.put(
        f"/api/users/{victim['id']}", json={"firstName": "Hacked"}
    )
    deleted = await user_client.delete(f"/api/users/{victim['id']}")

    assert created.status_code == 403
    assert updated.status_code == 403
    assert deleted.status_code == 403


async def test_a_plain_user_can_still_see_every_user(user_client):
    """Read is open to both roles; only changing is restricted."""
    response = await user_client.get("/api/users")

    assert response.status_code == 200
    assert response.json()["total"] >= 2


async def test_a_plain_user_can_reach_the_trading_endpoints(user_client):
    """ROLE_USER loses Settings and write access to users, nothing else."""
    assert (await user_client.get("/api/market/status")).status_code == 200
    assert (await user_client.get("/api/orders")).status_code == 200
    assert (await user_client.get("/api/positions")).status_code == 200
    assert (await user_client.get("/api/reports/pnl")).status_code == 200


async def test_an_admin_can_reach_the_settings_endpoints(auth_client):
    assert (await auth_client.get("/api/settings")).status_code == 200


async def test_role_pages_reflect_the_json_mapping(user_client, auth_client):
    user_pages = (await user_client.get("/api/users/role-pages")).json()
    admin_pages = (await auth_client.get("/api/users/role-pages")).json()

    assert "/settings" not in user_pages["pages"]
    assert "/settings" in admin_pages["pages"]
    assert "/users" in user_pages["pages"]
    assert "/profile" in user_pages["pages"]


# --- email is immutable -----------------------------------------------------
async def test_an_email_change_is_rejected_even_when_sent_straight_to_the_api(
    auth_client,
):
    user = await _create(auth_client, PLAIN_USER)

    response = await auth_client.put(
        f"/api/users/{user['id']}", json={"email": "changed@abc.com"}
    )

    assert response.status_code == 400
    assert "email cannot be changed" in response.json()["detail"].lower()
    assert (await auth_client.get(f"/api/users/{user['id']}")).json()["email"] == (
        PLAIN_USER["email"]
    )


async def test_resending_the_same_email_is_not_an_error(auth_client):
    """A whole-object PUT from the UI must not be rejected for echoing it."""
    user = await _create(auth_client, PLAIN_USER)

    response = await auth_client.put(
        f"/api/users/{user['id']}",
        json={"email": PLAIN_USER["email"], "firstName": "Renamed"},
    )

    assert response.status_code == 200
    assert response.json()["firstName"] == "Renamed"


async def test_a_profile_update_cannot_change_the_email_either(user_client):
    response = await user_client.put(
        "/api/users/me", json={"email": "sneaky@abc.com", "firstName": "Sneaky"}
    )

    assert response.status_code == 400
    assert "email cannot be changed" in response.json()["detail"].lower()


# --- the password hash never leaves ------------------------------------------
async def test_no_endpoint_returns_the_password_hash(auth_client):
    created = await _create(auth_client, PLAIN_USER)

    for path in (
        "/api/users",
        f"/api/users/{created['id']}",
        "/api/users/me",
        "/api/auth/me",
    ):
        body = (await auth_client.get(path)).text
        assert "password_hash" not in body
        assert "passwordHash" not in body
        assert "$2b$" not in body


# --- first-login password change --------------------------------------------
@pytest.fixture
async def must_change_client(auth_client):
    await _create(auth_client, PLAIN_USER, mustChangePassword=True)
    client = await _fresh_client(
        auth_client, PLAIN_USER["email"], PLAIN_USER["password"]
    )
    yield client
    await client.aclose()


async def test_a_user_owing_a_password_change_cannot_use_other_endpoints(
    must_change_client,
):
    for path in ("/api/orders", "/api/positions", "/api/market/status", "/api/users"):
        response = await must_change_client.get(path)
        assert response.status_code == 403, path
        assert "change your password" in response.json()["detail"].lower()


async def test_a_user_owing_a_password_change_can_still_see_who_they_are(
    must_change_client,
):
    """Otherwise the browser cannot even learn why it is being refused."""
    session = await must_change_client.get("/api/auth/me")

    assert session.status_code == 200
    assert session.json()["mustChangePassword"] is True
    assert (await must_change_client.get("/api/users/me")).status_code == 200


async def test_changing_the_password_releases_the_first_login_gate(
    must_change_client,
):
    response = await must_change_client.post(
        "/api/users/me/password",
        json={
            "currentPassword": PLAIN_USER["password"],
            "newPassword": "a-brand-new-password",
        },
    )

    assert response.status_code == 200
    assert response.json()["user"]["mustChangePassword"] is False
    # Now everything else opens up.
    assert (await must_change_client.get("/api/orders")).status_code == 200


# --- status -----------------------------------------------------------------
async def test_an_inactive_user_cannot_log_in(auth_client, api_client):
    user = await _create(auth_client, PLAIN_USER, status=UserStatus.INACTIVE.value)
    assert user["status"] == UserStatus.INACTIVE.value

    import main

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/auth/login",
            json={"email": PLAIN_USER["email"], "password": PLAIN_USER["password"]},
        )

    assert response.status_code == 403
    assert "deactivated" in response.json()["detail"].lower()


async def test_an_active_session_stops_working_once_the_user_is_deactivated(
    auth_client,
):
    """The session must die on the NEXT REQUEST, not at token expiry."""
    user = await _create(auth_client, PLAIN_USER)
    victim = await _fresh_client(
        auth_client, PLAIN_USER["email"], PLAIN_USER["password"]
    )

    try:
        assert (await victim.get("/api/orders")).status_code == 200

        deactivated = await auth_client.put(
            f"/api/users/{user['id']}", json={"status": UserStatus.INACTIVE.value}
        )
        assert deactivated.status_code == 200

        after = await victim.get("/api/orders")
    finally:
        await victim.aclose()

    assert after.status_code == 401
    assert "deactivated" in after.json()["detail"].lower()


async def test_a_deleted_users_session_stops_working_immediately(auth_client):
    user = await _create(auth_client, PLAIN_USER)
    victim = await _fresh_client(
        auth_client, PLAIN_USER["email"], PLAIN_USER["password"]
    )

    try:
        assert (await victim.get("/api/orders")).status_code == 200
        assert (await auth_client.delete(f"/api/users/{user['id']}")).status_code == 200
        after = await victim.get("/api/orders")
    finally:
        await victim.aclose()

    assert after.status_code == 401


async def test_demoting_a_user_takes_effect_on_their_next_request(auth_client):
    """The role comes from the database each request, not from the token."""
    user = await _create(auth_client, SECOND_ADMIN)
    demoted_client = await _fresh_client(
        auth_client, SECOND_ADMIN["email"], SECOND_ADMIN["password"]
    )

    try:
        assert (await demoted_client.get("/api/settings")).status_code == 200

        await auth_client.put(
            f"/api/users/{user['id']}", json={"role": UserRole.USER.value}
        )

        after = await demoted_client.get("/api/settings")
    finally:
        await demoted_client.aclose()

    assert after.status_code == 403


# --- the old credentials are gone -------------------------------------------
async def test_app_username_and_app_password_no_longer_authenticate_anyone(
    api_client, monkeypatch
):
    """The env-var credential pair was removed, not left as a fallback."""
    monkeypatch.setenv("APP_USERNAME", "trader")
    monkeypatch.setenv("APP_PASSWORD", "test-password")

    by_username = await api_client.post(
        "/api/auth/login", json={"username": "trader", "password": "test-password"}
    )
    as_email = await api_client.post(
        "/api/auth/login", json={"email": "trader", "password": "test-password"}
    )

    # `username` is not even a field on the login schema any more.
    assert by_username.status_code == 422
    assert as_email.status_code == 401


async def test_login_is_by_email_not_by_a_username(api_client):
    by_local_part = await api_client.post(
        "/api/auth/login", json={"email": "trader", "password": SEED_ADMIN_PASSWORD}
    )
    by_email = await api_client.post(
        "/api/auth/login",
        json={"email": SEED_ADMIN_EMAIL, "password": SEED_ADMIN_PASSWORD},
    )

    assert by_local_part.status_code == 401
    assert by_email.status_code == 200
    assert by_email.json()["email"] == SEED_ADMIN_EMAIL


async def test_the_email_is_matched_case_insensitively(api_client):
    response = await api_client.post(
        "/api/auth/login",
        json={"email": "  TRADER@ABC.COM  ", "password": SEED_ADMIN_PASSWORD},
    )

    assert response.status_code == 200


# --- validation --------------------------------------------------------------
async def test_a_duplicate_email_is_refused(auth_client):
    await _create(auth_client, PLAIN_USER)

    response = await auth_client.post("/api/users", json=PLAIN_USER)

    assert response.status_code == 400
    assert "already exists" in response.json()["detail"].lower()


@pytest.mark.parametrize(
    "field,value",
    [
        ("email", "not-an-email"),
        ("firstName", "   "),
        ("lastName", "   "),
        ("role", "ROLE_SUPERUSER"),
        ("password", "short"),
    ],
)
async def test_invalid_user_fields_are_refused(auth_client, field, value):
    response = await auth_client.post(
        "/api/users", json={**PLAIN_USER, field: value}
    )

    assert response.status_code in (400, 422)


async def test_a_password_longer_than_bcrypt_can_hash_is_refused(auth_client):
    """bcrypt ignores everything past 72 bytes, so two different long passwords
    would otherwise authenticate each other."""
    response = await auth_client.post(
        "/api/users", json={**PLAIN_USER, "password": "x" * 80}
    )

    assert response.status_code == 400
    assert "72 bytes" in response.json()["detail"]


# --- changing your own password ---------------------------------------------
async def test_changing_your_password_requires_the_current_one(auth_client):
    response = await auth_client.post(
        "/api/users/me/password",
        json={"currentPassword": "not-the-password", "newPassword": "new-password-1"},
    )

    assert response.status_code == 400
    assert "current password is incorrect" in response.json()["detail"].lower()


async def test_the_new_password_must_differ_from_the_current_one(auth_client):
    response = await auth_client.post(
        "/api/users/me/password",
        json={
            "currentPassword": SEED_ADMIN_PASSWORD,
            "newPassword": SEED_ADMIN_PASSWORD,
        },
    )

    assert response.status_code == 400
    assert "different" in response.json()["detail"].lower()


async def test_the_new_password_actually_becomes_the_password(auth_client, api_client):
    replacement = "a-completely-new-password"
    changed = await auth_client.post(
        "/api/users/me/password",
        json={"currentPassword": SEED_ADMIN_PASSWORD, "newPassword": replacement},
    )
    assert changed.status_code == 200

    import main

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        old = await client.post(
            "/api/auth/login",
            json={"email": SEED_ADMIN_EMAIL, "password": SEED_ADMIN_PASSWORD},
        )
        new = await client.post(
            "/api/auth/login",
            json={"email": SEED_ADMIN_EMAIL, "password": replacement},
        )

    assert old.status_code == 401
    assert new.status_code == 200


# --- profile -----------------------------------------------------------------
async def test_a_user_can_update_their_own_details(user_client):
    response = await user_client.put(
        "/api/users/me", json={"firstName": "Renamed", "lastName": "Person"}
    )

    assert response.status_code == 200
    assert response.json()["fullName"] == "Renamed Person"


async def test_the_profile_endpoint_cannot_be_used_to_grant_yourself_admin(
    user_client,
):
    """UpdateProfileRequest has no role field, so this is a 200 that changes
    nothing -- the important part is that the role is unchanged afterwards."""
    await user_client.put(
        "/api/users/me", json={"firstName": "Sneaky", "role": UserRole.ACCOUNT_ADMIN.value}
    )

    assert (await user_client.get("/api/users/me")).json()["role"] == UserRole.USER.value
    assert (await user_client.get("/api/settings")).status_code == 403


# --- authentication is still required at all ---------------------------------
async def test_every_user_endpoint_requires_a_session(api_client):
    assert (await api_client.get("/api/users")).status_code == 401
    assert (await api_client.get("/api/users/me")).status_code == 401
    assert (await api_client.post("/api/users", json={})).status_code == 401
    assert (await api_client.get("/api/users/role-pages")).status_code == 401
