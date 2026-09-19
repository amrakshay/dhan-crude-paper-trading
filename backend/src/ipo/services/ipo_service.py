"""The IPO dashboard's business logic: ingest, the three tabs, and the actions.

No FastAPI in here (backend `CLAUDE.md` §1); the controller turns
`IpoValidationError` into an HTTP status.

Three rules in this module are decisions rather than implementation, and each
one is asserted by a test:

* **SME issues are filtered out and never stored.** Not hidden at read time --
  never written. A filter at the edge is a filter somebody can forget.
* **THE LISTED TAB IS FORWARD-ONLY.** An IPO first observed already in a listed
  state is not stored at all, so the tab is genuinely empty on day one and
  fills only with IPOs this application watched close and then list. It is not
  a read-time filter over a backfilled table; there is no backfill.
* **A failed refresh never looks like a fresh one.** Every GMP reading carries
  the time it was captured AND the time the source says it last moved the
  figure, and a reading older than its expected cadence is labelled stale
  wherever it appears -- the page and the alert body alike.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from src import config_utils
from src.core.time_utils import IST, ist_today, utc_now
from src.ipo.database.db_models.ipo_model import (
    ACTION_APPLIED,
    ACTION_MANDATE_ACCEPTED,
    ACTION_REJECTED,
    BOARD_MAINBOARD,
    IPO_ACTIONS,
    Ipo,
    STATUS_LISTED,
)
from src.ipo.database.db_operations.ipo_repository import (
    IpoActionRepository,
    IpoGmpRepository,
    IpoRepository,
)
from src.ipo.services.ipo_parser import SourceIpo, mainboard_only, parse_rows
from src.ipo.services.ipo_source_client import IpoSourceClient
from src.logging_config import get_logger

logger = get_logger("ipo.service")

# How old a GMP reading may be before it is called stale. Two thresholds,
# because there are two cadences: the whole board is refreshed once a day, and
# an IPO closing TODAY is refreshed again before every hourly reminder.
DEFAULT_STALE_AFTER_MINUTES_CLOSING = 90
DEFAULT_STALE_AFTER_HOURS = 26
# The source's OWN figure can be stale while our fetch is fresh -- we read it
# successfully and it had not moved. A day is generous; a GMP nobody has
# updated in that long is not context, it is history.
DEFAULT_SOURCE_STALE_AFTER_HOURS = 24


class IpoValidationError(Exception):
    """The request cannot be honoured, and it is the caller's fault."""


def ist_naive_to_utc(value: Optional[datetime]) -> Optional[datetime]:
    """A naive IST wall-clock time -> the naive UTC this project stores.

    Local to this package rather than added to `core.time_utils`: the source's
    unzoned local timestamps are a property of this one third party, and
    nothing else in the application has ever needed the conversion in this
    direction.
    """
    if value is None:
        return None
    return (
        value.replace(tzinfo=IST).astimezone(timezone.utc).replace(tzinfo=None)
    )


def next_open_day(day: date) -> date:
    """The next day the exchange is open, skipping weekends.

    **THERE IS NO HOLIDAY LIST IN THIS CODEBASE AND THIS DOES NOT ADD ONE.**
    `src/strategies/services/market_clock.py` makes the same choice and the
    swing scheduler's `next_occurrence` says so in the payload it serves: the
    trading calendar this application trusts is the regime index's own bar
    dates, and a holiday simply has no bar. That works for a strategy deciding
    on bars and cannot work here, because an IPO's close date comes from a
    third party and has nothing to do with NSE's bar history.

    So this skips Saturday and Sunday and nothing else, and every surface that
    uses it says a public holiday may put an IPO in the "Closing next" tab a
    day early. An honest caveat beats a calendar nobody maintains.
    """
    candidate = day + timedelta(days=1)
    while candidate.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        candidate += timedelta(days=1)
    return candidate


@dataclass
class GmpView:
    """One GMP reading, with whether it can be trusted as current."""

    gmp: Optional[Decimal]
    gmp_percent: Optional[Decimal]
    captured_at: Optional[datetime]
    source_updated_at: Optional[datetime]
    is_stale: bool
    stale_reason: Optional[str]


