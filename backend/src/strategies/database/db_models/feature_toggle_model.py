"""Runtime enable/disable state for strategies and capabilities.

The YAML says what a strategy IS; this table says whether it is RUNNING. They
are kept apart so that switching a strategy off is something an operator does
from the Strategies & Features page, not a redeploy -- and so that a strategy's
definition (its rates, its contract specs, its margin model) cannot be edited
from the UI by accident.

One table with a `scope` rather than one per kind, because the toggles are
read together, written together and applied together. A row whose key is not a
configured strategy or a known capability is IGNORED on load, the same property
`MANAGED_KEYS` gives the settings table: a stray row must never start
influencing configuration.
"""
from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, UniqueConstraint

from src.database.base import TimestampedModel

SCOPE_STRATEGY = "STRATEGY"
SCOPE_CAPABILITY = "CAPABILITY"
# Whether an AUTOMATED strategy may submit an order of its own accord. A third
# scope rather than a column on the strategy row, because it is the same kind
# of fact -- runtime state an operator flips from a page -- and the loader,
# the writer and the "ignore a row that names nothing real" rule are already
# written once for a scope. `toggle_key` is the strategy key.
#
# Enabled and armed are deliberately separate switches: enabling a strategy
# makes it compute, decide and journal; arming is what lets it spend money.
SCOPE_AUTOMATION = "AUTOMATION"


class FeatureToggle(TimestampedModel):
    __tablename__ = "feature_toggles"

    scope = Column(String(16), nullable=False, index=True)
    toggle_key = Column(String(64), nullable=False, index=True)
    enabled = Column(Boolean, nullable=False, default=True)
    updated_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("scope", "toggle_key", name="uq_feature_toggle"),
    )
