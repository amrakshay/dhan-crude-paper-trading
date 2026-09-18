"""add connections, connection_settings, alerts and users.telegram_user_id

Three new tables plus one column, and one DATA MIGRATION that is the risky part.

**The data migration moves Dhan's `client_id` and `access_token` out of
`app_settings` into a `dhan` connection row.** Getting it wrong takes the live
feed down, so:

- the token moves **as Fernet ciphertext, verbatim**. It is never decrypted:
  there is no reason to have a live Dhan token in the migration's memory, and
  decrypting-then-re-encrypting would put it there for no gain;
- `downgrade()` puts both rows back where they came from, so the move is
  reversible rather than merely forward-compatible;
- `market_feed.synthetic_feed` deliberately does NOT move. It is a mode of THIS
  application rather than a credential, and it would sit oddly on a card
  describing a connection to somebody else.

**`users.telegram_user_id` is nullable and unique.** Nobody is mapped by
default and commands are off until somebody is. It is `BigInteger` because
Telegram user ids have already passed 2^31.

No server defaults, VARCHAR lengths everywhere, `PreciseDateTime` for the audit
columns (backend/CLAUDE.md section 3).

Revision ID: e1f4a90c72b6
Revises: c9e42a7b51d8
Create Date: 2026-09-18 21:40:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from src.database.base import PreciseDateTime

revision: str = "e1f4a90c72b6"
down_revision: Union[str, None] = "c9e42a7b51d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The two `app_settings` keys that move, and the connection keys they become.
MOVED = (
    ("dhan.client_id", "client_id"),
    ("dhan.access_token", "access_token"),
)
DHAN_PROVIDER = "dhan"
DHAN_NAME = "Dhan market data"


def _now() -> str:
    from src.core.time_utils import utc_now

    return utc_now()


def upgrade() -> None:
    op.create_table(
        "connections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        # NULL means never checked, which is a third state and not "false".
        sa.Column("last_checked_at", PreciseDateTime, nullable=True),
        sa.Column("last_check_ok", sa.Boolean(), nullable=True),
        sa.Column("last_check_detail", sa.String(length=500), nullable=True),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", PreciseDateTime, nullable=False),
        sa.Column("updated_at", PreciseDateTime, nullable=False),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "name", name="uq_connection_provider_name"),
    )
    op.create_index(op.f("ix_connections_id"), "connections", ["id"], unique=False)
    op.create_index(
        op.f("ix_connections_provider"), "connections", ["provider"], unique=False
    )
    op.create_index(
        op.f("ix_connections_created_at"), "connections", ["created_at"], unique=False
    )

    op.create_table(
        "connection_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        # A value lives in exactly ONE of these, enforced in the repository --
        # the invariant that has kept a secret out of a plaintext column here.
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("encrypted_value", sa.Text(), nullable=True),
        sa.Column("is_encrypted", sa.Boolean(), nullable=False),
        sa.Column("set_at", PreciseDateTime, nullable=True),
        sa.Column("created_at", PreciseDateTime, nullable=False),
        sa.Column("updated_at", PreciseDateTime, nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["connections.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "connection_id", "key", name="uq_connection_setting_key"
        ),
    )
    op.create_index(
        op.f("ix_connection_settings_id"), "connection_settings", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_connection_settings_connection_id"),
        "connection_settings",
        ["connection_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_connection_settings_key"), "connection_settings", ["key"], unique=False
    )
    op.create_index(
        op.f("ix_connection_settings_created_at"),
        "connection_settings",
        ["created_at"],
        unique=False,
    )

    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        # Nullable: a fact is worth RECORDING even when nothing is configured
        # to carry it. The row is written SUPPRESSED with the reason rather
        # than the fact being dropped because there was no bot to tell.
        sa.Column("connection_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("dedupe_key", sa.String(length=200), nullable=True),
        sa.Column("suppressed_count", sa.Integer(), nullable=False),
        sa.Column("sent_at", PreciseDateTime, nullable=True),
        sa.Column("created_at", PreciseDateTime, nullable=False),
        sa.Column("updated_at", PreciseDateTime, nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["connections.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_alerts_id"), "alerts", ["id"], unique=False)
    op.create_index(
        op.f("ix_alerts_connection_id"), "alerts", ["connection_id"], unique=False
    )
    op.create_index(op.f("ix_alerts_kind"), "alerts", ["kind"], unique=False)
    op.create_index(op.f("ix_alerts_severity"), "alerts", ["severity"], unique=False)
    op.create_index(op.f("ix_alerts_status"), "alerts", ["status"], unique=False)
    op.create_index(
        op.f("ix_alerts_dedupe_key"), "alerts", ["dedupe_key"], unique=False
    )
    op.create_index(op.f("ix_alerts_created_at"), "alerts", ["created_at"], unique=False)

    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("telegram_user_id", sa.BigInteger(), nullable=True))
    op.create_index(
        op.f("ix_users_telegram_user_id"),
        "users",
        ["telegram_user_id"],
        unique=True,
    )

    _move_dhan_credentials_into_a_connection()


def _move_dhan_credentials_into_a_connection() -> None:
    """Copy the two `app_settings` rows into a `dhan` connection, then delete them.

    The ciphertext is moved as-is. Nothing here decrypts a token.
    """
    connection = op.get_bind()
    now = _now()

    existing = connection.execute(
        sa.text(
            "SELECT key, value, encrypted_value, is_encrypted FROM app_settings "
            "WHERE key IN (:client_key, :token_key)"
        ),
        {"client_key": MOVED[0][0], "token_key": MOVED[1][0]},
    ).mappings().all()
    if not existing:
        return

    connection.execute(
        sa.text(
            "INSERT INTO connections "
            "(provider, name, enabled, updated_by_user_id, created_at, updated_at) "
            "VALUES (:provider, :name, :enabled, NULL, :now, :now)"
        ),
        {"provider": DHAN_PROVIDER, "name": DHAN_NAME, "enabled": True, "now": now},
    )
    connection_id = connection.execute(
        sa.text(
            "SELECT id FROM connections WHERE provider = :provider AND name = :name"
        ),
        {"provider": DHAN_PROVIDER, "name": DHAN_NAME},
    ).scalar()

    by_key = {row["key"]: row for row in existing}
    for config_key, setting_key in MOVED:
        row = by_key.get(config_key)
        if row is None:
            continue
        connection.execute(
            sa.text(
                "INSERT INTO connection_settings "
                "(connection_id, key, value, encrypted_value, is_encrypted, set_at, "
                " created_at, updated_at) "
                "VALUES (:connection_id, :key, :value, :encrypted_value, "
                "        :is_encrypted, :now, :now, :now)"
            ),
            {
                "connection_id": connection_id,
                "key": setting_key,
                "value": row["value"],
                "encrypted_value": row["encrypted_value"],
                "is_encrypted": bool(row["is_encrypted"]),
                "now": now,
            },
        )

    connection.execute(
        sa.text(
            "DELETE FROM app_settings WHERE key IN (:client_key, :token_key)"
        ),
        {"client_key": MOVED[0][0], "token_key": MOVED[1][0]},
    )


def _move_dhan_credentials_back() -> None:
    """The reverse, so the move is reversible rather than merely forward-only."""
    connection = op.get_bind()
    now = _now()

    connection_id = connection.execute(
        sa.text("SELECT id FROM connections WHERE provider = :provider ORDER BY id"),
        {"provider": DHAN_PROVIDER},
    ).scalar()
    if connection_id is None:
        return

    rows = connection.execute(
        sa.text(
            "SELECT key, value, encrypted_value, is_encrypted FROM connection_settings "
            "WHERE connection_id = :connection_id"
        ),
        {"connection_id": connection_id},
    ).mappings().all()
    by_key = {row["key"]: row for row in rows}

    for config_key, setting_key in MOVED:
        row = by_key.get(setting_key)
        if row is None:
            continue
        connection.execute(
            sa.text("DELETE FROM app_settings WHERE key = :key"), {"key": config_key}
        )
        connection.execute(
            sa.text(
                "INSERT INTO app_settings "
                "(key, value, encrypted_value, is_encrypted, set_at, created_at, "
                " updated_at) "
                "VALUES (:key, :value, :encrypted_value, :is_encrypted, :now, :now, :now)"
            ),
            {
                "key": config_key,
                "value": row["value"],
                "encrypted_value": row["encrypted_value"],
                "is_encrypted": bool(row["is_encrypted"]),
                "now": now,
            },
        )


def downgrade() -> None:
    # The credentials go back FIRST, while the tables holding them still exist.
    _move_dhan_credentials_back()

    op.drop_index(op.f("ix_users_telegram_user_id"), table_name="users")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("telegram_user_id")

    op.drop_index(op.f("ix_alerts_created_at"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_dedupe_key"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_status"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_severity"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_kind"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_connection_id"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_id"), table_name="alerts")
    op.drop_table("alerts")

    op.drop_index(
        op.f("ix_connection_settings_created_at"), table_name="connection_settings"
    )
    op.drop_index(op.f("ix_connection_settings_key"), table_name="connection_settings")
    op.drop_index(
        op.f("ix_connection_settings_connection_id"), table_name="connection_settings"
    )
    op.drop_index(op.f("ix_connection_settings_id"), table_name="connection_settings")
    op.drop_table("connection_settings")

    op.drop_index(op.f("ix_connections_created_at"), table_name="connections")
    op.drop_index(op.f("ix_connections_provider"), table_name="connections")
    op.drop_index(op.f("ix_connections_id"), table_name="connections")
    op.drop_table("connections")
