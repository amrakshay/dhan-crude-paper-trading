"""Encryption at rest for the Dhan access token."""
import time

import jwt
import pytest

from src import config_utils
from src.settings.services import crypto_service
from src.settings.services.settings_service import inspect_token


def test_ciphertext_does_not_contain_the_plaintext():
    secret = "eyJhbGciOiJIUzI1NiJ9.super-secret-dhan-token"
    ciphertext = crypto_service.encrypt(secret)

    assert secret not in ciphertext
    assert "super-secret" not in ciphertext


def test_encryption_is_nondeterministic():
    """Fernet uses a random IV, so the same input must not produce the same
    ciphertext -- otherwise equal tokens would be identifiable at rest."""
    assert crypto_service.encrypt("same") != crypto_service.encrypt("same")


def test_roundtrip():
    secret = "a-token-with-unicode-€-and-length"
    assert crypto_service.decrypt(crypto_service.encrypt(secret)) == secret


def test_tampered_ciphertext_is_rejected():
    """Fernet is authenticated; a modified ciphertext must not decrypt."""
    ciphertext = crypto_service.encrypt("token")
    tampered = ciphertext[:-8] + "AAAAAAAA"

    assert crypto_service.decrypt(tampered) is None


def test_decrypting_under_a_different_key_returns_none_rather_than_raising():
    """A rotated secret must not prevent the app from starting.

    Both key sources are rotated: whichever one was actually in use when the
    ciphertext was produced must change, or this would silently pass by
    decrypting under an unrotated key.
    """
    ciphertext = crypto_service.encrypt("token")

    config = config_utils.load_config()
    previous_jwt = config["auth"]["jwt_secret"]
    previous_key = config.setdefault("security", {}).get("encryption_key")
    config["auth"]["jwt_secret"] = "a-completely-different-secret-value"
    config["security"]["encryption_key"] = "also-a-completely-different-value"
    try:
        assert crypto_service.decrypt(ciphertext) is None
    finally:
        config["auth"]["jwt_secret"] = previous_jwt
        config["security"]["encryption_key"] = previous_key


def test_encryption_is_refused_without_a_stable_secret():
    """Encrypting under a per-process random key would be unrecoverable after
    a restart, which is worse than declining to store the token."""
    config = config_utils.load_config()
    previous_jwt = config["auth"]["jwt_secret"]
    previous_key = config.get("security", {}).get("encryption_key")
    config["auth"]["jwt_secret"] = ""
    config.setdefault("security", {})["encryption_key"] = ""
    try:
        assert crypto_service.is_available() is False
        with pytest.raises(crypto_service.EncryptionUnavailableError):
            crypto_service.encrypt("token")
        assert "APP_ENCRYPTION_KEY" in crypto_service.unavailable_reason()
    finally:
        config["auth"]["jwt_secret"] = previous_jwt
        config["security"]["encryption_key"] = previous_key


def test_dedicated_encryption_key_takes_priority_over_the_jwt_secret():
    """So rotating the session secret does not destroy the stored token."""
    config = config_utils.load_config()
    config.setdefault("security", {})["encryption_key"] = "dedicated-encryption-key-value"
    try:
        ciphertext = crypto_service.encrypt("token")
        previous = config["auth"]["jwt_secret"]
        config["auth"]["jwt_secret"] = "rotated-session-secret"
        try:
            assert crypto_service.decrypt(ciphertext) == "token"
        finally:
            config["auth"]["jwt_secret"] = previous
    finally:
        config["security"]["encryption_key"] = ""


def test_empty_values_are_not_encrypted():
    with pytest.raises(ValueError):
        crypto_service.encrypt("")


def test_mask_never_reveals_the_middle_of_a_secret():
    masked = crypto_service.mask("eyJhbGciOiJIUzI1NiJ9.abcdefghijklmnop")

    assert "abcdefghijklmnop" not in masked
    assert masked.startswith("eyJh")
    assert masked.endswith("chars)")


def test_short_secrets_are_fully_masked():
    assert crypto_service.mask("abc123") == "******"


def test_mask_of_nothing_is_nothing():
    assert crypto_service.mask(None) is None
    assert crypto_service.mask("") is None


# --- token introspection ---------------------------------------------------
def test_expiry_is_read_from_the_jwt_without_the_signing_key():
    """Dhan tokens are JWTs; the real expiry is available locally, so there is
    no need to assume '24 hours from whenever it was pasted'."""
    token = jwt.encode(
        {"dhanClientId": "1100123456", "exp": int(time.time()) + 3600},
        "a-key-we-do-not-have",
        algorithm="HS256",
    )

    info = inspect_token(token)

    assert info.present is True
    assert info.is_jwt is True
    assert info.expired is False
    assert 3500 < info.seconds_remaining <= 3600
    assert info.dhan_client_id == "1100123456"


def test_an_expired_token_is_reported_as_expired():
    token = jwt.encode({"exp": int(time.time()) - 60}, "k", algorithm="HS256")

    info = inspect_token(token)

    assert info.expired is True
    assert info.seconds_remaining < 0


def test_an_opaque_token_is_usable_but_has_no_known_expiry():
    info = inspect_token("not-a-jwt-at-all")

    assert info.present is True
    assert info.is_jwt is False
    assert info.expires_at is None
    assert info.expired is False


def test_an_absent_token_reports_nothing():
    info = inspect_token(None)

    assert info.present is False
    assert info.masked is None


def test_token_info_never_carries_the_raw_token():
    token = jwt.encode({"exp": int(time.time()) + 3600}, "k", algorithm="HS256")

    payload = inspect_token(token).as_dict()

    assert token not in str(payload), "the raw token must never reach the browser"
