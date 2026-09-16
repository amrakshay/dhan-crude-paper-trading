"""Market data endpoints: REST status/snapshot plus the browser WebSocket."""
import asyncio
import json
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from src.auth.dependencies import require_session
from src.auth.services.auth_service import AuthError, AuthService
from src.logging_config import get_logger
from src.market.api_schemas.market_schemas import (
    FeedStatusResponse,
    ResyncResponse,
    SnapshotResponse,
)
from src.market.services.feed_manager import get_feed_manager
from src.market.services.market_book import now_ms

logger = get_logger("market.router")

market_router = APIRouter(prefix="/market", tags=["Market Data"])
market_ws_router = APIRouter()


@market_router.get("/status", response_model=FeedStatusResponse)
async def get_feed_status(_: str = Depends(require_session)) -> FeedStatusResponse:
    """Connection health, book size, last tick age and market hours."""
    return FeedStatusResponse(**get_feed_manager().status())


@market_router.get("/snapshot", response_model=SnapshotResponse)
async def get_snapshot(
    security_ids: Optional[str] = Query(
        None, alias="securityIds", description="Comma-separated; omit for everything"
    ),
    _: str = Depends(require_session),
) -> SnapshotResponse:
    """Current book. The WebSocket is the live path; this is for first paint
    and for debugging without opening a socket."""
    manager = get_feed_manager()
    if security_ids:
        wanted = [value.strip() for value in security_ids.split(",") if value.strip()]
        rows = manager.book.get_many(wanted)
    else:
        rows = manager.book.snapshot()
    return SnapshotResponse(ts=now_ms(), rows=rows, status=manager.status())


@market_router.post("/resync", response_model=ResyncResponse)
async def resync_subscriptions(user: str = Depends(require_session)) -> ResyncResponse:
    """Recompute the ATM window and reconcile upstream subscriptions.

    Called automatically after an instrument-master refresh and whenever the
    underlying drifts; exposed so it can be forced by hand.
    """
    logger.info("Manual feed resync requested by %s", user)
    return ResyncResponse(**await get_feed_manager().resync())


@market_ws_router.websocket("/ws/market")
async def market_websocket(websocket: WebSocket, token: Optional[str] = Query(None)) -> None:
    """Live book fan-out.

    Authenticated from the session cookie (same-origin) or ?token= (dev, where
    Vite serves the UI from a different port). Clients may narrow what they
    receive with:

        {"action": "configure", "securityIds": ["565899"], "includeDepth": false}

    which matters for the live-price page: it needs one instrument, not the
    whole chain.
    """
    candidate = websocket.cookies.get(AuthService.cookie_name()) or token
    if not candidate:
        logger.warning(
            "Rejected an unauthenticated WebSocket handshake from %s",
            websocket.client.host if websocket.client else "unknown",
        )
        await websocket.close(code=4401, reason="Not authenticated")
        return
    try:
        AuthService.decode_token(candidate)
    except AuthError as exc:
        logger.warning("Rejected a WebSocket handshake: %s", exc)
        await websocket.close(code=4401, reason=str(exc))
        return

    await websocket.accept()

    manager = get_feed_manager()
    broadcaster = manager.broadcaster
    client = broadcaster.register(websocket, client_id=uuid.uuid4().hex[:8])

    async def send_loop() -> None:
        while True:
            payload = await client.queue.get()
            await websocket.send_text(json.dumps(payload, default=str))

    async def receive_loop() -> None:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                logger.debug(
                    "Client %s sent a non-JSON frame (%s bytes); ignored",
                    client.client_id, len(raw),
                )
                continue

            action = message.get("action")
            logger.debug(
                "Client %s sent action=%s", client.client_id, action or "(none)"
            )
            if action == "configure":
                client.configure(
                    message.get("securityIds"),
                    bool(message.get("includeDepth", True)),
                )
            elif action == "snapshot":
                client.needs_snapshot = True
            elif action == "ping":
                await websocket.send_text(json.dumps({"type": "pong", "ts": now_ms()}))

    # Prime the client immediately rather than waiting for the next broadcast
    # interval, so a fresh tab paints with data instead of an empty table.
    await websocket.send_text(
        json.dumps(broadcaster.build_snapshot(client), default=str)
    )
    client.needs_snapshot = False

    sender = asyncio.create_task(send_loop())
    receiver = asyncio.create_task(receive_loop())
    try:
        done, pending = await asyncio.wait(
            {sender, receiver}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                raise exc
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Market WebSocket error for client %s", client.client_id)
    finally:
        sender.cancel()
        receiver.cancel()
        broadcaster.unregister(client)
