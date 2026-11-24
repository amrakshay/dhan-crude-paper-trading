"""Turns engine results and refusals into HTTP for the Options Trader page."""
from datetime import date
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.time_utils import to_ist
from src.logging_config import get_logger


def to_ist_iso(value):
    return to_ist(value).isoformat() if value is not None else None
from src.options.services.basket_service import BasketError, BasketService, LegRequest
from src.options.services.chain_view_service import ChainViewError, ChainViewService
from src.options.services.span_store import get_span_store
from src.options_trader.api_schemas.options_trader_schemas import (
    BasketRequest,
    ChainResponse,
    ExitGroupRequest,
    UnderlyingListResponse,
    UnderlyingResponse,
)
from src.strategies.services.strategy_registry import get_strategy_registry

logger = get_logger("options_trader.controller")

# The module may name itself; the engine may not (src/options/__init__.py).
STRATEGY_KEY = "nse-options-trader"

# Shown beside every margin figure until Phase 2 replaces the model.
MARGIN_NOTE = (
    "Margin is an ESTIMATE: NSE SPAN from the exchange's daily risk file plus "
    "exposure margin, at live prices. A broker's figure can differ slightly."
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

    # --- Phase 2: baskets, exits, positions, margin ---------------------------
    def _enabled(self) -> None:
        self._service()  # raises the same 404/409 the chain does

    def _baskets(self) -> BasketService:
        self._enabled()
        return BasketService(self.session)

    @staticmethod
    def _legs(request: BasketRequest):
        return [
            LegRequest(leg.security_id, leg.side, leg.lots, leg.order_type, leg.limit_price)
            for leg in request.legs
        ]

    async def preview_basket(self, request: BasketRequest) -> dict:
        try:
            return await self._baskets().preview(STRATEGY_KEY, request.portfolio_id, self._legs(request))
        except BasketError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    async def place_basket(self, request: BasketRequest) -> dict:
        try:
            result = await self._baskets().submit_paper_basket(
                STRATEGY_KEY, request.portfolio_id, self._legs(request), product=request.product
            )
        except BasketError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        await self.session.commit()
        return result

    async def basket(self, basket_id: int) -> dict:
        service = self._baskets()
        if await service.baskets.get(basket_id) is None:
            raise HTTPException(status_code=404, detail=f"No basket {basket_id}")
        return await service.describe(basket_id)

    async def exit_group(self, request: ExitGroupRequest) -> dict:
        results = await self._baskets().exit_group(
            request.portfolio_id, request.underlying.upper(), request.expiry, reason="Exit all"
        )
        await self.session.commit()
        return {"orders": results}

    async def positions(self, portfolio_id: int) -> dict:
        payload = await self._baskets().option_positions(portfolio_id)
        payload["span"] = get_span_store().status()
        payload["marginNote"] = MARGIN_NOTE
        return payload

    def span_status(self) -> dict:
        return get_span_store().status()

    async def risk_desk(self, portfolio_id: int) -> dict:
        """What the risk desk did to this portfolio, newest first, and its state."""
        import json

        from src.options.database.db_operations.risk_action_repository import (
            RiskActionRepository,
        )
        from src.options.services.option_margin_service import card_for
        from src.options.services.risk_desk import get_risk_desk

        from src.core.time_utils import ist_now
        from src.options.services.expiry_service import ExpiryService

        self._enabled()
        rows = await RiskActionRepository(self.session).recent(portfolio_id, limit=50)
        card = card_for(STRATEGY_KEY)
        expiries = await ExpiryService(self.session, card).upcoming(portfolio_id, ist_now())
        return {
            "status": get_risk_desk().status(),
            "rules": {
                "intradaySquareOffAt": card.intraday_square_off_at.strftime("%H:%M"),
                "intradayEntryCutoffAt": card.intraday_entry_cutoff_at.strftime("%H:%M"),
                "mtmLossFraction": card.mtm_loss_fraction,
                "noFreshStockOptionsOnExpiry": card.no_fresh_stock_options_on_expiry,
                "deliveryMarginFrom": card.delivery_margin_from.strftime("%H:%M"),
                "deliveryMarginFraction": card.delivery_margin_fraction,
                "settleAfter": card.settle_after.strftime("%H:%M"),
                "physicalDeliveryChargeRate": card.physical_delivery_charge_rate,
                "exerciseFeePerOrder": card.exercise_fee_per_order,
            },
            "expiries": expiries,
            "actions": [
                {
                    "id": row.id,
                    "rule": row.rule,
                    "outcome": row.outcome,
                    "detail": row.detail,
                    "strategyKeys": (row.strategy_keys or "").split(",") if row.strategy_keys else [],
                    "orders": json.loads(row.orders_json or "[]"),
                    "createdAt": to_ist_iso(row.created_at),
                }
                for row in rows
            ],
        }
