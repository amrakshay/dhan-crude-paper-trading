"""Which NSE SPAN file is in force -- the choice that made our first figures
look 25% off Dhan's (tests/test_options_dhan_parity.py has the evidence)."""
from datetime import date

from src.options.services import nse_span_client
from src.options.services.span_store import BEGINNING_OF_DAY, LATEST, SpanStore

MON, TUE, WED = date(2026, 11, 23), date(2026, 11, 24), date(2026, 11, 25)
LOCAL = {
    (MON, "i1"): "a", (MON, "i5"): "b", (MON, "s"): "c",
    (WED, "i1"): "d",   # published Monday evening, for Wednesday's session
}


def test_dhan_parity_uses_the_latest_sessions_beginning_of_day_file():
    """Tuesday 24-Nov was a holiday: Dhan was still on Monday's i1, though
    Wednesday's i1 was already published."""
    assert SpanStore._choose(LOCAL, TUE, BEGINNING_OF_DAY) == (MON, "i1")


def test_on_the_session_itself_its_own_beginning_of_day_file_is_used():
    assert SpanStore._choose(LOCAL, WED, BEGINNING_OF_DAY) == (WED, "i1")


def test_latest_takes_the_newest_snapshot_of_any_kind():
    assert SpanStore._choose(LOCAL, TUE, LATEST) == (WED, "i1")
    assert SpanStore._choose({k: v for k, v in LOCAL.items() if k[0] == MON}, TUE, LATEST) == (MON, "s")


def test_with_no_beginning_of_day_file_anything_is_better_than_nothing():
    assert SpanStore._choose({(MON, "s"): "c"}, TUE, BEGINNING_OF_DAY) == (MON, "s")


def test_beginning_of_day_candidates_skip_weekends_and_ask_only_for_i1():
    saturday = date(2026, 11, 28)
    wanted = SpanStore._candidates(saturday, BEGINNING_OF_DAY)
    assert wanted[0] == (date(2026, 11, 27), "i1")
    assert all(variant == "i1" and day.weekday() < 5 for day, variant in wanted)


def test_the_client_names_every_snapshot_and_refuses_anything_else():
    assert nse_span_client.url_for(MON, "i1").endswith("/nsccl.20261123.i1.zip")
    assert nse_span_client.url_for(MON).endswith("/nsccl.20261123.s.zip")
    try:
        nse_span_client.url_for(MON, "../x")
    except ValueError:
        pass
    else:
        raise AssertionError("an unknown variant must not reach a URL")


# --- intraday snapshots (Phase 5) ----------------------------------------------
def test_under_latest_each_intraday_snapshot_replaces_the_one_before():
    session = {(WED, "i1"): "a", (WED, "i2"): "b"}
    assert SpanStore._choose(session, WED, LATEST) == (WED, "i2")
    session[(WED, "i3")] = "c"
    assert SpanStore._choose(session, WED, LATEST) == (WED, "i3")
    # ...while Dhan's beginning-of-day rule keeps the session's i1 all day.
    assert SpanStore._choose(session, WED, BEGINNING_OF_DAY) == (WED, "i1")


def test_mid_session_latest_asks_nse_for_the_next_snapshot_before_older_ones():
    wanted = SpanStore._candidates(WED, LATEST)
    todays = [variant for day, variant in wanted if day == WED]
    assert todays == ["s", "i5", "i4", "i3", "i2", "i1"]


def test_the_refresher_wakes_just_after_each_snapshot_not_half_an_hour_late():
    from datetime import datetime

    from src.core.time_utils import IST
    from src.options.services.span_store import seconds_until_next_pass

    at_1100 = datetime(2026, 11, 25, 11, 0, tzinfo=IST)
    assert seconds_until_next_pass(at_1100, 1800) == 600          # 11:10, not 11:30
    at_2200 = datetime(2026, 11, 25, 22, 0, tzinfo=IST)
    assert seconds_until_next_pass(at_2200, 1800) == 1800         # nothing due: the interval
    friday_night = datetime(2026, 11, 27, 21, 30, tzinfo=IST)
    assert seconds_until_next_pass(friday_night, 10 ** 6) == (datetime(2026, 11, 30, 11, 10, tzinfo=IST) - friday_night).total_seconds()


def test_the_status_says_which_snapshot_is_in_force():
    from src.options.services.span_store import SNAPSHOT_LABELS

    assert SNAPSHOT_LABELS["i1"] == "beginning of day"
    assert "intraday" in SNAPSHOT_LABELS["i2"]
