"""Market data endpoints: REST status/snapshot plus the browser WebSocket."""
import asyncio
import json
import uuid
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)

from src.auth.dependencies import (
    SessionPrincipal,
    require_session,
    resolve_principal,
)
from src.auth.services.auth_service import AuthService
from src.logging_config import get_logger
from src.market.api_schemas.market_schemas import (
    CandleResponse,
    FeedStatusResponse,
    ResyncResponse,
    SnapshotResponse,
    TimeframesResponse,
)
from src.market.controllers.market_controller import (
    MarketController,
    get_market_controller,
)
from src.market.services.candle_service import DEFAULT_TIMEFRAME
from src.database.session import session_scope
from src.market.services.feed_manager import get_feed_manager
from src.market.services.market_book import now_ms

logger = get_logger("market.router")

market_router = APIRouter(prefix="/market", tags=["Market Data"])
market_ws_router = APIRouter()


@market_router.get("/status", response_model=FeedStatusResponse)
async def get_feed_status(_: SessionPrincipal = Depends(require_session)) -> FeedStatusResponse:
    """Connection health, book size, last tick age and market hours."""
    return FeedStatusResponse(**get_feed_manager().status())


@market_router.get("/snapshot", response_model=SnapshotResponse)
async def get_snapshot(
    security_ids: Optional[str] = Query(
        None, alias="securityIds", description="Comma-separated; omit for everything"
    ),
    _: SessionPrincipal = Depends(require_session),
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
async def resync_subscriptions(user: SessionPrincipal = Depends(require_session)) -> ResyncResponse:
    """Recompute the ATM window and reconcile upstream subscriptions.

    Called automatically after an instrument-master refresh and whenever the
    underlying drifts; exposed so it can be forced by hand.
    """
    logger.info("Manual feed resync requested by %s", user)
    return ResyncResponse(**await get_feed_manager().resync())


@market_router.get("/timeframes", response_model=TimeframesResponse)
async def get_timeframes(
    controller: MarketController = Depends(get_market_controller),
    _: SessionPrincipal = Depends(require_session),
) -> TimeframesResponse:
    """The chart timeframes this build serves, and which are aggregated.

    Dhan's intraday endpoint offers 1/5/15/25/60 minutes; the rest are derived
    here. The UI reads this rather than hardcoding a list, so a timeframe can
    never appear as a button that the backend would reject.
    """
    return controller.get_timeframes()


@market_router.get("/candles", response_model=CandleResponse)
async def get_candles(
    security_id: str = Query(..., alias="securityId", description="Instrument to chart"),
    timeframe: str = Query(DEFAULT_TIMEFRAME, description="One of /market/timeframes"),
    controller: MarketController = Depends(get_market_controller),
    _: SessionPrincipal = Depends(require_session),
) -> CandleResponse:
    """Candle history, oldest bar first.

    MARKET DATA ONLY: this reads Dhan's chart endpoints, or -- when the
    synthetic feed is on -- returns locally generated bars flagged
    `synthetic: true`. The newest bar is updated in the browser from the
    existing WebSocket rather than by polling this.
    """
    return await controller.get_candles(security_id, timeframe)


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
    # Same resolver as every HTTP route, so the two auth paths cannot drift:
    # a deactivated or deleted user is refused a socket exactly as they are
    # refused a request, rather than only when their token eventually expires.
    candidate = websocket.cookies.get(AuthService.cookie_name()) or token
    # Counted as well as logged: a run of refusals is an auth problem, and
    # until the system health page existed it reached a human only as a
    # scattering of warnings in app.log. get_feed_manager() is safe here --
    # the broadcaster survives reconfigure() precisely so it is the stable
    # place to keep a process-lifetime counter.
    broadcaster = get_feed_manager().broadcaster
    try:
        async with session_scope() as session:
            principal = await resolve_principal(candidate, session)
    except HTTPException as exc:
        broadcaster.record_rejected_handshake()
        logger.warning(
            "Rejected a WebSocket handshake from %s: %s",
            websocket.client.host if websocket.client else "unknown",
            exc.detail,
        )
        await websocket.close(code=4401, reason=str(exc.detail))
        return

    if principal.must_change_password:
        broadcaster.record_rejected_handshake()
        logger.warning(
            "Rejected a WebSocket handshake for %s: password change required",
            principal.email,
        )
        await websocket.close(code=4403, reason="Password change required")
        return

    await websocket.accept()
    logger.debug("WebSocket accepted for %s", principal)

    client = broadcaster.register(
        websocket, client_id=uuid.uuid4().hex[:8], user_email=principal.email
    )

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
