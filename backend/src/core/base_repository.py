"""Generic async CRUD repository."""
from typing import Any, Generic, List, Optional, Type, TypeVar

from sqlalchemy import asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.base_models import BaseAPIFilter
from src.core.paginated_response import Pageable, create_pageable_response
from src.logging_config import get_logger

logger = get_logger("core.repository")

ModelType = TypeVar("ModelType")


class BaseRepository(Generic[ModelType]):
    """Common data-access operations, extended per feature."""

    def __init__(self, session: AsyncSession, model_class: Type[ModelType]):
        self.session = session
        self.model_class = model_class

    async def create(self, **kwargs: Any) -> ModelType:
        record = self.model_class(**kwargs)
        self.session.add(record)
        await self.session.flush()
        await self.session.refresh(record)
        return record

    async def get_by_id(self, record_id: int) -> Optional[ModelType]:
        result = await self.session.execute(
            select(self.model_class).where(self.model_class.id == record_id)
        )
        return result.scalar_one_or_none()

    async def update(self, record_id: int, **kwargs: Any) -> Optional[ModelType]:
        record = await self.get_by_id(record_id)
        if record is None:
            return None
        for key, value in kwargs.items():
            if hasattr(record, key):
                setattr(record, key, value)
        await self.session.flush()
        await self.session.refresh(record)
        return record

    async def delete(self, record_id: int) -> bool:
        record = await self.get_by_id(record_id)
        if record is None:
            return False
        await self.session.delete(record)
        await self.session.flush()
        return True

    async def list_records(
        self,
        filter_obj: Optional[BaseAPIFilter] = None,
        page_number: int = 0,
        size: int = 20,
        sort: Optional[List[str]] = None,
    ) -> Pageable:
        query = select(self.model_class)
        if filter_obj is not None:
            query = self._apply_filters(query, filter_obj)
        if sort:
            query = self._apply_sorting(query, sort)

        count_result = await self.session.execute(
            select(func.count()).select_from(query.order_by(None).subquery())
        )
        total_count = count_result.scalar_one()

        result = await self.session.execute(query.offset(page_number * size).limit(size))
        records = list(result.scalars().all())

        return create_pageable_response(
            content=records,
            total_elements=total_count,
            page_number=page_number,
            size=size,
            sort=sort or [],
        )

    def _apply_filters(self, query: Any, filter_obj: BaseAPIFilter) -> Any:
        if filter_obj.id is not None:
            query = query.where(self.model_class.id == filter_obj.id)
        if filter_obj.created_at_from and hasattr(self.model_class, "created_at"):
            query = query.where(self.model_class.created_at >= filter_obj.created_at_from)
        if filter_obj.created_at_to and hasattr(self.model_class, "created_at"):
            query = query.where(self.model_class.created_at <= filter_obj.created_at_to)
        if filter_obj.updated_at_from and hasattr(self.model_class, "updated_at"):
            query = query.where(self.model_class.updated_at >= filter_obj.updated_at_from)
        if filter_obj.updated_at_to and hasattr(self.model_class, "updated_at"):
            query = query.where(self.model_class.updated_at <= filter_obj.updated_at_to)
        return query

    def _apply_sorting(self, query: Any, sort: List[str]) -> Any:
        for sort_field in sort:
            descending = sort_field.startswith("-")
            field_name = sort_field[1:] if descending else sort_field
            column = getattr(self.model_class, field_name, None)
            if column is None:
                logger.debug("Ignoring unknown sort field %r", field_name)
                continue
            query = query.order_by(desc(column) if descending else asc(column))
        return query
