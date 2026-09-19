"""Every query this feature makes. Repositories own queries (backend §1)."""
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional, Sequence

from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ipo.database.db_models.ipo_model import (
    Ipo,
    IpoAction,
    IpoGmpReading,
    IpoJobRun,
    STATUS_LISTED,
)


class IpoRepository:
    """The IPOs themselves."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, ipo_id: int) -> Optional[Ipo]:
        return await self.session.get(Ipo, ipo_id)

    async def get_by_source_id(self, source_id: int) -> Optional[Ipo]:
        result = await self.session.execute(
            select(Ipo).where(Ipo.source_id == source_id)
        )
        return result.scalar_one_or_none()

    async def by_source_ids(self, source_ids: Sequence[int]) -> Dict[int, Ipo]:
        """Every stored IPO among these source ids, keyed by source id.

        One query rather than one per row: a refresh reads the whole board.
        """
        if not source_ids:
            return {}
        result = await self.session.execute(
            select(Ipo).where(Ipo.source_id.in_(list(source_ids)))
        )
        return {row.source_id: row for row in result.scalars().all()}

    async def add(self, **fields) -> Ipo:
        row = Ipo(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def closing_on(self, day: date) -> List[Ipo]:
        """Mainboard IPOs whose subscription closes on that day.

        A LISTED row is excluded: an IPO that has already listed is not
        closing, whatever its stored close date says.
        """
        result = await self.session.execute(
            select(Ipo)
            .where(and_(Ipo.close_date == day, Ipo.status != STATUS_LISTED))
            .order_by(Ipo.company_name)
        )
        return list(result.scalars().all())

    async def listed(self, limit: int = 100) -> List[Ipo]:
        """Newest first. Forward-only by construction, not by a filter here:
        an IPO first observed already listed was never stored."""
        result = await self.session.execute(
            select(Ipo)
            .where(Ipo.status == STATUS_LISTED)
            .order_by(desc(Ipo.listing_date), desc(Ipo.id))
            .limit(limit)
        )
        return list(result.scalars().all())

    async def not_listed(self) -> List[Ipo]:
        """Everything the daily refresh still has a reason to look at."""
        result = await self.session.execute(
            select(Ipo).where(Ipo.status != STATUS_LISTED).order_by(Ipo.close_date)
        )
        return list(result.scalars().all())

    async def count(self) -> int:
        result = await self.session.execute(select(func.count(Ipo.id)))
        return int(result.scalar_one())


class IpoGmpRepository:
    """The GMP time series. Appended to; never updated."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(
        self,
        *,
        ipo_id: int,
        captured_at: datetime,
        source_updated_at: Optional[datetime],
        gmp,
        gmp_percent,
    ) -> IpoGmpReading:
        row = IpoGmpReading(
            ipo_id=ipo_id,
            captured_at=captured_at,
            source_updated_at=source_updated_at,
            gmp=gmp,
            gmp_percent=gmp_percent,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def latest_for(self, ipo_id: int) -> Optional[IpoGmpReading]:
        result = await self.session.execute(
            select(IpoGmpReading)
            .where(IpoGmpReading.ipo_id == ipo_id)
            .order_by(desc(IpoGmpReading.captured_at), desc(IpoGmpReading.id))
            .limit(1)
        )
        return result.scalars().first()

    async def latest_for_many(
        self, ipo_ids: Sequence[int]
    ) -> Dict[int, IpoGmpReading]:
        """The newest reading per IPO, in one pass.

        Read whole and reduced in Python rather than with a correlated
        subquery per row: the series is small (one row per IPO per refresh) and
        a query that is obvious beats one that is clever here.
        """
        if not ipo_ids:
            return {}
        result = await self.session.execute(
            select(IpoGmpReading)
            .where(IpoGmpReading.ipo_id.in_(list(ipo_ids)))
            .order_by(IpoGmpReading.captured_at, IpoGmpReading.id)
        )
        latest: Dict[int, IpoGmpReading] = {}
        for row in result.scalars().all():
            latest[row.ipo_id] = row
        return latest

    async def history_for(self, ipo_id: int, limit: int = 200) -> List[IpoGmpReading]:
        result = await self.session.execute(
            select(IpoGmpReading)
            .where(IpoGmpReading.ipo_id == ipo_id)
            .order_by(desc(IpoGmpReading.captured_at))
            .limit(limit)
        )
        return list(result.scalars().all())


class IpoActionRepository:
    """The append-only action log, and the current state derived from it."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(
        self,
        *,
        ipo_id: int,
        action: str,
        value: bool,
        acted_at: datetime,
        user_id: Optional[int],
    ) -> IpoAction:
        row = IpoAction(
            ipo_id=ipo_id,
            action=action,
            value=value,
            acted_at=acted_at,
            user_id=user_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def current_for(self, ipo_ids: Sequence[int]) -> Dict[int, Dict[str, IpoAction]]:
        """The NEWEST row per (ipo, action). `{ipo_id: {action: row}}`.

        The whole log for these IPOs is read and reduced in order, so the
        answer is the last thing that happened rather than the last thing
        written -- the two differ if a row is ever backdated.
        """
        if not ipo_ids:
            return {}
        result = await self.session.execute(
            select(IpoAction)
            .where(IpoAction.ipo_id.in_(list(ipo_ids)))
            .order_by(IpoAction.acted_at, IpoAction.id)
        )
        current: Dict[int, Dict[str, IpoAction]] = {}
        for row in result.scalars().all():
            current.setdefault(row.ipo_id, {})[row.action] = row
        return current

    async def history_for(self, ipo_id: int) -> List[IpoAction]:
        result = await self.session.execute(
            select(IpoAction)
            .where(IpoAction.ipo_id == ipo_id)
            .order_by(desc(IpoAction.acted_at), desc(IpoAction.id))
        )
        return list(result.scalars().all())


class IpoJobRunRepository:
    """When a scheduled job ran, keyed on the slot it ran for.

    The DURABLE half of the once-per-slot guard. The in-memory half is in the
    scheduler; a restart clears that one, and a restart is exactly how the
    swing nightly once re-ran every fifteen minutes for two hours.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, job_kind: str, slot_key: str) -> Optional[IpoJobRun]:
        result = await self.session.execute(
            select(IpoJobRun).where(
                and_(IpoJobRun.job_kind == job_kind, IpoJobRun.slot_key == slot_key)
            )
        )
        return result.scalar_one_or_none()

    async def has_run(self, job_kind: str, slot_key: str) -> bool:
        """Did this slot's work already SUCCEED?

        A failed run's row is deleted rather than kept (see `record`), so a
        present row always means done. `RETRY_AFTER` bounds how often a FAILED
        job is retried and is deliberately not what decides this.
        """
        return await self.get(job_kind, slot_key) is not None

    async def record(
        self,
        *,
        job_kind: str,
        slot_key: str,
        started_at: datetime,
        finished_at: datetime,
        ok: bool,
        detail: Optional[str],
    ) -> Optional[IpoJobRun]:
        """Mark the slot done. A FAILED run records nothing and returns None.

        Recording a failure would consume the slot, and the slot is the only
        thing that gets the work done -- a 14:00 sweep that could not reach the
        source must be allowed to try again at 14:02, not be written off until
        15:00.
        """
        if not ok:
            return None
        existing = await self.get(job_kind, slot_key)
        if existing is not None:
            return existing
        row = IpoJobRun(
            job_kind=job_kind,
            slot_key=slot_key,
            started_at=started_at,
            finished_at=finished_at,
            ok=True,
            detail=(detail or None),
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def latest(self, job_kind: str) -> Optional[IpoJobRun]:
        result = await self.session.execute(
            select(IpoJobRun)
            .where(IpoJobRun.job_kind == job_kind)
            .order_by(desc(IpoJobRun.started_at), desc(IpoJobRun.id))
            .limit(1)
        )
        return result.scalars().first()

    async def recent(self, limit: int = 20) -> List[IpoJobRun]:
        result = await self.session.execute(
            select(IpoJobRun)
            .order_by(desc(IpoJobRun.started_at), desc(IpoJobRun.id))
            .limit(limit)
        )
        return list(result.scalars().all())
