"""record the regime policy per decision and per position

Two changes, both belonging to trading through the regime gate.

`swing_decisions` gains the regime the decision was taken under and the POLICY
in force when it was taken. `orders -> swing_decisions -> swing_sessions`
already answered "was the gate off when this trade was opened?", and the
journal is append-only so that join cannot go stale -- these columns are not
here because the data was missing. They are here because `swing_decisions` is
the row that is ALWAYS written, for every action and every non-action, which
makes it the reliable carrier, and because a two-hop join is not something
anybody writes by hand when they want to split a performance report in two.

`swing_stops` gains the same pair per POSITION, and its `stop_price` and
`entry_atr` become nullable. That second half is the load-bearing one. A
position opened while the regime gate was being OBSERVED rather than enforced
keeps that policy: re-enforcing the gate stops new entries and does NOT
liquidate the book (it leaves by rotation or by its trailing stop instead), and
`plan_sells` reads the flag per position to decide. For that to be safe a row
has to exist for EVERY position -- including one entered on a session where
ATR14 could not be computed, which previously got no row at all. Those are
exactly the positions an exemption built on this table would have failed OPEN
for. So the row is always written, `stop_price` is null when there is no level
yet, the monitor skips a null stop rather than comparing against it, and the
nightly ratchet sets the first stop as soon as an ATR exists.

Both sets of columns are NULLABLE, and null means "not recorded". Every row
written before this migration has no policy on it, and a null saying so is the
honest answer -- it is not false and it is not "enforced". The planner treats a
null `entry_regime_enforced` as ENFORCED, so an unknown fails towards the
specification's own behaviour rather than towards an exemption nothing can
justify.

No server defaults (backend/CLAUDE.md section 3): the SQLite and MySQL
spellings differ and autogenerate emits the SQLite one.

Revision ID: b7d3f0c19a45
Revises: d4a17e6b2c88
Create Date: 2026-09-18 15:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from src.database.base import Money


revision: str = "b7d3f0c19a45"
down_revision: Union[str, None] = "d4a17e6b2c88"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("swing_decisions", schema=None) as batch_op:
        # What the index was doing.
        batch_op.add_column(sa.Column("regime_gate_on", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("regime_index_close", Money(), nullable=True))
        batch_op.add_column(sa.Column("regime_index_sma", Money(), nullable=True))
        batch_op.add_column(sa.Column("regime_index_return", Money(), nullable=True))
        # Which of its rules were being OBEYED. Not what they are -- the
        # lookbacks and thresholds are in the strategy's YAML and are editable
        # from nowhere (root CLAUDE.md section 3a).
        batch_op.add_column(sa.Column("regime_enforced", sa.Boolean(), nullable=True))
        batch_op.add_column(
            sa.Column("entry_return_enforced", sa.Boolean(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("gate_variant", sa.String(length=16), nullable=True)
        )

    with op.batch_alter_table("swing_stops", schema=None) as batch_op:
        batch_op.add_column(sa.Column("entry_gate_on", sa.Boolean(), nullable=True))
        batch_op.add_column(
            sa.Column("entry_regime_enforced", sa.Boolean(), nullable=True)
        )
        # NULL now means "this position has no stop yet", which is a real
        # state rather than an absent row.
        batch_op.alter_column(
            "stop_price", existing_type=Money(), nullable=True
        )
        batch_op.alter_column(
            "entry_atr", existing_type=Money(), nullable=True
        )


def downgrade() -> None:
    # A row with no stop price cannot exist in the old schema, and there is no
    # honest value to invent for it: a stop of zero would read as a level that
    # can never be hit, and the entry price would read as a stop that fires
    # immediately. Those rows are DELETED on the way down -- they are the
    # positions that had no stop, so nothing that was protecting anything is
    # lost, and the position itself lives in `positions` either way.
    op.execute("DELETE FROM swing_stops WHERE stop_price IS NULL")
    op.execute("DELETE FROM swing_stops WHERE entry_atr IS NULL")

    with op.batch_alter_table("swing_stops", schema=None) as batch_op:
        batch_op.alter_column(
            "entry_atr", existing_type=Money(), nullable=False
        )
        batch_op.alter_column(
            "stop_price", existing_type=Money(), nullable=False
        )
        batch_op.drop_column("entry_regime_enforced")
        batch_op.drop_column("entry_gate_on")

    with op.batch_alter_table("swing_decisions", schema=None) as batch_op:
        batch_op.drop_column("gate_variant")
        batch_op.drop_column("entry_return_enforced")
        batch_op.drop_column("regime_enforced")
        batch_op.drop_column("regime_index_return")
        batch_op.drop_column("regime_index_sma")
        batch_op.drop_column("regime_index_close")
        batch_op.drop_column("regime_gate_on")
