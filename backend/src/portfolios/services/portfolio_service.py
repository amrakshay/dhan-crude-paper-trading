"""Portfolio lifecycle and the money that moves in and out of one.

Rules this module enforces, all of them on data rather than on convention:

* **A portfolio with history is archived, never deleted.** Deleting one would
  silently change every total that ever included it, so the API refuses it
  independently of whether the UI offers the button.
* **A withdrawal may not take `available` below zero**, and may not reach money
  blocked against open short positions. The blocked figure is an estimate, and
  the refusal says so.
* **Every movement is an append-only ledger entry.** A correction is a new
  entry; nothing here updates or deletes one.
"""
from decimal import Decimal
from typing import List, Optional, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.time_utils import utc_now
from src.logging_config import get_logger
from src.portfolios.database.db_models.portfolio_model import (
    ENTRY_DEPOSIT,
    ENTRY_WITHDRAWAL,
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    Portfolio,
)
from src.portfolios.database.db_operations.portfolio_repository import (
    CashLedgerRepository,
    PortfolioRepository,
)
from src.portfolios.services.balance_service import BalanceService
from src.strategies.services.strategy_registry import get_strategy_registry

logger = get_logger("portfolios.service")

ZERO = Decimal("0")


class PortfolioError(Exception):
    """A portfolio rule was broken. Controllers turn these into 400s."""


class PortfolioNotFound(PortfolioError):
    pass


class InsufficientFunds(PortfolioError):
    """Not enough available money. Carries the shortfall so callers can say so."""

    def __init__(self, message: str, required: Decimal, available: Decimal):
        super().__init__(message)
        self.required = required
        self.available = available


