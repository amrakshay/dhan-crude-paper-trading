import asyncio
import os
import sys

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Deliberately NOT logging.config.fileConfig(alembic.ini): the application's
# own logging config (conf/logging-config.ini) is used instead, so migration
# output is written to logs/app.log alongside everything else. Alembic's own
# `alembic` logger propagates to root, which that ini configures.
#
# This must run before importing anything that builds a logger at module scope
# (src.database.connection does), or `logging.log_dir` is ignored.
from src.app_utils import load_config_properties  # noqa: E402

load_config_properties()

from src.database.base import Base  # noqa: E402
from src.database.connection import get_database_url  # noqa: E402
from src.logging_config import get_logger  # noqa: E402
import src.database.models  # noqa: E402,F401  (registers every model)

config = context.config

logger = get_logger("database.migrations")

target_metadata = Base.metadata

# The URL always comes from the YAML config (which itself reads DATABASE_URL),
# so `alembic upgrade head` targets whatever the app targets.
config.set_main_option("sqlalchemy.url", get_database_url().replace("%", "%%"))


def run_migrations_offline() -> None:
    logger.info("Generating migration SQL offline (no database connection)")
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    logger.info(
        "Running migrations against %s (batch mode=%s)",
        connection.dialect.name,
        connection.dialect.name == "sqlite",
    )
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        # SQLite cannot ALTER most things in place; batch mode rewrites the
        # table instead, so the same migration script runs on both backends.
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()
    logger.info("Migrations complete")


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
