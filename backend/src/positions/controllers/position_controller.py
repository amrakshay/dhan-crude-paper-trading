"""Position orchestration, including live marks from the in-memory book."""
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import OrderSide
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.logging_config import get_logger
from src.orders.api_schemas.order_schemas import OrderResponse
from src.orders.database.db_operations.order_repository import OrderRepository
from src.positions.api_schemas.position_schemas import (
    ClosePositionRequest,
    PositionListResponse,
    PositionResponse,
    PositionSummaryResponse,
)
from src.positions.database.db_operations.position_repository import PositionRepository
from src.positions.services.position_service import PositionService

logger = get_logger("positions.controller")


class PositionController:
    def __init__(self, session: AsyncSession, book=None):
        self.session = session
        self.repository = PositionRepository(session)
        self.service = PositionService(self.repository)
        self._book = book

    @property
    def book(self):
        if self._book is None:
            from src.market.services.feed_manager import get_feed_manager

            self._book = get_feed_manager().book
        return self._book

    def _mark_for(self, security_id: str) -> Optional[Decimal]:
        """Best available mark: LTP, else the mid of the touch.

        Returns None when there is no live data at all, so the UI can say
        "no mark" instead of showing a P&L of zero.
        """
        row = self.book.get(security_id)
        if not row:
            return None
        # Feed prices are IEEE floats; quantise so marks and MTM do not carry
        # binary noise into the UI.
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

    def _to_response(self, position) -> PositionResponse:
        mark = self._mark_for(position.security_id)
        unrealized = self.service.unrealized_pnl(position, mark)
        response = PositionResponse.model_validate(position)
        response.mark_price = mark
        response.unrealized_pnl = unrealized
        response.has_mark = mark is not None
        response.net_lots = (
            round(int(position.net_quantity) / int(position.lot_size), 4)
            if position.lot_size
            else None
        )
        return response

    async def list_positions(self, include_closed: bool = False) -> PositionListResponse:
        positions = await self.repository.list_all(include_closed=include_closed)
        responses = [self._to_response(position) for position in positions]

        open_responses = [r for r in responses if r.is_open and r.net_quantity != 0]
        marked = [r for r in open_responses if r.unrealized_pnl is not None]
        total_realized = sum(
            (Decimal(str(r.realized_pnl)) for r in responses), Decimal("0")
        )
        total_charges = sum(
            (Decimal(str(r.total_charges)) for r in responses), Decimal("0")
        )
        total_unrealized = (
            sum((Decimal(str(r.unrealized_pnl)) for r in marked), Decimal("0"))
            if marked
            else None
        )
        without_marks = len(open_responses) - len(marked)

        net = total_realized - total_charges
        if total_unrealized is not None:
            net += total_unrealized

        return PositionListResponse(
            positions=responses,
            summary=PositionSummaryResponse(
                openPositions=len(open_responses),
                totalRealizedPnl=total_realized,
                totalUnrealizedPnl=total_unrealized,
                totalCharges=total_charges,
                netPnl=net,
                positionsWithoutMarks=without_marks,
            ),
        )

    async def close_position(
        self, position_id: int, request: ClosePositionRequest
    ) -> OrderResponse:
        """Close (or partially close) by placing an offsetting paper order."""
        position = await self.repository.get_by_id(position_id)
        if position is None:
            raise HTTPException(status_code=404, detail=f"Position {position_id} not found")
        if not position.is_open or int(position.net_quantity) == 0:
            raise HTTPException(status_code=400, detail="Position is already closed")

        net_quantity = int(position.net_quantity)
        lot_size = int(position.lot_size)
        open_quantity = abs(net_quantity)
        open_lots = open_quantity // lot_size

        if request.lots is None:
            # Close everything, including a residual smaller than one lot. A
            # market order can partially fill below a lot when the visible book
            # runs out, and that remainder must still be closable.
            lots = max(1, open_lots)
            quantity_override = open_quantity
        else:
            lots = request.lots
            if lots <= 0:
                raise HTTPException(status_code=400, detail="Nothing to close")
            if lots > open_lots:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Cannot close {lots} lots; the position is "
                        f"{open_quantity} barrels ({open_lots} whole lots)"
                    ),
                )
            quantity_override = None

        # Closing a long means selling, and vice versa.
        side = OrderSide.SELL.value if net_quantity > 0 else OrderSide.BUY.value

        # Imported here rather than at module scope: closing a position places
        # an order, and the orders package imports the position repository, so
        # a top-level import would be circular.
        from src.orders.services.order_service import OrderService, OrderValidationError

        service = OrderService(
            order_repository=OrderRepository(self.session),
            instrument_repository=InstrumentRepository(self.session),
            position_repository=self.repository,
            book=self._book,
        )
        try:
            order = await service.submit_paper_order(
                security_id=position.security_id,
                side=side,
                order_type=request.order_type,
                lots=lots,
                limit_price=request.limit_price,
                is_close_order=True,
                quantity_override=quantity_override,
            )
        except OrderValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        order_id = order.id
        await self.session.commit()
        self.session.expire_all()
        refreshed = await OrderRepository(self.session).get_by_id(order_id)
        return OrderResponse.model_validate(refreshed)
