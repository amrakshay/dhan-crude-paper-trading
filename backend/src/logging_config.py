"""Application logging. One app logger and one access logger."""
import logging
import os
import sys
from typing import Optional

_APP_LOGGER_NAME = "dcpt"
_ACCESS_LOGGER_NAME = "dcpt.access"
_CONFIGURED = False


def _build_handler() -> logging.Handler:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-5.5s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    return handler


def configure_logging(level: Optional[str] = None, access_level: Optional[str] = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    resolved = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()
    app_logger = logging.getLogger(_APP_LOGGER_NAME)
    app_logger.setLevel(resolved)
    if not app_logger.handlers:
        app_logger.addHandler(_build_handler())
    app_logger.propagate = False

    access_logger = logging.getLogger(_ACCESS_LOGGER_NAME)
    access_logger.setLevel((access_level or resolved).upper())
    if not access_logger.handlers:
        access_logger.addHandler(_build_handler())
    access_logger.propagate = False

    _CONFIGURED = True


def get_logger(name: Optional[str] = None) -> logging.Logger:
    configure_logging()
    return logging.getLogger(f"{_APP_LOGGER_NAME}.{name}" if name else _APP_LOGGER_NAME)


def get_access_logger() -> logging.Logger:
    configure_logging()
    return logging.getLogger(_ACCESS_LOGGER_NAME)
