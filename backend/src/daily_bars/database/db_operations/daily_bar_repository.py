"""Daily bar data access.

Written for two access patterns and no others:

  * **write a symbol's bars**, idempotently, from an importer or the nightly
    refresh -- upsert on `(exchange_segment, symbol, bar_date)`;
  * **read one symbol's history in date order**, which is what every indicator
    does.
"""
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.base_repository import BaseRepository
from src.daily_bars.database.db_models.daily_bar_model import DailyBar
from src.logging_config import get_logger

logger = get_logger("daily_bars.repository")


class DailyBarRepository(BaseRepository[DailyBar]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, DailyBar)

    async def history(
        self,
        symbol: str,
        exchange_segment: str,
        limit: Optional[int] = None,
        on_or_before: Optional[date] = None,
    ) -> List[DailyBar]:
        """One symbol's bars, OLDEST FIRST.

        `limit` takes the most RECENT n and still returns them oldest first,
        because an indicator walks forward through time. Getting that backwards
        would compute a 200-day SMA of the oldest 200 sessions on record.
        """
        query = select(DailyBar).where(
            and_(
                DailyBar.symbol == symbol,
                DailyBar.exchange_segment == exchange_segment,
            )
        )
        if on_or_before is not None:
            query = query.where(DailyBar.bar_date <= on_or_before)

        if limit is None:
            result = await self.session.execute(query.order_by(DailyBar.bar_date.asc()))
            return list(result.scalars().all())

        result = await self.session.execute(
            query.order_by(DailyBar.bar_date.desc()).limit(int(limit))
        )
        return list(reversed(result.scalars().all()))

    async def latest_date(
        self, symbol: str, exchange_segment: str
    ) -> Optional[date]:
        result = await self.session.execute(
            select(func.max(DailyBar.bar_date)).where(
                and_(
                    DailyBar.symbol == symbol,
                    DailyBar.exchange_segment == exchange_segment,
                )
            )
        )
        return result.scalar_one_or_none()

    async def latest_dates(
        self, exchange_segment: str, symbols: Sequence[str]
    ) -> Dict[str, date]:
        """The newest stored bar per symbol, in ONE query.

        The nightly refresh asks this for 500 symbols before deciding what to
        fetch; a query per symbol would be 500 round trips to save nothing.
        """
        if not symbols:
            return {}
        result = await self.session.execute(
            select(DailyBar.symbol, func.max(DailyBar.bar_date))
            .where(
                and_(
                    DailyBar.exchange_segment == exchange_segment,
                    DailyBar.symbol.in_(list(symbols)),
                )
            )
            .group_by(DailyBar.symbol)
        )
        return {symbol: latest for symbol, latest in result.all() if latest}

    async def trading_dates(
        self, symbol: str, exchange_segment: str, limit: Optional[int] = None
    ) -> List[date]:
        """The session calendar, as observed for one symbol.

        The regime index's own dates are this application's trading calendar:
        a date NSE published a bar for is a date NSE traded. That is why there
        is no holiday list to maintain and no second source to go stale.
        """
        query = select(DailyBar.bar_date).where(
            and_(
                DailyBar.symbol == symbol,
                DailyBar.exchange_segment == exchange_segment,
            )
        )
        if limit is None:
            result = await self.session.execute(query.order_by(DailyBar.bar_date.asc()))
            return list(result.scalars().all())
        result = await self.session.execute(
            query.order_by(DailyBar.bar_date.desc()).limit(int(limit))
        )
        return list(reversed(result.scalars().all()))

    async def count_for(self, symbol: str, exchange_segment: str) -> int:
        result = await self.session.execute(
            select(func.count(DailyBar.id)).where(
                and_(
                    DailyBar.symbol == symbol,
                    DailyBar.exchange_segment == exchange_segment,
                )
            )
        )
        return int(result.scalar_one() or 0)

    async def coverage(self, exchange_segment: str) -> Dict[str, Any]:
        """What this table holds for a segment, for the health page."""
        result = await self.session.execute(
            select(
                func.count(func.distinct(DailyBar.symbol)),
                func.count(DailyBar.id),
                func.min(DailyBar.bar_date),
                func.max(DailyBar.bar_date),
            ).where(DailyBar.exchange_segment == exchange_segment)
        )
        symbols, bars, first, last = result.one()
        return {
            "symbols": int(symbols or 0),
            "bars": int(bars or 0),
            "firstDate": first.isoformat() if first else None,
            "lastDate": last.isoformat() if last else None,
        }

    async def upsert_many(self, rows: Iterable[Dict[str, Any]]) -> Dict[str, int]:
        """Insert new bars and update changed ones. Idempotent.

        Select-then-write rather than a dialect-specific upsert, for the same
        reason `InstrumentRepository.upsert_many` is: the same code has to run
        on SQLite and MySQL.

        A bar that already exists IS updated rather than skipped. Dhan restates
        a series after a corporate action, and a split that silently failed to
        propagate would leave a price history that no longer matches the
        instrument it describes.
        """
        rows = list(rows)
        if not rows:
            return {"inserted": 0, "updated": 0, "unchanged": 0}

        keys = {(row["exchange_segment"], row["symbol"], row["bar_date"]) for row in rows}
        segments = {key[0] for key in keys}
        symbols = {key[1] for key in keys}
        dates = {key[2] for key in keys}

        existing_rows = await self.session.execute(
            select(DailyBar).where(
                and_(
                    DailyBar.exchange_segment.in_(sorted(segments)),
                    DailyBar.symbol.in_(sorted(symbols)),
                    DailyBar.bar_date.in_(sorted(dates)),
                )
            )
        )
        existing: Dict[Tuple[str, str, date], DailyBar] = {
            (bar.exchange_segment, bar.symbol, bar.bar_date): bar
            for bar in existing_rows.scalars().all()
        }

        inserted = updated = unchanged = 0
        mutable = ("security_id", "open", "high", "low", "close", "volume", "source")
        # A key added during THIS batch is not yet in `existing`; without this
        # a file listing the same date twice would insert it twice and break
        # the unique constraint at flush.
        added: set = set()

        for row in rows:
            key = (row["exchange_segment"], row["symbol"], row["bar_date"])
            current = existing.get(key)
            if current is None:
                if key in added:
                    unchanged += 1
                    continue
                self.session.add(DailyBar(**row))
                added.add(key)
                inserted += 1
                continue
            changed = False
            for field in mutable:
                if field not in row:
                    continue
                if getattr(current, field) != row[field]:
                    setattr(current, field, row[field])
                    changed = True
            if changed:
                updated += 1
            else:
                unchanged += 1

        await self.session.flush()
        return {"inserted": inserted, "updated": updated, "unchanged": unchanged}
