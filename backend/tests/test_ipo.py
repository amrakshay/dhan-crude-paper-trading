"""The IPO dashboard: the parser, the rules, the clock and the reminder.

The rules under test here are the ones a future change could break without
anything else noticing: the mainboard filter, the forward-only Listed tab, the
"applied alone does not stop the reminders" rule, the hourly window's
boundaries, the restart guard, and the stale labelling.

The source parser is pinned against a SAVED COPY of the real response
(`fixtures/ipo_gmp_report.json`, captured 2026-09-19), so the markup dependency
documented in `ipo_source_client`'s docstring is asserted rather than hoped
for.
"""
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from src.core.time_utils import utc_now
from src.ipo.database.db_models.ipo_model import (
    ACTION_APPLIED,
    ACTION_MANDATE_ACCEPTED,
    ACTION_REJECTED,
    JOB_CLOSING_SWEEP,
    STATUS_LISTED,
)
from src.ipo.database.db_operations.ipo_repository import IpoJobRunRepository
from src.ipo.services import ipo_parser
from src.ipo.services.ipo_reminder_service import (
    IpoReminderService,
    build_body,
    dedupe_key_for,
)
from src.ipo.services.ipo_service import IpoService, next_open_day
from src.ipo.services.scheduler import IpoScheduler

FIXTURE = Path(__file__).parent / "fixtures" / "ipo_gmp_report.json"
# The day the fixture was captured. Passed in rather than read from the clock
# so this file still means something in a year's time.
CAPTURED_ON = date(2026, 9, 19)


def fixture_rows():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["reportTableData"]


class FakeSource:
    """Stands in for the network. Records how many times it was asked."""

    def __init__(self, rows=None, error=None):
        self._rows = rows if rows is not None else fixture_rows()
        self._error = error
        self.calls = 0

    async def fetch_rows(self):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._rows


# --- the parser, against the real response ---------------------------------
def test_the_parser_reads_the_real_response():
    rows = fixture_rows()
    parsed = ipo_parser.parse_rows(rows, today=CAPTURED_ON)

    assert len(parsed) == len(rows), "every row in the real response parses"

    nse = next(item for item in parsed if item.source_id == 2305)
    assert nse.company_name == "NSE"
    assert nse.board == "MAINBOARD"
    assert nse.issue_price == Decimal("1785")
    assert nse.lot_size == 8
    assert nse.close_date == date(2026, 9, 21)
    assert nse.listing_date == date(2026, 9, 24)
    # THE CURRENT premium, out of the `GMP` HTML fragment -- NOT `~max_gmp1`,
    # which is the highest it has ever shown (310 for this row). They look
    # interchangeable and are not.
    assert nse.gmp == Decimal("61")
    assert nse.gmp_percent == Decimal("3.42")
    assert nse.gmp_updated_at_ist == datetime(2026, 9, 19, 22, 37)


def test_the_parser_reads_a_listing_price_out_of_the_name_markup():
    parsed = ipo_parser.parse_rows(fixture_rows(), today=CAPTURED_ON)
    veegaland = next(item for item in parsed if item.source_id == 1601)

    assert veegaland.status == STATUS_LISTED
    assert veegaland.listing_price == Decimal("154.00")
    assert veegaland.issue_price == Decimal("140")


def test_an_ipo_with_no_grey_market_reads_as_no_gmp_not_as_zero():
    """The source prints `--`. That is a real answer and not a premium of 0."""
    parsed = ipo_parser.parse_rows(fixture_rows(), today=CAPTURED_ON)
    blank = next(item for item in parsed if item.source_id == 2063)

    assert blank.gmp is None
    assert blank.gmp_percent is None


def test_a_field_that_stops_matching_yields_none_rather_than_a_wrong_number():
    """The failure mode is "no GMP, and it says so", never a plausible figure."""
    row = dict(fixture_rows()[0])
    row["GMP"] = "<span>the site redesigned this column</span>"
    row["Updated-On"] = ""
    row["~Srt_Close"] = "not a date"

    item = ipo_parser.parse_row(row, today=CAPTURED_ON)

    assert item is not None
    assert item.gmp is None
    assert item.gmp_updated_at_ist is None
    assert item.close_date is None


