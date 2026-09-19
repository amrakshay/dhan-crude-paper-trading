"""What the BTST page reads, and the things about it that have already been wrong.

`BtstService` and `BtstHealthService` decide nothing, so these are assertions
about a PAYLOAD -- but the payload is the only thing standing between an
operator and a page that reads plausibly while saying something untrue. Three
classes of that, and one of each has already happened here:

  * **A figure that is not measured must not arrive as a number.** An overnight
    gap on a position still held, an equity figure `BalanceService` withheld, a
    funnel stage an older record never measured.
  * **A fact composed in two places drifts.** Every threshold on the page comes
    from the YAML by way of this payload; the one sentence that insisted on
    that rule was itself restating the numbers until 2026-09-19.
  * **A fact borrowed from a shared component must say whose it is.** One clock
    runs every automated strategy and one `progress` field describes whatever
    long job is in flight, so a page rendering either without attribution
    reports another strategy's work as its own.

The page must also render with the strategy OFF, with no portfolio in scope and
with nothing ever traded, because that is the ordinary state today.
"""
from decimal import Decimal

import pytest

from src.btst.services.btst_health_service import BtstHealthService
from src.btst.services.btst_parameters import BtstParameters
from src.btst.services.btst_service import BtstService
from src.portfolios.services.portfolio_service import PortfolioService
from src.strategies.services.strategy_registry import get_strategy_registry

STRATEGY = "nse-btst-overnight"


@pytest.fixture
def definition():
    return get_strategy_registry().require(STRATEGY)


@pytest.fixture
def parameters(definition):
    return BtstParameters.from_definition(definition)


def _service(session) -> BtstService:
    return BtstService.for_strategy(session, STRATEGY)


# --- the Live tab's payload -------------------------------------------------


async def test_the_status_payload_renders_with_nothing_traded_and_no_portfolio(
    db_session,
):
    """The ordinary state today, and the one the page is most often opened in."""
    payload = await _service(db_session).status(None)

    assert payload["holdings"] == []
    assert payload["lastScan"] is None
    assert payload["lastExit"] is None
    # Not zero. No portfolio in scope is not a book worth nothing.
    assert payload["balance"] is None
    # Everything the strip needs is still present, so the page renders rather
    # than falling back to a spinner that never resolves.
    assert payload["scheduler"]["recent"] == []
    assert payload["subscriptionWindow"] is not None
    assert payload["closingAuction"] is not None


async def test_the_status_payload_survives_the_strategy_being_switched_off(
    db_session,
):
    """Off is not broken, and history never moves."""
    registry = get_strategy_registry()
    registry.set_enabled(STRATEGY, False)
    try:
        payload = await _service(db_session).status(None)
    finally:
        registry.set_enabled(STRATEGY, True)

    assert payload["enabled"] is False
    # The rules, the times and the clock are still reported: an operator
    # switching it off still has to be able to see what it WOULD do.
    assert payload["policies"]
    assert payload["settings"]
    assert payload["schedule"]["scanAtIst"]


async def test_this_strategy_is_absent_from_the_schedulers_own_schedule_list(
    db_session,
):
    """The reason `_scheduler_payload` exists at all.

    `SwingScheduler.status()["schedules"]` builds a row per automated strategy
    by constructing `SwingParameters` for it and SKIPPING any definition that
    raises -- and this one raises, because its YAML has no `breadth_lower`. The
    rotation's page finds its own row by key and never noticed. A BTST strip
    built the same way would have found nothing and shown an empty clock
    forever, so this pins the reason rather than leaving the next reader to
    rediscover it.
    """
    from src.swing.services.scheduler import get_swing_scheduler

    rows = get_swing_scheduler().status()["schedules"]
    assert all(row["strategyKey"] != STRATEGY for row in rows)

    # And the payload supplies the times anyway, from this module's own
    # schedule rather than from that list.
    payload = await _service(db_session).status(None)
    assert payload["schedule"]["scanAtIst"]
    assert payload["schedule"]["exitAtIst"]


async def test_the_clock_says_it_is_shared_and_the_runs_are_filtered_to_this_strategy(
    db_session,
):
    """One clock, several strategies. A page must not claim another's work."""
    from src.strategies.services.scheduling import JobRun
    from src.core.time_utils import ist_now
    from src.swing.services.scheduler import get_swing_scheduler

    scheduler = get_swing_scheduler()
    mine = JobRun(strategy_key=STRATEGY, kind="SCAN", at=ist_now(), detail="mine")
    theirs = JobRun(
        strategy_key="nse-swing-momentum", kind="NIGHTLY", at=ist_now(),
        detail="not mine",
    )
    scheduler.history.extend([theirs, mine])
    try:
        payload = await _service(db_session).status(None)
    finally:
        for entry in (mine, theirs):
            if entry in scheduler.history:
                scheduler.history.remove(entry)

    kinds = {row["strategyKey"] for row in payload["scheduler"]["recent"]}
    assert kinds == {STRATEGY}
    assert "another" in payload["scheduler"]["clockNote"]


