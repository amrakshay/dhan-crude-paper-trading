"""The alert outbox.

**An alert is a ROW first and an HTTP call second**, and never the other way
round. Three reasons, every one of which has bitten this codebase in some other
form:

- A fill must never block on somebody else's HTTP. `PositionService.apply_fill`
  runs inside the order path; a synchronous `sendMessage` there would put
  Telegram's latency on every trade and Telegram's outage on every fill.
- An alert raised during a crash still needs to arrive after the restart. A
  direct call made while the process is dying delivers nothing and leaves no
  trace that it tried.
- "What did it tell me, and did it arrive" should be answerable. A row with a
  status answers it; a log line saying `sendMessage returned 200` does not.

**De-duplication is visible rather than silent.** The swing stop monitor once
logged "database is locked" once a second for twelve minutes, and the nightly
re-ran every fifteen minutes for two hours: unthrottled, either would have sent
hundreds of messages and hit Telegram's flood control. A collapsed occurrence
does not get its own row -- that would move the flood into this table -- it
increments `suppressed_count` on the row that is already holding the window
open, so the collapsing is a number an operator can read rather than an absence
they have to infer.
"""
from sqlalchemy import Column, ForeignKey, Integer, String, Text

from src.database.base import PreciseDateTime, TimestampedModel

# --- status ---------------------------------------------------------------
ALERT_PENDING = "PENDING"      # written, not yet delivered
ALERT_SENT = "SENT"
ALERT_FAILED = "FAILED"        # delivery was attempted and gave up
ALERT_SUPPRESSED = "SUPPRESSED"  # deliberately not delivered; `last_error` says why
ALERT_STATUSES = (ALERT_PENDING, ALERT_SENT, ALERT_FAILED, ALERT_SUPPRESSED)

# --- kind: what happened, not how it is delivered -------------------------
KIND_TRADE_BOUGHT = "TRADE_BOUGHT"
KIND_TRADE_SOLD = "TRADE_SOLD"
KIND_ERROR = "ERROR"
KIND_HEALTH = "HEALTH"
KIND_COMMAND = "COMMAND"
KIND_TEST = "TEST"

# --- severity -------------------------------------------------------------
SEVERITY_INFO = "INFO"
SEVERITY_WARNING = "WARNING"
SEVERITY_ERROR = "ERROR"
SEVERITY_CRITICAL = "CRITICAL"


class Alert(TimestampedModel):
    """One thing worth telling somebody about, and what became of it."""

    __tablename__ = "alerts"

    # Which connection it is for. Nullable because a fact is worth RECORDING
    # even when nothing is configured to carry it -- the row is then written
    # SUPPRESSED with the reason, rather than the fact being dropped because
    # there was no bot to tell.
    connection_id = Column(
        Integer,
        ForeignKey("connections.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    kind = Column(String(32), nullable=False, index=True)
    severity = Column(String(16), nullable=False, index=True)

    # Which strategy this is ABOUT, when it is about one. STORED rather than
    # derived, for the same reason `orders` and `positions` store it (root
    # CLAUDE.md section 3a): reading it back out of the body at query time
    # would mean parsing English, which is exactly what `swing_stops.exit_kind`
    # exists to avoid. NULL means the alert is process-wide -- a dead task, the
    # feed, the token -- and those are not a strategy's business.
    strategy_key = Column(String(64), nullable=True, index=True)

    title = Column(String(200), nullable=False)
    body = Column(Text, nullable=False)

    status = Column(String(16), nullable=False, index=True)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(String(500), nullable=True)

    # Logger name plus a NORMALISED message, never the raw text: a counter
    # inside the message ("this has now happened 47 times") would otherwise
    # defeat the de-duplication it is reporting.
    dedupe_key = Column(String(200), nullable=True, index=True)
    # How many further occurrences this row is standing in for. 0 means it is
    # the only one.
    suppressed_count = Column(Integer, nullable=False, default=0)

    sent_at = Column(PreciseDateTime, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<Alert(id={self.id}, kind={self.kind!r}, status={self.status!r}, "
            f"title={self.title!r})>"
        )
