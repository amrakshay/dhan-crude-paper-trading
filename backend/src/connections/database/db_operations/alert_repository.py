"""The alert outbox's queries."""
from datetime import datetime, timedelta
from typing import List, Optional

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
    ) -> Alert:
        alert = Alert(
            connection_id=connection_id,
            kind=kind,
            severity=severity,
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

    async def recent(self, limit: int = 50) -> List[Alert]:
        result = await self.session.execute(
            select(Alert).order_by(Alert.id.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def latest_open_for_dedupe(
        self, dedupe_key: str, since: datetime
    ) -> Optional[Alert]:
        """The most recent row holding this dedupe window open.

        A collapsed occurrence increments that row's `suppressed_count` rather
        than inserting its own, which is what stops a flood in the log becoming
        a flood in this table.
        """
        result = await self.session.execute(
            select(Alert)
            .where(Alert.dedupe_key == dedupe_key, Alert.created_at >= since)
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
