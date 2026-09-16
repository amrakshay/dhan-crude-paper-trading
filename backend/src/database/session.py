"""Async session factory and the FastAPI session dependency."""
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.database.connection import get_async_engine
from src.logging_config import get_logger

logger = get_logger("database.session")


def get_session_factory() -> async_sessionmaker:
    return async_sessionmaker(
        bind=get_async_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=True,
        autocommit=False,
    )


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency. Commits on success, rolls back on failure."""
    session = get_session_factory()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """Session for background tasks, which have no request to hang off."""
    session = get_session_factory()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


class DatabaseManager:
    """Schema helpers. Production schema changes go through Alembic; these are
    for tests and first-run bootstrapping."""

    @staticmethod
    async def create_tables() -> None:
        from src.database.base import Base
        import src.database.models  # noqa: F401  (registers every model)

        engine = get_async_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables created")

    @staticmethod
    async def drop_tables() -> None:
        from src.database.base import Base
        import src.database.models  # noqa: F401

        engine = get_async_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        logger.info("Database tables dropped")