class PortfolioService:
    def __init__(self, session: AsyncSession, book=None):
        self.session = session
        self.repository = PortfolioRepository(session)
        self.ledger = CashLedgerRepository(session)
        self.balances = BalanceService(session, book=book)

    # --- resolution --------------------------------------------------------
    async def get(self, portfolio_id: int) -> Portfolio:
        portfolio = await self.repository.get_by_id(int(portfolio_id))
        if portfolio is None:
            raise PortfolioNotFound(f"No portfolio with id {portfolio_id}")
        return portfolio

    async def resolve_for_trading(
        self, portfolio_id: Optional[int]
    ) -> Portfolio:
        """The portfolio a trade belongs to.

        An explicit id is used as given. Without one, a single active portfolio
        is unambiguous and is used; with several, the caller is told to choose
        rather than having one picked for them -- a trade landing in the wrong
        book is exactly what the portfolio selector exists to prevent.
        """
        if portfolio_id is not None:
            portfolio = await self.get(portfolio_id)
            if portfolio.status != STATUS_ACTIVE:
                raise PortfolioError(
                    f"Portfolio {portfolio.name!r} is archived and cannot trade. "
                    f"Its history stays readable."
                )
            return portfolio

        active = await self.repository.list_portfolios(include_archived=False)
        if not active:
            raise PortfolioError(
                "There is no active portfolio to trade into. An account admin "
                "creates one on the Portfolios page."
            )
        if len(active) > 1:
            raise PortfolioError(
                "Several portfolios are active, so this trade would be "
                "ambiguous. Pick one with the portfolio selector."
            )
        return active[0]

    # --- lifecycle ---------------------------------------------------------
    async def create(
        self,
        name: str,
        description: Optional[str] = None,
        strategy_keys: Optional[Sequence[str]] = None,
        opening_balance: Decimal = ZERO,
        created_by_user_id: Optional[int] = None,
    ) -> Portfolio:
        name = str(name or "").strip()
        if not name:
            raise PortfolioError("A portfolio needs a name")
        if len(name) > 80:
            raise PortfolioError("A portfolio name may be at most 80 characters")
        if await self.repository.get_by_name(name) is not None:
            raise PortfolioError(f"A portfolio called {name!r} already exists")

        registry = get_strategy_registry()
        keys = [str(key) for key in (strategy_keys or [])]
        for key in keys:
            if registry.get(key) is None:
                raise PortfolioError(f"Unknown strategy module {key!r}")

        portfolio = Portfolio(
            name=name,
            description=(description or None),
            status=STATUS_ACTIVE,
        )
        self.session.add(portfolio)
        await self.session.flush()

        if keys:
            await self.repository.set_strategies(portfolio.id, keys)

        opening = Decimal(str(opening_balance or 0))
        if opening > 0:
            await self.ledger.add_entry(
                portfolio_id=portfolio.id,
                entry_type=ENTRY_DEPOSIT,
                amount=opening,
                entry_at=utc_now(),
                note="Opening balance",
                created_by_user_id=created_by_user_id,
            )

        logger.info(
            "Portfolio %r created (id=%s) with strategies %s and an opening "
            "balance of %s",
            name, portfolio.id, keys or "none", opening,
        )
        return portfolio

    async def update(
        self,
        portfolio_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        strategy_keys: Optional[Sequence[str]] = None,
    ) -> Portfolio:
        portfolio = await self.get(portfolio_id)

        if name is not None:
            name = str(name).strip()
            if not name:
                raise PortfolioError("A portfolio needs a name")
            existing = await self.repository.get_by_name(name)
            if existing is not None and existing.id != portfolio.id:
                raise PortfolioError(f"A portfolio called {name!r} already exists")
            portfolio.name = name

        if description is not None:
            portfolio.description = description or None

        if strategy_keys is not None:
            registry = get_strategy_registry()
            keys = [str(key) for key in strategy_keys]
            for key in keys:
                if registry.get(key) is None:
                    raise PortfolioError(f"Unknown strategy module {key!r}")
            await self.repository.set_strategies(portfolio.id, keys)

        await self.session.flush()
        return portfolio

    async def archive(self, portfolio_id: int) -> Portfolio:
        portfolio = await self.get(portfolio_id)
        portfolio.status = STATUS_ARCHIVED
        await self.session.flush()
        logger.info(
            "Portfolio %r archived; its history stays readable", portfolio.name
        )
        return portfolio

    async def restore(self, portfolio_id: int) -> Portfolio:
        portfolio = await self.get(portfolio_id)
        portfolio.status = STATUS_ACTIVE
        await self.session.flush()
        return portfolio

    async def delete(self, portfolio_id: int) -> None:
        """Only ever a portfolio that has never been used.

        Refused on DATA -- any order, position, chart trade or ledger entry is
        history -- rather than by comparing names or trusting the UI to hide a
        button.
        """
        portfolio = await self.get(portfolio_id)
        if await self.repository.has_history(portfolio.id):
            raise PortfolioError(
                f"Portfolio {portfolio.name!r} has history and cannot be "
                f"deleted. Archive it instead: its trades and ledger stay "
                f"readable, and no past total moves."
            )
        await self.session.delete(portfolio)
        await self.session.flush()
        logger.info("Portfolio %r deleted; it had no history", portfolio.name)

    # --- money -------------------------------------------------------------
    async def deposit(
        self,
        portfolio_id: int,
        amount: Decimal,
        note: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
    ):
        portfolio = await self.get(portfolio_id)
        amount = Decimal(str(amount))
        if amount <= 0:
            raise PortfolioError("A deposit must be a positive amount")

        entry = await self.ledger.add_entry(
            portfolio_id=portfolio.id,
            entry_type=ENTRY_DEPOSIT,
            amount=amount,
            entry_at=utc_now(),
            note=note,
            created_by_user_id=created_by_user_id,
        )
        logger.info("Deposited %s into portfolio %r", amount, portfolio.name)
        return entry

    async def withdraw(
        self,
        portfolio_id: int,
        amount: Decimal,
        note: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
    ):
        portfolio = await self.get(portfolio_id)
        amount = Decimal(str(amount))
        if amount <= 0:
            raise PortfolioError("A withdrawal must be a positive amount")

        balance = await self.balances.balance_for(portfolio.id)
        if amount > balance.available:
            detail = (
                f"Cannot withdraw {amount} from {portfolio.name!r}: only "
                f"{balance.available} is available."
            )
            if balance.blocked_margin > 0:
                detail += (
                    f" Cash is {balance.cash}, of which {balance.blocked_margin} "
                    f"is an ESTIMATE of the margin blocked against open short "
                    f"positions and cannot be withdrawn."
                )
            raise InsufficientFunds(detail, amount, balance.available)

        entry = await self.ledger.add_entry(
            portfolio_id=portfolio.id,
            entry_type=ENTRY_WITHDRAWAL,
            # Signed: money out is negative, so the balance stays a plain SUM.
            amount=-amount,
            entry_at=utc_now(),
            note=note,
            created_by_user_id=created_by_user_id,
        )
        logger.info("Withdrew %s from portfolio %r", amount, portfolio.name)
        return entry

    # --- listing -----------------------------------------------------------
    async def list_with_balances(self, include_archived: bool = False) -> List[dict]:
        portfolios = await self.repository.list_portfolios(
            include_archived=include_archived
        )
        attachments = await self.repository.strategies_for_many(
            [portfolio.id for portfolio in portfolios]
        )

        rows: List[dict] = []
        for portfolio in portfolios:
            balance = await self.balances.balance_for(portfolio.id)
            rows.append(
                {
                    "portfolio": portfolio,
                    "strategies": attachments.get(portfolio.id, []),
                    "balance": balance,
                }
            )
        return rows
