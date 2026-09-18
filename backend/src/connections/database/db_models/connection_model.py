"""A connection: something this application authenticates to and calls.

Two tables, and the split is the same one `app_settings` already draws between
a key and its value -- except that here a value belongs to a NAMED connection
rather than to the process, which is what makes a second Telegram bot or a
second Dhan account a row rather than a schema change.

**`value` XOR `encrypted_value`.** Enforced in the repository, exactly as
`AppSettingRepository` enforces it, and for the same reason: that invariant is
why a secret has never landed in a plaintext column in this codebase. A secret
goes in `encrypted_value` (Fernet, via `crypto_service`) and nowhere else.

**A row naming a provider this build does not know is IGNORED on load.** Same
property `MANAGED_KEYS` gives `app_settings`: a stray row must never start
influencing configuration. `src/connections/services/providers.py` is the set
of providers that exist.

**`last_check_ok` is nullable on purpose.** Never checked, checked and failed,
and checked and fine are THREE states. Collapsing the first two into one
boolean would put "we have no idea" and "it is broken" in the same red pill,
and those are the two an operator most needs to tell apart while setting a
connection up.
"""
from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from src.database.base import PreciseDateTime, TimestampedModel


class Connection(TimestampedModel):
    """One configured third party, as the Connections page's cards show it."""

    __tablename__ = "connections"
    __table_args__ = (
        UniqueConstraint("provider", "name", name="uq_connection_provider_name"),
    )

    # 'dhan' | 'telegram'. A value outside providers.PROVIDERS is ignored on
    # load rather than raising -- see the module docstring.
    provider = Column(String(32), nullable=False, index=True)

    # The operator's label, shown on the card. Two Telegram bots are two rows
    # with two names.
    name = Column(String(80), nullable=False)

    enabled = Column(Boolean, nullable=False, default=True)

    # Decision 3: the pill is LAST KNOWN, with its age. Opening the page costs
    # no network call; these three columns are what it renders.
    last_checked_at = Column(PreciseDateTime, nullable=True)
    last_check_ok = Column(Boolean, nullable=True)
    last_check_detail = Column(String(500), nullable=True)

    # Who last touched it. SET NULL rather than CASCADE: deleting a user must
    # not delete the record that a connection was changed.
    updated_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    def __repr__(self) -> str:
        return f"<Connection(id={self.id}, provider={self.provider!r}, name={self.name!r})>"


class ConnectionSetting(TimestampedModel):
    """One key of one connection. A value lives in exactly one column."""

    __tablename__ = "connection_settings"
    __table_args__ = (
        UniqueConstraint("connection_id", "key", name="uq_connection_setting_key"),
    )

    connection_id = Column(
        Integer,
        ForeignKey("connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key = Column(String(64), nullable=False, index=True)

    value = Column(Text, nullable=True)            # plaintext
    encrypted_value = Column(Text, nullable=True)  # Fernet ciphertext
    is_encrypted = Column(Boolean, nullable=False, default=False)

    set_at = Column(PreciseDateTime, nullable=True)

    def __repr__(self) -> str:
        # Deliberately never renders the value.
        return (
            f"<ConnectionSetting(connection_id={self.connection_id}, "
            f"key={self.key!r}, encrypted={self.is_encrypted})>"
        )
