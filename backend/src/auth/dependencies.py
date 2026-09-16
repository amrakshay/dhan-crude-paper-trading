"""FastAPI dependency that gates every protected endpoint."""
from fastapi import Cookie, Depends, HTTPException, Query, WebSocket, status

from src.auth.services.auth_service import AuthError, AuthService


async def require_session(
    session_cookie: str = Cookie(default=None, alias="dcpt_session"),
) -> str:
    """Returns the authenticated username, or raises 401."""
    if not session_cookie:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    try:
        payload = AuthService.decode_token(session_cookie)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc
    return payload.get("sub", "")


async def require_websocket_session(
    websocket: WebSocket,
    token: str = Query(default=None),
) -> str:
    """WebSocket variant.

    Browsers do send cookies on same-origin WebSocket handshakes, so the cookie
    is tried first; `?token=` is the fallback for cross-origin dev (Vite on
    :5173 talking to the API on :8000).
    """
    candidate = websocket.cookies.get(AuthService.cookie_name()) or token
    if not candidate:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    try:
        payload = AuthService.decode_token(candidate)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc
    return payload.get("sub", "")


AuthenticatedUser = Depends(require_session)
