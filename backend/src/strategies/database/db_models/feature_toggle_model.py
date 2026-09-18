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

# Whether one of a strategy's own RULES is enforced. `toggle_key` is
# "<strategy_key>/<policy>" -- see KNOWN_POLICIES in strategy_definition.py for
# what a policy is and, just as importantly, what it is not.
#
# A fourth scope rather than a `strategy_settings` table, because every one of
# these is a named boolean, per scope, overlaid on a default at startup and
# after every change, with unknown keys ignored and an audit column -- which is
# precisely what this table already is. A dedicated table becomes worth its
# migration the moment the FIRST NON-BOOLEAN policy appears (a threshold, a
# mode with three values); until then it would be a second copy of this
# machinery.
SCOPE_POLICY = "POLICY"


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
