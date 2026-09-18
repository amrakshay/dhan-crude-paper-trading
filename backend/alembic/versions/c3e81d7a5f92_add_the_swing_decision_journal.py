"""add the swing decision journal

Order events record what happened to an order. These record what the STRATEGY
decided, and why -- including the decisions that produced no trade, which are
most of them and which nothing else would preserve.

Both tables are append-only by construction: the repository has no update and
no delete, the same way `cash_ledger` has none. A decision record that can be
edited is not a record of what was decided.

`swing_sessions` stores the INPUTS a decision was made from, not just its
conclusion -- the index close and its SMA, the 63-session return, the breadth
numerator AND denominator, the slot count, the ranking as it stood and the
configuration in force. "Gate OFF" cannot be checked in six months;
"NIFTY 23,270.6 against SMA200 24,501.7, breadth 215/448" can.

`session_date` is the SESSION decided -- the regime index's own bar date, which
is this application's trading calendar -- and not the wall-clock date the job
ran. A job that runs at 18:15 on a holiday decides nothing about that day, and
records a SKIPPED row saying so, because "the job did not run" and "the job ran
and there was no session" are different facts.

Revision ID: c3e81d7a5f92
Revises: a1c7f2b93e40
Create Date: 2026-09-18 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from src.database.base import Money, PreciseDateTime


revision: str = "c3e81d7a5f92"
down_revision: Union[str, None] = "a1c7f2b93e40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "swing_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("strategy_key", sa.String(length=64), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("run_kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", PreciseDateTime, nullable=False),
        sa.Column("completed_at", PreciseDateTime, nullable=True),
        # The regime gate's inputs (P8, P9).
        sa.Column("index_symbol", sa.String(length=32), nullable=True),
        sa.Column("index_close", Money(), nullable=True),
        sa.Column("index_sma", Money(), nullable=True),
        sa.Column("gate_on", sa.Boolean(), nullable=True),
        sa.Column("index_return_over_window", Money(), nullable=True),
        sa.Column("entries_allowed", sa.Boolean(), nullable=True),
        # Breadth as a fraction that can be checked, and the slots it produced.
        sa.Column("breadth_above", sa.Integer(), nullable=True),
        sa.Column("breadth_liquid", sa.Integer(), nullable=True),
        sa.Column("slots", sa.Integer(), nullable=True),
        sa.Column("universe_size", sa.Integer(), nullable=True),
        sa.Column("symbols_with_bars", sa.Integer(), nullable=True),
        sa.Column("candidate_count", sa.Integer(), nullable=True),
        # Read whole, never queried into; a column per parameter would be a
        # migration every time the specification gains one.
        sa.Column("ranking_json", sa.Text(), nullable=True),
        sa.Column("parameters_json", sa.Text(), nullable=True),
        sa.Column("filter_counts_json", sa.Text(), nullable=True),
        sa.Column("message", sa.String(length=500), nullable=True),
        sa.Column("created_at", PreciseDateTime, nullable=False),
        sa.Column("updated_at", PreciseDateTime, nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_swing_sessions_id"), "swing_sessions", ["id"], unique=False)
    op.create_index(
        op.f("ix_swing_sessions_created_at"), "swing_sessions", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_swing_sessions_strategy_key"), "swing_sessions", ["strategy_key"], unique=False
    )
    op.create_index(
        op.f("ix_swing_sessions_portfolio_id"), "swing_sessions", ["portfolio_id"], unique=False
    )
    op.create_index(
        op.f("ix_swing_sessions_session_date"), "swing_sessions", ["session_date"], unique=False
    )
    op.create_index(
        op.f("ix_swing_sessions_run_kind"), "swing_sessions", ["run_kind"], unique=False
    )
    op.create_index(
        op.f("ix_swing_sessions_status"), "swing_sessions", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_swing_sessions_started_at"), "swing_sessions", ["started_at"], unique=False
    )
    # "has this run already decided this session?" -- the scheduler's idempotence
    # check and the missed-run detector both read this.
    op.create_index(
        "ix_swing_sessions_lookup",
        "swing_sessions",
        ["strategy_key", "session_date", "run_kind"],
        unique=False,
    )

    op.create_table(
        "swing_decisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("score", Money(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("reference_price", Money(), nullable=True),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("created_at", PreciseDateTime, nullable=False),
        sa.Column("updated_at", PreciseDateTime, nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["swing_sessions.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_swing_decisions_id"), "swing_decisions", ["id"], unique=False)
    op.create_index(
        op.f("ix_swing_decisions_created_at"), "swing_decisions", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_swing_decisions_session_id"), "swing_decisions", ["session_id"], unique=False
    )
    op.create_index(
        op.f("ix_swing_decisions_symbol"), "swing_decisions", ["symbol"], unique=False
    )
    op.create_index(
        op.f("ix_swing_decisions_action"), "swing_decisions", ["action"], unique=False
    )
    op.create_index(
        op.f("ix_swing_decisions_order_id"), "swing_decisions", ["order_id"], unique=False
    )
    op.create_index(
        "ix_swing_decisions_session_symbol",
        "swing_decisions",
        ["session_id", "symbol"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_swing_decisions_session_symbol", table_name="swing_decisions")
    op.drop_index(op.f("ix_swing_decisions_order_id"), table_name="swing_decisions")
    op.drop_index(op.f("ix_swing_decisions_action"), table_name="swing_decisions")
    op.drop_index(op.f("ix_swing_decisions_symbol"), table_name="swing_decisions")
    op.drop_index(op.f("ix_swing_decisions_session_id"), table_name="swing_decisions")
    op.drop_index(op.f("ix_swing_decisions_created_at"), table_name="swing_decisions")
    op.drop_index(op.f("ix_swing_decisions_id"), table_name="swing_decisions")
    op.drop_table("swing_decisions")

    op.drop_index("ix_swing_sessions_lookup", table_name="swing_sessions")
    op.drop_index(op.f("ix_swing_sessions_started_at"), table_name="swing_sessions")
    op.drop_index(op.f("ix_swing_sessions_status"), table_name="swing_sessions")
    op.drop_index(op.f("ix_swing_sessions_run_kind"), table_name="swing_sessions")
    op.drop_index(op.f("ix_swing_sessions_session_date"), table_name="swing_sessions")
    op.drop_index(op.f("ix_swing_sessions_portfolio_id"), table_name="swing_sessions")
    op.drop_index(op.f("ix_swing_sessions_strategy_key"), table_name="swing_sessions")
    op.drop_index(op.f("ix_swing_sessions_created_at"), table_name="swing_sessions")
    op.drop_index(op.f("ix_swing_sessions_id"), table_name="swing_sessions")
    op.drop_table("swing_sessions")