def test_the_source_publishes_no_year_so_one_is_chosen_defensively():
    """A December reading read in January must not be dated a year ahead."""
    new_year = date(2026, 1, 2)
    raw = '<small><b>31-Dec 22:10</b></small>'

    assert ipo_parser.parse_updated_on(raw, today=new_year) == datetime(
        2025, 12, 31, 22, 10
    )
    # An ordinary same-month reading keeps the current year.
    assert ipo_parser.parse_updated_on(
        '<small><b>19-Sep 22:37</b></small>', today=CAPTURED_ON
    ) == datetime(2026, 9, 19, 22, 37)


def test_the_mainboard_filter_drops_every_sme_row():
    parsed = ipo_parser.parse_rows(fixture_rows(), today=CAPTURED_ON)
    mainboard = ipo_parser.mainboard_only(parsed)

    assert len(parsed) == 50
    assert len(mainboard) == 22
    assert all(item.board == "MAINBOARD" for item in mainboard)
    assert any(item.board == "SME" for item in parsed), "the fixture has SME rows"


# --- ingest ----------------------------------------------------------------
async def test_sme_issues_are_never_stored(db_session):
    service = IpoService(db_session, client=FakeSource())

    await service.refresh(today=CAPTURED_ON)
    await db_session.commit()

    stored = await service.ipos.count()
    assert stored > 0
    rows = await service.ipos.not_listed()
    assert all(row.board == "MAINBOARD" for row in rows)
    # 22 mainboard rows, of which 10 are already listed and skipped.
    assert stored == 12


async def test_an_ipo_first_seen_already_listed_is_not_stored_at_all(db_session):
    """The Listed tab is forward-only, and there is no backfill to filter."""
    service = IpoService(db_session, client=FakeSource())

    result = await service.refresh(today=CAPTURED_ON)
    await db_session.commit()

    assert result.skipped_already_listed == 10
    listed = await service.listed(CAPTURED_ON)
    assert listed == [], "day one shows nothing, by design"


async def test_an_ipo_watched_through_its_listing_appears_in_the_listed_tab(db_session):
    """The other half of forward-only: what it DOES collect."""
    rows = fixture_rows()
    # First sight: still open.
    opening = [dict(row) for row in rows if row.get("~id") == 2305]
    service = IpoService(db_session, client=FakeSource(rows=opening))
    await service.refresh(today=CAPTURED_ON)
    await db_session.commit()

    # Later: the same IPO, now listed at a premium.
    listed_row = dict(opening[0])
    listed_row["~ipo_status1"] = "LP"
    listed_row["Name"] = (
        '<a href="/gmp/nse-ipo/2305/">NSE</a> '
        '<span class="text-success"><small><b>L@1900.00 (6.44%)</b></small></span>'
    )
    service.client = FakeSource(rows=[listed_row])
    await service.refresh(today=CAPTURED_ON)
    await db_session.commit()

    listed = await service.listed(CAPTURED_ON)
    assert [view.company_name for view in listed] == ["NSE"]
    assert listed[0].listing_price == Decimal("1900.00")
    assert listed[0].listing_gain == Decimal("115.0000")
    assert round(listed[0].listing_gain_percent, 2) == Decimal("6.44")


async def test_a_refresh_appends_to_the_gmp_series_rather_than_overwriting(db_session):
    row = next(r for r in fixture_rows() if r.get("~id") == 2305)
    service = IpoService(db_session, client=FakeSource(rows=[dict(row)]))

    await service.refresh(today=CAPTURED_ON)
    moved = dict(row)
    moved["GMP"] = "&#8377;<b>75</b> (4.20%)"
    moved["~gmp_percent_calc"] = "4.20"
    service.client = FakeSource(rows=[moved])
    await service.refresh(today=CAPTURED_ON)
    await db_session.commit()

    stored = await service.ipos.get_by_source_id(2305)
    history = await service.gmp_history(stored.id)
    assert [reading.gmp for reading in history] == [Decimal("75.0000"), Decimal("61.0000")]


