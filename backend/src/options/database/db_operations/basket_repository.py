"""Queries for option baskets. Baskets and legs are written once."""
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.options.database.db_models.basket_model import OptionBasket, OptionBasketLeg


class BasketRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_basket(self, **fields) -> OptionBasket:
        basket = OptionBasket(**fields)
        self.session.add(basket)
        await self.session.flush()
        return basket

    async def add_leg(self, **fields) -> OptionBasketLeg:
        leg = OptionBasketLeg(**fields)
        self.session.add(leg)
        await self.session.flush()
        return leg

    async def get(self, basket_id: int) -> Optional[OptionBasket]:
        result = await self.session.execute(
            select(OptionBasket).where(OptionBasket.id == int(basket_id))
        )
        return result.scalars().first()

    async def legs(self, basket_id: int) -> List[OptionBasketLeg]:
        result = await self.session.execute(
            select(OptionBasketLeg)
            .where(OptionBasketLeg.basket_id == int(basket_id))
            .order_by(OptionBasketLeg.sequence.asc())
        )
        return list(result.scalars().all())

    async def recent(self, portfolio_id: int, limit: int = 20) -> List[OptionBasket]:
        result = await self.session.execute(
            select(OptionBasket)
            .where(OptionBasket.portfolio_id == int(portfolio_id))
            .order_by(OptionBasket.id.desc())
            .limit(int(limit))
        )
        return list(result.scalars().all())
