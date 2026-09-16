"""Trade note payloads."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from src.core.time_utils import as_utc_aware


class CreateNoteRequest(BaseModel):
    note_text: str = Field(..., min_length=1, max_length=20000, alias="noteText")
    order_id: Optional[int] = Field(None, alias="orderId")
    position_id: Optional[int] = Field(None, alias="positionId")
    security_id: Optional[str] = Field(None, alias="securityId")

    model_config = ConfigDict(populate_by_name=True)


class UpdateNoteRequest(BaseModel):
    note_text: str = Field(..., min_length=1, max_length=20000, alias="noteText")

    model_config = ConfigDict(populate_by_name=True)


class NoteResponse(BaseModel):
    id: int
    order_id: Optional[int] = Field(None, alias="orderId")
    position_id: Optional[int] = Field(None, alias="positionId")
    security_id: Optional[str] = Field(None, alias="securityId")
    trading_symbol: Optional[str] = Field(None, alias="tradingSymbol")
    note_text: str = Field(alias="noteText")
    noted_at: datetime = Field(alias="notedAt")
    edited_at: Optional[datetime] = Field(None, alias="editedAt")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_serializer("noted_at", "edited_at")
    def _ts(self, value: Optional[datetime]) -> Optional[str]:
        return as_utc_aware(value).isoformat() if value else None


class NoteListResponse(BaseModel):
    notes: List[NoteResponse]
    total: int
    page: int
    size: int
