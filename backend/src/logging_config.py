"""Application logging. One app logger and one access logger.

Configuration lives in `conf/logging-config.ini` (or `local-logging-config.ini`
beside it), loaded with `logging.config.fileConfig`, honouring CONFIG_PATH the
same way the YAML does. Output goes to rotating files under the configured log
directory plus stdout.

`get_logger()` / `get_access_logger()` are the only public API -- every call
site in the codebase uses them and none of them need to know any of the above.
"""
import logging
import logging.config
import os
import sys
from typing import Optional

from src import log_redaction

_APP_LOGGER_NAME = "dcpt"
_ACCESS_LOGGER_NAME = "dcpt.access"

DEFAULT_LOGGING_CONFIG_FILE = "logging-config.ini"
LOCAL_LOGGING_CONFIG_FILE = "local-logging-config.ini"
DEFAULT_LOG_DIR = "./logs"

# The ini reads the log directory out of the environment, because fileConfig
# has no other way to parameterise a handler's arguments.
LOG_DIR_ENV_VAR = "DCPT_LOG_DIR"

_CONFIGURED = False


def get_config_path() -> str:
    """Directory holding the config files. Mirrors config_utils.get_config_path."""
    return os.environ.get("CONFIG_PATH", os.path.join(os.getcwd(), "conf"))


def resolve_logging_config_file() -> Optional[str]:
    """The ini to load: a local override if present, else the default, else None."""
    directory = get_config_path()
    local_file = os.path.join(directory, LOCAL_LOGGING_CONFIG_FILE)
    if os.path.exists(local_file):
        return local_file
    default_file = os.path.join(directory, DEFAULT_LOGGING_CONFIG_FILE)
    if os.path.exists(default_file):
        return default_file
    return None


def ensure_log_files_exist(log_dir: str) -> str:
    """Create the log directory and touch the files, so a missing directory
    surfaces here rather than as a handler error on the first log line."""
    resolved = os.path.abspath(log_dir)
    os.makedirs(resolved, exist_ok=True)
    for filename in ("app.log", "access.log"):
        path = os.path.join(resolved, filename)
        if not os.path.exists(path):
            with open(path, "a", encoding="utf-8"):
                pass
    return resolved


def _fallback_handler() -> logging.Handler:
    """Stdout-only handler, used when no ini is present or it fails to load."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        log_redaction.RedactingFormatter(
            fmt=(
                "%(asctime)s [%(levelname)s] "
                "%(filename)s:%(funcName)s:%(lineno)d [%(name)s] : %(message)s"
            ),
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    return handler


def _configure_fallback(level: str, access_level: str) -> None:
    app_logger = logging.getLogger(_APP_LOGGER_NAME)
    if not app_logger.handlers:
        app_logger.addHandler(_fallback_handler())
    app_logger.propagate = False

    access_logger = logging.getLogger(_ACCESS_LOGGER_NAME)
    if not access_logger.handlers:
        access_logger.addHandler(_fallback_handler())
    access_logger.propagate = False


def _apply_levels(level: Optional[str], access_level: Optional[str]) -> None:
    """Overlay explicit levels on top of whatever the ini set.

    Precedence: the explicit argument (i.e. `logging.level` from the YAML),
    then LOG_LEVEL, then the level already in force from the ini.
    """
    app_logger = logging.getLogger(_APP_LOGGER_NAME)
    access_logger = logging.getLogger(_ACCESS_LOGGER_NAME)

    resolved = level or os.environ.get("LOG_LEVEL")
    if resolved:
        app_logger.setLevel(resolved.upper())

    resolved_access = access_level or level or os.environ.get("LOG_LEVEL")
    if resolved_access:
        access_logger.setLevel(resolved_access.upper())


def configure_logging(
    level: Optional[str] = None,
    access_level: Optional[str] = None,
    log_dir: Optional[str] = None,
) -> None:
    """Set up logging once. Calling it again only re-applies explicit levels.

    `get_logger()` calls this with no arguments, so a module that is imported
    before the application bootstrap still logs. `load_config_properties()`
    then calls it again with the YAML values, which is why explicit levels are
    re-applied rather than ignored.
    """
    global _CONFIGURED

    if _CONFIGURED:
        _apply_levels(level, access_level)
        return

    log_redaction.register_environment_secrets()

    resolved_dir = log_dir or os.environ.get(LOG_DIR_ENV_VAR) or DEFAULT_LOG_DIR
    config_file = resolve_logging_config_file()

    failure: Optional[str] = None
    if config_file:
        try:
            os.environ[LOG_DIR_ENV_VAR] = ensure_log_files_exist(resolved_dir)
            logging.config.fileConfig(config_file, disable_existing_loggers=False)
        except Exception as exc:  # pragma: no cover - depends on a broken ini
            failure = f"Could not load logging config {config_file}: {exc}"

    _configure_fallback(level or "INFO", access_level or level or "INFO")
    _apply_levels(level, access_level)
    _CONFIGURED = True

    if failure:
        logging.getLogger(f"{_APP_LOGGER_NAME}.logging").error(
            "%s -- falling back to stdout logging only", failure
        )
    elif not config_file:
        logging.getLogger(f"{_APP_LOGGER_NAME}.logging").warning(
            "No %s found under %s -- logging to stdout only, with no log files",
            DEFAULT_LOGGING_CONFIG_FILE,
            get_config_path(),
        )


def reset_logging_for_tests() -> None:
    """Drop the configured flag so a test can reconfigure from scratch."""
    global _CONFIGURED
    for name in (_APP_LOGGER_NAME, _ACCESS_LOGGER_NAME):
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:  # pragma: no cover - best effort
                pass
    _CONFIGURED = False


def get_logger(name: Optional[str] = None) -> logging.Logger:
    configure_logging()
    return logging.getLogger(f"{_APP_LOGGER_NAME}.{name}" if name else _APP_LOGGER_NAME)


def get_access_logger() -> logging.Logger:
    configure_logging()
    return logging.getLogger(_ACCESS_LOGGER_NAME)
