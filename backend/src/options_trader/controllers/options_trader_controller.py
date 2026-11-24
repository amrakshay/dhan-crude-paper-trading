"""Turns engine results and refusals into HTTP for the Options Trader page."""
from datetime import date
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.logging_config import get_logger
from src.options.services.chain_view_service import ChainViewError, ChainViewService
from src.options_trader.api_schemas.options_trader_schemas import (
    ChainResponse,
    UnderlyingListResponse,
    UnderlyingResponse,
)
from src.strategies.services.strategy_registry import get_strategy_registry

logger = get_logger("options_trader.controller")

# The module may name itself; the engine may not (src/options/__init__.py).
STRATEGY_KEY = "nse-options-trader"

# Shown beside every margin figure until Phase 2 replaces the model.
MARGIN_NOTE = (
    "Margin shown is a Phase 1 placeholder ESTIMATE, not SPAN: a real index or "
    "stock option short blocks far more at a broker."
)


class OptionsTraderController:
    def __init__(self, session: AsyncSession):
        self.session = session

    def _service(self) -> ChainViewService:
        registry = get_strategy_registry()
        strategy = registry.get(STRATEGY_KEY)
        if strategy is None:
            raise HTTPException(status_code=404, detail="The NSE Options Trader module is not installed.")
        if not registry.is_enabled(STRATEGY_KEY):
            raise HTTPException(
                status_code=409,
                detail=(
                    "NSE Options Trader is switched off. Switch it on from "
                    "Strategies & Features, then refresh the instrument master."
                ),
            )
        return ChainViewService(self.session, strategy)

    async def underlyings(self) -> UnderlyingListResponse:
        found = await self._service().underlyings()
        return UnderlyingListResponse(
            strategyKey=STRATEGY_KEY,
            underlyings=[
                UnderlyingResponse(symbol=one.symbol, kind=one.kind, lotSize=one.lot_size)
                for one in found
            ],
        )

    async def chain(self, underlying: str, expiry: Optional[date]) -> ChainResponse:
        try:
            view = await self._service().chain(underlying, expiry)
        except ChainViewError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return ChainResponse(strategyKey=STRATEGY_KEY, marginNote=MARGIN_NOTE, **view)

    async def release(self) -> None:
        await self._service().release()
