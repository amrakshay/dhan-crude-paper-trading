"""add strategy_settings for runtime values

The sibling of `feature_toggles`, and the split between them is deliberate. A
toggle is a named BOOLEAN -- is this strategy on, is it armed, is this rule
enforced -- and the whole of that machinery assumes one. `feature_toggle_model`
said a dedicated table would earn its migration the moment the first
NON-boolean setting appeared. It appeared on 2026-09-18: the two clock times
the scheduler runs on.

Root CLAUDE.md section 3a is intact and these two fit inside it. They are not
parameters of the RULE -- they are when the machine wakes up, like
`swing.scheduler_interval_seconds`. Moving the order placement from 09:16 to
09:30 does not change what the strategy is. P1-P19 stay in the YAML, including
the rebalance CADENCE (P18), which was measured both ways and where choosing is
picking a different strategy rather than configuring this one.

`value` is a short VARCHAR rather than JSON, because a value somebody has to
read in a database row should be readable. It is parsed and validated by the
strategy's own module (`src/swing/services/schedule_settings.py`), for the same
reason the registry stores a policy override without learning what a regime
gate is.

No server defaults, and VARCHAR lengths everywhere (backend/CLAUDE.md
section 3).

Revision ID: c9e42a7b51d8
Revises: b7d3f0c19a45
Create Date: 2026-09-18 16:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from src.database.base import PreciseDateTime


revision: str = "c9e42a7b51d8"
down_revision: Union[str, None] = "b7d3f0c19a45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "strategy_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("strategy_key", sa.String(length=64), nullable=False),
        sa.Column("setting_key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(length=64), nullable=False),
        # Who moved it. The same audit column `feature_toggles` carries, and
        # SET NULL rather than CASCADE: deleting a user must not delete the
        # record that the schedule was changed.
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        # No server default: the SQLite and MySQL spellings of CURRENT_TIMESTAMP
        # differ and autogenerate emits the SQLite one. Set in Python.
        sa.Column("created_at", PreciseDateTime, nullable=False),
        sa.Column("updated_at", PreciseDateTime, nullable=False),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("strategy_key", "setting_key", name="uq_strategy_setting"),
    )
    # `Base` indexes the primary key and `TimestampedModel` indexes created_at;
    # without both of these the next --autogenerate reports drift.
    op.create_index(
        op.f("ix_strategy_settings_id"), "strategy_settings", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_strategy_settings_strategy_key"),
        "strategy_settings",
        ["strategy_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_strategy_settings_setting_key"),
        "strategy_settings",
        ["setting_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_strategy_settings_created_at"),
        "strategy_settings",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    # Every row here is an override of a value the YAML also carries, so
    # dropping the table loses a preference and never a fact: the strategy
    # falls back to its own configured times.
    op.drop_index(
        op.f("ix_strategy_settings_created_at"), table_name="strategy_settings"
    )
    op.drop_index(
        op.f("ix_strategy_settings_setting_key"), table_name="strategy_settings"
    )
    op.drop_index(
        op.f("ix_strategy_settings_strategy_key"), table_name="strategy_settings"
    )
    op.drop_index(op.f("ix_strategy_settings_id"), table_name="strategy_settings")
    op.drop_table("strategy_settings")
