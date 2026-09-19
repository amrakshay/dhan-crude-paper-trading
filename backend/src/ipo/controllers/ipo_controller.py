"""IPO dashboard orchestration: service results and exceptions -> HTTP."""
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.time_utils import ist_today
from src.ipo.api_schemas.ipo_schemas import (
    IpoResponse,
    IpoStatusResponse,
    IpoTabResponse,
    SetActionRequest,
)
from src.ipo.database.db_models.ipo_model import JOB_DAILY_REFRESH
from src.ipo.database.db_operations.ipo_repository import IpoJobRunRepository
from src.ipo.services.ipo_service import IpoService, IpoValidationError, next_open_day
from src.ipo.services.ipo_source_client import IpoSourceError

TAB_CLOSING_TODAY = "closing-today"
TAB_CLOSING_NEXT = "closing-next"
TAB_LISTED = "listed"

# Said on the tab itself, not buried in a README. There is deliberately no
# holiday list in this codebase (`market_clock`), so "the next day the exchange
# is open" is really "the next weekday".
HOLIDAY_CAVEAT = (
    "Weekends are skipped, but this application keeps no exchange holiday "
    "list -- on the day before a public holiday these are the IPOs closing "
    "the next weekday, which may be a day early."
)
FORWARD_ONLY_CAVEAT = (
    "Forward-only: this lists IPOs this application watched close and then "
    "list. Nothing is backfilled from the source, so it is empty until the "
    "first IPO observed here lists."
)


class IpoController:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.service = IpoService(session)
        self.runs = IpoJobRunRepository(session)

    async def tab(self, tab: str) -> IpoTabResponse:
        today = ist_today()
        if tab == TAB_CLOSING_TODAY:
            views = await self.service.closing_today(today)
            day, caveat = today, None
        elif tab == TAB_CLOSING_NEXT:
            views = await self.service.closing_next(today)
            day, caveat = next_open_day(today), HOLIDAY_CAVEAT
        elif tab == TAB_LISTED:
            views = await self.service.listed(today)
            day, caveat = None, FORWARD_ONLY_CAVEAT
        else:
            raise HTTPException(status_code=404, detail=f"No IPO tab called {tab!r}")

        last = await self.runs.latest(JOB_DAILY_REFRESH)
        return IpoTabResponse(
            tab=tab,
            ipos=[IpoResponse.model_validate(view) for view in views],
            day=day,
            caveat=caveat,
            last_refresh_at=last.finished_at if last else None,
            last_refresh_detail=last.detail if last else None,
        )

    async def set_action(
        self, ipo_id: int, request: SetActionRequest, user_id: Optional[int]
    ) -> IpoResponse:
        try:
            view = await self.service.set_action(
                ipo_id=ipo_id,
                action=request.action,
                value=request.value,
                user_id=user_id,
            )
        except IpoValidationError as exc:
            # A bad action name is a 400; an unknown IPO is a 404. Two
            # different mistakes by the caller.
            status = 404 if "is not one this application knows" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        await self.session.commit()
        return view

    async def refresh_now(self) -> IpoTabResponse:
        """A manual refresh from the page.

        Does NOT consume a scheduled slot: the scheduler's own `_record` is
        what marks a slot done, and this does not go through it. Pressing
        Refresh at 13:55 must not be what stops the 14:00 reminder.
        """
        try:
            await self.service.refresh(today=ist_today())
        except IpoSourceError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        await self.session.commit()
        return await self.tab(TAB_CLOSING_TODAY)

    async def status(self) -> IpoStatusResponse:
        from src.ipo.services.scheduler import get_ipo_scheduler

        state = get_ipo_scheduler().status()
        last = await self.runs.latest(JOB_DAILY_REFRESH)
        return IpoStatusResponse(
            enabled=state["enabled"],
            running=state["running"],
            daily_refresh_at=state["dailyRefreshAt"],
            reminder_from=state["reminderFrom"],
            reminder_to=state["reminderTo"],
            last_refresh_at=last.finished_at if last else None,
            last_refresh_detail=last.detail if last else None,
            last_error=state["lastError"],
            stored_ipos=await self.service.ipos.count(),
        )
