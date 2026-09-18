"""add daily_bars

Stored daily OHLCV, one row per symbol per session.

**This is not the tick accumulation the fetch-never-accumulate rule forbids.**
Root `CLAUDE.md` section 4 prohibits writing ticks to back a chart -- database
I/O on the hot path, and rebuilding from a stream what the vendor already
serves. This table is written by a background job, once per symbol per day,
from Dhan's `/charts/historical` endpoint. No tick reaches it and the feed does
not know it exists.

It exists because a momentum rotation needs 260+ sessions for ~500 symbols
available instantly at 09:15, and re-fetching that is a five-minute job at
Dhan's rate limits.

Uniqueness is `(exchange_segment, symbol, bar_date)` and NOT the security id:
Dhan's ids move (the universe file's own ids for HEG and HFCL no longer match
the master), and keying a price series on one would silently start a second
copy of a symbol's history the day its id changed. The id is stored as data,
because it is what the next fetch is made with.

Revision ID: a1c7f2b93e40
Revises: 2966a4a8ed93
Create Date: 2026-09-18 09:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from src.database.base import Money, PreciseDateTime


revision: str = "a1c7f2b93e40"
down_revision: Union[str, None] = "2966a4a8ed93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "daily_bars",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("exchange_segment", sa.String(length=16), nullable=False),
        sa.Column("security_id", sa.String(length=32), nullable=True),
        sa.Column("bar_date", sa.Date(), nullable=False),
        sa.Column("open", Money(), nullable=False),
        sa.Column("high", Money(), nullable=False),
        sa.Column("low", Money(), nullable=False),
        sa.Column("close", Money(), nullable=False),
        # Nullable: Dhan reports 0 volume on some index sessions, and a missing
        # figure must stay missing rather than becoming a zero an indicator
        # would happily average.
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_at", PreciseDateTime, nullable=False),
        sa.Column("updated_at", PreciseDateTime, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "exchange_segment", "symbol", "bar_date", name="uq_daily_bars_series_date"
        ),
    )
    op.create_index(op.f("ix_daily_bars_id"), "daily_bars", ["id"], unique=False)
    op.create_index(
        op.f("ix_daily_bars_created_at"), "daily_bars", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_daily_bars_symbol"), "daily_bars", ["symbol"], unique=False)
    op.create_index(
        op.f("ix_daily_bars_bar_date"), "daily_bars", ["bar_date"], unique=False
    )
    # The read every indicator makes: one symbol's history, in order.
    op.create_index(
        "ix_daily_bars_symbol_date", "daily_bars", ["symbol", "bar_date"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_daily_bars_symbol_date", table_name="daily_bars")
    op.drop_index(op.f("ix_daily_bars_bar_date"), table_name="daily_bars")
    op.drop_index(op.f("ix_daily_bars_symbol"), table_name="daily_bars")
    op.drop_index(op.f("ix_daily_bars_created_at"), table_name="daily_bars")
    op.drop_index(op.f("ix_daily_bars_id"), table_name="daily_bars")
    op.drop_table("daily_bars")
