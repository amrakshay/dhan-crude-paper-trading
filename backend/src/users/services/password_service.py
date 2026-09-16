"""Password hashing.

bcrypt, with a per-user salt. One-way: a stored hash cannot be turned back into
a password, which is the entire point -- see the note in `user_model.py` on why
this is hashed rather than encrypted like the Dhan token.

`bcrypt` is used directly rather than through passlib: passlib 1.7.4 reads
`bcrypt.__about__`, which bcrypt >= 4.1 removed, so importing it logs a trapped
AttributeError traceback on every start. The API needed here is two functions.
"""
import bcrypt

from src.logging_config import get_logger

logger = get_logger("users.password")

# bcrypt hashes at most 72 BYTES and, as of 4.2, silently ignores the rest --
# so "<72 chars>a" and "<72 chars>b" would authenticate each other. The limit is
# enforced here rather than relied upon, and it is bytes, not characters,
# because a non-ASCII password reaches 72 bytes sooner than 72 characters.
MAX_PASSWORD_BYTES = 72

# Long enough to be worth having, short enough not to annoy a single-user tool.
MIN_PASSWORD_LENGTH = 8

# bcrypt's work factor. 12 is ~0.25s per hash on this hardware, which is the
# right order for a login that happens a handful of times a day.
BCRYPT_ROUNDS = 12


class PasswordError(Exception):
    """A password that cannot be used, with a reason safe to show the user."""


def validate_password(password: str) -> None:
    """Reject a password we could not store faithfully. Raises PasswordError."""
    if not password:
        raise PasswordError("A password is required")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
        )
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise PasswordError(
            f"Password must be at most {MAX_PASSWORD_BYTES} bytes "
            f"({len(encoded)} given). bcrypt ignores anything beyond that, so a "
            "longer password would not be stored in full."
        )


def hash_password(password: str) -> str:
    """Validate and hash. The plaintext never leaves this call."""
    validate_password(password)
    hashed = bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    )
    return hashed.decode("ascii")


_DUMMY_HASH: str = ""


def dummy_hash() -> str:
    """A real hash of nothing in particular, computed once.

    Verified against when the email is unknown, so a missing account costs the
    same time as a wrong password and the two cannot be told apart by timing.
    It has to be a *valid* hash, or verify_password would take the error path
    instead of doing the work.
    """
    global _DUMMY_HASH
    if not _DUMMY_HASH:
        _DUMMY_HASH = hash_password("not-a-real-account-placeholder")
    return _DUMMY_HASH


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time comparison of a candidate against a stored hash."""
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"), password_hash.encode("ascii")
        )
    except (ValueError, TypeError):
        # A malformed or truncated hash in the database. Refuse the login
        # rather than raising it up into a 500 -- but say so, because it means
        # a row is corrupt.
        logger.exception(
            "Stored password hash is unusable; refusing the login. The user's "
            "password will have to be reset."
        )
        return False
