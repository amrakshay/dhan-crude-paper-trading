"""Trade note endpoints."""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import SessionPrincipal, require_session
from src.core.singleton_utils import SingletonDepends
from src.database.session import get_async_session
from src.notes.api_schemas.note_schemas import (
    CreateNoteRequest,
    NoteListResponse,
    NoteResponse,
    UpdateNoteRequest,
)
from src.notes.controllers.note_controller import NoteController

note_router = APIRouter(prefix="/notes", tags=["Trade Notes"])


async def get_note_controller(
    session: AsyncSession = Depends(get_async_session),
) -> NoteController:
    return SingletonDepends(NoteController, called_inside_fastapi_depends=True)(session)


@note_router.post("", response_model=NoteResponse, status_code=201)
async def create_note(
    request: CreateNoteRequest,
    controller: NoteController = Depends(get_note_controller),
    _: SessionPrincipal = Depends(require_session),
) -> NoteResponse:
    """Attach a note to a completed trade."""
    return await controller.create(request)


@note_router.get("", response_model=NoteListResponse)
async def search_notes(
    q: Optional[str] = Query(None, description="Substring search on note text and contract"),
    order_id: Optional[int] = Query(None, alias="orderId"),
    position_id: Optional[int] = Query(None, alias="positionId"),
    security_id: Optional[str] = Query(None, alias="securityId"),
    page: int = Query(0, ge=0),
    size: int = Query(50, gt=0, le=500),
    controller: NoteController = Depends(get_note_controller),
    _: SessionPrincipal = Depends(require_session),
) -> NoteListResponse:
    """Search notes. Used by both the notes page and order history."""
    return await controller.search(
        query=q,
        order_id=order_id,
        position_id=position_id,
        security_id=security_id,
        page=page,
        size=size,
    )


@note_router.put("/{note_id}", response_model=NoteResponse)
async def update_note(
    note_id: int,
    request: UpdateNoteRequest,
    controller: NoteController = Depends(get_note_controller),
    _: SessionPrincipal = Depends(require_session),
) -> NoteResponse:
    """Edit a note. The original timestamp is kept; an edit stamp is added."""
    return await controller.update(note_id, request)


@note_router.delete("/{note_id}")
async def delete_note(
    note_id: int,
    controller: NoteController = Depends(get_note_controller),
    _: SessionPrincipal = Depends(require_session),
) -> dict:
    """Delete a note."""
    return await controller.delete(note_id)
