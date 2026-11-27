"""Add the product type (Intraday / Carry-forward) to orders and positions

For the NSE Options Trader (OPTIONS_TRADER_PLAN.md). Nullable, with no server
default (backend CLAUDE.md section 3): every existing row is a strategy that
never chose a product, and NULL says exactly that.

Additive only. Nothing existing is altered.

Revision ID: c3d9e7a1b204
Revises: 0b9557d42113
Create Date: 2026-11-24 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c3d9e7a1b204'
down_revision: Union[str, None] = '0b9557d42113'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('product', sa.String(length=16), nullable=True))
    with op.batch_alter_table('positions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('product', sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('positions', schema=None) as batch_op:
        batch_op.drop_column('product')
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_column('product')
