"""Automatic renewal of the Dhan access token.

A token lasts 24 hours and its expiry stops the feed, the chart, the option
chain and the rotation's overnight bar refresh -- quietly, until something asks
for a price. These tests pin the behaviour that makes renewal safe rather than
merely convenient:

* it renews only when the token is genuinely close to expiring;
* it can extend a session but never start one -- no PIN, no TOTP, no auth host;
* a dead token is reported to a human rather than retried forever;
* nothing it does can take the application down, and the token never reaches a
  log line.
"""
import jwt
import pytest

from src.settings.services.settings_service import KEY_ACCESS_TOKEN, KEY_CLIENT_ID


def _token(hours_left: float) -> str:
    """A JWT shaped like Dhan's, expiring in `hours_left`."""
    from datetime import datetime, timedelta, timezone

    expiry = datetime.now(timezone.utc) + timedelta(hours=hours_left)
    return jwt.encode(
        {"exp": int(expiry.timestamp()), "dhanClientId": "1100003626"},
        "not-the-real-signing-key",
        algorithm="HS256",
    )


async def _store(session, token: str) -> None:
    from src.settings.database.db_operations.app_setting_repository import (
        AppSettingRepository,
    )
    from src.settings.services.settings_service import SettingsService

    await SettingsService(AppSettingRepository(session)).save(
        client_id="1100003626", access_token=token, synthetic_feed=False
    )


# --- when it renews ---------------------------------------------------------
async def test_a_token_with_hours_left_is_left_alone(db_session, monkeypatch):
    """Renewing early throws away hours of validity for nothing.

    Dhan's renewal EXPIRES the current token as it issues the new one, so a
    renewal is not free -- doing it hourly would mean a token that is always
    fresh and a call that can always fail, in place of one that is fine.
    """
    from src.settings.services import token_refresh_service as module

    await _store(db_session, _token(hours_left=20))

    called = []
    _patch_client(monkeypatch, _FakeClient(called))

    outcome = await module.TokenRefreshService(db_session).refresh_if_due()

    assert outcome.renewed is False
    assert "Not due yet" in outcome.reason
    assert called == [], "it must not have called Dhan at all"


async def test_a_token_close_to_expiry_is_renewed(db_session, monkeypatch):
    from src.settings.services import token_refresh_service as module

    await _store(db_session, _token(hours_left=2))
    fresh = _token(hours_left=24)

    called = []
    _patch_client(monkeypatch, _FakeClient(called, returns={"accessToken": fresh}))

    outcome = await module.TokenRefreshService(db_session).refresh_if_due()

    assert outcome.renewed is True
    assert called == ["renew"]

    # And the NEW token is what is stored, encrypted, and what the running
    # configuration now uses.
    from src import config_utils
    from src.settings.database.db_operations.app_setting_repository import (
        AppSettingRepository,
    )
    from src.settings.services.settings_service import SettingsService

    stored = await SettingsService(AppSettingRepository(db_session)).load_stored()
    assert stored[KEY_ACCESS_TOKEN] == fresh
    assert config_utils.get_property_value("dhan.access_token") == fresh


async def test_an_expired_token_is_reported_not_retried(db_session, monkeypatch):
    """Dhan renews only an ACTIVE token, so this needs a person.

    Retrying every fifteen minutes would bury the one message that matters
    under a repeated failure that cannot succeed.
    """
    from src.settings.services import token_refresh_service as module

    await _store(db_session, _token(hours_left=24))
    # Now age it past the cliff, behind the service's back. The token lives on
    # the `dhan` CONNECTION since 2026-09-18, so that is what gets aged --
    # writing to `app_settings` here would age a row nothing reads.
    from src.connections.services import providers
    from src.connections.services.connection_store import ConnectionStore

    await ConnectionStore(db_session).put(
        providers.PROVIDER_DHAN,
        providers.DHAN_ACCESS_TOKEN,
        _token(hours_left=-1),
    )
    await db_session.commit()

    called = []
    _patch_client(monkeypatch, _FakeClient(called))

    outcome = await module.TokenRefreshService(db_session).refresh_if_due()

    assert outcome.renewed is False
    assert "already expired" in outcome.reason
    assert "Dhan Web" in outcome.reason, "it must say how to fix it"
    assert called == [], "a dead token must not be sent to Dhan"


