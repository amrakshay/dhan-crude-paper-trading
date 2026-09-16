"""Last-resort scrubbing of secrets out of log output.

The primary guarantee is that no call site passes a secret to a logger --
`tests/test_no_secrets_in_logs.py` exercises the real flows with sentinel
credentials and fails if one reaches the log. This module is the second layer:
a formatter that rewrites any registered secret out of the final string,
including out of formatted tracebacks, so a careless `logger.exception` in a
future change cannot leak a token.

Secrets are registered rather than discovered, because the Dhan access token
can change at runtime from the Settings page (see settings_service) and is
never in the environment in that case.
"""
import logging
import os
import threading
from typing import Iterable, Optional

REDACTED = "***REDACTED***"

# Short values are refused: redacting an 8-character-or-less string risks
# mangling unrelated log lines that merely contain it, and a password that
# short has bigger problems than appearing in a log.
MIN_SECRET_LENGTH = 9

# Environment variables holding secrets, registered at logging setup.
SECRET_ENV_VARS = (
    "APP_PASSWORD",
    "APP_ADMIN_PASSWORD",
    "APP_JWT_SECRET",
    "APP_ENCRYPTION_KEY",
    "DHAN_ACCESS_TOKEN",
)

_SECRETS: set = set()
_LOCK = threading.Lock()


def register_secret(value: Optional[str]) -> bool:
    """Register a value to be scrubbed from every future log line.

    Returns True if it was registered. Values shorter than MIN_SECRET_LENGTH
    are ignored -- see the note above.
    """
    if not value or not isinstance(value, str):
        return False
    if len(value) < MIN_SECRET_LENGTH:
        return False
    with _LOCK:
        _SECRETS.add(value)
    return True


def register_secrets(values: Iterable[Optional[str]]) -> None:
    for value in values:
        register_secret(value)


def register_environment_secrets() -> None:
    """Register the secrets that arrive through the environment."""
    register_secrets(os.environ.get(name) for name in SECRET_ENV_VARS)


def clear_secrets() -> None:
    """Drop every registered secret. For tests."""
    with _LOCK:
        _SECRETS.clear()


def redact(text: str) -> str:
    """Replace every registered secret in `text` with the mask."""
    if not _SECRETS or not text:
        return text
    with _LOCK:
        secrets = tuple(_SECRETS)
    # Longest first, so a secret that contains another is masked whole.
    for secret in sorted(secrets, key=len, reverse=True):
        if secret in text:
            text = text.replace(secret, REDACTED)
    return text


class RedactingFormatter(logging.Formatter):
    """Formatter that scrubs registered secrets from the finished line.

    Subclassing the formatter rather than filtering the record is deliberate:
    it is the only hook that sees the rendered traceback, which is where a
    secret is most likely to surface (an exception message carrying a token).
    """

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))
