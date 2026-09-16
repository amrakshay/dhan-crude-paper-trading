"""Instrument master data access."""
from datetime import date
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.base_repository import BaseRepository
from src.instruments.database.db_models.instrument_model import Instrument
from src.logging_config import get_logger

logger = get_logger("instruments.repository")


class InstrumentRepository(BaseRepository[Instrument]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, Instrument)

    async def get_by_security_id(self, security_id: str) -> Optional[Instrument]:
        result = await self.session.execute(
            select(Instrument).where(Instrument.security_id == security_id)
        )
        return result.scalar_one_or_none()

    async def get_many_by_security_ids(
        self, security_ids: Sequence[str]
    ) -> List[Instrument]:
        if not security_ids:
            return []
        result = await self.session.execute(
            select(Instrument).where(Instrument.security_id.in_(list(security_ids)))
        )
        return list(result.scalars().all())

    async def list_expiries(
        self, underlying_symbol: str, instrument_type: str, on_or_after: Optional[date] = None
    ) -> List[date]:
        """Distinct expiry dates, ascending."""
        query = (
            select(Instrument.expiry_date)
            .where(
                Instrument.underlying_symbol == underlying_symbol,
                Instrument.instrument_type == instrument_type,
                Instrument.expiry_date.is_not(None),
            )
            .distinct()
            .order_by(Instrument.expiry_date.asc())
        )
        if on_or_after is not None:
            query = query.where(Instrument.expiry_date >= on_or_after)
        result = await self.session.execute(query)
        return [row for row in result.scalars().all() if row is not None]

    async def list_chain(
        self, underlying_symbol: str, expiry_date: date
    ) -> List[Instrument]:
        """Every option contract for one expiry, ordered by strike then type."""
        result = await self.session.execute(
            select(Instrument)
            .where(
                Instrument.underlying_symbol == underlying_symbol,
                Instrument.expiry_date == expiry_date,
                Instrument.option_type.is_not(None),
            )
            .order_by(Instrument.strike_price.asc(), Instrument.option_type.asc())
        )
        return list(result.scalars().all())

    async def list_futures(
        self, underlying_symbol: str, futures_instrument_type: str, on_or_after: Optional[date] = None
    ) -> List[Instrument]:
        query = (
            select(Instrument)
            .where(
                Instrument.underlying_symbol == underlying_symbol,
                Instrument.instrument_type == futures_instrument_type,
            )
            .order_by(Instrument.expiry_date.asc())
        )
        if on_or_after is not None:
            query = query.where(Instrument.expiry_date >= on_or_after)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_strikes(self, underlying_symbol: str, expiry_date: date) -> List[Decimal]:
        result = await self.session.execute(
            select(Instrument.strike_price)
            .where(
                Instrument.underlying_symbol == underlying_symbol,
                Instrument.expiry_date == expiry_date,
                Instrument.option_type.is_not(None),
            )
            .distinct()
            .order_by(Instrument.strike_price.asc())
        )
        return [value for value in result.scalars().all() if value is not None]

    async def count_all(self) -> int:
        result = await self.session.execute(select(func.count(Instrument.id)))
        return result.scalar_one()

    async def last_refreshed_at(self):
        result = await self.session.execute(select(func.max(Instrument.refreshed_at)))
        return result.scalar_one_or_none()

    async def upsert_many(self, rows: Iterable[Dict[str, Any]]) -> Dict[str, int]:
        """Insert new contracts and update changed ones.

        Written as select-then-write rather than a dialect-specific upsert
        (ON CONFLICT / ON DUPLICATE KEY) so the same code runs on SQLite and
        MySQL. Row counts here are in the low thousands, so the extra round trip
        is not worth optimising away.
        """
        rows = list(rows)
        if not rows:
            return {"inserted": 0, "updated": 0, "unchanged": 0}

        incoming_ids = [row["security_id"] for row in rows]
        existing = {
            instrument.security_id: instrument
            for instrument in await self.get_many_by_security_ids(incoming_ids)
        }

        inserted = updated = unchanged = 0
        mutable_fields = (
            "exchange_id", "exchange_segment", "segment_code", "instrument_type",
            "underlying_symbol", "underlying_scrip", "trading_symbol", "display_name",
            "expiry_date", "strike_price", "option_type", "lot_size", "tick_size",
            "is_active", "refreshed_at",
        )

        for row in rows:
            current = existing.get(row["security_id"])
            if current is None:
                self.session.add(Instrument(**row))
                inserted += 1
                continue

            changed = False
            for field in mutable_fields:
                if field not in row:
                    continue
                if field == "refreshed_at":
                    continue
                if getattr(current, field) != row[field]:
                    setattr(current, field, row[field])
                    changed = True
            current.refreshed_at = row.get("refreshed_at")
            if changed:
                updated += 1
            else:
                unchanged += 1

        await self.session.flush()
        return {"inserted": inserted, "updated": updated, "unchanged": unchanged}

    async def deactivate_missing(
        self, underlying_symbol: str, present_security_ids: Sequence[str]
    ) -> int:
        """Mark contracts that have dropped out of the master as inactive.

        Expired option series vanish from the master. Their rows are kept (order
        history references them) but flagged inactive so they never get
        subscribed or offered for trading again.
        """
        present = set(present_security_ids)
        result = await self.session.execute(
            select(Instrument).where(
                and_(
                    Instrument.underlying_symbol == underlying_symbol,
                    Instrument.is_active.is_(True),
                )
            )
        )
        deactivated = 0
        for instrument in result.scalars().all():
            if instrument.security_id not in present:
                instrument.is_active = False
                deactivated += 1
        if deactivated:
            await self.session.flush()
        return deactivated
