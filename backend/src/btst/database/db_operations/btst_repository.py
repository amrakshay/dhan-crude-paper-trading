"""Decision-journal and overnight-book data access.

Two of the three repositories here are **append-only, and enforced rather than
documented**, the same way the rotation's journal is: `BaseRepository` hands
every subclass an `update` and a `delete`, so a rule that lives only in a
docstring is a convention. A decision record that can be edited is not a record
of what was decided.

`BtstHoldingRepository` is the exception and says why in its own docstring. It
holds the OPEN QUESTION "did this position get out", which is answered by
changing it.
"""
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.btst.database.db_models.btst_session_model import (
    BtstDecision,
    BtstHolding,
    BtstSession,
    EXIT_DONE,
    EXIT_FAILED,
    EXIT_LATE,
    EXIT_PENDING,
    STATUS_COMPLETED,
    STATUS_SKIPPED,
)
from src.core.base_repository import BaseRepository
from src.logging_config import get_logger

logger = get_logger("btst.journal")


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


class BtstSessionRepository(_AppendOnly, BaseRepository[BtstSession]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, BtstSession)

    async def create(self, **fields: Any) -> BtstSession:
        record = BtstSession(**fields)
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_by_id(self, session_id: int) -> Optional[BtstSession]:
        result = await self.session.execute(
            select(BtstSession).where(BtstSession.id == int(session_id))
        )
        return result.scalar_one_or_none()

    async def latest(
        self,
        strategy_key: str,
        run_kind: Optional[str] = None,
        statuses: Optional[Sequence[str]] = None,
    ) -> Optional[BtstSession]:
        query = select(BtstSession).where(BtstSession.strategy_key == strategy_key)
        if run_kind:
            query = query.where(BtstSession.run_kind == run_kind)
        if statuses:
            query = query.where(BtstSession.status.in_(list(statuses)))
        result = await self.session.execute(
            query.order_by(
                desc(BtstSession.session_date), desc(BtstSession.id)
            ).limit(1)
        )
        return result.scalar_one_or_none()

    async def list_recent(
        self,
        strategy_key: str,
        limit: int = 30,
        run_kind: Optional[str] = None,
    ) -> List[BtstSession]:
        query = select(BtstSession).where(BtstSession.strategy_key == strategy_key)
        if run_kind:
            query = query.where(BtstSession.run_kind == run_kind)
        result = await self.session.execute(
            query.order_by(
                desc(BtstSession.session_date), desc(BtstSession.id)
            ).limit(int(limit))
        )
        return list(result.scalars().all())

    async def sessions_completed_on(
        self, strategy_key: str, session_date: date, run_kind: str
    ) -> List[BtstSession]:
        """Has this run already happened for this session?

        Idempotence is the JOURNAL's, not a flag's -- the same rule the
        rotation follows. A restart at 15:25 must not re-scan a session that
        was scanned at 15:20 and, far worse, must not buy the same names twice.
        """
        result = await self.session.execute(
            select(BtstSession).where(
                and_(
                    BtstSession.strategy_key == strategy_key,
                    BtstSession.session_date == session_date,
                    BtstSession.run_kind == run_kind,
                    BtstSession.status.in_([STATUS_COMPLETED, STATUS_SKIPPED]),
                )
            )
        )
        return list(result.scalars().all())

    async def decided_session_dates(
        self, strategy_key: str, run_kind: str, since: date
    ) -> List[date]:
        result = await self.session.execute(
            select(BtstSession.session_date)
            .where(
                and_(
                    BtstSession.strategy_key == strategy_key,
                    BtstSession.run_kind == run_kind,
                    BtstSession.session_date >= since,
                    BtstSession.status.in_([STATUS_COMPLETED, STATUS_SKIPPED]),
                )
            )
            .distinct()
        )
        return sorted({value for value in result.scalars().all() if value})

    async def first_completed_session(self, strategy_key: str) -> Optional[date]:
        """The earliest session this strategy actually COMPLETED a run for.

        The boundary for missed-run detection, and both halves matter for the
        same reasons they do on the rotation: `session_date` rather than
        `created_at`, because a record is written when the job RUNS; and
        COMPLETED rather than any status, because a SKIPPED row can be stamped
        with a session from before the module existed. Without this bound a
        fresh installation reports every date in the lookback as missed, which
        on this codebase has already been forwarded to somebody's phone every
        five minutes.
        """
        result = await self.session.execute(
            select(func.min(BtstSession.session_date)).where(
                and_(
                    BtstSession.strategy_key == strategy_key,
                    BtstSession.status == STATUS_COMPLETED,
                )
            )
        )
        return result.scalar_one_or_none()

    async def ran_on_day(self, strategy_key: str, run_kind: str, day: date) -> bool:
        """Did this job already RUN on that IST day? Keyed on `started_at`.

        The durable half of the once-a-day guard, and it matters more here than
        it does on the rotation: a second scan does not merely waste requests,
        it BUYS A SECOND SET OF POSITIONS. The in-memory half stops a running
        process; this stops a restart.
        """
        from src.core.time_utils import ist_day_bounds_utc

        start, end = ist_day_bounds_utc(day)
        result = await self.session.execute(
            select(func.count(BtstSession.id)).where(
                and_(
                    BtstSession.strategy_key == strategy_key,
                    BtstSession.run_kind == run_kind,
                    BtstSession.started_at >= start,
                    BtstSession.started_at < end,
                )
            )
        )
        return int(result.scalar_one() or 0) > 0

    async def count_for(self, strategy_key: str) -> int:
        result = await self.session.execute(
            select(func.count(BtstSession.id)).where(
                BtstSession.strategy_key == strategy_key
            )
        )
        return int(result.scalar_one() or 0)


