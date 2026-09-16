"""Trade note orchestration."""
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.notes.api_schemas.note_schemas import (
    CreateNoteRequest,
    NoteListResponse,
    NoteResponse,
    UpdateNoteRequest,
)
from src.notes.database.db_operations.trade_note_repository import TradeNoteRepository
from src.notes.services.note_service import NoteService, NoteValidationError
from src.orders.database.db_operations.order_repository import OrderRepository


class NoteController:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repository = TradeNoteRepository(session)
        self.service = NoteService(self.repository, OrderRepository(session))

    async def create(self, request: CreateNoteRequest) -> NoteResponse:
        try:
            note = await self.service.create(
                note_text=request.note_text,
                order_id=request.order_id,
                position_id=request.position_id,
                security_id=request.security_id,
            )
        except NoteValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await self.session.commit()
        return NoteResponse.model_validate(note)

    async def update(self, note_id: int, request: UpdateNoteRequest) -> NoteResponse:
        try:
            note = await self.service.update(note_id, request.note_text)
        except NoteValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if note is None:
            raise HTTPException(status_code=404, detail=f"Note {note_id} not found")
        await self.session.commit()
        return NoteResponse.model_validate(note)

    async def delete(self, note_id: int) -> dict:
        deleted = await self.service.delete(note_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Note {note_id} not found")
        await self.session.commit()
        return {"message": f"Note {note_id} deleted"}

    async def search(
        self,
        query: Optional[str] = None,
        order_id: Optional[int] = None,
        position_id: Optional[int] = None,
        security_id: Optional[str] = None,
        page: int = 0,
        size: int = 50,
    ) -> NoteListResponse:
        notes, total = await self.repository.search(
            query=query,
            order_id=order_id,
            position_id=position_id,
            security_id=security_id,
            page=page,
            size=size,
        )
        return NoteListResponse(
            notes=[NoteResponse.model_validate(note) for note in notes],
            total=total,
            page=page,
            size=size,
        )