def test_a_long_job_on_the_shared_clock_is_attributed_to_whoever_owns_it(
    db_session,
):
    """`progress` has no owner on it, and only the ROTATION ever sets it.

    Rendering it unattributed would put the rotation's twelve-minute bar
    refresh on this strategy's page as though this one were running it. This
    strategy has no long job of its own; the note is what makes showing the bar
    honest rather than misleading.
    """
    from src.swing.services.scheduler import get_swing_scheduler

    scheduler = get_swing_scheduler()
    scheduler.progress = {"done": 10, "total": 500, "item": "ABB", "startedAtIst": None}
    try:
        block = _service(db_session)._scheduler_payload()
    finally:
        scheduler.progress = None

    assert block["progress"]["total"] == 500
    assert block["progressNote"] is not None
    assert "rotation" in block["progressNote"]

    # And no bar means NO NOTE, rather than a note about a job that is not
    # running: `JobProgress` renders nothing for null progress, and a caption
    # under nothing would be a caption about nothing.
    assert _service(db_session)._scheduler_payload()["progressNote"] is None


async def test_the_closing_auction_note_says_the_scan_is_INSIDE_the_window(
    db_session,
):
    """The reason `fno.exclude` exists, stated rather than left to be noticed.

    Continuous cash trading ends at 15:15 for F&O-eligible names and the scan
    is at 15:20. Those two configured times being on the wrong side of each
    other is the whole justification for excluding those names, and the payload
    computes the relationship rather than asserting a sentence that would stay
    put if either time moved.
    """
    payload = await _service(db_session).status(None)
    auction = payload["closingAuction"]

    assert auction["scanIsInsideTheWindow"] is True
    assert auction["continuousClose"] < auction["scanAtIst"]
    assert "INSIDE" in auction["note"]


async def test_the_four_figures_arrive_together_or_not_at_all(db_session):
    """Four figures, never one -- and equity is the one that can be withheld."""
    portfolio = await PortfolioService(db_session).create(
        name="BTST payload", description="", strategy_keys=[STRATEGY],
        opening_balance=Decimal("500000"),
    )
    await db_session.commit()

    payload = await _service(db_session).status(portfolio.id)
    balance = payload["balance"]

    assert set(balance) >= {"cash", "blockedMargin", "available", "equity"}
    # Always labelled an estimate, because it is a configured approximation
    # rather than what a broker would hold.
    assert balance["marginIsEstimate"] is True


# --- the "How it works" tab -------------------------------------------------


async def test_the_configuration_tab_restates_no_number_in_its_own_disclaimer(
    db_session, parameters,
):
    """The sentence insisting the numbers live in the YAML used to restate them.

    Every threshold in `notEditable` is now composed from the parameters, so
    changing the file changes the sentence. A literal "55-day" there would
    outlive the configuration it describes -- which is exactly what the
    sentence claims cannot happen.
    """
    payload = _service(db_session).configuration()

    assert (
        f"{parameters.breakout_lookback_sessions}-session breakout"
        in payload["notEditable"]
    )
    assert f"{parameters.slots} slots" in payload["notEditable"]
    # And the things somebody comes looking for and will not find are NAMED,
    # rather than left inside a range of B-numbers.
    assert any("EXIT ITSELF" in one for one in payload["alsoNotEditable"])
    assert any("THE STOP" in one for one in payload["alsoNotEditable"])


async def test_every_funnel_stage_the_journal_records_is_one_the_diagram_can_draw(
    db_session,
):
    """The explainer's funnel and the scan's own census, keyed identically.

    The first draft of that diagram carried its own list of stage keys and five
    of nine matched nothing, so every bar silently read "not measured" against
    a scan that had measured all of them. Both now come from `FILTER_STAGES`.
    """
    from src.btst.services.scan_service import FILTER_STAGES

    stages = _service(db_session).explain()["funnelStages"]

    assert [one["key"] for one in stages] == [key for key, _ in FILTER_STAGES]
    # And each says what it TESTS, which is what a page called "How it works"
    # is for. An empty one would draw a bar with no caption.
    assert all(one["test"] for one in stages)


async def test_the_timeline_can_be_drawn_entirely_from_the_payload(db_session):
    """Five configured times, none of them typed into JSX."""
    payload = _service(db_session).explain()

    assert payload["marketHours"]["open"]
    assert payload["marketHours"]["close"]
    assert payload["marketHours"]["closingAuction"]["continuousClose"]
    assert payload["subscription"]["windowOpensAtIst"]
    assert payload["schedule"]["scanAtIst"]
    assert payload["schedule"]["exitAtIst"]


# --- the Health tab ---------------------------------------------------------


async def test_the_health_payload_reports_no_portfolio_as_OFF_not_as_a_problem(
    db_session,
):
    """Off is not broken, applied to money.

    No portfolio in scope is a state an operator is in, not a fault, so it gets
    the third tone rather than the red one reserved for something that should
    be working and is not.
    """
    payload = await BtstHealthService.for_strategy(db_session, STRATEGY).payload(None)

    assert payload["money"]["tone"] == "off"
    assert payload["money"]["balance"] is None


async def test_the_health_payload_still_states_the_three_things_it_cannot_know(
    db_session,
):
    """Adding panels must not quietly drop the caveats under them."""
    payload = await BtstHealthService.for_strategy(db_session, STRATEGY).payload(None)

    assert len(payload["notes"]) == 3
    assert all(note for note in payload["notes"])
    # The stop watcher's ABSENCE is stated rather than left blank: a row
    # reading "not running" would invite somebody to fix it.
    assert payload["working"]["stopWatcher"]["applicable"] is False