class BtstDecisionRepository(_AppendOnly, BaseRepository[BtstDecision]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, BtstDecision)

    async def add_many(self, rows: Iterable[Dict[str, Any]]) -> int:
        rows = list(rows)
        for row in rows:
            self.session.add(BtstDecision(**row))
        if rows:
            await self.session.flush()
        return len(rows)

    async def for_session(
        self, session_id: int, actions: Optional[Sequence[str]] = None
    ) -> List[BtstDecision]:
        query = select(BtstDecision).where(
            BtstDecision.session_id == int(session_id)
        )
        if actions:
            query = query.where(BtstDecision.action.in_(list(actions)))
        result = await self.session.execute(
            query.order_by(
                BtstDecision.rank.is_(None),
                BtstDecision.rank.asc(),
                BtstDecision.symbol.asc(),
            )
        )
        return list(result.scalars().all())

    async def for_symbol(
        self, strategy_key: str, symbol: str, limit: int = 50
    ) -> List[BtstDecision]:
        """Every decision ever taken about one name, newest first."""
        result = await self.session.execute(
            select(BtstDecision)
            .join(BtstSession, BtstSession.id == BtstDecision.session_id)
            .where(
                and_(
                    BtstSession.strategy_key == strategy_key,
                    BtstDecision.symbol == symbol,
                )
            )
            .order_by(desc(BtstSession.session_date), desc(BtstDecision.id))
            .limit(int(limit))
        )
        return list(result.scalars().all())


