"""Async engine management. SQLite by default, MySQL by changing the URL."""
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src import config_utils
from src.logging_config import get_logger

logger = get_logger("database.connection")

_engine: Optional[AsyncEngine] = None


def get_database_url() -> str:
    return config_utils.get_property_value(
        "database.url", "sqlite+aiosqlite:///./data/paper_trading.db"
    )


def redact_database_url(database_url: str) -> str:
    """A connection URL safe to log or show in the UI.

    Two shapes carry a password: the credential pair in `user:pass@host`, and a
    driver option in the query string (`?password=...`). Both are removed.
    Extracted from get_async_engine() so the system health page shows exactly
    what the log shows, rather than a second, subtly different redaction.
    """
    if not database_url:
        return database_url

    safe_url, separator, query = database_url.partition("?")

    if "@" in safe_url and "//" in safe_url:
        scheme, _, rest = safe_url.partition("//")
        creds, _, host = rest.rpartition("@")
        if creds:
            user = creds.split(":")[0]
            safe_url = f"{scheme}//{user}:***@{host}"

    if separator and query:
        parts = []
        for parameter in query.split("&"):
            name, has_value, _ = parameter.partition("=")
            if has_value and name.strip().lower() in {"password", "passwd", "pwd"}:
                parts.append(f"{name}=***")
            else:
                parts.append(parameter)
        safe_url = f"{safe_url}?{'&'.join(parts)}"

    return safe_url


def get_async_engine() -> AsyncEngine:
    global _engine
    if _engine is not None:
        return _engine

    database_url = get_database_url()
    echo = config_utils.get_property_value_boolean("database.echo", False)
    kwargs = {"echo": echo, "future": True}

    if database_url.startswith("sqlite"):
        # SQLite uses a NullPool under aiosqlite; pool sizing options do not
        # apply and passing them raises.
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 20}
    else:
        kwargs.update(
            {
                "pool_pre_ping": True,
                "pool_recycle": 3600,
                "pool_size": 10,
                "max_overflow": 20,
                "pool_timeout": 30,
            }
        )

    logger.info("Creating database engine for %s", redact_database_url(database_url))

    _engine = create_async_engine(database_url, **kwargs)
    return _engine


async def close_database_connection() -> None:
    global _engine
    if _engine is not None:
        try:
            await _engine.dispose()
            logger.info("Database engine disposed")
        finally:
            _engine = None
