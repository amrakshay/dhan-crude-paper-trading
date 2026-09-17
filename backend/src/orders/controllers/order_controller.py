"""Order orchestration."""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.logging_config import get_logger
from src.orders.api_schemas.order_schemas import (
    OrderListResponse,
    OrderResponse,
    PlaceOrderRequest,
    PreviewFillResponse,
    PreviewOrderRequest,
    PreviewOrderResponse,
)
from src.orders.database.db_operations.order_repository import OrderRepository
from src.orders.services.order_service import OrderService, OrderValidationError
from src.positions.database.db_operations.position_repository import PositionRepository

logger = get_logger("orders.controller")


class OrderController:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repository = OrderRepository(session)
        self.service = OrderService(
            order_repository=self.repository,
            instrument_repository=InstrumentRepository(session),
            position_repository=PositionRepository(session),
        )

    async def submit_paper_order(self, request: PlaceOrderRequest) -> OrderResponse:
        try:
            order = await self.service.submit_paper_order(
                security_id=request.security_id,
                side=request.side,
                order_type=request.order_type,
                lots=request.lots,
                limit_price=request.limit_price,
                is_close_order=request.is_close_order,
                portfolio_id=request.portfolio_id,
            )
        except OrderValidationError as exc:
            logger.warning(
                "Rejected an order request for security_id=%s (%s %s %s lot(s)): %s",
                request.security_id, request.side, request.order_type,
                request.lots, exc,
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # The session is configured with expire_on_commit=False, so a re-fetch
        # would return the identity-mapped instance with its events/fills
        # collections as they were when first loaded. Expire first so the
        # response carries every transition that just happened.
        #
        # The id is captured BEFORE expiring: reading an attribute off an
        # expired instance triggers a lazy refresh, which is not allowed
        # outside an awaited context and raises MissingGreenlet.
        order_id = order.id
        await self.session.commit()
        self.session.expire_all()
        refreshed = await self.repository.get_by_id(order_id)
        return OrderResponse.model_validate(refreshed)

    async def preview_order(self, request: PreviewOrderRequest) -> PreviewOrderResponse:
        try:
            preview = await self.service.preview_order(
                security_id=request.security_id,
                side=request.side,
                order_type=request.order_type,
                lots=request.lots,
                limit_price=request.limit_price,
            )
        except OrderValidationError as exc:
            logger.debug(
                "Order preview rejected for security_id=%s: %s",
                request.security_id, exc,
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        instrument = preview["instrument"]
        charges = preview["charges"]
        return PreviewOrderResponse(
            securityId=instrument.security_id,
            tradingSymbol=instrument.trading_symbol,
            side=request.side.upper(),
            orderType=request.order_type.upper(),
            lots=request.lots,
            quantity=preview["quantity"],
            lotSize=int(instrument.lot_size),
            limitPrice=preview["limitPrice"],
            estimatedPrice=preview["estimatedPrice"],
            estimatedFillQuantity=preview["estimatedFillQuantity"],
            wouldRest=preview["wouldRest"],
            wouldPartiallyFill=preview["wouldPartiallyFill"],
            rejectionReason=preview["rejectionReason"],
            note=preview["note"] or None,
            grossValue=preview["grossValue"],
            netAmount=preview["netAmount"],
            charges=charges.as_dict() if charges else None,
            fills=[
                PreviewFillResponse(
                    price=fill.price, quantity=fill.quantity, bookLevel=fill.book_level
                )
                for fill in preview["fills"]
            ],
        )

    async def cancel_paper_order(self, order_id: int) -> OrderResponse:
        try:
            order = await self.service.cancel_paper_order(order_id)
        except OrderValidationError as exc:
            logger.warning("Cannot cancel order %s: %s", order_id, exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        order_id = order.id
        await self.session.commit()
        self.session.expire_all()
        return OrderResponse.model_validate(await self.repository.get_by_id(order_id))

    async def get_order(self, order_id: int) -> OrderResponse:
        order = await self.repository.get_by_id(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
        return OrderResponse.model_validate(order)

    async def list_orders(
        self,
        statuses: Optional[List[str]] = None,
        strategy_key: Optional[str] = None,
        portfolio_id: Optional[int] = None,
        security_id: Optional[str] = None,
        expiry_date: Optional[date] = None,
        placed_from: Optional[datetime] = None,
        placed_to: Optional[datetime] = None,
        search: Optional[str] = None,
        page: int = 0,
        size: int = 50,
    ) -> OrderListResponse:
        orders, total = await self.repository.list_orders(
            statuses=statuses,
            strategy_key=strategy_key,
            portfolio_id=portfolio_id,
            security_id=security_id,
            expiry_date=expiry_date,
            placed_from=placed_from,
            placed_to=placed_to,
            search=search,
            page=page,
            size=size,
        )
        return OrderListResponse(
            orders=[OrderResponse.model_validate(order) for order in orders],
            total=total,
            page=page,
            size=size,
        )
