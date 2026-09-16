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
        json={"username": "trader", "password": SENTINEL_PASSWORD},
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
        "/api/auth/login", json={"username": "trader", "password": "test-password"}
    )

    assert response.status_code == 200
    session_token = response.cookies.get("dcpt_session")
    assert session_token
    _assert_clean(captured_logs, session_token, "test-password")


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
        "/api/auth/login", json={"username": "trader", "password": "test-password"}
    )
    session_token = login.cookies.get("dcpt_session")

    # ?token= is the documented dev fallback for the WebSocket handshake; it is
    # the one place a session token travels in a URL.
    await api_client.get(f"/api/market/status?token={session_token}")

    _assert_clean(captured_logs, session_token)


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