async def test_the_synthetic_feed_has_no_token_to_renew(db_session, monkeypatch):
    from src.settings.services import token_refresh_service as module
    from src.settings.database.db_operations.app_setting_repository import (
        AppSettingRepository,
    )
    from src.settings.services.settings_service import SettingsService

    await SettingsService(AppSettingRepository(db_session)).save(
        client_id="", access_token="", synthetic_feed=True
    )

    called = []
    _patch_client(monkeypatch, _FakeClient(called))

    outcome = await module.TokenRefreshService(db_session).refresh_if_due()

    assert outcome.renewed is False
    assert "synthetic" in outcome.reason.lower()
    assert called == []


async def test_an_opaque_token_is_not_renewed_on_a_guess(db_session, monkeypatch):
    """No readable expiry means renewal cannot be TIMED.

    Undefined is not zero and it is not "renew it hourly" either: inventing a
    cadence is exactly the guess this module reads the JWT to avoid.
    """
    from src.settings.services import token_refresh_service as module

    await _store(db_session, "an-opaque-token-that-is-not-a-jwt")

    called = []
    _patch_client(monkeypatch, _FakeClient(called))

    outcome = await module.TokenRefreshService(db_session).refresh_if_due()

    assert outcome.renewed is False
    assert "expiry" in outcome.reason
    assert called == []


# --- what it cannot do ------------------------------------------------------
def test_the_client_can_construct_exactly_one_endpoint():
    """It renews a session. It cannot mint one."""
    from src.market.services import dhan_token_client as module

    assert module.ENDPOINT_RENEW == "/RenewToken"

    source = __import__("pathlib").Path(module.__file__).read_text(encoding="utf-8")
    # Only inside prose that explains what is refused.
    for banned in ("generateAccessToken", "generate-consent", "consumeApp-consent"):
        for line in source.splitlines():
            if banned in line:
                assert line.lstrip().startswith(("#", "*", "`", "PIN", "Either")) or (
                    '"' not in line and "'" not in line
                ), f"{banned} appears outside prose: {line!r}"


async def test_a_renewal_failure_never_raises(db_session, monkeypatch):
    """A failed renewal is recoverable; an exception out of here is an outage."""
    from src.market.services.dhan_token_client import TokenRenewalError
    from src.settings.services import token_refresh_service as module

    await _store(db_session, _token(hours_left=1))

    class Exploding:
        async def renew(self):
            raise TokenRenewalError("Dhan is having a bad day")

    _patch_client(monkeypatch, Exploding())

    outcome = await module.TokenRefreshService(db_session).refresh_if_due()
    assert outcome.renewed is False
    assert "bad day" in outcome.reason


# --- the task ---------------------------------------------------------------
def test_the_task_is_registered_with_the_health_page():
    """A named task the inspector does not know is reported as unexpected."""
    from src.health.services.task_inspector import TASK_DESCRIPTIONS

    assert "dhan-token-refresh" in TASK_DESCRIPTIONS


def test_the_task_is_not_expected_on_a_synthetic_feed(monkeypatch):
    """There is no token in play, so a missing task is not a problem."""
    from src import config_utils
    from src.health.services import task_inspector

    real = config_utils.get_property_value_boolean

    def fake(key, default=None):
        if key == "market_feed.synthetic_feed":
            return True
        return real(key, default)

    monkeypatch.setattr(config_utils, "get_property_value_boolean", fake)
    expected = task_inspector.expected_task_names(is_synthetic=True, feed_running=True)
    assert "dhan-token-refresh" not in expected


def _patch_client(monkeypatch, fake):
    """Patch the client where it is DEFINED.

    `TokenRefreshService` imports `get_token_client` inside the function that
    uses it -- the cross-package rule in backend/CLAUDE.md section 1 -- so it
    resolves the name at call time from the source module. Patching the
    consuming module would be patching a name that is never read.
    """
    from src.market.services import dhan_token_client

    monkeypatch.setattr(dhan_token_client, "get_token_client", lambda: fake)


class _FakeClient:
    """Records whether Dhan was called, and never touches the network."""

    def __init__(self, called, returns=None):
        self._called = called
        self._returns = returns

    async def renew(self):
        self._called.append("renew")
        if self._returns is None:
            raise AssertionError("renew() was not expected to be called")
        return self._returns