async def test_a_targeted_refresh_writes_only_the_ipos_it_was_asked_about(db_session):
    """The hourly sweep touches today's closers, not the whole board."""
    service = IpoService(db_session, client=FakeSource())
    await service.refresh(today=CAPTURED_ON)
    await db_session.commit()
    before = await service.ipos.count()

    result = await service.refresh(today=CAPTURED_ON, only_source_ids=[2305])

    assert result.gmp_readings == 1
    assert result.updated == 1
    assert await service.ipos.count() == before


# --- the tabs --------------------------------------------------------------
def test_closing_next_skips_the_weekend_and_claims_no_holiday_calendar():
    friday = date(2026, 9, 18)
    assert next_open_day(friday) == date(2026, 9, 21)  # Monday
    assert next_open_day(date(2026, 9, 21)) == date(2026, 9, 22)
    assert next_open_day(date(2026, 9, 19)) == date(2026, 9, 21)  # Sat -> Mon


async def test_closing_today_and_closing_next_split_on_the_close_date(db_session):
    service = IpoService(db_session, client=FakeSource())
    await service.refresh(today=CAPTURED_ON)
    await db_session.commit()

    # 2026-09-21 is a Monday; the fixture has NSE closing that day.
    sunday = date(2026, 9, 20)
    today = await service.closing_today(sunday)
    nxt = await service.closing_next(sunday)

    assert today == []
    # Two mainboard IPOs close that Monday in the captured response.
    assert sorted(view.company_name for view in nxt) == [
        "NSE",
        "Sonaselection India",
    ]


# --- the rule the whole feature exists for ---------------------------------
@pytest.fixture
async def one_closing_today(db_session):
    """One mainboard IPO, closing today, nothing done about it yet."""
    row = dict(next(r for r in fixture_rows() if r.get("~id") == 2305))
    today = date(2026, 9, 21)
    row["~Srt_Close"] = today.isoformat()
    service = IpoService(db_session, client=FakeSource(rows=[row]))
    await service.refresh(today=today)
    await db_session.commit()
    stored = await service.ipos.get_by_source_id(2305)
    return service, stored, today


async def test_applied_alone_does_not_stop_the_reminders(one_closing_today):
    """An unaccepted UPI mandate is a failed application."""
    service, ipo, today = one_closing_today

    await service.set_action(
        ipo_id=ipo.id, action=ACTION_APPLIED, value=True, user_id=1
    )
    outstanding = await service.outstanding_closing_today(today)

    assert [view.company_name for view in outstanding] == ["NSE"]
    assert outstanding[0].pending_steps == ["applied, mandate not accepted"]


async def test_applied_AND_accepted_stops_the_reminders(one_closing_today):
    service, ipo, today = one_closing_today

    await service.set_action(ipo_id=ipo.id, action=ACTION_APPLIED, value=True, user_id=1)
    await service.set_action(
        ipo_id=ipo.id, action=ACTION_MANDATE_ACCEPTED, value=True, user_id=1
    )

    assert await service.outstanding_closing_today(today) == []


async def test_reject_stops_the_reminders_and_is_reversible(one_closing_today):
    service, ipo, today = one_closing_today

    await service.set_action(ipo_id=ipo.id, action=ACTION_REJECTED, value=True, user_id=1)
    assert await service.outstanding_closing_today(today) == []

    # Terminal for the reminders, not for the IPO: undoing it restores exactly
    # the state that was there before.
    await service.set_action(ipo_id=ipo.id, action=ACTION_REJECTED, value=False, user_id=1)
    outstanding = await service.outstanding_closing_today(today)
    assert [view.company_name for view in outstanding] == ["NSE"]
    assert outstanding[0].pending_steps == ["not applied"]


async def test_a_mis_tap_is_undoable_and_the_log_keeps_both(one_closing_today):
    """Append-only: toggling off does not delete the row that turned it on."""
    service, ipo, today = one_closing_today

    await service.set_action(ipo_id=ipo.id, action=ACTION_APPLIED, value=True, user_id=1)
    await service.set_action(ipo_id=ipo.id, action=ACTION_APPLIED, value=False, user_id=1)

    history = await service.action_history(ipo.id)
    assert [row.value for row in history] == [False, True]
    assert all(row.user_id == 1 for row in history)
    views = await service.closing_today(today)
    assert views[0].applied is False


