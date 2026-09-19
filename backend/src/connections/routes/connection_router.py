"""Connections endpoints.

**ROLE_ACCOUNT_ADMIN only, on every route.** `conf/role-pages.json` also keeps
`/connections` out of a ROLE_USER's sidebar, but that is presentation --
`require_admin` here is what actually refuses the request, and
`tests/test_connections_api.py` asserts a ROLE_USER calling these directly gets
a 403.

These endpoints reach credentials, a bot token and the list of people who may
issue a command from a chat app. There is no read-only half worth opening up.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import SessionPrincipal, require_admin
from src.connections.api_schemas.connection_schemas import (
    AlertCatalogueResponse,
    AlertListResponse,
    ConnectionCard,
    ConnectionListResponse,
    ListenRequest,
    ListenResponse,
    SaveConnectionRequest,
    TestMessageResponse,
    ValidateConnectionRequest,
    ValidateConnectionResponse,
)
from src.connections.controllers.connection_controller import ConnectionController
from src.core.singleton_utils import SingletonDepends
from src.database.session import get_async_session

connection_router = APIRouter(prefix="/connections", tags=["Connections"])


async def get_connection_controller(
    session: AsyncSession = Depends(get_async_session),
) -> ConnectionController:
    return SingletonDepends(ConnectionController, called_inside_fastapi_depends=True)(
        session
    )


@connection_router.get("", response_model=ConnectionListResponse)
async def list_connections(
    controller: ConnectionController = Depends(get_connection_controller),
    _: SessionPrincipal = Depends(require_admin),
) -> ConnectionListResponse:
    """Every connection, with its LAST KNOWN status and the age of that check.

    Opening this page costs no network call. Checks happen when somebody asks
    for one, or in the background; a card that has never been checked says so
    rather than showing a green pill.
    """
    return await controller.list_connections()


@connection_router.get("/alerts", response_model=AlertListResponse)
async def list_alerts(
    limit: int = Query(50, ge=1, le=200),
    controller: ConnectionController = Depends(get_connection_controller),
    _: SessionPrincipal = Depends(require_admin),
) -> AlertListResponse:
    """The outbox: what was raised, what was sent, and what was suppressed."""
    return await controller.alerts(limit=limit)


@connection_router.get("/alerts/catalogue", response_model=AlertCatalogueResponse)
async def alert_catalogue(
    strategyKey: Optional[str] = Query(None),  # noqa: N803 - camelCase on the wire
    category: Optional[str] = Query(None),
    controller: ConnectionController = Depends(get_connection_controller),
    _: SessionPrincipal = Depends(require_admin),
) -> AlertCatalogueResponse:
    """Every alert rule this build has, with when it last fired.

    READ-ONLY. There is deliberately no edit and no delete: what gets alerted is
    a property of the build, decided in code and reviewed like code.

    `strategyKey` narrows it to the rules ABOUT that strategy, which is what a
    strategy's own page shows. Process-wide rules -- a dead task, the feed, the
    Dhan token -- are deliberately NOT repeated onto a strategy's page; the
    System Health page owns them, and duplicating them would make two pages that
    disagree the moment one changes.

    `category` narrows it the other way, for a page that is not a strategy
    page: the IPO dashboard's rules are deliberately not strategy-scoped, so
    `strategyKey` cannot reach them. The two are mutually exclusive and asking
    for both is a 400 rather than a silent empty list.

    **Admin-only, like the rest of this router.** It serves alert bodies, and a
    body can carry whatever a developer interpolated into a log line -- the same
    exposure `/api/healthcheck/problems` and the swing Health tab are gated for.
    """
    return await controller.alert_catalogue(strategyKey, category)


@connection_router.post("/telegram/test-message", response_model=TestMessageResponse)
async def send_test_message(
    controller: ConnectionController = Depends(get_connection_controller),
    _: SessionPrincipal = Depends(require_admin),
) -> TestMessageResponse:
    """Post a self-identifying test message to the configured channel.

    In one press this proves the bot token, the channel id, and that the bot
    has permission to post there. It runs only on an explicit press -- a page
    that posted every time somebody opened it would be spam, not validation.
    """
    return await controller.send_test_message()


@connection_router.post("/telegram/listen", response_model=ListenResponse)
async def listen_for_test(
    request: ListenRequest,
    controller: ConnectionController = Depends(get_connection_controller),
    _: SessionPrincipal = Depends(require_admin),
) -> ListenResponse:
    """Open a listening window and report the first update that arrives.

    This is the SETUP TOOL: it is how an operator discovers their own numeric
    Telegram user id, which `users.telegram_user_id` needs and which Telegram
    offers no friendly way to find. It works with commands switched off,
    because it is how you switch them on.

    Tell the operator to DM the bot rather than post in the channel -- a
    channel post carries no user, so it discovers the chat id and teaches them
    nothing about their own.
    """
    return await controller.listen_for_test(request.seconds)


@connection_router.get("/{provider}", response_model=ConnectionCard)
async def get_connection(
    provider: str,
    controller: ConnectionController = Depends(get_connection_controller),
    _: SessionPrincipal = Depends(require_admin),
) -> ConnectionCard:
    """One connection's detail. Secrets come back masked, never in full."""
    return await controller.get_connection(provider)


@connection_router.put("/{provider}", response_model=ConnectionCard)
async def save_connection(
    provider: str,
    request: SaveConnectionRequest,
    controller: ConnectionController = Depends(get_connection_controller),
    principal: SessionPrincipal = Depends(require_admin),
) -> ConnectionCard:
    """Save a connection. An omitted secret keeps the stored one."""
    return await controller.save(provider, request, user_id=principal.user_id)


@connection_router.post("/{provider}/validate", response_model=ValidateConnectionResponse)
async def validate_connection(
    provider: str,
    request: ValidateConnectionRequest,
    controller: ConnectionController = Depends(get_connection_controller),
    _: SessionPrincipal = Depends(require_admin),
) -> ValidateConnectionResponse:
    """Check a connection without saving it, and say WHICH part is wrong.

    Telegram has three outcomes, not two: a bad token, a good token whose bot
    is not in that channel, and a channel that does not exist have three
    different fixes. Nothing is posted -- "Send test message" is the separate,
    explicit way to prove delivery.
    """
    return await controller.validate(provider, request)
