"""The IPO dashboard's four tables.

**This is not a strategy.** Nothing here places an order, touches a portfolio,
a cash ledger or the feed. It fetches a public page, stores what it said, shows
it, and sends a reminder -- the shape of `src/notes/`, not of `src/swing/`.

Four tables, and each one is a different KIND of fact:

* `ipos` -- one durable row per IPO, the thing itself. Never deleted. An IPO
  that vanishes from the source page after listing stays here, which is the
  whole reason for storing it rather than rendering the source live.
* `ipo_gmp_readings` -- a TIME SERIES, one row per capture, never an
  overwritten column, so "how did GMP move in the three days before listing"
  is answerable. This is FETCHED data (root `CLAUDE.md` section 4: fetched bars
  may be stored, ticks may not be accumulated) -- no tick reaches it and
  `MarketBook` does not know it exists.
* `ipo_actions` -- an APPEND-ONLY log of what the operator did, so "when did I
  accept that mandate" has an answer rather than a current-state boolean that
  has forgotten. The current state is the newest row per (ipo, action).
* `ipo_job_runs` -- when a scheduled job RAN. The durable half of the
  once-per-slot guard; the in-memory half lives in the scheduler. Root
  `CLAUDE.md`: a job that has done its work is DONE, not due for a retry, and a
  restart is how the nightly once made 3,500 wasted requests in two hours.

ONLY MAINBOARD IPOs ARE EVER STORED. The `board` column exists so a row can say
what it is rather than leaving it implied, but the ingest filters SME out before
anything is written.
"""
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)

from src.database.base import Money, PreciseDateTime, TimestampedModel

# --- board ----------------------------------------------------------------
BOARD_MAINBOARD = "MAINBOARD"
BOARD_SME = "SME"

# --- lifecycle ------------------------------------------------------------
STATUS_UPCOMING = "UPCOMING"
STATUS_OPEN = "OPEN"
STATUS_CLOSED = "CLOSED"
STATUS_LISTED = "LISTED"
IPO_STATUSES = (STATUS_UPCOMING, STATUS_OPEN, STATUS_CLOSED, STATUS_LISTED)

# --- the three actions ----------------------------------------------------
# Each is a FAMILY, not an event: a row carries the new value, and the newest
# row wins. Applied and Accepted are independent and both togglable (a mis-tap
# has to be undoable); Reject is terminal for the reminders but is itself
# reversible, which is why it is a value here too rather than a status.
ACTION_APPLIED = "APPLIED"
ACTION_MANDATE_ACCEPTED = "MANDATE_ACCEPTED"
ACTION_REJECTED = "REJECTED"
IPO_ACTIONS = (ACTION_APPLIED, ACTION_MANDATE_ACCEPTED, ACTION_REJECTED)

# --- scheduled jobs -------------------------------------------------------
JOB_DAILY_REFRESH = "DAILY_REFRESH"
JOB_CLOSING_SWEEP = "CLOSING_SWEEP"


class Ipo(TimestampedModel):
    """One IPO, as this application has observed it."""

    __tablename__ = "ipos"

    # The source's own id. Stable across the IPO's life -- it is what its
    # detail page is keyed on -- and the only thing here that is. The company
    # NAME is not a key: the source edits it ("NSE" today, "NSE Ltd" tomorrow).
    source_id = Column(Integer, nullable=False, unique=True, index=True)

    company_name = Column(String(200), nullable=False)
    board = Column(String(16), nullable=False, default=BOARD_MAINBOARD)

    # The source's list endpoint publishes ONE issue price, not a band, so that
    # is what is stored and what the UI calls it. `issue_price_text` keeps the
    # source's own spelling beside the number, so nothing has to be inferred
    # back out of a Decimal when the source starts publishing a range.
    issue_price = Column(Money, nullable=True)
    issue_price_text = Column(String(64), nullable=True)
    lot_size = Column(Integer, nullable=True)

    open_date = Column(Date, nullable=True)
    close_date = Column(Date, nullable=True, index=True)
    listing_date = Column(Date, nullable=True, index=True)
    # Only once it has listed. The listing GAIN is derived from this and the
    # issue price rather than stored: two stored numbers that must agree with a
    # third are two chances to disagree.
    listing_price = Column(Money, nullable=True)

    status = Column(String(16), nullable=False, default=STATUS_UPCOMING, index=True)

    # WHEN THIS APPLICATION FIRST SAW IT, which is what makes the Listed tab
    # forward-only rather than a mirror of the source. An IPO first observed
    # already listed is not stored at all (see `ipo_service`), so every row
    # here was watched through at least part of its life.
    first_seen_at = Column(PreciseDateTime, nullable=False)
    last_seen_at = Column(PreciseDateTime, nullable=False)
    # The status it had when first seen. Kept as data so the forward-only rule
    # is auditable from the row rather than only from the code that applied it.
    first_seen_status = Column(String(16), nullable=False)

    # The source's own page for this IPO, as a relative path. Displayed, never
    # fetched -- the one module allowed to call the source names its host.
    source_path = Column(String(255), nullable=True)

    def __repr__(self) -> str:
        return f"<Ipo(id={self.id}, name={self.company_name!r}, status={self.status!r})>"


