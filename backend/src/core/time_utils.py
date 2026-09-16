"""Time handling.

Rule for this project: every timestamp is stored in the database as a NAIVE
datetime in UTC. SQLite keeps tz-aware values as ISO strings with an offset
while MySQL's DATETIME silently drops the offset, so storing aware values would
make the two backends behave differently on read-back. Converting to IST for
display happens at the edges.
"""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def utc_now() -> datetime:
    """Current time as a naive UTC datetime, microsecond precision."""
    return datetime.now(UTC).replace(tzinfo=None)


def epoch_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def as_utc_aware(value: datetime) -> datetime:
    """Tag a stored naive-UTC value as aware, for serialisation."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def to_ist(value: datetime) -> datetime:
    return as_utc_aware(value).astimezone(IST)


def ist_now() -> datetime:
    return datetime.now(IST)


def ist_today() -> date:
    return ist_now().date()


def ist_day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    """[start, end) of an IST calendar day, as naive UTC datetimes."""
    start_ist = datetime.combine(day, time.min, tzinfo=IST)
    end_ist = start_ist + timedelta(days=1)
    return (
        start_ist.astimezone(UTC).replace(tzinfo=None),
        end_ist.astimezone(UTC).replace(tzinfo=None),
    )


def parse_hhmm(value: str) -> time:
    hours, _, minutes = value.partition(":")
    return time(hour=int(hours), minute=int(minutes or 0))
