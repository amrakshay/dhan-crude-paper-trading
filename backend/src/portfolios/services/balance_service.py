"""The one place that knows what a portfolio is worth.

Four numbers, defined once:

    cash           = sum of the cash ledger                     (replayed)
    blocked_margin = sum of margin_estimate(p) for open SHORTS   (derived)
    available      = cash - blocked_margin                       (tradable)
    equity         = cash + mark_to_market(open positions)       (needs marks)

They are kept apart deliberately. Collapsing them into one "balance" is what
makes a short position's effect invisible: the premium it received is in cash,
but a large part of that cash cannot be spent.

Two honesty rules this module exists to enforce:

* **Blocked margin is an ESTIMATE.** Real margin is SPAN + exposure, computed
  by the exchange from files this tool does not consume. Every figure produced
  here carries `marginIsEstimate: true`, and no surface may print it as "margin
  required" without saying so.
* **Equity with an unmarked position says so.** A position whose strategy is
  switched off, or whose feed is down, has no mark. Treating it as worth zero
  would be the worst failure this page can have, so the count of unmarked
  positions travels with the number and the UI must show it.

Nothing here runs on the tick path. Equity is computed on request, at the
Positions/Portfolios cadence.
"""
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.logging_config import get_logger
from src.portfolios.database.db_operations.portfolio_repository import (
    CashLedgerRepository,
)
from src.positions.database.db_operations.position_repository import PositionRepository
from src.strategies.services.strategy_registry import get_strategy_registry

logger = get_logger("portfolios.balance")

ZERO = Decimal("0")
MONEY_QUANTUM = Decimal("0.01")


def _q(value: Decimal) -> Decimal:
    return Decimal(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


@dataclass
class PortfolioBalance:
    portfolio_id: int
    cash: Decimal
    blocked_margin: Decimal
    available: Decimal
    # None when a position has no mark: an equity figure that quietly treats an
    # unmarked position as worthless is a lie, so it is withheld instead.
    equity: Optional[Decimal]
    mark_to_market: Optional[Decimal]
    open_positions: int = 0
    unmarked_positions: int = 0
    # Which strategies the unmarked positions belong to, so the UI can say
    # *why* the mark is missing rather than just that it is.
    unmarked_strategies: List[str] = field(default_factory=list)

    @property
    def has_unmarked(self) -> bool:
        return self.unmarked_positions > 0

    def as_dict(self) -> Dict[str, object]:
        return {
            "portfolioId": self.portfolio_id,
            "cash": str(self.cash),
            "blockedMargin": str(self.blocked_margin),
            "marginIsEstimate": True,
            "available": str(self.available),
            "equity": str(self.equity) if self.equity is not None else None,
            "markToMarket": (
                str(self.mark_to_market) if self.mark_to_market is not None else None
            ),
            "openPositions": self.open_positions,
            "unmarkedPositions": self.unmarked_positions,
            "unmarkedStrategies": list(self.unmarked_strategies),
            "equityIncludesUnmarked": self.has_unmarked,
        }


class BalanceService:
    """Cash, margin, available and equity. Do not recompute these elsewhere."""

    def __init__(self, session: AsyncSession, book=None):
        self.session = session
        self.ledger = CashLedgerRepository(session)
        self.positions = PositionRepository(session)
        self._book = book

    @property
    def book(self):
        if self._book is None:
            from src.market.services.feed_manager import get_feed_manager

            self._book = get_feed_manager().book
        return self._book

    # --- marks -------------------------------------------------------------
    def mark_for(self, security_id: str) -> Optional[Decimal]:
        """LTP, else the mid of the touch, else None.

        None means "no mark", and every caller must carry that through rather
        than substituting zero.
        """
        row = self.book.get(str(security_id))
        if not row:
            return None
        if row.get("ltp"):
            return Decimal(str(row["ltp"])).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
        bid, ask = row.get("bid"), row.get("ask")
        if bid and ask:
            return ((Decimal(str(bid)) + Decimal(str(ask))) / 2).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
        return None

    # --- margin ------------------------------------------------------------
    @staticmethod
    def margin_estimate(position) -> Decimal:
        """What one open SHORT position ties up. AN ESTIMATE, NOT A BROKER FIGURE.

        A long option ties up nothing beyond the premium already paid: the
        worst case is that it expires worthless, and that money has already
        left the ledger. A short can lose more than it received, which is what
        the estimate stands in for.

        The model and its percentage come from the strategy module, with the
        same provenance discipline as a charge rate: a stated basis, an as-of
        date and a confidence marker of APPROXIMATION.
        """
        net = int(position.net_quantity or 0)
        if net >= 0:
            return ZERO

        strategy = get_strategy_registry().get(position.strategy_key)
        if strategy is None:
            # A position whose strategy has been removed still ties money up;
            # refusing to estimate would silently free it.
            logger.warning(
                "No strategy %r for position %s; margin estimated at zero, which "
                "understates what this short would really require",
                position.strategy_key, position.id,
            )
            return ZERO

        percent = Decimal(str(strategy.margin.short_option_percent_of_notional))
        price = Decimal(str(position.average_price or 0))
        notional = abs(Decimal(net)) * price
        return _q(percent * notional)

    # --- the four numbers --------------------------------------------------
    async def balance_for(self, portfolio_id: int) -> PortfolioBalance:
        cash = await self.ledger.balance(portfolio_id)
        positions = await self.positions.list_open(portfolio_id=portfolio_id)

        blocked = ZERO
        mark_to_market = ZERO
        unmarked = 0
        unmarked_strategies: List[str] = []
        live = 0

        for position in positions:
            net = int(position.net_quantity or 0)
            if net == 0:
                continue
            live += 1
            blocked += self.margin_estimate(position)

            mark = self.mark_for(position.security_id)
            if mark is None:
                unmarked += 1
                if position.strategy_key not in unmarked_strategies:
                    unmarked_strategies.append(position.strategy_key)
                continue
            # A long is worth what it would fetch; a short is a liability of
            # what it would cost to buy back. The sign of net_quantity carries
            # both cases.
            mark_to_market += Decimal(net) * mark

        cash = _q(cash)
        blocked = _q(blocked)
        return PortfolioBalance(
            portfolio_id=int(portfolio_id),
            cash=cash,
            blocked_margin=blocked,
            available=_q(cash - blocked),
            # Withheld entirely when anything is unmarked: a partial equity
            # figure reads as a complete one.
            equity=_q(cash + mark_to_market) if unmarked == 0 else None,
            mark_to_market=_q(mark_to_market) if unmarked == 0 else None,
            open_positions=live,
            unmarked_positions=unmarked,
            unmarked_strategies=unmarked_strategies,
        )

    async def available_balance(self, portfolio_id: int) -> Decimal:
        """What can be spent or withdrawn right now.

        Named for what it is. `get_fund_limits` is a broker SDK call and the
        name is banned outright by tests/test_no_real_orders.py; nothing here
        reaches a broker for a balance, because the balance is paper money in a
        local table.
        """
        return (await self.balance_for(portfolio_id)).available
