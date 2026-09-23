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


# --- the 2026-09-19 failure, pinned ----------------------------------------
async def test_the_renewal_is_a_GET_because_dhan_documents_a_GET(monkeypatch):
    """Shipped as a POST, and every one of the first 19 renewals returned 400.

    Dhan's documented call is `curl --location '.../RenewToken' --header ...`
    with no `--request` and no `--data`, which curl sends as a GET. The
    generateAccessToken example directly above it in the same document DOES
    say `--request POST`, so the docs distinguish and this was simply misread.
    """
    from src.market.services import dhan_token_client

    used = {}

    class _Recording:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            used["method"] = "GET"
            used["url"] = url

            class _Ok:
                status_code = 200

                @staticmethod
                def json():
                    return {"accessToken": "new-token", "expiryTime": "x"}

            return _Ok()

        async def post(self, *args, **kwargs):
            used["method"] = "POST"
            raise AssertionError("RenewToken is a GET; a POST returns 400")

    monkeypatch.setattr(dhan_token_client.httpx, "AsyncClient", _Recording)

    client = dhan_token_client.DhanTokenClient()
    _patch_credentials(monkeypatch)
    await client.renew()

    assert used["method"] == "GET"
    assert used["url"].endswith("/RenewToken")


async def test_a_refusal_is_not_retried_and_says_why(monkeypatch):
    """A 400 means the request is wrong; repeating it changes nothing.

    This is the whole 2026-09-19 failure in one test: the old code classified
    every non-401 as transient, so nineteen identical 400s went by as
    warnings while the six hours of remaining validity ran out, and the alert
    arrived only once the token was dead and unrenewable by anyone.
    """
    from src.market.services import dhan_token_client

    class _Refusing:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            class _Bad:
                status_code = 400

                @staticmethod
                def json():
                    return {
                        "errorType": "Invalid_Authentication",
                        "errorMessage": "Token cannot be renewed",
                    }

            return _Bad()

    monkeypatch.setattr(dhan_token_client.httpx, "AsyncClient", _Refusing)

    client = dhan_token_client.DhanTokenClient()
    _patch_credentials(monkeypatch)

    import pytest as _pytest

    with _pytest.raises(dhan_token_client.TokenRenewalRefused) as caught:
        await client.renew()

    # Dhan's OWN words reach the operator. Not logging these was why nineteen
    # failures said nothing but "400".
    assert "Token cannot be renewed" in str(caught.value)
    assert "Token cannot be renewed" in (client.last_error or "")

    # And a refusal is a refusal, not a "try later".
    assert isinstance(caught.value, dhan_token_client.TokenRenewalError)


async def test_rate_limiting_is_the_one_4xx_still_worth_retrying(monkeypatch):
    from src.market.services import dhan_token_client

    class _Limited:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            class _TooMany:
                status_code = 429

                @staticmethod
                def json():
                    return {"errorMessage": "Too many requests"}

            return _TooMany()

    monkeypatch.setattr(dhan_token_client.httpx, "AsyncClient", _Limited)

    client = dhan_token_client.DhanTokenClient()
    _patch_credentials(monkeypatch)

    import pytest as _pytest

    with _pytest.raises(dhan_token_client.TokenRenewalError) as caught:
        await client.renew()
    assert not isinstance(caught.value, dhan_token_client.TokenRenewalRefused)
    assert client.rate_limited == 1


async def test_a_refused_token_reports_that_a_human_is_needed(
    db_session, monkeypatch
):
    """The outcome carries it, so the alert can say so while there is time."""
    from src.market.services.dhan_token_client import TokenRenewalRefused
    from src.settings.services import token_refresh_service as module

    await _store(db_session, _token(hours_left=5))

    class _Refusing:
        async def renew(self):
            raise TokenRenewalRefused("Dhan refused: token cannot be renewed")

    _patch_client(monkeypatch, _Refusing())

    outcome = await module.TokenRefreshService(db_session).refresh_if_due()

    assert outcome.renewed is False
    assert outcome.needs_human is True
    assert "refused" in outcome.reason.lower()