@dataclass
class IpoView:
    """One IPO as a tab shows it."""

    id: int
    source_id: int
    company_name: str
    board: str
    status: str
    issue_price: Optional[Decimal]
    issue_price_text: Optional[str]
    lot_size: Optional[int]
    open_date: Optional[date]
    close_date: Optional[date]
    listing_date: Optional[date]
    listing_price: Optional[Decimal]
    listing_gain: Optional[Decimal]
    listing_gain_percent: Optional[Decimal]
    source_path: Optional[str]
    first_seen_at: datetime
    gmp: GmpView
    applied: bool
    applied_at: Optional[datetime]
    mandate_accepted: bool
    mandate_accepted_at: Optional[datetime]
    rejected: bool
    rejected_at: Optional[datetime]
    is_outstanding: bool
    pending_steps: List[str]


@dataclass
class RefreshResult:
    """What one pass over the source did."""

    fetched_rows: int
    mainboard_rows: int
    created: int
    updated: int
    skipped_already_listed: int
    gmp_readings: int
    at: datetime


class IpoService:
    def __init__(
        self, session: AsyncSession, client: Optional[IpoSourceClient] = None
    ):
        self.session = session
        self.ipos = IpoRepository(session)
        self.gmp = IpoGmpRepository(session)
        self.actions = IpoActionRepository(session)
        self.client = client or IpoSourceClient()

    # --- configuration -----------------------------------------------------
    @staticmethod
    def _stale_after_closing() -> timedelta:
        return timedelta(
            minutes=config_utils.get_property_value_int(
                "ipo.gmp_stale_after_minutes", DEFAULT_STALE_AFTER_MINUTES_CLOSING
            )
        )

    @staticmethod
    def _stale_after() -> timedelta:
        return timedelta(
            hours=config_utils.get_property_value_int(
                "ipo.gmp_stale_after_hours", DEFAULT_STALE_AFTER_HOURS
            )
        )

    @staticmethod
    def _source_stale_after() -> timedelta:
        return timedelta(
            hours=config_utils.get_property_value_int(
                "ipo.gmp_source_stale_after_hours", DEFAULT_SOURCE_STALE_AFTER_HOURS
            )
        )

    # --- ingest ------------------------------------------------------------
    async def refresh(
        self,
        *,
        today: date,
        only_source_ids: Optional[Sequence[int]] = None,
        now: Optional[datetime] = None,
    ) -> RefreshResult:
        """Fetch the board and store what changed.

        `only_source_ids` narrows what is WRITTEN, not what is fetched -- the
        source serves one list and there is no per-IPO endpoint, so a targeted
        refresh is the same request with a narrower write. The closing-day
        sweep uses it so an hourly pass touches only the IPOs closing that day,
        exactly as specified, rather than rewriting the whole board eight times
        a day.

        Raises `IpoSourceError` -- the caller decides what a failed fetch
        means, because on a closing day it means "send the reminder anyway with
        the GMP marked stale" and the rest of the time it means "try later".
        """
        captured_at = now or utc_now()
        rows = await self.client.fetch_rows()
        parsed = parse_rows(rows, today=today)
        mainboard = mainboard_only(parsed)

        wanted = set(only_source_ids) if only_source_ids is not None else None
        existing = await self.ipos.by_source_ids(
            [item.source_id for item in mainboard]
        )

        created = updated = skipped = readings = 0
        for item in mainboard:
            if wanted is not None and item.source_id not in wanted:
                continue
            row = existing.get(item.source_id)
            if row is None:
                if item.status == STATUS_LISTED:
                    # FORWARD-ONLY. First sight of this IPO is after it listed,
                    # so this application never watched it and has no business
                    # showing it as something it recorded. Not stored at all --
                    # which is what makes the Listed tab empty on day one
                    # rather than pre-filled with somebody else's history.
                    skipped += 1
                    continue
                row = await self._create(item, captured_at)
                created += 1
            else:
                self._update(row, item, captured_at)
                updated += 1

            await self.gmp.add(
                ipo_id=row.id,
                captured_at=captured_at,
                source_updated_at=ist_naive_to_utc(item.gmp_updated_at_ist),
                gmp=item.gmp,
                gmp_percent=item.gmp_percent,
            )
            readings += 1

        logger.info(
            "IPO refresh: %d row(s) from the source, %d mainboard, %d new, "
            "%d updated, %d already-listed and skipped, %d GMP reading(s)",
            len(rows), len(mainboard), created, updated, skipped, readings,
        )
        return RefreshResult(
            fetched_rows=len(rows),
            mainboard_rows=len(mainboard),
            created=created,
            updated=updated,
            skipped_already_listed=skipped,
            gmp_readings=readings,
            at=captured_at,
        )

    async def _create(self, item: SourceIpo, seen_at: datetime) -> Ipo:
        return await self.ipos.add(
            source_id=item.source_id,
            company_name=item.company_name,
            board=BOARD_MAINBOARD,
            issue_price=item.issue_price,
            issue_price_text=item.issue_price_text,
            lot_size=item.lot_size,
            open_date=item.open_date,
            close_date=item.close_date,
            listing_date=item.listing_date,
            listing_price=item.listing_price,
            status=item.status,
            first_seen_at=seen_at,
            last_seen_at=seen_at,
            first_seen_status=item.status,
            source_path=item.source_path,
        )

    @staticmethod
    def _update(row: Ipo, item: SourceIpo, seen_at: datetime) -> None:
        """Refresh a stored IPO from the source.

        A value the source has stopped publishing does NOT erase what was
        stored: the listing price disappears from the row the day the IPO drops
        off the live board, and blanking it would empty the Listed tab of the
        only number it exists for.
        """
        row.company_name = item.company_name
        row.status = item.status
        row.last_seen_at = seen_at
        for field in (
            "issue_price",
            "issue_price_text",
            "lot_size",
            "open_date",
            "close_date",
            "listing_date",
            "listing_price",
            "source_path",
        ):
            value = getattr(item, field)
            if value is not None:
                setattr(row, field, value)

    # --- the three tabs ----------------------------------------------------
    async def closing_today(self, today: date, now: Optional[datetime] = None):
        return await self._views(await self.ipos.closing_on(today), today, now)

    async def closing_next(self, today: date, now: Optional[datetime] = None):
        day = next_open_day(today)
        return await self._views(await self.ipos.closing_on(day), today, now)

    async def listed(self, today: date, limit: int = 100, now: Optional[datetime] = None):
        return await self._views(await self.ipos.listed(limit), today, now)

    async def outstanding_closing_today(
        self, today: date, now: Optional[datetime] = None
    ) -> List[IpoView]:
        """The IPOs a reminder at this hour should be about.

        **Applied alone does NOT stop the reminders.** An unaccepted mandate is
        a failed application, and the mandate is the step that actually gets
        forgotten. Only both steps together -- or an explicit Reject -- end
        them.
        """
        views = await self.closing_today(today, now)
        return [view for view in views if view.is_outstanding]

    async def _views(
        self, rows: List[Ipo], today: date, now: Optional[datetime]
    ) -> List[IpoView]:
        moment = now or utc_now()
        ids = [row.id for row in rows]
        gmp_rows = await self.gmp.latest_for_many(ids)
        action_rows = await self.actions.current_for(ids)
        return [
            self._view(row, gmp_rows.get(row.id), action_rows.get(row.id, {}), today, moment)
            for row in rows
        ]

    def _view(self, row: Ipo, reading, actions: Dict, today: date, now: datetime) -> IpoView:
        applied = bool(actions.get(ACTION_APPLIED).value) if ACTION_APPLIED in actions else False
        mandate = (
            bool(actions.get(ACTION_MANDATE_ACCEPTED).value)
            if ACTION_MANDATE_ACCEPTED in actions
            else False
        )
        rejected = (
            bool(actions.get(ACTION_REJECTED).value) if ACTION_REJECTED in actions else False
        )

        # WHAT IS STILL PENDING, in the words the reminder uses. Two steps,
        # and they are ordered: you cannot accept a mandate you were never
        # asked for, so "not applied" subsumes it rather than listing both.
        pending: List[str] = []
        if not rejected:
            if not applied:
                pending.append("not applied")
            elif not mandate:
                pending.append("applied, mandate not accepted")

        listing_gain = None
        listing_gain_percent = None
        if row.listing_price is not None and row.issue_price:
            listing_gain = Decimal(row.listing_price) - Decimal(row.issue_price)
            listing_gain_percent = (
                listing_gain / Decimal(row.issue_price) * Decimal("100")
            )

        return IpoView(
            id=row.id,
            source_id=row.source_id,
            company_name=row.company_name,
            board=row.board,
            status=row.status,
            issue_price=row.issue_price,
            issue_price_text=row.issue_price_text,
            lot_size=row.lot_size,
            open_date=row.open_date,
            close_date=row.close_date,
            listing_date=row.listing_date,
            listing_price=row.listing_price,
            listing_gain=listing_gain,
            listing_gain_percent=listing_gain_percent,
            source_path=row.source_path,
            first_seen_at=row.first_seen_at,
            gmp=self._gmp_view(reading, row, today, now),
            applied=applied,
            applied_at=actions[ACTION_APPLIED].acted_at if applied else None,
            mandate_accepted=mandate,
            mandate_accepted_at=(
                actions[ACTION_MANDATE_ACCEPTED].acted_at if mandate else None
            ),
            rejected=rejected,
            rejected_at=actions[ACTION_REJECTED].acted_at if rejected else None,
            is_outstanding=(not rejected) and not (applied and mandate),
            pending_steps=pending,
        )

    def _gmp_view(self, reading, row: Ipo, today: date, now: datetime) -> GmpView:
        """A reading, and whether it is old enough to be called stale.

        Two inputs, because there are two ways a number on this page can be out
        of date and they have different fixes. OUR fetch being old means the
        refresh is not running; the SOURCE's own timestamp being old means it
        is running and the third party has not moved the figure.
        """
        if reading is None:
            return GmpView(
                gmp=None,
                gmp_percent=None,
                captured_at=None,
                source_updated_at=None,
                is_stale=True,
                stale_reason="No GMP has been captured for this IPO yet.",
            )

        closing_today = row.close_date == today and row.status != STATUS_LISTED
        limit = self._stale_after_closing() if closing_today else self._stale_after()
        reason = None
        if reading.captured_at is not None and now - reading.captured_at > limit:
            reason = (
                "The last successful fetch is older than the expected refresh "
                "cadence."
            )
        elif (
            reading.source_updated_at is not None
            and now - reading.source_updated_at > self._source_stale_after()
        ):
            reason = "The source itself has not moved this GMP for over a day."

        return GmpView(
            gmp=reading.gmp,
            gmp_percent=reading.gmp_percent,
            captured_at=reading.captured_at,
            source_updated_at=reading.source_updated_at,
            is_stale=reason is not None,
            stale_reason=reason,
        )

    # --- the three actions -------------------------------------------------
    async def set_action(
        self,
        *,
        ipo_id: int,
        action: str,
        value: bool,
        user_id: Optional[int],
        now: Optional[datetime] = None,
    ) -> IpoView:
        """Record one action. Append-only: nothing is overwritten.

        Every one of the three is reversible, Reject included. Reject is
        terminal for the REMINDERS and for nothing else -- it clears no flag
        and deletes no history, so changing one's mind about an IPO restores
        exactly the state that was there before.
        """
        if action not in IPO_ACTIONS:
            raise IpoValidationError(
                f"{action!r} is not an IPO action; expected one of "
                f"{', '.join(IPO_ACTIONS)}"
            )
        row = await self.ipos.get(ipo_id)
        if row is None:
            raise IpoValidationError(f"IPO {ipo_id} is not one this application knows")

        await self.actions.add(
            ipo_id=ipo_id,
            action=action,
            value=bool(value),
            acted_at=now or utc_now(),
            user_id=user_id,
        )
        logger.info(
            "IPO %s (%s): %s set to %s by user %s",
            ipo_id, row.company_name, action, bool(value), user_id,
        )
        # The IST day, not the UTC one: "is this IPO closing today" is a
        # question about the operator's calendar, and between 18:30 and
        # midnight IST the two answers differ.
        views = await self._views([row], ist_today(), now)
        return views[0]

    async def action_history(self, ipo_id: int):
        return await self.actions.history_for(ipo_id)

    async def gmp_history(self, ipo_id: int, limit: int = 200):
        return await self.gmp.history_for(ipo_id, limit)
