"""Trade notes.

A first-class entity rather than a column on orders: a note is written after a
trade completes, edited later, and surfaced from both order history and the P&L
view. It therefore needs its own identity, its own timestamps and its own
search.
"""
from typing import Optional

from src.core.time_utils import utc_now
from src.logging_config import get_logger
from src.notes.database.db_models.trade_note_model import TradeNote
from src.notes.database.db_operations.trade_note_repository import TradeNoteRepository
from src.orders.database.db_operations.order_repository import OrderRepository

logger = get_logger("notes.service")


class NoteValidationError(Exception):
    pass


class NoteService:
    def __init__(self, repository: TradeNoteRepository, order_repository: OrderRepository):
        self.repository = repository
        self.orders = order_repository

    async def create(
        self,
        note_text: str,
        order_id: Optional[int] = None,
        position_id: Optional[int] = None,
        security_id: Optional[str] = None,
    ) -> TradeNote:
        if not note_text or not note_text.strip():
            raise NoteValidationError("A note cannot be empty")
        if order_id is None and position_id is None and not security_id:
            raise NoteValidationError(
                "A note must be attached to an order, a position or a contract"
            )

        trading_symbol = None
        if order_id is not None:
            order = await self.orders.get_by_id(order_id)
            if order is None:
                raise NoteValidationError(f"Order {order_id} not found")
            # Denormalised so a note stays searchable by contract even after an
            # expired security id is retired from the instrument master.
            trading_symbol = order.trading_symbol
            security_id = security_id or order.security_id

        note = TradeNote(
            order_id=order_id,
            position_id=position_id,
            security_id=security_id,
            trading_symbol=trading_symbol,
            note_text=note_text.strip(),
            noted_at=utc_now(),
        )
        self.repository.session.add(note)
        await self.repository.session.flush()
        logger.info(
            "Note %s created against order=%s position=%s security=%s (%s chars)",
            note.id, order_id, position_id, security_id, len(note.note_text),
        )
        return note

    async def update(self, note_id: int, note_text: str) -> Optional[TradeNote]:
        if not note_text or not note_text.strip():
            raise NoteValidationError("A note cannot be empty")
        note = await self.repository.get_by_id(note_id)
        if note is None:
            return None
        note.note_text = note_text.strip()
        note.edited_at = utc_now()
        await self.repository.session.flush()
        logger.info("Note %s edited (%s chars)", note_id, len(note.note_text))
        return note

    async def delete(self, note_id: int) -> bool:
        deleted = await self.repository.delete(note_id)
        if deleted:
            logger.info("Note %s deleted", note_id)
        else:
            logger.debug("Delete requested for note %s, which does not exist", note_id)
        return deleted
