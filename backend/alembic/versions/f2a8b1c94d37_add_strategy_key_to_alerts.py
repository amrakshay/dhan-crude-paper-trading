"""add strategy_key to alerts

The Alerts tab shows a strategy's own alerts on that strategy's page, which
means asking "which of these are about the rotation". The answer has to be a
COLUMN.

`alert.body` already names the strategy, and matching on it would mean parsing
English out of a message -- precisely what `swing_stops.exit_kind` exists to
avoid, and what root `CLAUDE.md` section 3a forbids for `orders` and
`positions`: resolve it once when the row is written, never at read time.

NULL means process-wide. A dead background task, a stale feed and an expiring
token are not any strategy's business, and giving them a strategy would be
worse than leaving them unattributed.

Revision ID: f2a8b1c94d37
Revises: e1f4a90c72b6
Create Date: 2026-09-18 23:55:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2a8b1c94d37"
down_revision: Union[str, None] = "e1f4a90c72b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("alerts") as batch:
        batch.add_column(sa.Column("strategy_key", sa.String(length=64), nullable=True))
    op.create_index(
        op.f("ix_alerts_strategy_key"), "alerts", ["strategy_key"], unique=False
    )


def downgrade() -> None:
    # Every row's strategy is still named in its body, so dropping the column
    # loses a query dimension and never a fact.
    op.drop_index(op.f("ix_alerts_strategy_key"), table_name="alerts")
    with op.batch_alter_table("alerts") as batch:
        batch.drop_column("strategy_key")
