"""HARD SAFETY TEST -- no secret may ever reach the log.

Same shape as tests/test_no_real_orders.py, applied to logging. Two layers:

  1. **Runtime.** Real flows are exercised with sentinel credentials while
     every `dcpt` log record is captured, then the captured output is searched
     for those sentinels. This is the layer that matters -- it proves the log
     the operator actually reads is clean.

  2. **Static.** Every `logger.*()` call in `src/` is parsed and rejected if it
     interpolates an identifier whose name says it holds a secret. This catches
     a leak on a code path the tests do not reach.

`src/log_redaction.py` is the third layer: even a careless call site has its
output scrubbed, including inside formatted tracebacks. The static test is
still worth having -- redaction only covers *registered* secrets, and a value
that was never registered would sail through.
"""
import ast
import logging
import time
from pathlib import Path
from typing import Iterator, List, Tuple

import jwt
import pytest

from src import log_redaction
from src.logging_config import get_access_logger, get_logger
from tests.conftest import SEED_ADMIN_EMAIL, SEED_ADMIN_PASSWORD

BACKEND_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = BACKEND_ROOT / "src"

EXCLUDED_DIR_NAMES = {"__pycache__", ".venv", "venv", ".git", ".pytest_cache"}

# Sentinels long enough to clear log_redaction.MIN_SECRET_LENGTH, and
# distinctive enough that a substring match cannot be a false positive.
SENTINEL_PASSWORD = "sentinel-password-DO-NOT-LOG-8f21c3"
SENTINEL_CLIENT_ID = "1100987654"


def _sentinel_token() -> str:
    """A Dhan-shaped JWT whose signature is a sentinel we can search for."""
    return jwt.encode(
        {"dhanClientId": SENTINEL_CLIENT_ID, "exp": int(time.time()) + 20 * 3600},
        "sentinel-token-signing-key-DO-NOT-LOG-4b7e19",
        algorithm="HS256",
    )


