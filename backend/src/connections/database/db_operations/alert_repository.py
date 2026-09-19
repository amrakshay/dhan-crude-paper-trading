"""The alert outbox's queries."""
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.connections.database.db_models.alert_model import (
    ALERT_PENDING,
    Alert,
)
from src.core.base_repository import BaseRepository
from src.core.time_utils import utc_now


class AlertRepository(BaseRepository[Alert]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, Alert)

    async def add(
        self,
        kind: str,
        severity: str,
        title: str,
        body: str,
        status: str = ALERT_PENDING,
        connection_id: Optional[int] = None,
        dedupe_key: Optional[str] = None,
        last_error: Optional[str] = None,
        strategy_key: Optional[str] = None,
    ) -> Alert:
        alert = Alert(
            connection_id=connection_id,
            kind=kind,
            severity=severity,
            strategy_key=strategy_key,
            title=title[:200],
            body=body,
            status=status,
            attempts=0,
            suppressed_count=0,
            dedupe_key=(dedupe_key or None) and dedupe_key[:200],
            last_error=(last_error or None) and last_error[:500],
        )
        self.session.add(alert)
        await self.session.flush()
        return alert

    async def pending(self, limit: int = 20) -> List[Alert]:
        """Oldest first -- an outbox delivers in the order things happened."""
        result = await self.session.execute(
            select(Alert)
            .where(Alert.status == ALERT_PENDING)
            .order_by(Alert.id)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def recent(
        self, limit: int = 50, strategy_key: Optional[str] = None
    ) -> List[Alert]:
        query = select(Alert)
        if strategy_key is not None:
            query = query.where(Alert.strategy_key == strategy_key)
        result = await self.session.execute(
            query.order_by(Alert.id.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def stats_by_key(
        self, strategy_key: Optional[str] = None
    ) -> List[dict]:
        """Per (kind, dedupe_key): how many, and which row was the last one.

        ONE grouped query rather than a query per catalogue rule, and it stays
        one as the table grows -- the alternative walks every row to find the
        newest of each kind, which is the sort of thing that is free for a
        month and then is not.
        """
        query = select(
            Alert.kind,
            Alert.dedupe_key,
            func.count(Alert.id).label("total"),
            func.max(Alert.id).label("last_id"),
            func.coalesce(func.sum(Alert.suppressed_count), 0).label("collapsed"),
        )
        if strategy_key is not None:
            query = query.where(Alert.strategy_key == strategy_key)
        result = await self.session.execute(
            query.group_by(Alert.kind, Alert.dedupe_key)
        )
        return [
            {
                "kind": row.kind,
                "dedupeKey": row.dedupe_key,
                "total": int(row.total or 0),
                "lastId": int(row.last_id),
                "collapsed": int(row.collapsed or 0),
            }
            for row in result.all()
        ]

    async def by_ids(self, ids: List[int]) -> Dict[int, Alert]:
        """The named rows, in one query. Pairs with `stats_by_key`."""
        if not ids:
            return {}
        result = await self.session.execute(select(Alert).where(Alert.id.in_(ids)))
        return {alert.id: alert for alert in result.scalars().all()}

    async def latest_open_for_dedupe(
        self, dedupe_key: str, since: datetime
    ) -> Optional[Alert]:
        """The most recent row holding this dedupe window open, BY CREATION.

        A collapsed occurrence increments that row's `suppressed_count` rather
        than inserting its own, which is what stops a flood in the log becoming
        a flood in this table.

        Keyed on `created_at`, so the window is a FLOOR BETWEEN MESSAGES: a
        flood that continues gets a fresh message every window, carrying the
        running count. That is right for an EVENT that keeps happening. It is
        wrong for a CONDITION that is simply true -- see
        `latest_observed_for_dedupe`.
        """
        result = await self.session.execute(
            select(Alert)
            .where(Alert.dedupe_key == dedupe_key, Alert.created_at >= since)
            .order_by(Alert.id.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def latest_observed_for_dedupe(
        self, dedupe_key: str, since: datetime
    ) -> Optional[Alert]:
        """The row for a condition that is STILL BEING OBSERVED.

        Keyed on `updated_at`, which moves every time a repeat is collapsed
        onto the row. So while the watcher keeps reporting a condition the same
        row stays current and nothing new is sent; once it stops reporting it
        for longer than the re-arm window, the row goes stale and a recurrence
        is a new message.

        That is the difference between alerting on a STATE and alerting on a
        TRANSITION, and it is the whole reason this method exists beside the
        one above. A standing condition -- ten missed sessions that will never
        be filled in -- was otherwise re-sent every five minutes for ever.
        """
        result = await self.session.execute(
            select(Alert)
            .where(Alert.dedupe_key == dedupe_key, Alert.updated_at >= since)
            .order_by(Alert.id.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def mark_sent(self, alert: Alert) -> Alert:
        from src.connections.database.db_models.alert_model import ALERT_SENT

        alert.status = ALERT_SENT
        alert.attempts = int(alert.attempts or 0) + 1
        alert.sent_at = utc_now()
        alert.last_error = None
        await self.session.flush()
        return alert

    async def mark_attempt_failed(
        self, alert: Alert, error: str, give_up: bool
    ) -> Alert:
        from src.connections.database.db_models.alert_model import ALERT_FAILED

        alert.attempts = int(alert.attempts or 0) + 1
        alert.last_error = (error or "")[:500] or None
        if give_up:
            alert.status = ALERT_FAILED
        await self.session.flush()
        return alert

    async def mark_suppressed(self, alert: Alert, reason: str) -> Alert:
        from src.connections.database.db_models.alert_model import ALERT_SUPPRESSED

        alert.status = ALERT_SUPPRESSED
        alert.last_error = (reason or "")[:500] or None
        await self.session.flush()
        return alert

    async def counts_by_status(self, within_hours: int = 24) -> dict:
        since = utc_now() - timedelta(hours=within_hours)
        result = await self.session.execute(
            select(Alert.status, func.count(Alert.id))
            .where(Alert.created_at >= since)
            .group_by(Alert.status)
        )
        return {status: int(count) for status, count in result.all()}