# --- staleness -------------------------------------------------------------
async def test_a_reading_older_than_the_cadence_is_labelled_stale(one_closing_today):
    service, ipo, today = one_closing_today

    fresh = await service.closing_today(today)
    assert fresh[0].gmp.is_stale is False

    # Two hours later, with no successful refresh in between. An IPO closing
    # today is refreshed hourly, so 90 minutes is already past its cadence.
    later = utc_now() + timedelta(hours=2)
    stale = await service.closing_today(today, now=later)
    assert stale[0].gmp.is_stale is True
    assert "older than the expected refresh cadence" in stale[0].gmp.stale_reason


async def test_an_ipo_with_no_reading_at_all_is_stale_rather_than_zero(db_session):
    service = IpoService(db_session, client=FakeSource())
    today = date(2026, 9, 21)
    row = await service.ipos.add(
        source_id=999,
        company_name="Never Fetched",
        board="MAINBOARD",
        status="OPEN",
        close_date=today,
        first_seen_at=utc_now(),
        last_seen_at=utc_now(),
        first_seen_status="OPEN",
    )
    await db_session.commit()

    views = await service.closing_today(today)

    assert views[0].id == row.id
    assert views[0].gmp.gmp is None
    assert views[0].gmp.is_stale is True


# --- the hourly window -----------------------------------------------------
@pytest.mark.parametrize(
    "moment, expected",
    [
        (datetime(2026, 9, 21, 9, 59), None),
        (datetime(2026, 9, 21, 10, 0), "2026-09-21|10"),
        (datetime(2026, 9, 21, 14, 20), "2026-09-21|14"),
        (datetime(2026, 9, 21, 17, 0), "2026-09-21|17"),
        # 17:01 is STILL the 17:00 slot, not a new one. The last reminder of a
        # closing day is the most valuable, so a process that came up at 17:01
        # sends it late rather than not at all; the slot guard is what stops it
        # going out twice.
        (datetime(2026, 9, 21, 17, 1), "2026-09-21|17"),
        (datetime(2026, 9, 21, 18, 0), None),
    ],
)
def test_the_hourly_window_boundaries(moment, expected):
    assert IpoScheduler()._sweep_due_slot(moment) == expected  # noqa: SLF001


def test_the_daily_refresh_has_no_upper_bound():
    """Recording what the source says is valid at any hour; the SLOT guard is
    what stops a second run, not a window. Same choice the swing nightly
    makes, and the reason its window is unbounded at the top."""
    scheduler = IpoScheduler()
    assert scheduler._daily_refresh_at().hour == 13  # noqa: SLF001


# --- the guards ------------------------------------------------------------
async def test_a_restart_does_not_resend_the_hour_that_already_went_out(db_session):
    """The in-memory guard dies with the process; the row does not.

    This is the failure that cost 3,500 wasted Dhan requests in the swing
    nightly: a restart cleared the memory and the job ran again.
    """
    runs = IpoJobRunRepository(db_session)
    slot = "2026-09-21|14"
    await runs.record(
        job_kind=JOB_CLOSING_SWEEP,
        slot_key=slot,
        started_at=utc_now(),
        finished_at=utc_now(),
        ok=True,
        detail="1 outstanding, reminder raised",
    )
    await db_session.commit()

    # A brand-new scheduler: exactly what a restart produces.
    scheduler = IpoScheduler()
    assert scheduler._done == set()  # noqa: SLF001
    assert await scheduler._already_done(JOB_CLOSING_SWEEP, slot) is True  # noqa: SLF001
    assert await scheduler._sweep_is_due(datetime(2026, 9, 21, 14, 20)) is None  # noqa: SLF001


async def test_a_failed_run_does_not_consume_its_slot(db_session):
    """A sweep that could not reach the source gets to try again at 14:02."""
    runs = IpoJobRunRepository(db_session)

    recorded = await runs.record(
        job_kind=JOB_CLOSING_SWEEP,
        slot_key="2026-09-21|14",
        started_at=utc_now(),
        finished_at=utc_now(),
        ok=False,
        detail="the source answered 503",
    )
    await db_session.commit()

    assert recorded is None
    assert await runs.has_run(JOB_CLOSING_SWEEP, "2026-09-21|14") is False


