"""Runtime VALUES an operator may change, per strategy.

The sibling of `feature_toggles`, and the distinction between them is the whole
reason this table exists. A toggle is a named BOOLEAN: is this strategy on, is
it armed, is this rule enforced. Everything about that machinery -- the loader,
the writer, the "ignore a row that names nothing real" rule -- assumes a
boolean, and the comment in `feature_toggle_model.py` said a dedicated table
would earn its migration the moment the first NON-boolean setting appeared.

It appeared on 2026-09-18: the two clock times the scheduler runs on.

READ THIS BEFORE ADDING A ROW TYPE HERE. Root `CLAUDE.md` section 3a says the
YAML says what a strategy IS and nothing about its rates, specs or margin model
is editable from the UI. That rule is intact, and these two settings fit inside
it, because they are not parameters of the RULE -- they are when the machine
wakes up. Moving the order placement from 09:16 to 09:30 does not change what
the strategy is; it changes when this installation acts on it, the same way
`swing.scheduler_interval_seconds` does.

P1-P19 are a different thing entirely and stay in the YAML: the momentum floor,
the ATR multiple, the breadth ramp, the rank cut-off, the lookbacks, and the
rebalance CADENCE -- which is P18, was measured both ways (daily 23.4% CAGR at
-22.2% drawdown against weekly's 19.9% and -18.3%), and picking between them is
choosing a different strategy rather than configuring this one. The moment one
of those appears in this table, the YAML stops being greppable against the
specification's own table and section 3a stops being true.

`value` is TEXT and is parsed by the strategy's own module, for the same reason
the registry stores a policy override without learning what a regime gate is:
the framework holds the row, the module knows what it means. A row naming an
unknown strategy or an unknown setting is IGNORED on load.
"""
from sqlalchemy import Column, ForeignKey, Integer, String, UniqueConstraint

from src.database.base import TimestampedModel


class StrategySetting(TimestampedModel):
    __tablename__ = "strategy_settings"

    strategy_key = Column(String(64), nullable=False, index=True)
    setting_key = Column(String(64), nullable=False, index=True)
    # Text, whatever the setting is. "18:15" today; a number would arrive as
    # "3.5" and be parsed by the module that owns it. Deliberately NOT a JSON
    # blob: a value somebody has to read in a database row should be readable.
    value = Column(String(64), nullable=False)
    updated_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("strategy_key", "setting_key", name="uq_strategy_setting"),
    )

    def __repr__(self) -> str:
        return (
            f"<StrategySetting({self.strategy_key}.{self.setting_key}"
            f"={self.value!r})>"
        )