class BtstHoldingRepository(BaseRepository[BtstHolding]):
    """The overnight book. **Deliberately NOT append-only.**

    Every other table in this package refuses `update`, and this one does not,
    because it is not a record of a decision -- it is the open question "is
    this position still held, and did the exit run". A question is answered by
    changing its answer. The append-only record of what happened is
    `btst_decisions`, which gets its own SOLD row with its own reason.

    `delete` is still refused. A holding that was opened happened, and removing
    the row would take the realised overnight gap out of every report with it.
    """

    def __init__(self, session: AsyncSession):
        super().__init__(session, BtstHolding)

    async def delete(self, *args, **kwargs):  # noqa: D102 - refuses by design
        raise NotImplementedError(
            "BtstHoldingRepository does not delete. A position that was opened "
            "happened; closing it is an UPDATE to its exit status, and the "
            "realised overnight gap on the row is what the reports read."
        )

    async def create(self, **fields: Any) -> BtstHolding:
        record = BtstHolding(**fields)
        self.session.add(record)
        await self.session.flush()
        return record

    async def open_for(
        self, strategy_key: str, portfolio_id: Optional[int] = None
    ) -> List[BtstHolding]:
        """Everything still held overnight.

        **Keyed per portfolio when one is given**, the same rule
        `PositionRepository.get_open_for_security` follows: dropping it merges
        two books silently -- no error, just an exit job selling somebody
        else's position.

        FAILED counts as open, and that is the point: an exit that was
        attempted and refused leaves a position held, which is the state the
        alarm exists for.
        """
        query = select(BtstHolding).where(
            and_(
                BtstHolding.strategy_key == strategy_key,
                BtstHolding.exit_status.in_([EXIT_PENDING, EXIT_FAILED]),
            )
        )
        if portfolio_id is not None:
            query = query.where(BtstHolding.portfolio_id == int(portfolio_id))
        result = await self.session.execute(
            query.order_by(BtstHolding.entry_session_date.asc(), BtstHolding.symbol.asc())
        )
        return list(result.scalars().all())

    async def overdue(
        self, strategy_key: str, before: datetime
    ) -> List[BtstHolding]:
        """Open holdings whose exit was due before this moment.

        What the alarm reads. Not "open holdings" -- between the scan and the
        next morning's exit, every holding is open and correctly so.
        """
        result = await self.session.execute(
            select(BtstHolding)
            .where(
                and_(
                    BtstHolding.strategy_key == strategy_key,
                    BtstHolding.exit_status.in_([EXIT_PENDING, EXIT_FAILED]),
                    BtstHolding.entry_at < before,
                )
            )
            .order_by(BtstHolding.entry_session_date.asc())
        )
        return list(result.scalars().all())

    async def get_open_for_security(
        self, strategy_key: str, portfolio_id: int, security_id: str
    ) -> Optional[BtstHolding]:
        """Is this name already held overnight in THIS book?

        Per portfolio, for the reason root `CLAUDE.md` section 3a gives about
        the two open-row lookups: the same strategy can run in several books
        and dropping the portfolio merges them silently.
        """
        result = await self.session.execute(
            select(BtstHolding).where(
                and_(
                    BtstHolding.strategy_key == strategy_key,
                    BtstHolding.portfolio_id == int(portfolio_id),
                    BtstHolding.security_id == str(security_id),
                    BtstHolding.exit_status.in_([EXIT_PENDING, EXIT_FAILED]),
                )
            )
        )
        return result.scalars().first()

    async def list_recent(
        self, strategy_key: str, limit: int = 60
    ) -> List[BtstHolding]:
        result = await self.session.execute(
            select(BtstHolding)
            .where(BtstHolding.strategy_key == strategy_key)
            .order_by(desc(BtstHolding.entry_session_date), desc(BtstHolding.id))
            .limit(int(limit))
        )
        return list(result.scalars().all())

    async def exit_counts(self, strategy_key: str) -> Dict[str, int]:
        """How many holdings ended each way, in ONE grouped query.

        Grouped rather than a count per status for the same reason
        `stats_by_key()` is: the alternative is free for a month and then is
        not. The Health tab reads this to answer "did the exit run" across the
        whole history rather than only the newest row.
        """
        result = await self.session.execute(
            select(BtstHolding.exit_status, func.count(BtstHolding.id))
            .where(BtstHolding.strategy_key == strategy_key)
            .group_by(BtstHolding.exit_status)
        )
        counts = {status: 0 for status in (EXIT_PENDING, EXIT_DONE, EXIT_LATE, EXIT_FAILED)}
        for status, count in result.all():
            counts[str(status)] = int(count or 0)
        return counts
