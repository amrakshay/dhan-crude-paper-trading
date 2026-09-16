"""FastAPI application for the MCX crude-oil options paper-trading tool.

PAPER RUPEES ONLY. This process never contacts a broker order endpoint. The
Dhan credentials it uses are for market data only: the WebSocket feed, the
option chain endpoint and the instrument master CSV. See
tests/test_no_real_orders.py, which fails the build if an order-placement path
ever appears in this codebase.
"""
import json
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src import config_utils
from src.app_utils import ensure_data_directories, load_config_properties
from src.core.singleton_utils import clear_singletons
from src.database.connection import close_database_connection
from src.logging_config import get_access_logger, get_logger

load_config_properties()
ensure_data_directories()

logger = get_logger("main")
access_logger = get_access_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Crude paper-trading backend starting up")

    from src.auth.services.auth_service import AuthService

    if not AuthService.is_configured():
        logger.error(
            "APP_PASSWORD is not set -- every login will be rejected. "
            "Copy .env.example to .env and set APP_USERNAME / APP_PASSWORD."
        )

    # One upstream Dhan connection per process, started here and fanned out to
    # every browser tab. See src/market/services/feed_manager.py.
    from src.market.services.feed_manager import get_feed_manager, shutdown_feed_manager
    from src.orders.services.order_matcher import (
        get_order_matcher,
        shutdown_order_matcher,
    )

    try:
        await get_feed_manager().start()
    except Exception:
        logger.exception("Market feed failed to start; the API is still available")

    try:
        await get_order_matcher().start()
    except Exception:
        logger.exception("Order matcher failed to start; resting limits will not fill")

    yield

    logger.info("Crude paper-trading backend shutting down")
    try:
        await shutdown_order_matcher()
        await shutdown_feed_manager()
        clear_singletons()
        await close_database_connection()
    except Exception as exc:  # pragma: no cover - shutdown best effort
        logger.error("Error during shutdown: %s", exc)


app = FastAPI(
    title="MCX Crude Oil Options Paper Trading",
    description=(
        "Single-user paper-trading platform for MCX CRUDEOIL options. "
        "Simulated fills against live market data. No real orders are ever placed."
    ),
    version="1.0.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config_utils.get_property_value_list(
        "server.cors_origins", ["http://localhost:5173"]
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class LogRequestsMiddleware(BaseHTTPMiddleware):
    """Access log, skipping health probes and the docs."""

    IGNORED_PATHS = {"/", "/api/healthcheck/status", "/api/docs", "/api/openapi.json"}

    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        if request.url.path not in self.IGNORED_PATHS:
            elapsed_ms = (time.perf_counter() - started) * 1000
            access_logger.info(
                '"%s %s" %s %.3f ms',
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
            )
        return response


app.add_middleware(LogRequestsMiddleware)


@app.exception_handler(404)
async def not_found_handler(request: Request, exc) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"success": False, "message": "Path not found", "path": request.url.path},
    )


@app.get("/", include_in_schema=False)
async def root() -> JSONResponse:
    return JSONResponse({"service": "crude-paper-trading", "status": "running"})


@app.get("/api/healthcheck/status", tags=["Health"])
async def health_check() -> JSONResponse:
    return JSONResponse({"status": "ok"})


# --- routes ---------------------------------------------------------------
from src.auth import auth_main_router  # noqa: E402
from src.instruments import instruments_main_router  # noqa: E402
from src.charges import charges_main_router  # noqa: E402
from src.orders import orders_main_router  # noqa: E402
from src.notes import notes_main_router  # noqa: E402
from src.positions import positions_main_router  # noqa: E402
from src.reports import reports_main_router  # noqa: E402
from src.settings import settings_main_router  # noqa: E402
from src.market import market_main_router, market_ws_router  # noqa: E402

app.include_router(auth_main_router, prefix="/api")
app.include_router(instruments_main_router, prefix="/api")
app.include_router(market_main_router, prefix="/api")
app.include_router(charges_main_router, prefix="/api")
app.include_router(orders_main_router, prefix="/api")
app.include_router(positions_main_router, prefix="/api")
app.include_router(reports_main_router, prefix="/api")
app.include_router(notes_main_router, prefix="/api")
app.include_router(settings_main_router, prefix="/api")
app.include_router(market_ws_router)   # /ws/market -- not under /api
