"""Portfolio orchestration. Services raise, this translates."""
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.logging_config import get_logger
from src.portfolios.api_schemas.portfolio_schemas import (
    CreatePortfolioRequest,
    LedgerEntryResponse,
    LedgerResponse,
    MoneyRequest,
    PortfolioBalanceResponse,
    PortfolioListResponse,
    PortfolioResponse,
    UpdatePortfolioRequest,
)
from src.portfolios.database.db_operations.portfolio_repository import (
    CashLedgerRepository,
    PortfolioRepository,
)
from src.portfolios.services.balance_service import BalanceService
from src.portfolios.services.portfolio_service import (
    InsufficientFunds,
    PortfolioError,
    PortfolioNotFound,
    PortfolioService,
)

logger = get_logger("portfolios.controller")


class PortfolioController:
    def __init__(self, session: AsyncSession, book=None):
        self.session = session
        self.repository = PortfolioRepository(session)
        self.ledger = CashLedgerRepository(session)
        self.service = PortfolioService(session, book=book)
        self.balances = BalanceService(session, book=book)

    # --- helpers -----------------------------------------------------------
    @staticmethod
    def _raise(error: Exception) -> None:
        if isinstance(error, PortfolioNotFound):
            raise HTTPException(status_code=404, detail=str(error)) from error
        raise HTTPException(status_code=400, detail=str(error)) from error

    async def _to_response(self, portfolio, with_balance: bool = True) -> PortfolioResponse:
        response = PortfolioResponse.model_validate(portfolio)
        response.strategies = await self.repository.strategies_for(portfolio.id)
        if with_balance:
            balance = await self.balances.balance_for(portfolio.id)
            response.balance = PortfolioBalanceResponse(**balance.as_dict())
        return response

    # --- reads -------------------------------------------------------------
    async def list_portfolios(self, include_archived: bool = False) -> PortfolioListResponse:
        portfolios = await self.repository.list_portfolios(
            include_archived=include_archived
        )
        return PortfolioListResponse(
            portfolios=[await self._to_response(portfolio) for portfolio in portfolios]
        )

    async def get_portfolio(self, portfolio_id: int) -> PortfolioResponse:
        try:
            portfolio = await self.service.get(portfolio_id)
        except PortfolioError as error:
            self._raise(error)
        return await self._to_response(portfolio)

    async def get_ledger(
        self, portfolio_id: int, page: int = 0, size: int = 100
    ) -> LedgerResponse:
        try:
            portfolio = await self.service.get(portfolio_id)
        except PortfolioError as error:
            self._raise(error)

        entries, total = await self.ledger.list_entries(portfolio.id, page=page, size=size)
        balance = await self.balances.balance_for(portfolio.id)
        return LedgerResponse(
            portfolioId=portfolio.id,
            entries=[LedgerEntryResponse.model_validate(entry) for entry in entries],
            total=total,
            page=page,
            size=size,
            balance=PortfolioBalanceResponse(**balance.as_dict()),
        )

    # --- writes ------------------------------------------------------------
    async def create(
        self, request: CreatePortfolioRequest, user_id: Optional[int] = None
    ) -> PortfolioResponse:
        try:
            portfolio = await self.service.create(
                name=request.name,
                description=request.description,
                strategy_keys=request.strategies,
                opening_balance=request.opening_balance,
                created_by_user_id=user_id,
            )
            portfolio_id = portfolio.id
            await self.session.commit()
            self.session.expire_all()
        except PortfolioError as error:
            logger.warning("Rejected a portfolio creation: %s", error)
            self._raise(error)

        return await self.get_portfolio(portfolio_id)

    async def update(
        self, portfolio_id: int, request: UpdatePortfolioRequest
    ) -> PortfolioResponse:
        try:
            await self.service.update(
                portfolio_id,
                name=request.name,
                description=request.description,
                strategy_keys=request.strategies,
            )
            await self.session.commit()
            self.session.expire_all()
        except PortfolioError as error:
            self._raise(error)
        return await self.get_portfolio(portfolio_id)

    async def archive(self, portfolio_id: int) -> PortfolioResponse:
        try:
            await self.service.archive(portfolio_id)
            await self.session.commit()
            self.session.expire_all()
        except PortfolioError as error:
            self._raise(error)
        return await self.get_portfolio(portfolio_id)

    async def restore(self, portfolio_id: int) -> PortfolioResponse:
        try:
            await self.service.restore(portfolio_id)
            await self.session.commit()
            self.session.expire_all()
        except PortfolioError as error:
            self._raise(error)
        return await self.get_portfolio(portfolio_id)

    async def delete(self, portfolio_id: int) -> None:
        """Refused for any portfolio with history -- see PortfolioService."""
        try:
            await self.service.delete(portfolio_id)
            await self.session.commit()
        except PortfolioError as error:
            logger.warning("Refused to delete portfolio %s: %s", portfolio_id, error)
            self._raise(error)

    async def deposit(
        self, portfolio_id: int, request: MoneyRequest, user_id: Optional[int] = None
    ) -> LedgerResponse:
        try:
            await self.service.deposit(
                portfolio_id, request.amount, request.note, created_by_user_id=user_id
            )
            await self.session.commit()
            self.session.expire_all()
        except PortfolioError as error:
            self._raise(error)
        return await self.get_ledger(portfolio_id)

    async def withdraw(
        self, portfolio_id: int, request: MoneyRequest, user_id: Optional[int] = None
    ) -> LedgerResponse:
        try:
            await self.service.withdraw(
                portfolio_id, request.amount, request.note, created_by_user_id=user_id
            )
            await self.session.commit()
            self.session.expire_all()
        except InsufficientFunds as error:
            logger.warning("Refused a withdrawal from portfolio %s: %s", portfolio_id, error)
            self._raise(error)
        except PortfolioError as error:
            self._raise(error)
        return await self.get_ledger(portfolio_id)
