"""Application users.

Replaces the single hardcoded `APP_USERNAME` / `APP_PASSWORD` credential pair.
Two roles (see `src.constants.UserRole`), and a status so an account can be
deactivated instead of deleted.

**The password is hashed, not encrypted.** Encryption is reversible: anyone
holding `APP_ENCRYPTION_KEY` could recover every password, and people reuse
passwords across systems. A password only ever needs comparing, never
recovering, so it is stored as a bcrypt hash with a per-user salt. Contrast the
Dhan access token in `src/settings/`, which is *correctly* Fernet-encrypted
because it has to be replayed to Dhan verbatim.
"""
from sqlalchemy import Boolean, Column, String

from src.constants import UserRole, UserStatus
from src.database.base import PreciseDateTime, TimestampedModel


class User(TimestampedModel):
    __tablename__ = "users"

    # The login identifier, and immutable once created -- an email change would
    # silently reassign every audit trail that refers to it. Stored lowercased
    # so lookups are case-insensitive without relying on collation, which
    # differs between SQLite and MySQL.
    email = Column(String(255), nullable=False, unique=True, index=True)

    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)

    # bcrypt output is always 60 chars; 255 leaves room for a future algorithm
    # without a migration.
    password_hash = Column(String(255), nullable=False)

    role = Column(String(64), nullable=False, default=UserRole.USER.value, index=True)
    status = Column(
        String(16), nullable=False, default=UserStatus.ACTIVE.value, index=True
    )

    # Set by an admin creating an account: the user picks their own password on
    # first sign-in, and nothing else is reachable until they do.
    must_change_password = Column(Boolean, nullable=False, default=False)

    # The seeded administrator. Marked with a column rather than inferred from
    # the email so the guard rails (never delete, never demote, never
    # deactivate) are enforced on data, not on a string comparison.
    is_seed_user = Column(Boolean, nullable=False, default=False, index=True)

    last_login_at = Column(PreciseDateTime, nullable=True)
    password_changed_at = Column(PreciseDateTime, nullable=True)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE.value

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ACCOUNT_ADMIN.value

    def __repr__(self) -> str:
        # Deliberately never renders password_hash.
        return (
            f"<User(id={self.id}, email={self.email!r}, role={self.role!r}, "
            f"status={self.status!r})>"
        )