class LogCapture(logging.Handler):
    """Captures the FORMATTED output of every record, redaction included.

    Formatting matters: the point is to test what lands in app.log, not what
    the call site passed. A record whose secret is scrubbed by
    RedactingFormatter is clean on disk even though its args are not.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: List[str] = []
        self.setFormatter(log_redaction.RedactingFormatter("%(name)s %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(self.format(record))
        except Exception:
            self.lines.append(f"<unformattable record: {record.msg!r}>")

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@pytest.fixture
def captured_logs():
    """Capture every dcpt log line at DEBUG, the noisiest setting we ship."""
    handler = LogCapture()
    loggers = [get_logger(), get_access_logger()]
    previous = [(logger, logger.level) for logger in loggers]
    for logger in loggers:
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        for logger, level in previous:
            logger.removeHandler(handler)
            logger.setLevel(level)


def _assert_clean(captured: LogCapture, *secrets: str) -> None:
    for secret in secrets:
        assert secret not in captured.text, (
            f"A secret reached the log.\n"
            f"Secret: {secret!r}\n"
            f"Offending lines:\n"
            + "\n".join(line for line in captured.lines if secret in line)
        )


# --- 1. runtime: real flows, sentinel credentials ---------------------------
async def test_a_failed_login_does_not_log_the_password(api_client, captured_logs):
    response = await api_client.post(
        "/api/auth/login",
        json={"email": SEED_ADMIN_EMAIL, "password": SENTINEL_PASSWORD},
    )

    assert response.status_code == 401
    # The attempt itself must be logged -- a failed login that leaves no trace
    # is its own problem.
    assert "Failed login attempt" in captured_logs.text
    _assert_clean(captured_logs, SENTINEL_PASSWORD)


async def test_a_successful_login_does_not_log_the_password_or_session_token(
    api_client, captured_logs
):
    response = await api_client.post(
        "/api/auth/login",
        json={"email": SEED_ADMIN_EMAIL, "password": SEED_ADMIN_PASSWORD},
    )

    assert response.status_code == 200
    session_token = response.cookies.get("dcpt_session")
    assert session_token
    _assert_clean(captured_logs, session_token, SEED_ADMIN_PASSWORD)


async def test_saving_a_dhan_access_token_never_logs_it(auth_client, captured_logs):
    token = _sentinel_token()

    response = await auth_client.put(
        "/api/settings",
        json={
            "syntheticFeed": True,
            "clientId": SENTINEL_CLIENT_ID,
            "accessToken": token,
        },
    )

    assert response.status_code == 200
    # The save must be logged, just not with the token in it.
    assert "access token" in captured_logs.text.lower()
    _assert_clean(captured_logs, token)


async def test_reading_settings_back_never_logs_the_stored_token(
    auth_client, captured_logs
):
    token = _sentinel_token()
    await auth_client.put(
        "/api/settings",
        json={
            "syntheticFeed": True,
            "clientId": SENTINEL_CLIENT_ID,
            "accessToken": token,
        },
    )
    captured_logs.lines.clear()

    response = await auth_client.get("/api/settings")

    assert response.status_code == 200
    assert response.json()["token"]["present"] is True
    _assert_clean(captured_logs, token)


async def test_the_access_log_never_carries_a_session_token(api_client, captured_logs):
    login = await api_client.post(
        "/api/auth/login",
        json={"email": SEED_ADMIN_EMAIL, "password": SEED_ADMIN_PASSWORD},
    )
    assert login.status_code == 200, login.text
    session_token = login.cookies.get("dcpt_session")
    assert session_token

    # ?token= is the documented dev fallback for the WebSocket handshake; it is
    # the one place a session token travels in a URL.
    await api_client.get(f"/api/market/status?token={session_token}")

    _assert_clean(captured_logs, session_token)


async def test_creating_a_user_never_logs_their_password(auth_client, captured_logs):
    response = await auth_client.post(
        "/api/users",
        json={
            "email": "sentinel.user@abc.com",
            "firstName": "Sentinel",
            "lastName": "User",
            "password": SENTINEL_PASSWORD,
            "role": "ROLE_USER",
        },
    )

    assert response.status_code == 201, response.text
    assert "created" in captured_logs.text.lower()
    _assert_clean(captured_logs, SENTINEL_PASSWORD)


async def test_changing_a_password_never_logs_either_password(
    auth_client, captured_logs
):
    replacement = "replacement-password-DO-NOT-LOG-77de10"

    response = await auth_client.post(
        "/api/users/me/password",
        json={"currentPassword": SEED_ADMIN_PASSWORD, "newPassword": replacement},
    )

    assert response.status_code == 200, response.text
    _assert_clean(captured_logs, SEED_ADMIN_PASSWORD, replacement)


async def test_no_response_body_ever_carries_a_password_hash(auth_client):
    """The hash is not a secret in the usual sense, but it is offline-crackable
    and has no business leaving the server."""
    created = await auth_client.post(
        "/api/users",
        json={
            "email": "hash.check@abc.com",
            "firstName": "Hash",
            "lastName": "Check",
            "password": "hash-check-password",
            "role": "ROLE_USER",
        },
    )
    assert created.status_code == 201, created.text

    bodies = [
        created.text,
        (await auth_client.get("/api/users")).text,
        (await auth_client.get(f"/api/users/{created.json()['id']}")).text,
        (await auth_client.get("/api/users/me")).text,
        (await auth_client.get("/api/auth/me")).text,
    ]

    for body in bodies:
        assert "password_hash" not in body
        assert "passwordHash" not in body
        assert "$2b$" not in body


# --- the system health endpoint, whose whole job is to display internals ----
# This is the single most likely place in the application to leak a secret,
# because "show me the configuration" is its reason to exist. Every one of
# these asserts a sentinel is absent from the RESPONSE BODY, not just the log.
async def test_the_health_endpoint_never_returns_the_dhan_access_token(
    auth_client, captured_logs
):
    token = _sentinel_token()
    saved = await auth_client.put(
        "/api/settings",
        json={
            "syntheticFeed": True,
            "clientId": SENTINEL_CLIENT_ID,
            "accessToken": token,
        },
    )
    assert saved.status_code == 200, saved.text

    response = await auth_client.get("/api/healthcheck/system")

    assert response.status_code == 200, response.text
    body = response.json()
    # The token must be reported as present, with a mask and an expiry -- the
    # page is useless if it cannot say the token is about to die -- but the
    # token itself must be nowhere in the payload.
    assert body["credentials"]["token"]["present"] is True
    assert body["credentials"]["token"]["masked"]
    assert body["credentials"]["token"]["expiresAt"]
    assert token not in response.text
    # A JWT is three dot-separated parts; a leak of only the signature would
    # still be a leak, so each part is checked on its own.
    for part in token.split("."):
        if len(part) >= 9:
            assert part not in response.text
    _assert_clean(captured_logs, token)


async def test_the_health_endpoint_never_returns_the_jwt_or_encryption_secrets(
    auth_client,
):
    """Both are in the in-memory config this page reports on."""
    import os

    response = await auth_client.get("/api/healthcheck/system")

    assert response.status_code == 200
    for name in ("APP_JWT_SECRET", "APP_ENCRYPTION_KEY", "APP_ADMIN_PASSWORD"):
        value = os.environ.get(name)
        if value and len(value) >= 9:
            assert value not in response.text, f"{name} reached the health payload"
    # Config keys that hold secrets must not be echoed either.
    assert "jwt_secret" not in response.text
    assert "encryption_key" not in response.text


async def test_the_health_endpoint_redacts_a_database_password(auth_client, monkeypatch):
    """The database URL is displayed, so its password must be removed first."""
    from src.health.services import health_service

    secret = "database-password-DO-NOT-SHOW-5c1e77"
    url = f"mysql+aiomysql://dcpt:{secret}@db.internal:3306/crude?charset=utf8mb4"
    # Patched where it is *used*: health_service imported the name, so it holds
    # its own reference and patching src.database.connection would not be seen.
    monkeypatch.setattr(health_service, "get_database_url", lambda: url)

    response = await auth_client.get("/api/healthcheck/system")

    assert response.status_code == 200
    assert secret not in response.text
    assert "dcpt:***@db.internal" in response.json()["process"]["database"]["urlRedacted"]


async def test_buffered_log_records_reach_the_health_endpoint_already_redacted(
    auth_client,
):
    """The buffer is read by HTTP, so it must hold scrubbed text, not raw records."""
    from src import log_buffer, log_redaction

    secret = "buffered-secret-DO-NOT-SHOW-2fa901"
    log_redaction.register_secret(secret)
    logger = get_logger("tests.health.buffer")
    try:
        logger.warning("upstream rejected %s", secret)

        response = await auth_client.get("/api/healthcheck/system")

        assert response.status_code == 200
        entries = response.json()["problems"]["entries"]
        assert any("upstream rejected" in entry["message"] for entry in entries)
        assert secret not in response.text
        assert log_redaction.REDACTED in response.text
    finally:
        log_redaction.clear_secrets()
        handler = log_buffer.get_handler()
        if handler is not None:
            handler.clear()


async def test_the_strategies_endpoint_never_returns_a_secret(
    auth_client, captured_logs
):
    """Strategies & Features shows configuration, so it is checked like Health.

    It reports a strategy's underlying, its rate card version and its margin
    model -- public facts -- and must never reach into the Dhan credentials or
    the application secrets that sit in the same config tree.
    """
    import os

    token = _sentinel_token()
    saved = await auth_client.put(
        "/api/settings",
        json={
            "syntheticFeed": True,
            "clientId": SENTINEL_CLIENT_ID,
            "accessToken": token,
        },
    )
    assert saved.status_code == 200, saved.text

    response = await auth_client.get("/api/strategies")

    assert response.status_code == 200, response.text
    assert token not in response.text
    for part in token.split("."):
        if len(part) >= 9:
            assert part not in response.text
    assert SENTINEL_CLIENT_ID not in response.text
    for name in ("APP_JWT_SECRET", "APP_ENCRYPTION_KEY", "APP_ADMIN_PASSWORD"):
        value = os.environ.get(name)
        if value and len(value) >= 9:
            assert value not in response.text, f"{name} reached the strategies payload"
    assert "jwt_secret" not in response.text
    assert "encryption_key" not in response.text
    assert "access_token" not in response.text
    _assert_clean(captured_logs, token)


async def test_the_portfolios_endpoint_never_returns_a_secret(
    auth_client, captured_logs
):
    """Portfolios shows money and configuration; same rule, same assertions."""
    import os

    token = _sentinel_token()
    await auth_client.put(
        "/api/settings",
        json={
            "syntheticFeed": True,
            "clientId": SENTINEL_CLIENT_ID,
            "accessToken": token,
        },
    )

    listed = await auth_client.get("/api/portfolios")
    assert listed.status_code == 200, listed.text
    portfolio_id = listed.json()["portfolios"][0]["id"]
    ledger = await auth_client.get(f"/api/portfolios/{portfolio_id}/ledger")

    for response in (listed, ledger):
        assert token not in response.text
        for part in token.split("."):
            if len(part) >= 9:
                assert part not in response.text
        assert SENTINEL_CLIENT_ID not in response.text
        for name in ("APP_JWT_SECRET", "APP_ENCRYPTION_KEY", "APP_ADMIN_PASSWORD"):
            value = os.environ.get(name)
            if value and len(value) >= 9:
                assert value not in response.text
    _assert_clean(captured_logs, token)


async def test_the_swing_endpoints_never_return_a_secret(auth_client, captured_logs):
    """Every new endpoint gets its assertion in the same change.

    The Swing Momentum page reports the regime, the breadth, the ranking, the
    schedule and the arming state -- configuration, like Health and Strategies.
    It reaches the strategy registry, the feed manager and the scheduler, all
    of which sit next to the Dhan credentials in the same config tree.
    """
    import os

    token = _sentinel_token()
    saved = await auth_client.put(
        "/api/settings",
        json={
            "syntheticFeed": True,
            "clientId": SENTINEL_CLIENT_ID,
            "accessToken": token,
        },
    )
    assert saved.status_code == 200, saved.text

    portfolios = await auth_client.get("/api/portfolios")
    portfolio_id = portfolios.json()["portfolios"][0]["id"]

    responses = [
        await auth_client.get("/api/swing/strategies"),
        await auth_client.get("/api/swing/status"),
        await auth_client.get(f"/api/swing/status?portfolioId={portfolio_id}"),
        await auth_client.get(f"/api/swing/book?portfolioId={portfolio_id}"),
        await auth_client.get("/api/swing/history"),
        await auth_client.get("/api/swing/explain"),
        await auth_client.get("/api/swing/stops"),
        await auth_client.get(f"/api/swing/performance?portfolioId={portfolio_id}"),
    ]

    for response in responses:
        assert response.status_code == 200, response.text
        assert token not in response.text
        for part in token.split("."):
            if len(part) >= 9:
                assert part not in response.text
        assert SENTINEL_CLIENT_ID not in response.text
        for name in ("APP_JWT_SECRET", "APP_ENCRYPTION_KEY", "APP_ADMIN_PASSWORD"):
            value = os.environ.get(name)
            if value and len(value) >= 9:
                assert value not in response.text, f"{name} reached a swing payload"
        assert "jwt_secret" not in response.text
        assert "encryption_key" not in response.text
        assert "access_token" not in response.text
    _assert_clean(captured_logs, token)


async def test_renewing_the_token_never_logs_either_token(captured_logs, monkeypatch):
    """The renewal handles TWO secrets at once, which is new here.

    Every other path in this application receives a token from a human or
    reads one from the database. This one sends the old token to Dhan and
    receives a new one, so a careless log line -- or an httpx exception
    carrying a request URL -- could leak either. Both are asserted.
    """
    from src.market.services import dhan_token_client

    old = _sentinel_token()
    new = _sentinel_token()

    client = dhan_token_client.DhanTokenClient()
    monkeypatch.setattr(
        dhan_token_client.DhanTokenClient,
        "_credentials",
        staticmethod(lambda: (SENTINEL_CLIENT_ID, old)),
    )

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {"accessToken": new, "expiryTime": "2026-09-19T21:00:00"}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return _Response()

    monkeypatch.setattr(dhan_token_client.httpx, "AsyncClient", _Client)

    payload = await client.renew()
    assert payload["accessToken"] == new

    _assert_clean(captured_logs, old)
    _assert_clean(captured_logs, new)
    _assert_clean(captured_logs, SENTINEL_CLIENT_ID)


async def test_a_failed_renewal_does_not_log_the_url_it_called(
    captured_logs, monkeypatch
):
    """An httpx error can carry the request; the request can carry a token.

    Dhan's own docs show tokens as QUERY PARAMETERS on other endpoints, so a
    handler that interpolated an exception's text into a log line would be one
    API change away from leaking one. Only the exception TYPE is reported.
    """
    from src.market.services import dhan_token_client

    token = _sentinel_token()
    monkeypatch.setattr(
        dhan_token_client.DhanTokenClient,
        "_credentials",
        staticmethod(lambda: (SENTINEL_CLIENT_ID, token)),
    )

    class _Exploding:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            raise RuntimeError(f"connection failed for access-token={token}")

    monkeypatch.setattr(dhan_token_client.httpx, "AsyncClient", _Exploding)

    client = dhan_token_client.DhanTokenClient()
    with pytest.raises(dhan_token_client.TokenRenewalError):
        await client.renew()

    _assert_clean(captured_logs, token)
    assert token not in (client.last_error or ""), "the token reached last_error"


async def test_the_swing_health_endpoint_never_returns_a_secret(
    auth_client, captured_logs
):
    """The Health tab's endpoint, added 2026-09-18, gets its own assertion.

    Every new endpoint gets one in the same change (root `CLAUDE.md`), and
    this one earns a test of its own rather than a line in the list above,
    because it is the only swing endpoint that SERVES LOG RECORDS. A warning
    can carry whatever a developer interpolated into it, which is exactly the
    exposure `/api/healthcheck/problems` is admin-only for. The records come
    from the buffer that stores text already formatted through
    `RedactingFormatter`; this asserts that holds through this endpoint too,
    and not only through the system health page.

    It also reaches the feed manager, the scheduler, the instrument master and
    the charge rate card -- all of which sit beside the Dhan credentials in the
    same config tree.
    """
    import os

    from src import log_buffer, log_redaction

    token = _sentinel_token()
    saved = await auth_client.put(
        "/api/settings",
        json={
            "syntheticFeed": True,
            "clientId": SENTINEL_CLIENT_ID,
            "accessToken": token,
        },
    )
    assert saved.status_code == 200, saved.text

    portfolios = await auth_client.get("/api/portfolios")
    portfolio_id = portfolios.json()["portfolios"][0]["id"]

    # A swing component logging a runtime secret is the specific path this
    # endpoint opens, so it is exercised rather than assumed.
    buffered = "swing-buffered-secret-DO-NOT-SHOW-7b31c4"
    log_redaction.register_secret(buffered)
    try:
        get_logger("swing.stops").warning("could not ratchet using %s", buffered)

        responses = [
            await auth_client.get("/api/swing/health"),
            await auth_client.get(
                f"/api/swing/health?portfolioId={portfolio_id}"
            ),
        ]

        for response in responses:
            assert response.status_code == 200, response.text
            assert buffered not in response.text
            assert token not in response.text
            for part in token.split("."):
                if len(part) >= 9:
                    assert part not in response.text
            assert SENTINEL_CLIENT_ID not in response.text
            for name in ("APP_JWT_SECRET", "APP_ENCRYPTION_KEY", "APP_ADMIN_PASSWORD"):
                value = os.environ.get(name)
                if value and len(value) >= 9:
                    assert value not in response.text, (
                        f"{name} reached the swing health payload"
                    )
            assert "jwt_secret" not in response.text
            assert "encryption_key" not in response.text
            assert "access_token" not in response.text

        # The record IS served -- redacted, not dropped. A page that silently
        # withheld the warning would be worse than one that showed it scrubbed.
        records = responses[0].json()["problems"]["records"]
        assert any("could not ratchet" in one["message"] for one in records)
        assert log_redaction.REDACTED in responses[0].text
    finally:
        log_redaction.clear_secrets()
        handler = log_buffer.get_handler()
        if handler is not None:
            handler.clear()

    _assert_clean(captured_logs, token)


async def test_the_policy_endpoints_never_return_a_secret(auth_client, captured_logs):
    """The enforcement switches, added 2026-09-18, get their own assertion.

    Every new endpoint gets one in the same change (root `CLAUDE.md`). These
    two report which of a strategy's rules are being obeyed and what flipping a
    switch would do -- configuration, like the rest of Strategies & Features --
    and must never reach into the Dhan credentials sitting in the same config
    tree.
    """
    import os

    token = _sentinel_token()
    saved = await auth_client.put(
        "/api/settings",
        json={
            "syntheticFeed": True,
            "clientId": SENTINEL_CLIENT_ID,
            "accessToken": token,
        },
    )
    assert saved.status_code == 200, saved.text

    key = "nse-swing-momentum"
    responses = [
        await auth_client.get(
            f"/api/strategies/{key}/policy-warnings/regime.enforce?enforced=false"
        ),
        await auth_client.put(
            f"/api/strategies/{key}/policies/regime.enforce",
            json={"enforced": True},
        ),
    ]
    for response in responses:
        assert response.status_code == 200, response.text
        assert token not in response.text
        for part in token.split("."):
            if len(part) >= 9:
                assert part not in response.text
        assert SENTINEL_CLIENT_ID not in response.text
        for name in ("APP_JWT_SECRET", "APP_ENCRYPTION_KEY", "APP_ADMIN_PASSWORD"):
            value = os.environ.get(name)
            if value and len(value) >= 9:
                assert value not in response.text, f"{name} reached a policy payload"
        assert "access_token" not in response.text
    _assert_clean(captured_logs, token)


async def test_the_setting_endpoints_never_return_a_secret(auth_client, captured_logs):
    """The schedule settings, added 2026-09-18, get their own assertion.

    Every new endpoint gets one in the same change. These report and move the
    two clock times -- configuration, like the rest of Strategies & Features --
    and must never reach into the Dhan credentials sitting in the same config
    tree.
    """
    import os

    token = _sentinel_token()
    saved = await auth_client.put(
        "/api/settings",
        json={
            "syntheticFeed": True,
            "clientId": SENTINEL_CLIENT_ID,
            "accessToken": token,
        },
    )
    assert saved.status_code == 200, saved.text

    key = "nse-swing-momentum"
    responses = [
        await auth_client.get(
            f"/api/strategies/{key}/setting-warnings/schedule.rebalance_at?value=09%3A30"
        ),
        await auth_client.put(
            f"/api/strategies/{key}/settings/schedule.rebalance_at",
            json={"value": "09:30"},
        ),
        # And put it back, so this test leaves the schedule where it found it.
        await auth_client.put(
            f"/api/strategies/{key}/settings/schedule.rebalance_at",
            json={"value": None},
        ),
    ]
    for response in responses:
        assert response.status_code == 200, response.text
        assert token not in response.text
        for part in token.split("."):
            if len(part) >= 9:
                assert part not in response.text
        assert SENTINEL_CLIENT_ID not in response.text
        for name in ("APP_JWT_SECRET", "APP_ENCRYPTION_KEY", "APP_ADMIN_PASSWORD"):
            value = os.environ.get(name)
            if value and len(value) >= 9:
                assert value not in response.text, f"{name} reached a setting payload"
        assert "access_token" not in response.text
    _assert_clean(captured_logs, token)


async def test_a_plain_user_cannot_move_the_schedule(auth_client):
    """Admin-only at the route, like arming and the rule switches."""
    import httpx

    created = await auth_client.post(
        "/api/users",
        json={
            "email": "schedule-reader@abc.com",
            "firstName": "Schedule",
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
                "email": "schedule-reader@abc.com",
                "password": "reader-password-123",
            },
        )
        assert login.status_code == 200, login.text

        key = "nse-swing-momentum"
        refused = await client.put(
            f"/api/strategies/{key}/settings/schedule.rebalance_at",
            json={"value": "09:30"},
        )
        assert refused.status_code == 403, refused.text


async def test_a_time_that_would_corrupt_the_bars_is_refused_by_the_api(auth_client):
    """The correctness refusal, at the edge rather than only in the service.

    An analysis time inside the session stores today's half-finished bar as a
    finished daily bar, and every ranking, ATR and stop computed afterwards
    reads it without being able to tell.
    """
    refused = await auth_client.put(
        "/api/strategies/nse-swing-momentum/settings/schedule.nightly_at",
        json={"value": "11:00"},
    )
    assert refused.status_code == 400, refused.text
    assert "inside the trading session" in refused.text


async def test_a_plain_user_cannot_change_an_enforcement_policy(auth_client):
    """Admin-only at the route, like arming, and for the same reason.

    Switching off the enforcement of a regime gate decides whether software may
    spend money in a market the rule says to stay out of.
    """
    import httpx

    created = await auth_client.post(
        "/api/users",
        json={
            "email": "policy-reader@abc.com",
            "firstName": "Policy",
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
            json={"email": "policy-reader@abc.com", "password": "reader-password-123"},
        )
        assert login.status_code == 200, login.text

        key = "nse-swing-momentum"
        refused = await client.put(
            f"/api/strategies/{key}/policies/regime.enforce",
            json={"enforced": False},
        )
        assert refused.status_code == 403, refused.text
        refused = await client.get(
            f"/api/strategies/{key}/policy-warnings/regime.enforce?enforced=false"
        )
        assert refused.status_code == 403, refused.text


async def test_a_discretionary_module_refuses_a_policy_change(auth_client):
    """MCX crude has a person in front of every order and nothing to enforce."""
    refused = await auth_client.put(
        "/api/strategies/mcx-crude-options/policies/regime.enforce",
        json={"enforced": False},
    )
    assert refused.status_code == 400, refused.text
    assert "automation" in refused.text


async def test_a_plain_user_cannot_trigger_a_swing_run(auth_client):
    """Reading the journal is open; TRIGGERING a rebalance spends money.

    Admin-only at the route, not merely hidden from the sidebar -- the same
    rule the settings and health endpoints follow.
    """
    import httpx

    created = await auth_client.post(
        "/api/users",
        json={
            "email": "swing-reader@abc.com",
            "firstName": "Swing",
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
            json={"email": "swing-reader@abc.com", "password": "reader-password-123"},
        )
        assert login.status_code == 200, login.text

        # Reading is allowed.
        assert (await client.get("/api/swing/status")).status_code == 200
        # Running is not.
        for path in ("/api/swing/runs/nightly", "/api/swing/runs/rebalance"):
            refused = await client.post(path, json={"portfolioId": 1})
            assert refused.status_code == 403, f"{path}: {refused.text}"


async def test_the_health_endpoint_feature_block_carries_no_secret(auth_client):
    """The features block was added to the health payload; its assertion goes
    in with it, per the root CLAUDE.md rule."""
    import os

    response = await auth_client.get("/api/healthcheck/system")
    features = response.json()["features"]
    text = str(features)

    assert features["strategies"], "the block must actually report something"
    for name in ("APP_JWT_SECRET", "APP_ENCRYPTION_KEY", "APP_ADMIN_PASSWORD"):
        value = os.environ.get(name)
        if value and len(value) >= 9:
            assert value not in text
    assert "token" not in text.lower()


async def test_a_plain_user_cannot_read_the_health_endpoints(auth_client):
    """Admin-only at the route, not merely hidden from the sidebar."""
    import httpx

    created = await auth_client.post(
        "/api/users",
        json={
            "email": "health.reader@abc.com",
            "firstName": "Health",
            "lastName": "Reader",
            "password": "health-reader-password",
            "role": "ROLE_USER",
        },
    )
    assert created.status_code == 201, created.text

    import main

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/api/auth/login",
            json={"email": "health.reader@abc.com", "password": "health-reader-password"},
        )
        assert login.status_code == 200, login.text
        assert (await client.get("/api/healthcheck/system")).status_code == 403
        assert (await client.get("/api/healthcheck/problems")).status_code == 403


async def test_an_anonymous_caller_cannot_read_the_health_endpoints(api_client):
    assert (await api_client.get("/api/healthcheck/system")).status_code == 401
    assert (await api_client.get("/api/healthcheck/problems")).status_code == 401
    # The plain liveness probe stays open -- it carries nothing.
    assert (await api_client.get("/api/healthcheck/status")).status_code == 200


def test_a_registered_secret_is_scrubbed_from_a_traceback(captured_logs):
    """The formatter, not the call site, is the last line of defence."""
    secret = "traceback-secret-DO-NOT-LOG-91ac44"
    log_redaction.register_secret(secret)
    logger = get_logger("tests.redaction")
    try:
        try:
            raise ValueError(f"upstream rejected {secret}")
        except ValueError:
            logger.exception("Something failed")
        _assert_clean(captured_logs, secret)
        assert log_redaction.REDACTED in captured_logs.text
    finally:
        log_redaction.clear_secrets()


# --- 2. static: no log call may interpolate a secret-named value ------------
# Identifiers that hold a secret. `token` alone is deliberately absent: this
# codebase has token *metadata* everywhere (token_info, inspect_token) and
# banning it would be noise rather than safety.
FORBIDDEN_NAMES = {
    "password",
    "expected_password",
    "raw_password",
    "new_password",
    "current_password",
    "plain_password",
    "access_token",
    "session_token",
    "jwt_secret",
    "encryption_key",
    "encrypted_value",
    "plaintext",
    "secret",
    "api_key",
}

LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "log"}

# Wrapping a secret in one of these makes it safe to log.
MASKING_CALLS = {"mask", "masked", "len", "bool", "redact"}


def _python_files() -> Iterator[Path]:
    for path in SRC_ROOT.rglob("*.py"):
        if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
            continue
        yield path


def _is_log_call(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr in LOG_METHODS
        and isinstance(func.value, ast.Name)
        and "log" in func.value.id.lower()
    )


def _called_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _leaked_names(node: ast.AST) -> Iterator[str]:
    """Secret-named identifiers reachable from a log argument, unmasked.

    Recurses by hand rather than with ast.walk so that a masking call prunes
    the whole subtree beneath it: `bool(access_token)` leaks nothing, and
    ast.walk would still reach the `access_token` inside it.
    """
    if isinstance(node, ast.Call) and _called_name(node) in MASKING_CALLS:
        return
    if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
        yield node.id
        return
    if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
        yield node.attr
        return
    for child in ast.iter_child_nodes(node):
        yield from _leaked_names(child)


def _scan_source(source: str) -> List[Tuple[int, str]]:
    """Secret-named values passed to a logger, as (line, name)."""
    found: List[Tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not _is_log_call(node):
            continue
        # Each argument is judged on its own: one masked argument does not
        # excuse an unmasked one beside it.
        for argument in list(node.args) + [kw.value for kw in node.keywords]:
            for leaked in _leaked_names(argument):
                found.append((node.lineno, leaked))
    return found


def _find_violations() -> List[Tuple[Path, int, str]]:
    violations: List[Tuple[Path, int, str]] = []
    for path in _python_files():
        source = path.read_text(encoding="utf-8")
        violations.extend(
            (path, line, name) for line, name in _scan_source(source)
        )
    return violations


def test_no_log_call_in_src_interpolates_a_secret_named_value():
    violations = _find_violations()

    assert not violations, "Secret-named values passed to a logger:\n" + "\n".join(
        f"  {path.relative_to(BACKEND_ROOT)}:{line} -> {name}"
        for path, line, name in sorted(violations)
    )


def test_the_static_scan_actually_scans_something():
    """Guard against the scan silently matching nothing (a renamed src/, say)."""
    assert len(list(_python_files())) > 40


# A guard that cannot detect anything is worse than no guard, so the scan is
# pointed at known-bad and known-good snippets rather than trusted.
LEAKY_SNIPPETS = (
    'logger.info("password is %s", password)',
    'logger.debug("token %s", access_token)',
    'logger.warning("key=%s", self.encryption_key)',
    'logger.error("failed for %s", user.password)',
    'logger.info("a=%s b=%s", client_id, plaintext)',
    'logger.exception("saving %s", extra=secret)',
    'logger.info("stored %s", str(access_token))',
)

SAFE_SNIPPETS = (
    'logger.info("password ok for %s", username)',
    'logger.info("token %s", crypto_service.mask(access_token))',
    'logger.debug("provided=%s", bool(password))',
    'logger.info("length %s", len(access_token))',
    'logger.warning("token %s", redact(access_token))',
    'logger.info("client id %s", client_id)',
    'password = request.password',            # not a log call at all
)


@pytest.mark.parametrize("snippet", LEAKY_SNIPPETS)
def test_the_static_scan_catches_a_leaky_log_call(snippet):
    assert _scan_source(snippet), f"scan missed a leak: {snippet}"


@pytest.mark.parametrize("snippet", SAFE_SNIPPETS)
def test_the_static_scan_does_not_flag_a_safe_log_call(snippet):
    assert not _scan_source(snippet), f"scan false-positived on: {snippet}"
