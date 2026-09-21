"""Order, event, fill and charge persistence."""
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import TERMINAL_ORDER_STATUSES, OrderStatus
from src.core.base_repository import BaseRepository
from src.orders.database.db_models.order_model import (
    Order,
    OrderCharge,
    OrderEvent,
    OrderFill,
)


class OrderRepository(BaseRepository[Order]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, Order)

    async def get_by_client_order_id(self, client_order_id: str) -> Optional[Order]:
        result = await self.session.execute(
            select(Order).where(Order.client_order_id == client_order_id)
        )
        return result.scalar_one_or_none()

    async def list_open_orders(self) -> List[Order]:
        """Orders still eligible to fill. The matcher polls these.

        `completed_at IS NULL` is what makes the name true. A MARKET order
        that half-filled against a thin book is left in PARTIALLY_FILLED and
        stamped `completed_at` -- it is finished, there being no price for it
        to rest at (`order_service._finalise`, `terminal_when_incomplete`).
        Selecting on status alone returned it to the matcher every 250 ms
        forever, where it was silently dropped for not being a LIMIT order:
        harmless, but it made this method's own docstring false and invited
        the reader to think the remainder might still fill.
        """
        result = await self.session.execute(
            select(Order)
            .where(
                Order.status.in_(
                    [OrderStatus.OPEN.value, OrderStatus.PARTIALLY_FILLED.value]
                ),
                Order.completed_at.is_(None),
            )
            .order_by(Order.placed_at.asc())
        )
        return list(result.scalars().all())

    async def list_orders(
        self,
        statuses: Optional[Sequence[str]] = None,
        strategy_key: Optional[str] = None,
        portfolio_id: Optional[int] = None,
        security_id: Optional[str] = None,
        expiry_date: Optional[date] = None,
        placed_from: Optional[datetime] = None,
        placed_to: Optional[datetime] = None,
        search: Optional[str] = None,
        page: int = 0,
        size: int = 50,
    ) -> tuple[List[Order], int]:
        query = select(Order)
        conditions = []
        if statuses:
            conditions.append(Order.status.in_(list(statuses)))
        if strategy_key:
            conditions.append(Order.strategy_key == strategy_key)
        if portfolio_id is not None:
            conditions.append(Order.portfolio_id == int(portfolio_id))
        if security_id:
            conditions.append(Order.security_id == security_id)
        if expiry_date:
            conditions.append(Order.expiry_date == expiry_date)
        if placed_from:
            conditions.append(Order.placed_at >= placed_from)
        if placed_to:
            conditions.append(Order.placed_at <= placed_to)
        if search:
            conditions.append(Order.trading_symbol.ilike(f"%{search}%"))
        if conditions:
            query = query.where(and_(*conditions))

        count_result = await self.session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        result = await self.session.execute(
            query.order_by(Order.placed_at.desc()).offset(page * size).limit(size)
        )
        return list(result.scalars().all()), total

    async def add_event(
        self,
        order: Order,
        event_type: str,
        status: str,
        event_at: datetime,
        price: Optional[Decimal] = None,
        quantity: Optional[int] = None,
        message: Optional[str] = None,
    ) -> OrderEvent:
        event = OrderEvent(
            order_id=order.id,
            event_type=event_type,
            status=status,
            event_at=event_at,
            price=price,
            quantity=quantity,
            message=message,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def add_fill(
        self,
        order: Order,
        fill_at: datetime,
        price: Decimal,
        quantity: int,
        book_level: Optional[int] = None,
        slippage_ticks: int = 0,
        reference_price: Optional[Decimal] = None,
    ) -> OrderFill:
        fill = OrderFill(
            order_id=order.id,
            fill_at=fill_at,
            price=price,
            quantity=quantity,
            book_level=book_level,
            slippage_ticks=slippage_ticks,
            reference_price=reference_price,
        )
        self.session.add(fill)
        await self.session.flush()
        return fill

    async def upsert_charges(self, order: Order, values: Dict[str, Any]) -> OrderCharge:
        """One charges row per order, recomputed as the order fills further."""
        result = await self.session.execute(
            select(OrderCharge).where(OrderCharge.order_id == order.id)
        )
        charge = result.scalar_one_or_none()
        if charge is None:
            charge = OrderCharge(order_id=order.id, **values)
            self.session.add(charge)
        else:
            for key, value in values.items():
                setattr(charge, key, value)
        await self.session.flush()
        return charge

    async def list_fills(
        self,
        security_id: Optional[str] = None,
        placed_from: Optional[datetime] = None,
        placed_to: Optional[datetime] = None,
        strategy_key: Optional[str] = None,
        portfolio_id: Optional[int] = None,
    ) -> List[tuple]:
        """(fill, order) pairs, oldest first. Drives the P&L reports."""
        query = select(OrderFill, Order).join(Order, OrderFill.order_id == Order.id)
        conditions = []
        if strategy_key:
            conditions.append(Order.strategy_key == strategy_key)
        if portfolio_id is not None:
            conditions.append(Order.portfolio_id == int(portfolio_id))
        if portfolio_id is not None:
            conditions.append(Order.portfolio_id == int(portfolio_id))
        if security_id:
            conditions.append(Order.security_id == security_id)
        if placed_from:
            conditions.append(OrderFill.fill_at >= placed_from)
        if placed_to:
            conditions.append(OrderFill.fill_at <= placed_to)
        if conditions:
            query = query.where(and_(*conditions))
        result = await self.session.execute(query.order_by(OrderFill.fill_at.asc()))
        return list(result.all())

    async def list_charges_for_orders(self, order_ids: Sequence[int]) -> Dict[int, OrderCharge]:
        if not order_ids:
            return {}
        result = await self.session.execute(
            select(OrderCharge).where(OrderCharge.order_id.in_(list(order_ids)))
        )
        return {charge.order_id: charge for charge in result.scalars().all()}

    async def count_by_status(self) -> Dict[str, int]:
        result = await self.session.execute(
            select(Order.status, func.count(Order.id)).group_by(Order.status)
        )
        return {status: count for status, count in result.all()}
