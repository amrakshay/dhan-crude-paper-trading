"""FastAPI application for the MCX crude-oil options paper-trading tool.

PAPER RUPEES ONLY. This process never contacts a broker order endpoint. The
Dhan credentials it uses are for market data only: the WebSocket feed, the
option chain endpoint and the instrument master CSV. See
tests/test_no_real_orders.py, which fails the build if an order-placement path
ever appears in this codebase.
"""
import json
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Bootstrap FIRST, before importing anything that builds a logger at module
# scope (src.database.connection does). configure_logging() is one-shot: if a
# logger is created before the config is loaded, logging is set up with its
# defaults and `logging.log_dir` is silently ignored.
from src.app_utils import ensure_data_directories, load_config_properties  # noqa: E402

load_config_properties()
ensure_data_directories()

from src import config_utils  # noqa: E402
from src.core.singleton_utils import clear_singletons  # noqa: E402
from src.database.connection import close_database_connection  # noqa: E402
from src.logging_config import get_access_logger, get_logger  # noqa: E402

logger = get_logger("main")
access_logger = get_access_logger()


async def _seed_default_administrator() -> None:
    """Make sure the default administrator exists before anyone tries to log in.

    Idempotent, and run here as well as in the migration so a database built by
    `create_tables()` (which the tests use) reaches the same state as one built
    by `alembic upgrade head`. An existing seed user is never overwritten --
    its password is not reset from the environment on every boot, or changing
    it in the UI would be undone by the next restart.
    """
    from src.database.session import session_scope
    from src.users.database.db_operations.user_repository import UserRepository
    from src.users.services.user_service import UserService

    try:
        async with session_scope() as session:
            service = UserService(UserRepository(session))
            await service.ensure_seed_user(
                config_utils.get_property_value("auth.admin_password", "")
            )
            await session.commit()
    except Exception:
        logger.exception(
            "Could not seed the default administrator. If the users table is "
            "empty, nobody will be able to log in."
        )


def _load_strategy_modules() -> None:
    from src.strategies.services.strategy_registry import get_strategy_registry

    registry = get_strategy_registry()
    logger.info(
        "Strategy modules: %s",
        {
            definition.key: (
                "enabled" if registry.is_enabled(definition.key) else "disabled"
            )
            for definition in registry.all()
        },
    )


async def _apply_stored_settings() -> None:
    """Overlay settings saved in the UI onto the in-memory config.

    **This must run before the feed starts.** `SettingsService.apply_to_config()`
    mutates the config dict every `config_utils` caller reads, which is what
    makes the database beat `.env`. It was previously called only from
    `SettingsService.save()`, so a process that restarted after a save ran on
    its `.env` values: a Dhan token configured on the Settings page was
    silently not the one the feed used, while the Settings page went on showing
    the stored one. The system health page is what caught it.

    Not fatal. If the settings cannot be read the application still starts --
    on `.env`, saying so -- because refusing to boot over a settings row would
    be a worse failure than running with the fallback.

    Reading the settings also registers the decrypted token with
    `log_redaction` (see `SettingsService.load_stored`), so a token that only
    ever existed in the database is scrubbed from the log from the first line.
    """
    from src.database.session import session_scope
    from src.settings.database.db_operations.app_setting_repository import (
        AppSettingRepository,
    )
    from src.settings.services.settings_service import SettingsService

    try:
        async with session_scope() as session:
            applied = await SettingsService(AppSettingRepository(session)).apply_to_config()
        if not applied:
            logger.info(
                "No settings are stored in the database; the .env values stand"
            )
    except Exception:
        logger.exception(
            "Could not apply stored settings. This process will run on its .env "
            "values, which may not be what the Settings page shows. Re-save on "
            "the Settings page once the cause is fixed."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Nothing recorded when this process started, so the health page had no
    # uptime to report. This is that one line.
    from src.health.services.process_stats import mark_started

    mark_started()

    logger.info(
        "Crude paper-trading backend starting up (config=%s, logs=%s)",
        config_utils.get_config_path(),
        os.path.abspath(
            config_utils.get_property_value("logging.log_dir", "./logs")
        ),
    )

    await _seed_default_administrator()

    # BEFORE anything reads a strategy: the registry parses conf/strategies/*.yaml
    # and a malformed one is fatal here rather than at the first request. What
    # each strategy IS comes from those files; whether it is RUNNING comes from
    # the database, applied next.
    _load_strategy_modules()

    # BEFORE the feed starts: settings saved in the UI beat .env, and the feed
    # reads its credentials and its synthetic flag out of the config this
    # overlays. Starting the feed first would connect with the .env values and
    # only pick up the stored ones at the next save.
    await _apply_stored_settings()

    # One upstream Dhan connection per process, started here and fanned out to
    # every browser tab. See src/market/services/feed_manager.py.
    from src.market.services.feed_manager import get_feed_manager, shutdown_feed_manager
    from src.orders.services.order_matcher import (
        get_order_matcher,
        shutdown_order_matcher,
    )
    # Chart stop-loss / take-profit levels are watched server-side: a stop that
    # lives in the browser dies with the tab.
    from src.chart_trading.services.bracket_monitor import (
        get_bracket_monitor,
        shutdown_bracket_monitor,
    )

    try:
        await get_feed_manager().start()
    except Exception:
        logger.exception("Market feed failed to start; the API is still available")

    try:
        await get_order_matcher().start()
    except Exception:
        logger.exception("Order matcher failed to start; resting limits will not fill")

    try:
        await get_bracket_monitor().start()
    except Exception:
        logger.exception(
            "Bracket monitor failed to start; chart stop-loss and take-profit "
            "levels will NOT be watched"
        )

    logger.info("Startup complete; the API is accepting requests")

    yield

    logger.info("Crude paper-trading backend shutting down")
    try:
        await shutdown_bracket_monitor()
        await shutdown_order_matcher()
        await shutdown_feed_manager()
        clear_singletons()
        await close_database_connection()
        logger.info("Shutdown complete")
    except Exception:  # pragma: no cover - shutdown best effort
        logger.exception("Error during shutdown")


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
    """Access log, skipping health probes and the docs.

    Every request gets a short id, echoed back as `X-Request-Id` and stamped on
    the access line, so a browser complaint can be tied to a specific server
    line. An inbound `X-Request-Id` is honoured if the caller supplies one.
    """

    # The system health page polls, and it reports on this very log -- an
    # entry per poll would be the monitor distorting what it monitors.
    IGNORED_PATHS = {
        "/",
        "/api/healthcheck/status",
        "/api/healthcheck/system",
        "/api/healthcheck/problems",
        "/api/docs",
        "/api/openapi.json",
    }

    # A request slower than this is worth knowing about: nothing here should
    # take seconds.
    SLOW_REQUEST_MS = 1000.0

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:12]
        request.state.request_id = request_id

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.exception(
                "[%s] Unhandled error serving %s %s after %.1f ms",
                request_id, request.method, request.url.path, elapsed_ms,
            )
            raise
        response.headers["X-Request-Id"] = request_id

        if request.url.path not in self.IGNORED_PATHS:
            elapsed_ms = (time.perf_counter() - started) * 1000
            access_logger.info(
                '[%s] "%s %s" %s %.3f ms',
                request_id,
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
            )
            if elapsed_ms > self.SLOW_REQUEST_MS:
                logger.warning(
                    "[%s] Slow request: %s %s took %.0f ms (status %s)",
                    request_id, request.method, request.url.path,
                    elapsed_ms, response.status_code,
                )
        return response


