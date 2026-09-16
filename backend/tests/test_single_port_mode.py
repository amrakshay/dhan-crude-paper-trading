"""Single-port mode: FastAPI serving the built frontend alongside the API.

The two-port dev setup must keep working unchanged, so these tests build their
own app rather than leaning on whether `frontend/dist` happens to exist in the
checkout. What they are really guarding is route ordering: a catch-all that
swallows `/api/*` turns every frontend bug into a mystery, because a typo'd
endpoint returns 200 and an HTML page instead of a JSON 404.
"""
import os

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from src import config_utils
from src.static_serving import (
    ASSET_CACHE_CONTROL,
    INDEX_CACHE_CONTROL,
    mount_spa,
    resolve_static_dir,
)

INDEX_HTML = "<!doctype html><html><head><title>DCPT</title></head><body><div id=root></div></body></html>"
ASSET_JS = "console.log('hashed bundle');"


@pytest.fixture
def built_dist(tmp_path):
    """A directory shaped like a real `npm run build` output."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (dist / "assets" / "index-DX91zFbL.js").write_text(ASSET_JS, encoding="utf-8")
    (dist / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    return dist


@pytest.fixture
def spa_app(built_dist, monkeypatch):
    """An app with API routes and the SPA mounted after them, as main.py does."""
    app = _build_app_with_api()
    _point_static_dir_at(monkeypatch, str(built_dist))
    assert mount_spa(app) is True
    return app


def _build_app_with_api() -> FastAPI:
    app = FastAPI()

    @app.get("/api/healthcheck/status")
    async def health():
        return JSONResponse({"status": "ok"})

    @app.get("/api/orders")
    async def orders():
        return JSONResponse({"orders": []})

    return app


def _point_static_dir_at(monkeypatch, path):
    original = config_utils.get_property_value

    def patched(key, default=None):
        if key == "server.static_dir":
            return path
        return original(key, default)

    monkeypatch.setattr(config_utils, "get_property_value", patched)


async def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


# --- the API keeps working -------------------------------------------------
async def test_api_routes_still_answer_once_the_spa_is_mounted(spa_app):
    async with await _client(spa_app) as client:
        assert (await client.get("/api/healthcheck/status")).json() == {"status": "ok"}
        assert (await client.get("/api/orders")).json() == {"orders": []}


async def test_an_unknown_api_path_gets_a_json_404_not_the_spa_shell(spa_app):
    """The single most important assertion in this file."""
    async with await _client(spa_app) as client:
        response = await client.get("/api/definitely-not-a-route")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["success"] is False
    assert "<html" not in response.text


async def test_an_unknown_websocket_path_gets_a_json_404_not_the_spa_shell(spa_app):
    async with await _client(spa_app) as client:
        response = await client.get("/ws/not-a-socket")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


# --- the SPA is served -----------------------------------------------------
async def test_the_root_path_serves_the_spa_shell(spa_app):
    async with await _client(spa_app) as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert "<div id=root>" in response.text


@pytest.mark.parametrize(
    "deep_link", ["/settings", "/positions", "/orders", "/live", "/chain/anything"]
)
async def test_a_deep_link_returns_the_spa_shell_for_client_side_routing(
    spa_app, deep_link
):
    async with await _client(spa_app) as client:
        response = await client.get(deep_link)

    assert response.status_code == 200
    assert "<div id=root>" in response.text


async def test_a_root_level_file_is_served_rather_than_the_shell(spa_app):
    async with await _client(spa_app) as client:
        response = await client.get("/favicon.ico")

    assert response.status_code == 200
    assert response.content == b"\x00\x00\x01\x00"


async def test_a_hashed_asset_is_served_from_the_assets_mount(spa_app):
    async with await _client(spa_app) as client:
        response = await client.get("/assets/index-DX91zFbL.js")

    assert response.status_code == 200
    assert "hashed bundle" in response.text


# --- caching ---------------------------------------------------------------
async def test_hashed_assets_are_cached_for_a_year(spa_app):
    async with await _client(spa_app) as client:
        response = await client.get("/assets/index-DX91zFbL.js")

    assert response.headers["cache-control"] == ASSET_CACHE_CONTROL


async def test_the_shell_is_never_cached_so_a_rebuild_is_not_masked(spa_app):
    """A cached index.html would keep pointing at the previous bundle hash."""
    async with await _client(spa_app) as client:
        for path in ("/", "/settings"):
            response = await client.get(path)
            assert response.headers["cache-control"] == INDEX_CACHE_CONTROL


# --- traversal -------------------------------------------------------------
@pytest.mark.parametrize(
    "attempt",
    [
        "/../.env",
        "/../../.env",
        "/..%2f..%2f.env",
        "/assets/../../.env",
    ],
)
async def test_the_catch_all_never_serves_a_file_outside_the_dist(spa_app, attempt):
    async with await _client(spa_app) as client:
        response = await client.get(attempt)

    # Either refused outright, or handed the SPA shell -- never file contents.
    assert response.status_code in (200, 307, 404)
    if response.status_code == 200:
        assert "APP_PASSWORD" not in response.text
        assert "DHAN_ACCESS_TOKEN" not in response.text


# HTTP clients normalise `..` out of a URL before it is sent, so the guard is
# also exercised directly -- that is the layer that actually has to hold.
@pytest.mark.parametrize(
    "relative",
    ["../.env", "../../etc/passwd", "a/../../../.env", "../backend/conf/charges.yaml"],
)
def test_safe_join_refuses_a_path_that_escapes_the_dist(relative):
    from src.static_serving import _safe_join

    assert _safe_join("/tmp/dist", relative) is None


@pytest.mark.parametrize(
    "relative,expected",
    [
        ("index.html", "/tmp/dist/index.html"),
        ("assets/index-abc.js", "/tmp/dist/assets/index-abc.js"),
        ("nested/deep/file.txt", "/tmp/dist/nested/deep/file.txt"),
    ],
)
def test_safe_join_allows_a_path_inside_the_dist(relative, expected):
    from src.static_serving import _safe_join

    assert _safe_join("/tmp/dist", relative) == expected


# --- graceful degradation --------------------------------------------------
async def test_a_missing_build_leaves_the_api_working_instead_of_500ing(
    tmp_path, monkeypatch
):
    app = _build_app_with_api()
    _point_static_dir_at(monkeypatch, str(tmp_path / "never-built"))

    assert mount_spa(app) is False

    async with await _client(app) as client:
        assert (await client.get("/api/healthcheck/status")).status_code == 200


async def test_a_blank_static_dir_turns_single_port_mode_off(monkeypatch):
    app = _build_app_with_api()
    _point_static_dir_at(monkeypatch, "")

    assert mount_spa(app) is False


async def test_a_dist_without_an_index_is_reported_rather_than_served(
    tmp_path, monkeypatch
):
    empty = tmp_path / "dist"
    (empty / "assets").mkdir(parents=True)
    app = _build_app_with_api()
    _point_static_dir_at(monkeypatch, str(empty))

    assert mount_spa(app) is False


# --- path resolution -------------------------------------------------------
def test_a_relative_static_dir_resolves_against_the_backend_directory(monkeypatch):
    _point_static_dir_at(monkeypatch, "../frontend/dist")
    backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    resolved = resolve_static_dir()

    assert resolved == os.path.normpath(
        os.path.join(backend_root, "../frontend/dist")
    )
    assert os.path.isabs(resolved)


def test_an_absolute_static_dir_is_used_as_given(monkeypatch, tmp_path):
    _point_static_dir_at(monkeypatch, str(tmp_path))

    assert resolve_static_dir() == str(tmp_path)
