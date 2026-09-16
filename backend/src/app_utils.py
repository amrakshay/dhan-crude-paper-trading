"""Application bootstrap helpers."""
import os
from typing import Optional

from dotenv import load_dotenv

from src import config_utils
from src.logging_config import configure_logging, get_logger


def load_environment(dotenv_path: Optional[str] = None) -> None:
    """Load .env before the YAML config, so ${VAR} substitution can see it.

    Looked for next to the backend directory first (project root), then inside
    it. Existing environment variables always win over the file.
    """
    candidates = [dotenv_path] if dotenv_path else [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(os.getcwd()), ".env"),
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            load_dotenv(candidate, override=False)
            return


def load_config_properties(config_path: Optional[str] = None) -> dict:
    """Load .env + YAML config and configure logging. Safe to call twice."""
    load_environment()
    config = config_utils.load_config(config_path, force=True)
    configure_logging(
        level=config_utils.get_property_value("logging.level", "INFO"),
        access_level=config_utils.get_property_value("logging.access_log_level", "INFO"),
        log_dir=config_utils.get_property_value("logging.log_dir", "./logs"),
    )
    return config


def ensure_data_directories() -> None:
    """Create the directories the app writes into (SQLite file, CSV cache)."""
    logger = get_logger("app_utils")

    database_url = config_utils.get_property_value("database.url", "")
    if database_url.startswith("sqlite") and ":///" in database_url:
        db_path = database_url.split(":///", 1)[1]
        directory = os.path.dirname(os.path.abspath(db_path))
        if directory:
            os.makedirs(directory, exist_ok=True)

    cache_dir = config_utils.get_property_value(
        "dhan.instrument_master_cache_dir", "./data/instrument_master"
    )
    os.makedirs(cache_dir, exist_ok=True)
    logger.debug(
        "Data directories ready: database=%s instrument_master_cache=%s",
        database_url.split("://", 1)[0] if database_url else "(unset)",
        os.path.abspath(cache_dir),
    )
