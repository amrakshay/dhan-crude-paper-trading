"""Serve the built frontend from FastAPI, so one port gives both UI and API.

This is additive. The two-port dev setup is unchanged and remains the
development default: Vite on :5173 proxies /api and /ws to :8000, which is what
keeps the session cookie same-origin for the WebSocket handshake. Single-port
mode is for running the thing without a Node process at all --
`npm run build` once, then `python server.py`, and http://localhost:8000 serves
everything.

Route ordering is the whole game here:

* The SPA catch-all is registered LAST, after every API and WebSocket router,
  because FastAPI matches in registration order and `/{path:path}` matches
  everything.
* It still refuses to answer for `/api/*` and `/ws/*`. Without that, a typo'd
  API path would hand the caller a 200 and an HTML page instead of a JSON 404,
  which is a genuinely confusing way to debug a frontend.
* Hashed assets get a one-year immutable cache; `index.html` gets no-store, or
  a rebuild would be masked by the stale shell the browser kept.

A missing `dist/` is not an error -- it is what a checkout looks like before
anyone has run `npm run build`. The app logs how to fix it and carries on
serving the API.
"""
import os
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.staticfiles import StaticFiles

from src import config_utils
from src.logging_config import get_logger

logger = get_logger("static.spa")

# Vite emits content-hashed filenames into dist/assets, so they are safe to
# cache forever. index.html references them and must never be cached.
ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"
INDEX_CACHE_CONTROL = "no-store, no-cache, must-revalidate"

# Prefixes the SPA must never answer for.
API_PREFIXES = ("api/", "ws/")

DEFAULT_STATIC_DIR = "../frontend/dist"


def resolve_static_dir() -> Optional[str]:
    """The configured dist directory, or None when it is not usable.

    Resolved relative to the backend directory (where server.py runs), not the
    process working directory, so `server.static_dir` can stay a stable
    relative path.
    """
    configured = config_utils.get_property_value("server.static_dir", DEFAULT_STATIC_DIR)
    if not configured:
        return None

    backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    resolved = configured if os.path.isabs(configured) else os.path.join(
        backend_root, configured
    )
    return os.path.normpath(resolved)


class ImmutableStaticFiles(StaticFiles):
    """StaticFiles that marks content-hashed assets as immutable."""

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = ASSET_CACHE_CONTROL
        return response


def mount_spa(app: FastAPI) -> bool:
    """Mount the built frontend. Returns True if it was actually mounted.

    Call this AFTER every router is registered. It adds a catch-all route, and
    anything registered after it would be unreachable.
    """
    static_dir = resolve_static_dir()
    if static_dir is None:
        logger.info(
            "server.static_dir is blank; single-port mode is off and only the "
            "API is served. Use the Vite dev server on :5173 for the UI."
        )
        return False

    index_path = os.path.join(static_dir, "index.html")
    if not os.path.isfile(index_path):
        # Not an error: this is what a fresh checkout looks like.
        logger.warning(
            "No built frontend at %s -- serving the API only. Run "
            "`cd frontend && npm run build` to enable single-port mode, or "
            "use the Vite dev server on :5173.",
            index_path,
        )
        return False

    assets_dir = os.path.join(static_dir, "assets")
    if os.path.isdir(assets_dir):
        app.mount(
            "/assets",
            ImmutableStaticFiles(directory=assets_dir),
            name="spa-assets",
        )
        logger.debug("Mounted hashed assets from %s", assets_dir)
    else:
        logger.warning(
            "Built frontend at %s has no assets/ directory; the page will load "
            "without its stylesheet or scripts. Rebuild it.",
            static_dir,
        )

    @app.get("/{spa_path:path}", include_in_schema=False)
    async def serve_spa(request: Request, spa_path: str) -> Response:
        """Static file if one exists, else index.html for client-side routing."""
        # An unmatched API or WebSocket path is a 404, not the SPA shell.
        if spa_path.startswith(API_PREFIXES):
            logger.debug("No API route for /%s; returning a JSON 404", spa_path)
            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "message": "Path not found",
                    "path": request.url.path,
                },
            )

        candidate = _safe_join(static_dir, spa_path)
        if candidate is not None and os.path.isfile(candidate):
            # favicon.ico, robots.txt and friends sit at the dist root.
            return FileResponse(candidate)

        # Everything else is a client-side route (/settings, /positions, ...).
        return FileResponse(
            index_path, headers={"Cache-Control": INDEX_CACHE_CONTROL}
        )

    logger.info(
        "Single-port mode: serving the built frontend from %s. The UI and the "
        "API share this port.",
        static_dir,
    )
    return True


def _safe_join(root: str, relative: str) -> Optional[str]:
    """Join `relative` onto `root`, refusing anything that escapes it.

    `/{path:path}` will happily deliver `../../.env`, so the resolved path is
    checked to be inside the root before it is opened.
    """
    if not relative:
        return None
    candidate = os.path.normpath(os.path.join(root, relative))
    root_prefix = os.path.join(os.path.normpath(root), "")
    if not candidate.startswith(root_prefix):
        logger.warning("Refused a traversal attempt for %r", relative)
        return None
    return candidate
