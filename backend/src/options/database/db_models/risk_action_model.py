"""What the options risk desk did, and why -- one row per action.

APPEND-ONLY. A square-off is a fact about the past and the record of it never
changes; the orders it placed are ordinary `orders` rows whose own PLACED event
carries the same reason, so the owning strategy's history explains the close
without knowing this table exists.
"""
from sqlalchemy import Column, ForeignKey, Integer, String, Text

from src.database.base import Money, TimestampedModel

RULE_INTRADAY_SQUARE_OFF = "INTRADAY_SQUARE_OFF"
RULE_MTM_LOSS = "MTM_LOSS"
RULE_MARGIN_SHORTFALL = "MARGIN_SHORTFALL"
# Phase 4: an expiry-day delivery obligation that cannot be met, and the
# after-close settlement of everything that expired.
RULE_PHYSICAL_DELIVERY = "PHYSICAL_DELIVERY"
RULE_EXPIRY_SETTLEMENT = "EXPIRY_SETTLEMENT"
RULES = (
    RULE_INTRADAY_SQUARE_OFF, RULE_MTM_LOSS, RULE_MARGIN_SHORTFALL,
    RULE_PHYSICAL_DELIVERY, RULE_EXPIRY_SETTLEMENT,
)


class RiskAction(TimestampedModel):
    __tablename__ = "options_risk_actions"

    portfolio_id = Column(
        Integer, ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    rule = Column(String(32), nullable=False, index=True)
    # Comma-separated keys of the strategies whose positions were closed.
    strategy_keys = Column(String(255), nullable=True)
    detail = Column(String(500), nullable=False)
    # The measurement that triggered it, kept so the row explains itself.
    measured = Column(Money, nullable=True)
    threshold = Column(Money, nullable=True)
    # JSON: [{securityId, tradingSymbol, side, quantity, orderId, outcome, reason}]
    orders_json = Column(Text, nullable=False)
    outcome = Column(String(16), nullable=False)   # DONE / PARTIAL / FAILED