class IpoGmpReading(TimestampedModel):
    """One capture of one IPO's grey market premium.

    Appended, never updated. The point is the series.
    """

    __tablename__ = "ipo_gmp_readings"

    ipo_id = Column(
        Integer, ForeignKey("ipos.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # WHEN THIS APPLICATION FETCHED IT. Naive UTC, like every timestamp here.
    captured_at = Column(PreciseDateTime, nullable=False, index=True)
    # WHEN THE SOURCE SAYS IT LAST UPDATED THE FIGURE. Nullable, because the
    # source may not publish one -- and different from `captured_at`, which is
    # the distinction that makes "we fetched successfully but the figure is a
    # day old" expressible. Reporting only our own fetch time would dress a
    # stale number as a fresh one.
    source_updated_at = Column(PreciseDateTime, nullable=True)

    # Rupees. Nullable: the source prints "--" for an IPO with no grey market,
    # and that is a real answer, not a zero.
    gmp = Column(Money, nullable=True)
    # As a percentage of the issue price, as the source computes it. Stored
    # rather than derived because the source publishes it and the two can
    # differ on a band; the UI shows this one and says whose it is.
    gmp_percent = Column(Money, nullable=True)

    __table_args__ = (
        Index("ix_ipo_gmp_readings_ipo_captured", "ipo_id", "captured_at"),
    )


class IpoAction(TimestampedModel):
    """Something the operator did about one IPO, and when, and who.

    APPEND-ONLY. Toggling Applied off writes a row with `value=False`; it does
    not delete the row that turned it on. "When did I accept that mandate" is a
    question about history, and a mutable boolean cannot answer it.
    """

    __tablename__ = "ipo_actions"

    ipo_id = Column(
        Integer, ForeignKey("ipos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action = Column(String(24), nullable=False, index=True)
    value = Column(Boolean, nullable=False)

    acted_at = Column(PreciseDateTime, nullable=False, index=True)
    # WHO. Nullable only so a deleted user does not take the history with them.
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    __table_args__ = (
        Index("ix_ipo_actions_ipo_action_acted", "ipo_id", "action", "acted_at"),
    )


class IpoJobRun(TimestampedModel):
    """A scheduled job that ran, keyed on the SLOT it ran for.

    `slot_key` is the IST day for the daily refresh and `YYYY-MM-DD|HH` for an
    hourly sweep. The unique constraint is the guard: a restart at 14:20 cannot
    re-send the 14:00 reminder, because the 14:00 slot is already taken.

    Keyed on the slot rather than on `started_at` for the same reason
    `SwingSessionRepository.ran_on_day` is keyed on when the job RAN: the
    question is "has this piece of work been done", and only a key that names
    the piece of work can answer it.
    """

    __tablename__ = "ipo_job_runs"

    job_kind = Column(String(32), nullable=False, index=True)
    slot_key = Column(String(32), nullable=False, index=True)

    started_at = Column(PreciseDateTime, nullable=False, index=True)
    finished_at = Column(PreciseDateTime, nullable=True)
    # A run that FAILED is recorded as failed and is not what marks the slot
    # done -- the row is deleted rather than kept, so the next tick retries.
    # It is written first so a crash mid-run leaves a trace.
    ok = Column(Boolean, nullable=False, default=False)
    detail = Column(String(500), nullable=True)

    __table_args__ = (
        UniqueConstraint("job_kind", "slot_key", name="uq_ipo_job_runs_kind_slot"),
    )
