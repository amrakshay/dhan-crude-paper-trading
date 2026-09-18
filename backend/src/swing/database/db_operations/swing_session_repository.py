"""Decision-journal data access.

**Append-only, and ENFORCED rather than documented.** `CashLedgerRepository`
states the same rule in its docstring, but `BaseRepository` hands every
subclass an `update` and a `delete` regardless, so the rule has only ever been
a convention there. Here both are overridden to raise: a decision record that
can be edited is not a record of what was decided, and this is the table an
operator will go to in six months to find out why the system sold something.
If the system reconsiders, that is a new row.
"""
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.base_repository import BaseRepository
from src.logging_config import get_logger
from src.swing.database.db_models.swing_session_model import (
    STATUS_COMPLETED,
    STATUS_SKIPPED,
    SwingDecision,
    SwingSession,
)

logger = get_logger("swing.journal")


class _AppendOnly:
    """Refuses the mutating half of `BaseRepository`.

    Raising is deliberately louder than simply not offering the methods: a
    caller reaching for `update` on the journal has misunderstood what the
    journal is, and should find that out at the call site.
    """

    async def update(self, *args, **kwargs):  # noqa: D102 - refuses by design
        raise NotImplementedError(
            f"{type(self).__name__} is append-only. A decision record is never "
            f"edited; if the system reconsiders, write a new record."
        )

    async def delete(self, *args, **kwargs):  # noqa: D102 - refuses by design
        raise NotImplementedError(
            f"{type(self).__name__} is append-only. A decision record is never "
            f"deleted; the journal is the audit trail."
        )


class SwingSessionRepository(_AppendOnly, BaseRepository[SwingSession]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, SwingSession)

    async def create(self, **fields: Any) -> SwingSession:
        record = SwingSession(**fields)
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_by_id(self, session_id: int) -> Optional[SwingSession]:
        result = await self.session.execute(
            select(SwingSession).where(SwingSession.id == int(session_id))
        )
        return result.scalar_one_or_none()

    async def latest(
        self,
        strategy_key: str,
        run_kind: Optional[str] = None,
        statuses: Optional[Sequence[str]] = None,
    ) -> Optional[SwingSession]:
        query = select(SwingSession).where(SwingSession.strategy_key == strategy_key)
        if run_kind:
            query = query.where(SwingSession.run_kind == run_kind)
        if statuses:
            query = query.where(SwingSession.status.in_(list(statuses)))
        result = await self.session.execute(
            query.order_by(desc(SwingSession.session_date), desc(SwingSession.id)).limit(1)
        )
        return result.scalar_one_or_none()

    async def list_recent(
        self,
        strategy_key: str,
        limit: int = 30,
        run_kind: Optional[str] = None,
    ) -> List[SwingSession]:
        query = select(SwingSession).where(SwingSession.strategy_key == strategy_key)
        if run_kind:
            query = query.where(SwingSession.run_kind == run_kind)
        result = await self.session.execute(
            query.order_by(desc(SwingSession.session_date), desc(SwingSession.id)).limit(
                int(limit)
            )
        )
        return list(result.scalars().all())

    async def sessions_completed_on(
        self, strategy_key: str, session_date: date, run_kind: str
    ) -> List[SwingSession]:
        """Has this run already happened for this session?

        The scheduler is at-least-once: a restart at 18:20 must not re-decide a
        session it already decided at 18:15, and a missed run must be visible
        rather than quietly skipped.
        """
        result = await self.session.execute(
            select(SwingSession).where(
                and_(
                    SwingSession.strategy_key == strategy_key,
                    SwingSession.session_date == session_date,
                    SwingSession.run_kind == run_kind,
                    SwingSession.status.in_([STATUS_COMPLETED, STATUS_SKIPPED]),
                )
            )
        )
        return list(result.scalars().all())

    async def decided_session_dates(
        self, strategy_key: str, run_kind: str, since: date
    ) -> List[date]:
        """Sessions this run has completed since a date, for missed-run detection."""
        result = await self.session.execute(
            select(SwingSession.session_date)
            .where(
                and_(
                    SwingSession.strategy_key == strategy_key,
                    SwingSession.run_kind == run_kind,
                    SwingSession.session_date >= since,
                    SwingSession.status.in_([STATUS_COMPLETED, STATUS_SKIPPED]),
                )
            )
            .distinct()
        )
        return sorted({value for value in result.scalars().all() if value})

    async def ran_on_day(
        self, strategy_key: str, run_kind: str, day: date
    ) -> bool:
        """Did this job already RUN on that IST day? Keyed on `started_at`.

        Deliberately not `session_date`: that is the regime index's newest bar
        date, which does not move on a day the vendor has published nothing, so
        it cannot answer "have I already done tonight's work". `started_at` is
        when the job ran, which is exactly the question.

        This is the durable half of the once-a-day guard. The in-memory half
        (`SwingScheduler._succeeded`) is what stops a running process
        re-attempting; this is what stops a RESTART doing it, and a restart is
        how a twelve-minute five-hundred-symbol refresh got run eight times in
        one evening before it was caught.
        """
        from src.core.time_utils import ist_day_bounds_utc

        start, end = ist_day_bounds_utc(day)
        result = await self.session.execute(
            select(func.count(SwingSession.id)).where(
                and_(
                    SwingSession.strategy_key == strategy_key,
                    SwingSession.run_kind == run_kind,
                    SwingSession.started_at >= start,
                    SwingSession.started_at < end,
                )
            )
        )
        return int(result.scalar_one() or 0) > 0

    async def count_for(self, strategy_key: str) -> int:
        result = await self.session.execute(
            select(func.count(SwingSession.id)).where(
                SwingSession.strategy_key == strategy_key
            )
        )
        return int(result.scalar_one() or 0)


class SwingDecisionRepository(_AppendOnly, BaseRepository[SwingDecision]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, SwingDecision)

    async def add_many(self, rows: Iterable[Dict[str, Any]]) -> int:
        rows = list(rows)
        for row in rows:
            self.session.add(SwingDecision(**row))
        if rows:
            await self.session.flush()
        return len(rows)

    async def for_session(
        self, session_id: int, actions: Optional[Sequence[str]] = None
    ) -> List[SwingDecision]:
        query = select(SwingDecision).where(
            SwingDecision.session_id == int(session_id)
        )
        if actions:
            query = query.where(SwingDecision.action.in_(list(actions)))
        result = await self.session.execute(
            query.order_by(SwingDecision.rank.is_(None), SwingDecision.rank.asc(),
                           SwingDecision.symbol.asc())
        )
        return list(result.scalars().all())

    async def for_symbol(
        self, strategy_key: str, symbol: str, limit: int = 50
    ) -> List[SwingDecision]:
        """Every decision ever taken about one name, newest first."""
        result = await self.session.execute(
            select(SwingDecision)
            .join(SwingSession, SwingSession.id == SwingDecision.session_id)
            .where(
                and_(
                    SwingSession.strategy_key == strategy_key,
                    SwingDecision.symbol == symbol,
                )
            )
            .order_by(desc(SwingSession.session_date), desc(SwingDecision.id))
            .limit(int(limit))
        )
        return list(result.scalars().all())