app.add_middleware(LogRequestsMiddleware)


@app.exception_handler(404)
async def not_found_handler(request: Request, exc) -> JSONResponse:
    """JSON for every 404, preserving whatever the endpoint actually said.

    This used to flatten every 404 to "Path not found", which meant a real
    answer ("No near-month future -- refresh the instrument master") reached
    the UI as a routing error. The SPA catch-all in static_serving.py is what
    answers for a genuinely unknown path now, so this handler no longer has to
    speak for both cases.
    """
    detail = getattr(exc, "detail", None)
    message = str(detail) if detail else "Path not found"
    logger.debug("404 for %s %s: %s", request.method, request.url.path, message)
    return JSONResponse(
        status_code=404,
        content={
            "success": False,
            "message": message,
            "detail": message,
            "path": request.url.path,
        },
    )


# NOTE: no `@app.get("/")` here. In single-port mode `/` must return the SPA
# shell, and a route registered at import time would win over the catch-all
# that mount_spa() adds at the bottom of this file. When the frontend is not
# built, that same catch-all serves the JSON identity response below instead.
async def service_identity() -> JSONResponse:
    return JSONResponse({"service": "crude-paper-trading", "status": "running"})


@app.get("/api/healthcheck/status", tags=["Health"])
async def health_check() -> JSONResponse:
    return JSONResponse({"status": "ok"})


# --- routes ---------------------------------------------------------------
from src.auth import auth_main_router  # noqa: E402
from src.instruments import instruments_main_router  # noqa: E402
from src.charges import charges_main_router  # noqa: E402
from src.health import health_main_router  # noqa: E402
from src.chart_trading import chart_trading_main_router  # noqa: E402
from src.orders import orders_main_router  # noqa: E402
from src.notes import notes_main_router  # noqa: E402
from src.positions import positions_main_router  # noqa: E402
from src.reports import reports_main_router  # noqa: E402
from src.settings import settings_main_router  # noqa: E402
from src.users import users_main_router  # noqa: E402
from src.market import market_main_router, market_ws_router  # noqa: E402

app.include_router(auth_main_router, prefix="/api")
app.include_router(instruments_main_router, prefix="/api")
app.include_router(market_main_router, prefix="/api")
app.include_router(charges_main_router, prefix="/api")
app.include_router(health_main_router, prefix="/api")
app.include_router(orders_main_router, prefix="/api")
app.include_router(chart_trading_main_router, prefix="/api")
app.include_router(positions_main_router, prefix="/api")
app.include_router(reports_main_router, prefix="/api")
app.include_router(notes_main_router, prefix="/api")
app.include_router(settings_main_router, prefix="/api")
app.include_router(users_main_router, prefix="/api")
app.include_router(market_ws_router)   # /ws/market -- not under /api


# --- static frontend (single-port mode) -----------------------------------
# MUST come after every router above: mount_spa() registers a `/{path:path}`
# catch-all, and anything added after it would never be reached.
from src.static_serving import mount_spa  # noqa: E402

SPA_MOUNTED = mount_spa(app)

if not SPA_MOUNTED:
    # Two-port dev, or a checkout with no build. Keep the original behaviour:
    # `/` identifies the service, and unknown paths get the JSON 404.
    app.get("/", include_in_schema=False)(service_identity)