def _patch_credentials(monkeypatch):
    """Give the client a client id and token without touching real config.

    Through monkeypatch, NOT by assigning to the class: an earlier version of
    this helper set `DhanTokenClient._credentials` permanently, which leaked a
    fake client id into every test that ran after it in the same worker and
    took six unrelated files down with it.
    """
    from src.market.services import dhan_token_client

    monkeypatch.setattr(
        dhan_token_client.DhanTokenClient,
        "_credentials",
        staticmethod(lambda: ("1100003626", "a-token")),
    )


async def test_a_200_carrying_no_token_is_a_refusal_not_a_retry(monkeypatch):
    """Observed against a live working token on 2026-09-19.

    Dhan answered the renewal 200 with a body that had no accessToken in it,
    then DH-906 "Invalid Token" on every call after -- while the same token
    went on serving market data perfectly. A 200 with no token will not start
    carrying one on the next attempt, so classifying it as transient repeats
    the exact mistake the 400 made: retry until the token dies, tell nobody in
    time.
    """
    from src.market.services import dhan_token_client

    class _Empty:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            class _Ok:
                status_code = 200

                @staticmethod
                def json():
                    return {"dhanClientId": "1100003626", "status": "success"}

            return _Ok()

    monkeypatch.setattr(dhan_token_client.httpx, "AsyncClient", _Empty)

    client = dhan_token_client.DhanTokenClient()
    _patch_credentials(monkeypatch)

    import pytest as _pytest

    with _pytest.raises(dhan_token_client.TokenRenewalRefused) as caught:
        await client.renew()

    # It names what DID come back, so the next person does not have to
    # reproduce the call to find out.
    assert "dhanClientId" in str(caught.value)
    assert "status" in str(caught.value)
    assert "replaced by hand" in str(caught.value)


async def test_the_new_token_arrives_as_token_not_accessToken(monkeypatch):
    """The live API contradicts its own documentation, and it cost a week.

    Dhan documents the renewal response as carrying `accessToken`. Measured
    2026-09-23, three times, on three separate Dhan Web tokens, what actually
    comes back is:

        {"createTime": ..., "expiryTime": ..., "token": "<327-char JWT>"}

    Reading only `accessToken` made every renewal look like a refusal -- while
    the call had ALREADY rotated the credential, so the old token was dead a
    second later. That produced a daily hand-replacement and a written-down
    theory that application-generated tokens did not qualify. Neither was
    true: the returned `token` authenticated against /charts/historical on the
    next call.

    Both keys are read, because the documented one may start working and a
    client that only understood the undocumented one would break that day.
    """
    from src.market.services import dhan_token_client

    class _LiveShape:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            class _Ok:
                status_code = 200

                @staticmethod
                def json():
                    return {
                        "createTime": "2026-09-23T12:57:50.555",
                        "expiryTime": "2026-09-24T12:57:50.553",
                        "token": "the-renewed-token",
                    }

            return _Ok()

    monkeypatch.setattr(dhan_token_client.httpx, "AsyncClient", _LiveShape)
    client = dhan_token_client.DhanTokenClient()
    _patch_credentials(monkeypatch)

    payload = await client.renew()

    assert (payload.get("accessToken") or payload.get("token")) == "the-renewed-token"
    assert client.renewals == 1
    assert client.last_error is None


async def test_a_body_with_neither_key_is_still_refused(monkeypatch):
    """Widening the read must not turn "no token at all" into a success.

    The refusal path stays, and its message no longer blames how the token was
    generated -- that diagnosis was wrong. It says the credential has probably
    been rotated anyway, which is the part that matters to whoever reads it.
    """
    from src.market.services import dhan_token_client

    class _Empty:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            class _Ok:
                status_code = 200

                @staticmethod
                def json():
                    return {"createTime": "x", "expiryTime": "y"}

            return _Ok()

    monkeypatch.setattr(dhan_token_client.httpx, "AsyncClient", _Empty)
    client = dhan_token_client.DhanTokenClient()
    _patch_credentials(monkeypatch)

    with pytest.raises(dhan_token_client.TokenRenewalRefused) as refused:
        await client.renew()

    message = str(refused.value)
    assert "accessToken" in message and "token" in message
    assert "rotated" in message
    assert "Dhan Web" not in message, "the old, wrong diagnosis must not return"
