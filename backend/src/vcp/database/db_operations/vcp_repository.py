"""Data access for the VCP module. Every query the module makes lives here.

`VcpSessionRepository` and `VcpDecisionRepository` are append-only, enforced
the way the rotation's and BTST's journals are. `VcpSignalRepository` allows
exactly one change, its status, through `set_status`. `VcpPositionRepository`
is live state and is updated; `VcpScanStateRepository` is a cursor.
"""
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.time_utils import utc_now
from src.vcp.database.db_models.vcp_models import (
    POSITION_OPEN,
    STATUS_COMPLETED,
    STATUS_SKIPPED,
    VcpDecision,
    VcpPosition,
    VcpScanState,
    VcpSession,
    VcpSignal,
)


class _AppendOnly:
    async def update(self, *args, **kwargs):  # noqa: D102 - refuses by design
        raise NotImplementedError(f"{type(self).__name__} is append-only.")

    async def delete(self, *args, **kwargs):  # noqa: D102 - refuses by design
        raise NotImplementedError(f"{type(self).__name__} is append-only.")


class VcpSessionRepository(_AppendOnly):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **fields: Any) -> VcpSession:
        record = VcpSession(**fields)
        self.session.add(record)
        await self.session.flush()
        return record

    async def finish(self, record: VcpSession, status: str, message: str, **counts: Any) -> None:
        """The one write after creation: a run's own outcome, set once."""
        record.status = status
        record.message = message[:500]
        record.completed_at = utc_now()
        for key, value in counts.items():
            setattr(record, key, value)
        await self.session.flush()

    async def latest(self, strategy_key: str, run_kind: Optional[str] = None,
                     statuses: Optional[Sequence[str]] = None,
                     portfolio_id: Optional[int] = None) -> Optional[VcpSession]:
        query = select(VcpSession).where(VcpSession.strategy_key == strategy_key)
        if run_kind:
            query = query.where(VcpSession.run_kind == run_kind)
        if statuses:
            query = query.where(VcpSession.status.in_(list(statuses)))
        if portfolio_id is not None:
            query = query.where(VcpSession.portfolio_id == int(portfolio_id))
        result = await self.session.execute(
            query.order_by(desc(VcpSession.session_date), desc(VcpSession.id)).limit(1)
        )
        return result.scalar_one_or_none()

    async def list_recent(self, strategy_key: str, limit: int = 30) -> List[VcpSession]:
        result = await self.session.execute(
            select(VcpSession).where(VcpSession.strategy_key == strategy_key)
            .order_by(desc(VcpSession.started_at), desc(VcpSession.id)).limit(int(limit))
        )
        return list(result.scalars().all())

    async def completed(self, strategy_key: str, run_kind: str, session_date: date,
                        portfolio_id: Optional[int] = None) -> bool:
        """Has this run already been done for this session? The idempotence key."""
        query = select(VcpSession.id).where(and_(
            VcpSession.strategy_key == strategy_key,
            VcpSession.run_kind == run_kind,
            VcpSession.session_date == session_date,
            VcpSession.status.in_([STATUS_COMPLETED, STATUS_SKIPPED]),
        ))
        if portfolio_id is not None:
            query = query.where(VcpSession.portfolio_id == int(portfolio_id))
        result = await self.session.execute(query.limit(1))
        return result.scalar_one_or_none() is not None

    async def ran_on_day(self, strategy_key: str, run_kind: str, day: date) -> bool:
        """Did this job COMPLETE on this calendar day (IST date of starting)?

        Keyed on when the job RAN, as the rotation's is: a restart clears the
        in-memory guard, and this is what stops the same job repeating.
        """
        from src.core.time_utils import ist_day_bounds_utc

        start, end = ist_day_bounds_utc(day)
        result = await self.session.execute(
            select(VcpSession.id).where(and_(
                VcpSession.strategy_key == strategy_key,
                VcpSession.run_kind == run_kind,
                VcpSession.started_at >= start,
                VcpSession.started_at < end,
                VcpSession.status.in_([STATUS_COMPLETED, STATUS_SKIPPED]),
            )).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def first_completed_session(self, strategy_key: str) -> Optional[date]:
        result = await self.session.execute(
            select(VcpSession.session_date).where(and_(
                VcpSession.strategy_key == strategy_key,
                VcpSession.status == STATUS_COMPLETED,
            )).order_by(VcpSession.session_date.asc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def decided_session_dates(self, strategy_key: str, run_kind: str,
                                    since: date) -> List[date]:
        result = await self.session.execute(
            select(VcpSession.session_date).where(and_(
                VcpSession.strategy_key == strategy_key,
                VcpSession.run_kind == run_kind,
                VcpSession.session_date >= since,
                VcpSession.status.in_([STATUS_COMPLETED, STATUS_SKIPPED]),
            )).distinct()
        )
        return list(result.scalars().all())


class VcpSignalRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **fields: Any) -> VcpSignal:
        record = VcpSignal(**fields)
        self.session.add(record)
        await self.session.flush()
        return record

    async def set_status(self, signal: VcpSignal, status: str, reason: Optional[str] = None) -> None:
        signal.status = status
        if reason:
            signal.reason = reason[:500]
        await self.session.flush()

    async def remembered(self, strategy_key: str, symbols: Sequence[str],
                         since: date) -> Dict[str, List[Tuple[date, date]]]:
        """(base start, seen date) of every accepted base still recent enough
        to overlap a new one, per symbol, in ONE query."""
        if not symbols:
            return {}
        result = await self.session.execute(
            select(VcpSignal.symbol, VcpSignal.base_start_date, VcpSignal.seen_date)
            .where(and_(
                VcpSignal.strategy_key == strategy_key,
                VcpSignal.symbol.in_(list(symbols)),
                VcpSignal.seen_date >= since,
            ))
        )
        out: Dict[str, List[Tuple[date, date]]] = {}
        for symbol, start, seen in result.all():
            out.setdefault(symbol, []).append((start, seen))
        return out

    async def with_status(self, strategy_key: str, statuses: Sequence[str],
                          seen_date: Optional[date] = None) -> List[VcpSignal]:
        query = select(VcpSignal).where(and_(
            VcpSignal.strategy_key == strategy_key,
            VcpSignal.status.in_(list(statuses)),
        ))
        if seen_date is not None:
            query = query.where(VcpSignal.seen_date == seen_date)
        result = await self.session.execute(query.order_by(VcpSignal.symbol.asc()))
        return list(result.scalars().all())

    async def list_recent(self, strategy_key: str, limit: int = 200,
                          statuses: Optional[Sequence[str]] = None) -> List[VcpSignal]:
        query = select(VcpSignal).where(VcpSignal.strategy_key == strategy_key)
        if statuses:
            query = query.where(VcpSignal.status.in_(list(statuses)))
        result = await self.session.execute(
            query.order_by(desc(VcpSignal.seen_date), VcpSignal.symbol.asc()).limit(int(limit))
        )
        return list(result.scalars().all())


class VcpDecisionRepository(_AppendOnly):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **fields: Any) -> VcpDecision:
        if fields.get("reason"):
            fields["reason"] = str(fields["reason"])[:500]
        record = VcpDecision(**fields)
        self.session.add(record)
        await self.session.flush()
        return record

    async def list_recent(self, portfolio_id: Optional[int] = None,
                          limit: int = 200) -> List[VcpDecision]:
        query = select(VcpDecision)
        if portfolio_id is not None:
            query = query.where(VcpDecision.portfolio_id == int(portfolio_id))
        result = await self.session.execute(
            query.order_by(desc(VcpDecision.id)).limit(int(limit))
        )
        return list(result.scalars().all())

    async def for_sessions(self, session_ids: Iterable[int]) -> List[VcpDecision]:
        ids = [int(one) for one in session_ids]
        if not ids:
            return []
        result = await self.session.execute(
            select(VcpDecision).where(VcpDecision.session_id.in_(ids))
            .order_by(VcpDecision.id.asc())
        )
        return list(result.scalars().all())


class VcpPositionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **fields: Any) -> VcpPosition:
        record = VcpPosition(**fields)
        self.session.add(record)
        await self.session.flush()
        return record

    async def save(self, record: VcpPosition, **fields: Any) -> None:
        for key, value in fields.items():
            setattr(record, key, value)
        await self.session.flush()

    async def open_for(self, strategy_key: str,
                       portfolio_id: Optional[int] = None) -> List[VcpPosition]:
        query = select(VcpPosition).where(and_(
            VcpPosition.strategy_key == strategy_key,
            VcpPosition.status == POSITION_OPEN,
        ))
        if portfolio_id is not None:
            query = query.where(VcpPosition.portfolio_id == int(portfolio_id))
        result = await self.session.execute(query.order_by(VcpPosition.entry_date.asc(),
                                                           VcpPosition.symbol.asc()))
        return list(result.scalars().all())

    async def get_open_for_security(self, strategy_key: str, portfolio_id: int,
                                    security_id: str) -> Optional[VcpPosition]:
        """Keyed per PORTFOLIO, like every open-row lookup here (CLAUDE.md 3a)."""
        result = await self.session.execute(
            select(VcpPosition).where(and_(
                VcpPosition.strategy_key == strategy_key,
                VcpPosition.portfolio_id == int(portfolio_id),
                VcpPosition.security_id == str(security_id),
                VcpPosition.status == POSITION_OPEN,
            )).limit(1)
        )
        return result.scalar_one_or_none()

    async def list_recent(self, strategy_key: str, portfolio_id: Optional[int] = None,
                          limit: int = 200) -> List[VcpPosition]:
        query = select(VcpPosition).where(VcpPosition.strategy_key == strategy_key)
        if portfolio_id is not None:
            query = query.where(VcpPosition.portfolio_id == int(portfolio_id))
        result = await self.session.execute(
            query.order_by(desc(VcpPosition.entry_date), desc(VcpPosition.id)).limit(int(limit))
        )
        return list(result.scalars().all())


class VcpScanStateRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def all_for(self, strategy_key: str) -> Dict[str, date]:
        result = await self.session.execute(
            select(VcpScanState.symbol, VcpScanState.scanned_through)
            .where(VcpScanState.strategy_key == strategy_key)
        )
        return {symbol: through for symbol, through in result.all()}

    async def advance(self, strategy_key: str, symbol: str, through: date) -> None:
        result = await self.session.execute(
            select(VcpScanState).where(and_(
                VcpScanState.strategy_key == strategy_key,
                VcpScanState.symbol == symbol,
            ))
        )
        row = result.scalar_one_or_none()
        if row is None:
            self.session.add(VcpScanState(strategy_key=strategy_key, symbol=symbol,
                                          scanned_through=through))
        elif through > row.scanned_through:
            row.scanned_through = through
        await self.session.flush()
