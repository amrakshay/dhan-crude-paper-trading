"""add swing trailing stops, and F&O eligibility on instruments

Two changes, both belonging to the chandelier stop (specification P14/P15).

`swing_stops` holds the state a trailing stop needs to survive a restart, which
specification section 14.4 lists exactly: the position, the ATR at entry, the
highest close since entry and the current stop. None of it is recomputable
after the fact -- the highest close depends on the entry DATE and the stop is a
running maximum, so a bar restated after a corporate action would silently move
a stop that has already been acted on. Unlike the decision journal this table
is deliberately mutable: raising the stop is the whole point. What the
repository refuses is a SECOND ACTIVE ROW for one holding, which would fire
twice.

`instruments.fno_eligible` is derived from the master's own FUTSTK rows on the
same parse pass -- 228 distinct NSE underlyings as of 2026-09-18 -- and exists
for the Closing Auction Session, live since 3 August 2026: continuous cash
trading for an F&O-eligible name now ends at 15:15 with an auction to 15:35, so
a stop that triggers after 15:15 on one of those names cannot fill in
continuous trading. Non-F&O names trade continuously to 15:30 and are
unaffected. It defaults to false in Python rather than with a server default,
because the SQLite and MySQL spellings differ (backend/CLAUDE.md section 3).

Revision ID: d4a17e6b2c88
Revises: c3e81d7a5f92
Create Date: 2026-09-18 12:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from src.database.base import Money, PreciseDateTime


revision: str = "d4a17e6b2c88"
down_revision: Union[str, None] = "c3e81d7a5f92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "swing_stops",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("strategy_key", sa.String(length=64), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("security_id", sa.String(length=32), nullable=False),
        sa.Column("entry_order_id", sa.Integer(), nullable=True),
        # The SESSION of entry -- the regime index's own bar date -- because
        # "highest close since entry" counts sessions, not wall-clock days.
        sa.Column("entry_session", sa.Date(), nullable=False),
        sa.Column("entry_price", Money(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        # P14's inputs, kept so the initial stop can be checked rather than
        # believed.
        sa.Column("entry_atr", Money(), nullable=False),
        sa.Column("atr_multiple", Money(), nullable=False),
        # P15's running state. Both of these only ever rise.
        sa.Column("highest_close", Money(), nullable=False),
        sa.Column("stop_price", Money(), nullable=False),
        sa.Column("last_atr", Money(), nullable=True),
        sa.Column("last_ratcheted_session", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("exit_kind", sa.String(length=16), nullable=True),
        sa.Column("triggered_at", PreciseDateTime, nullable=True),
        sa.Column("trigger_price", Money(), nullable=True),
        sa.Column("exit_order_id", sa.Integer(), nullable=True),
        sa.Column("closed_at", PreciseDateTime, nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("created_at", PreciseDateTime, nullable=False),
        sa.Column("updated_at", PreciseDateTime, nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"]),
        sa.ForeignKeyConstraint(["entry_order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["exit_order_id"], ["orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_swing_stops_id"), "swing_stops", ["id"], unique=False)
    op.create_index(
        op.f("ix_swing_stops_created_at"), "swing_stops", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_swing_stops_strategy_key"), "swing_stops", ["strategy_key"], unique=False
    )
    op.create_index(
        op.f("ix_swing_stops_portfolio_id"), "swing_stops", ["portfolio_id"], unique=False
    )
    op.create_index(op.f("ix_swing_stops_symbol"), "swing_stops", ["symbol"], unique=False)
    op.create_index(
        op.f("ix_swing_stops_security_id"), "swing_stops", ["security_id"], unique=False
    )
    op.create_index(
        op.f("ix_swing_stops_entry_order_id"), "swing_stops", ["entry_order_id"], unique=False
    )
    op.create_index(
        op.f("ix_swing_stops_exit_order_id"), "swing_stops", ["exit_order_id"], unique=False
    )
    op.create_index(op.f("ix_swing_stops_status"), "swing_stops", ["status"], unique=False)
    op.create_index(
        op.f("ix_swing_stops_exit_kind"), "swing_stops", ["exit_kind"], unique=False
    )
    # The monitor's read: every active stop for a strategy.
    op.create_index(
        "ix_swing_stops_active", "swing_stops", ["strategy_key", "status"], unique=False
    )
    # And one holding's stop, per portfolio -- two portfolios holding the same
    # name are two books and two stops.
    op.create_index(
        "ix_swing_stops_portfolio_security",
        "swing_stops",
        ["portfolio_id", "security_id", "status"],
        unique=False,
    )

    # Batch mode so SQLite gets the table rebuild it needs for a NOT NULL
    # column added to an existing table; MySQL takes the plain ALTER.
    with op.batch_alter_table("instruments") as batch:
        batch.add_column(
            sa.Column(
                "fno_eligible",
                sa.Boolean(),
                nullable=False,
                # Existing rows are MCX contracts, which have no cash session
                # and no closing auction. The next master refresh recomputes
                # every row from the file's own FUTSTK entries.
                server_default=sa.false(),
            )
        )
    # Dropped immediately: this project sets defaults in Python, because the
    # SQLite and MySQL spellings differ and autogenerate emits the SQLite one
    # (backend/CLAUDE.md section 3). The default exists only to back-fill the
    # rows already in the table.
    with op.batch_alter_table("instruments") as batch:
        batch.alter_column("fno_eligible", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("instruments") as batch:
        batch.drop_column("fno_eligible")

    op.drop_index("ix_swing_stops_portfolio_security", table_name="swing_stops")
    op.drop_index("ix_swing_stops_active", table_name="swing_stops")
    op.drop_index(op.f("ix_swing_stops_exit_kind"), table_name="swing_stops")
    op.drop_index(op.f("ix_swing_stops_status"), table_name="swing_stops")
    op.drop_index(op.f("ix_swing_stops_exit_order_id"), table_name="swing_stops")
    op.drop_index(op.f("ix_swing_stops_entry_order_id"), table_name="swing_stops")
    op.drop_index(op.f("ix_swing_stops_security_id"), table_name="swing_stops")
    op.drop_index(op.f("ix_swing_stops_symbol"), table_name="swing_stops")
    op.drop_index(op.f("ix_swing_stops_portfolio_id"), table_name="swing_stops")
    op.drop_index(op.f("ix_swing_stops_strategy_key"), table_name="swing_stops")
    op.drop_index(op.f("ix_swing_stops_created_at"), table_name="swing_stops")
    op.drop_index(op.f("ix_swing_stops_id"), table_name="swing_stops")
    op.drop_table("swing_stops")
