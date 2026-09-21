"""Entry point. `python server.py` reads host/port/workers from the YAML config."""
import uvicorn

from src import config_utils
from src.app_utils import ensure_data_directories, load_config_properties
from src.logging_config import get_logger


def main() -> None:
    load_config_properties()
    ensure_data_directories()
    logger = get_logger("server")

    host = config_utils.get_property_value("server.host", "0.0.0.0")
    port = config_utils.get_property_value_int("server.port", 24601)
    workers = config_utils.get_property_value_int("server.uvicorn_workers", 1)

    if workers != 1:
        # The upstream Dhan feed is one connection per process, fanned out to
        # browsers. More than one worker means more than one upstream
        # connection, which burns the 5-connection cap and desynchronises the
        # in-memory book between workers.
        logger.warning(
            "server.uvicorn_workers=%s but the market feed requires a single "
            "process. Forcing workers=1.",
            workers,
        )
        workers = 1

    uvicorn.run(app="main:app", host=host, port=port, workers=workers)


if __name__ == "__main__":
    main()
