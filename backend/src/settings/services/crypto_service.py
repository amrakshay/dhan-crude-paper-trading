"""Encryption at rest for secret settings.

The Dhan access token is stored encrypted in the database. Fernet is used
(AES-128-CBC with HMAC-SHA256 authentication and a random IV per message), with
the key derived by HKDF-SHA256 from an application secret.

Key material, in priority order:

1. ``APP_ENCRYPTION_KEY`` -- a dedicated secret. Preferred, because rotating the
   session-signing secret then does not destroy the stored token.
2. ``APP_JWT_SECRET`` -- reused as a fallback so a single-secret deployment
   still works.

If neither is configured, encryption is REFUSED rather than performed with a
per-process random key. A token encrypted under an ephemeral key would be
silently unrecoverable after the next restart, which is worse than declining to
store it.

Decryption failures (typically a rotated secret) are reported as "no usable
token" rather than raised, so the application still starts and the operator is
told to re-enter it.
"""
import base64
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from src import config_utils
from src.logging_config import get_logger

logger = get_logger("settings.crypto")

# Bound into the key derivation so the same secret used elsewhere cannot
# produce the same key.
_HKDF_INFO = b"dhan-crude-paper-trading:settings:v1"


class EncryptionUnavailableError(Exception):
    """No stable secret is configured, so secrets cannot be stored safely."""


def _key_material() -> Optional[str]:
    dedicated = config_utils.get_property_value("security.encryption_key", "") or ""
    if dedicated:
        return dedicated
    # Fall back to the session-signing secret, but only when it is configured;
    # a blank APP_JWT_SECRET means a random per-process key (see AuthService).
    shared = config_utils.get_property_value("auth.jwt_secret", "") or ""
    return shared or None


def is_available() -> bool:
    return _key_material() is not None


def unavailable_reason() -> str:
    return (
        "Cannot store the Dhan access token securely: neither APP_ENCRYPTION_KEY "
        "nor APP_JWT_SECRET is set in .env. Without a stable secret the token "
        "would be encrypted under a key that changes on every restart and could "
        "never be read back. Set APP_ENCRYPTION_KEY (generate one with "
        "`python3 -c \"import secrets; print(secrets.token_urlsafe(48))\"`) and "
        "restart."
    )


def _fernet() -> Fernet:
    material = _key_material()
    if material is None:
        raise EncryptionUnavailableError(unavailable_reason())

    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_HKDF_INFO,
    ).derive(material.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt(plaintext: str) -> str:
    """Encrypt a secret for storage. Raises if no stable key is configured."""
    if not plaintext:
        raise ValueError("Refusing to encrypt an empty value")
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> Optional[str]:
    """Decrypt a stored secret, or None if it cannot be read.

    Returns None (rather than raising) when the key has changed or the stored
    value is corrupt, so startup is never blocked by an unreadable setting.
    """
    if not ciphertext:
        return None
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except EncryptionUnavailableError:
        logger.warning("Stored secret cannot be decrypted: no encryption key configured")
        return None
    except (InvalidToken, ValueError, TypeError):
        logger.warning(
            "Stored secret could not be decrypted -- the encryption secret has "
            "probably changed. Re-enter the Dhan access token in Settings."
        )
        return None


def mask(secret: Optional[str]) -> Optional[str]:
    """A non-reversible hint for the UI. Never return a secret in full."""
    if not secret:
        return None
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}…{secret[-4:]} ({len(secret)} chars)"
