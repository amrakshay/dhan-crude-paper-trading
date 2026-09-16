"""Runtime-editable application settings.

Settings entered in the UI live here and take priority over `.env`. Only the
keys listed in `src.settings.services.settings_service.MANAGED_KEYS` are ever
read back out, so a stray row cannot start influencing configuration.

The Dhan access token is stored in `encrypted_value`, never in `value`.
"""
from sqlalchemy import Boolean, Column, String, Text

from src.database.base import PreciseDateTime, TimestampedModel


class AppSetting(TimestampedModel):
    __tablename__ = "app_settings"

    key = Column(String(64), nullable=False, unique=True, index=True)

    # Plaintext settings (client id, feature toggles).
    value = Column(Text, nullable=True)

    # Fernet ciphertext for secrets. A value is in exactly one of these two
    # columns, never both.
    encrypted_value = Column(Text, nullable=True)
    is_encrypted = Column(Boolean, nullable=False, default=False)

    set_at = Column(PreciseDateTime, nullable=True)

    def __repr__(self) -> str:
        # Deliberately never renders the value.
        return f"<AppSetting(key={self.key!r}, encrypted={self.is_encrypted})>"