def test_a_failing_job_backs_off_instead_of_retrying_every_tick():
    scheduler = IpoScheduler()
    now = datetime(2026, 9, 21, 14, 0)

    assert scheduler._may_attempt(JOB_CLOSING_SWEEP, "s", now) is True  # noqa: SLF001
    assert scheduler._may_attempt(  # noqa: SLF001
        JOB_CLOSING_SWEEP, "s", now + timedelta(seconds=30)
    ) is False
    assert scheduler._may_attempt(  # noqa: SLF001
        JOB_CLOSING_SWEEP, "s", now + timedelta(minutes=6)
    ) is True


# --- the reminder ----------------------------------------------------------
def test_the_dedupe_key_carries_the_ist_date_and_the_hour():
    """The hourly cadence survives; a double tick inside the hour does not."""
    assert dedupe_key_for(datetime(2026, 9, 21, 14, 0)) == "ipo|closing-reminder|2026-09-21|14"
    assert dedupe_key_for(datetime(2026, 9, 21, 14, 59)) == dedupe_key_for(
        datetime(2026, 9, 21, 14, 2)
    )
    assert dedupe_key_for(datetime(2026, 9, 21, 15, 0)) != dedupe_key_for(
        datetime(2026, 9, 21, 14, 0)
    )


async def test_the_reminder_body_says_what_is_pending_and_how_old_the_gmp_is(
    one_closing_today,
):
    service, ipo, today = one_closing_today
    await service.set_action(ipo_id=ipo.id, action=ACTION_APPLIED, value=True, user_id=1)
    outstanding = await service.outstanding_closing_today(today)

    body = build_body(outstanding, datetime(2026, 9, 21, 14, 0))

    assert "NSE" in body
    assert "21 Sep 2026" in body           # the close date
    assert "₹1,785.00" in body             # the issue price
    assert "₹61.00" in body                # the GMP
    assert "captured" in body              # and when it was captured
    assert "applied, mandate not accepted" in body


async def test_nothing_outstanding_sends_nothing(db_session):
    """There is deliberately no all-clear message."""
    outcome = await IpoReminderService(db_session).send_sweep(
        [], datetime(2026, 9, 21, 14, 0)
    )

    assert outcome.sent is False
    assert outcome.outstanding == 0


async def test_a_second_sweep_in_the_same_hour_collapses(one_closing_today):
    service, ipo, today = one_closing_today
    reminders = IpoReminderService(service.session)
    outstanding = await service.outstanding_closing_today(today)
    slot = datetime(2026, 9, 21, 14, 0)

    first = await reminders.send_sweep(outstanding, slot)
    await service.session.commit()
    second = await reminders.send_sweep(outstanding, slot)
    await service.session.commit()

    assert first.sent is True
    assert second.sent is False
    assert "collapsed" in second.reason


async def test_the_next_hour_is_a_new_message(one_closing_today):
    """The cadence is the point: collapsing every repeat would silence it."""
    service, ipo, today = one_closing_today
    reminders = IpoReminderService(service.session)
    outstanding = await service.outstanding_closing_today(today)

    first = await reminders.send_sweep(outstanding, datetime(2026, 9, 21, 14, 0))
    await service.session.commit()
    second = await reminders.send_sweep(outstanding, datetime(2026, 9, 21, 15, 0))
    await service.session.commit()

    assert first.sent is True
    assert second.sent is True


async def test_the_reminder_row_names_no_strategy(one_closing_today):
    """No strategy owns an IPO, and a strategy's Alerts tab filters on this."""
    service, ipo, today = one_closing_today
    outstanding = await service.outstanding_closing_today(today)

    await IpoReminderService(service.session).send_sweep(
        outstanding, datetime(2026, 9, 21, 14, 0)
    )
    await service.session.commit()

    from src.connections.database.db_operations.alert_repository import AlertRepository

    rows = await AlertRepository(service.session).recent(limit=10)
    ipo_rows = [row for row in rows if row.kind == "IPO"]
    assert len(ipo_rows) == 1
    assert ipo_rows[0].strategy_key is None
