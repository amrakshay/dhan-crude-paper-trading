"""The hourly closing-day reminder: what it says, and how it is de-duplicated.

**AN ALERT IS A ROW FIRST AND AN HTTP CALL SECOND** (root `CLAUDE.md`). This
writes outbox rows through `AlertService.raise_alert`; `alert-dispatcher` is
the only thing in this process that sends anything. A reminder raised while
Telegram is unreachable still arrives after the restart, and "what did it tell
me, and did it arrive" stays answerable.

**THE DEDUPE KEY CARRIES THE HOUR, WHICH IS THE WHOLE TRICK.** The default
`DEDUPE_WINDOW` mode collapses repeats inside a five-minute window, and this
alert is SUPPOSED to repeat -- hourly, for up to eight hours. Rather than
widening or weakening the dedupe machinery (which exists because this codebase
has twice produced a message flood), each sweep gets its own key:

    ipo|closing-reminder|2026-09-19|14

so 14:00 and 15:00 are different keys and both go out, while a second tick
inside the 14:00 hour is the same key and collapses. The durable
once-per-slot guard is `ipo_job_runs`; this is the belt to its braces, and the
two fail in different directions on purpose.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.connections.database.db_models.alert_model import (
    KIND_IPO,
    SEVERITY_WARNING,
)
from src.connections.services.alert_catalogue import EVENT_IPO_SOURCE_UNREACHABLE
from src.connections.services.alert_service import AlertService
from src.core.time_utils import to_ist
from src.ipo.services.ipo_service import IpoView
from src.logging_config import get_logger

logger = get_logger("ipo.reminders")


def dedupe_key_for(slot: datetime) -> str:
    """`ipo|closing-reminder|<IST date>|<IST hour>`.

    Built from the IST wall clock the operator lives on, not from UTC: the
    reminder window is 10:00-17:00 IST and a key in UTC would put 17:00 IST and
    10:00 IST of the following morning in different days from the ones the
    page shows.
    """
    return f"ipo|closing-reminder|{slot:%Y-%m-%d}|{slot:%H}"


@dataclass
class ReminderOutcome:
    """What one sweep did, so the scheduler can report it without guessing."""

    slot_key: str
    outstanding: int
    sent: bool
    reason: str


def _money(value: Optional[Decimal]) -> str:
    if value is None:
        return "not published"
    return f"₹{Decimal(str(value)):,.2f}"


def _ist(moment: Optional[datetime]) -> str:
    if moment is None:
        return "never"
    return to_ist(moment).strftime("%H:%M IST, %d %b")


def build_body(views: List[IpoView], slot: datetime) -> str:
    """The message. Every figure it quotes says how old it is.

    Named separately from the send so a test can read it without an outbox,
    and so the wording is one thing rather than a format string buried in a
    branch.
    """
    lines = [
        f"{len(views)} mainboard IPO(s) close today "
        f"({slot:%d %b %Y}) with a step still outstanding.",
        "",
    ]
    for view in views:
        gmp = view.gmp
        gmp_text = _money(gmp.gmp)
        if gmp.gmp_percent is not None:
            gmp_text += f" ({Decimal(str(gmp.gmp_percent)):.2f}% of issue price)"
        # The capture time is the SOURCE's own where it published one, because
        # that is when the premium was actually observed; ours is when this
        # application last read it. Both are shown when they differ, and a
        # stale reading says so rather than being quietly omitted.
        captured = _ist(gmp.source_updated_at or gmp.captured_at)
        if gmp.is_stale:
            gmp_text += f" -- STALE, captured {captured}"
        else:
            gmp_text += f", captured {captured}"

        pending = "; ".join(view.pending_steps) or "outstanding"
        lines.extend(
            [
                f"• {view.company_name}",
                f"  Closes: {view.close_date:%d %b %Y}"
                if view.close_date
                else "  Closes: not published",
                f"  Issue price: {_money(view.issue_price)}"
                + (f" (lot {view.lot_size})" if view.lot_size else ""),
                f"  GMP: {gmp_text}",
                f"  STILL PENDING: {pending}",
                "",
            ]
        )
    lines.append(
        "An unaccepted UPI mandate is a failed application. These stop only "
        "when an IPO is marked Applied AND Accepted, or Rejected."
    )
    return "\n".join(lines)


class IpoReminderService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.alerts = AlertService(session)

    async def send_sweep(
        self, views: List[IpoView], slot: datetime
    ) -> ReminderOutcome:
        """One hourly sweep.

        **Nothing outstanding sends nothing.** There is deliberately no
        all-clear message: a reminder that also fires when there is nothing to
        do is a reminder somebody learns to ignore, and the one that mattered
        goes with it.
        """
        key = dedupe_key_for(slot)
        if not views:
            logger.debug("IPO sweep %s: nothing outstanding, sending nothing", key)
            return ReminderOutcome(
                slot_key=key, outstanding=0, sent=False, reason="nothing outstanding"
            )

        row = await self.alerts.raise_alert(
            kind=KIND_IPO,
            severity=SEVERITY_WARNING,
            title=(
                f"{len(views)} IPO(s) close today with a step outstanding"
                if len(views) > 1
                else f"{views[0].company_name} closes today"
            ),
            body=build_body(views, slot),
            dedupe_key=key,
            # NULL, explicitly: no strategy owns an IPO, and a strategy's
            # Alerts tab filters on this column.
            strategy_key=None,
        )
        if row is None:
            logger.info("IPO sweep %s collapsed onto an existing row", key)
            return ReminderOutcome(
                slot_key=key,
                outstanding=len(views),
                sent=False,
                reason="collapsed onto the hour's existing alert",
            )
        logger.info(
            "IPO sweep %s: raised a reminder about %d outstanding IPO(s)",
            key, len(views),
        )
        return ReminderOutcome(
            slot_key=key, outstanding=len(views), sent=True, reason="raised"
        )

    async def report_source_failure(self, detail: str) -> None:
        """The fetch failed. Say so, as a CONDITION rather than an event.

        A source that is down stays down for a while, and a WINDOW dedupe would
        put one of these on a phone every hour alongside the reminders it is
        apologising for.
        """
        await self.alerts.record_health(
            event=EVENT_IPO_SOURCE_UNREACHABLE,
            severity=SEVERITY_WARNING,
            title="The IPO GMP source could not be read",
            body=(
                f"{detail}\n\nThe dashboard is still showing the last figures "
                "it captured, labelled stale. Closing-day reminders continue "
                "-- the reminder is the point and the GMP is context."
            ),
        )
