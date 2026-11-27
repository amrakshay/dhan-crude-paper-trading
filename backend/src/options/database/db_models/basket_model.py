"""Option baskets: several legs placed together, each its own paper order.

A basket is a RECORD of an intent and of what each leg did, not a container
that owns orders. Every leg is an ordinary row in `orders`, placed through
`submit_paper_order` like any other, so the order book, positions, charges and
P&L reports need to know nothing about baskets. `option_basket_legs.order_id`
is the join, and it is NULL for a leg refused before an order existed.

APPEND-ONLY in spirit: a basket's legs are written once, in the order they
were SENT (buys first), with the outcome each one got. Dhan's basket behaves
this way -- a leg can fill, part-fill, rest or be rejected while the others go
-- and so does this one (OPTIONS_TRADER_PLAN.md D3).
"""
from sqlalchemy import Column, ForeignKey, Integer, String

from src.database.base import Money, TimestampedModel

BASKET_PLACING = "PLACING"
BASKET_COMPLETE = "COMPLETE"          # every leg filled
BASKET_PARTIAL = "PARTIAL"            # some legs filled or resting, some not
BASKET_RESTING = "RESTING"            # nothing refused, something still open
BASKET_REJECTED = "REJECTED"          # nothing executed at all
BASKET_CANCELLED = "CANCELLED"        # nothing executed, and the rest was withdrawn


class OptionBasket(TimestampedModel):
    __tablename__ = "option_baskets"

    strategy_key = Column(String(64), nullable=False, index=True)
    portfolio_id = Column(
        Integer, ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    product = Column(String(16), nullable=True)
    status = Column(String(16), nullable=False, index=True)
    leg_count = Column(Integer, nullable=False)
    # The portfolio's SPAN margin as previewed when it was placed, kept so a
    # basket's record says what it was EXPECTED to block. An estimate.
    previewed_margin = Column(Money, nullable=True)


class OptionBasketLeg(TimestampedModel):
    __tablename__ = "option_basket_legs"

    basket_id = Column(
        Integer, ForeignKey("option_baskets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence = Column(Integer, nullable=False)          # 1-based, in SENT order
    security_id = Column(String(32), nullable=False)
    trading_symbol = Column(String(128), nullable=True)
    side = Column(String(4), nullable=False)
    lots = Column(Integer, nullable=False)
    order_type = Column(String(8), nullable=False)
    limit_price = Column(Money, nullable=True)
    order_id = Column(
        Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True
    )
    outcome = Column(String(24), nullable=False)        # the order's status, or REJECTED
    reason = Column(String(500), nullable=True)
