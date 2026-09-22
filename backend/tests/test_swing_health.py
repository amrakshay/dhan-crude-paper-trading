"""The Swing Momentum Health tab's endpoint.

`GET /api/swing/health` answers a different question from `/api/swing/status`:
not "what does the rule say right now" but "is the machinery under it working,
and what has it actually been doing".

These tests assert the behaviour the tab exists for, and in particular the
things it must NOT do:

* it must be readable ONLY by an administrator, unlike every other read on
  this router -- the rest of the page is a journal and a journal is history,
  but this reports live machinery state and recent log records;
* it must render for a strategy that is switched OFF and has never traded,
  as "nothing has happened yet" rather than as "everything is broken";
* an absent figure must be absent, not zero;
* it must place nothing and change nothing.
"""
import pytest

from tests.conftest import login_as

STRATEGY = "nse-swing-momentum"


async def test_the_health_endpoint_is_admin_only(auth_client):
    """The one read on this router a ROLE_USER may not make.

    `/swing` itself stays open to any signed-in user and so does every other
    endpoint on it. This one is refused in the API, not merely hidden in the
    sidebar -- `backend/CLAUDE.md` section 10: `require_admin` is the gate.
    """
    created = await auth_client.post(
        "/api/users",
        json={
            "email": "reader@abc.com",
            "firstName": "Read",
            "lastName": "Only",
            "password": "Sup3rSecret!pass",
            "role": "ROLE_USER",
            "mustChangePassword": False,
        },
    )
    assert created.status_code in (200, 201), created.text

    import httpx

    import main

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await login_as(client, "reader@abc.com", "Sup3rSecret!pass")

        # Everything else on the page stays readable for this user...
        assert (await client.get("/api/swing/status")).status_code == 200
        assert (await client.get("/api/swing/history")).status_code == 200
        assert (await client.get("/api/swing/explain")).status_code == 200

        # ...and the health payload is not.
        assert (await client.get("/api/swing/health")).status_code == 403


async def test_an_anonymous_caller_cannot_read_the_health_endpoint(api_client):
    assert (await api_client.get("/api/swing/health")).status_code == 401


async def test_it_reads_for_a_strategy_that_has_never_traded(auth_client):
    """Nothing has happened yet is a state, not a failure.

    Every number on this payload is zero or absent on a fresh installation.
    The tab has to read as "nothing has happened yet", which means the
    endpoint must answer at all rather than erroring its way through a book
    that does not exist.
    """
    response = await auth_client.get(f"/api/swing/health?strategyKey={STRATEGY}")
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["strategyKey"] == STRATEGY
    assert payload["nowIst"]

    working = payload["working"]
    assert set(working) >= {"enabled", "automated", "armed", "market", "jobs", "feed"}
    assert [row["name"] for row in working["jobs"]["rows"]] == [
        "swing-scheduler",
        "swing-stop-monitor",
    ]

    # No run has been journalled and none has run in this process.
    assert payload["schedule"]["journal"]["recordedSessions"] == 0
    assert payload["schedule"]["journal"]["latestAnalysis"] is None
    assert payload["schedule"]["journal"]["latestOrderPlacement"] is None
    assert payload["schedule"]["recent"] == []

    holdings = payload["holdings"]
    assert holdings["stops"]["active"] == 0
    assert holdings["stops"]["triggeredWaiting"] == 0


async def test_it_reads_with_the_strategy_switched_off(auth_client):
    """Off must not break the page that says whether it is off.

    Nothing on this payload computes a ranking, resolves a subscription or
    needs the feed, precisely so that switching the strategy off leaves the
    tab readable -- which is when an operator is most likely to be looking at
    it.
    """
    from src.strategies.services.strategy_registry import get_strategy_registry

    registry = get_strategy_registry()
    registry.set_enabled(STRATEGY, False)
    try:
        response = await auth_client.get(f"/api/swing/health?strategyKey={STRATEGY}")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["working"]["enabled"] is False

        # Every panel still answers, so the tab reads rather than erroring --
        # which is when an operator is most likely to be looking at it.
        for block in ("working", "schedule", "data", "holdings", "rules", "problems"):
            assert payload[block] is not None

        # Neither swing task is EXPECTED with nothing automated enabled, so
        # neither is reported as a failure. A health page that invented two
        # missing tasks the moment a strategy was switched off would be crying
        # wolf on the state an operator chose.
        assert payload["working"]["jobs"]["healthy"] is True
        assert all(
            row["expected"] is False for row in payload["working"]["jobs"]["rows"]
        )
    finally:
        registry.set_enabled(STRATEGY, True)


async def test_no_feed_subscription_means_no_count_rather_than_zero(auth_client):
    """Absent from the feed's per-strategy map is not zero instruments.

    "The subscription was built and matched nothing" and "no subscription was
    ever built for this strategy" are different facts, and the page colours
    and words them differently. The invariant the payload has to keep is that
    the second one carries no count at all.
    """
    payload = (
        await auth_client.get(f"/api/swing/health?strategyKey={STRATEGY}")
    ).json()
    feed = payload["working"]["feed"]

    if feed["subscribed"]:
        assert feed["instrumentCount"] is not None
    else:
        assert feed["instrumentCount"] is None


async def test_a_missing_figure_is_absent_rather_than_zero(auth_client):
    """Undefined is not zero, on every block that can be undefined.

    With no bars stored, "how stale is the newest session" has no answer. A
    daysBehind of 0 there would read as "perfectly fresh" about a table that
    is empty, which is the worst possible way to say "there is nothing here".
    """
    response = await auth_client.get(f"/api/swing/health?strategyKey={STRATEGY}")
    payload = response.json()

    staleness = payload["data"]["staleness"]
    assert staleness["newestSession"] is None
    assert staleness["daysBehind"] is None
    assert staleness["wouldRefuse"] is None
    assert staleness["refusalReason"] is None

    # Nothing can be behind anything when nothing is stored.
    assert payload["data"]["laggingSymbols"]["count"] is None

    # No portfolio was asked for, so there is no book to report and no money.
    holdings = payload["holdings"]
    assert holdings["portfolioId"] is None
    assert holdings["positions"] is None
    assert holdings["unmarkedPositions"] is None
    assert holdings["money"] is None
    assert holdings["positionsNote"]


async def test_the_money_block_carries_four_figures_never_one(auth_client):
    """Cash, blocked margin, available and equity, from `BalanceService` alone.

    The one place these are computed. A second implementation anywhere is a
    disagreement waiting to happen, so this asserts the shape comes through
    rather than being re-derived for a health panel.
    """
    portfolios = await auth_client.get("/api/portfolios")
    portfolio_id = portfolios.json()["portfolios"][0]["id"]

    response = await auth_client.get(
        f"/api/swing/health?strategyKey={STRATEGY}&portfolioId={portfolio_id}"
    )
    assert response.status_code == 200, response.text
    money = response.json()["holdings"]["money"]

    assert money is not None
    assert set(money) >= {"cash", "blockedMargin", "available", "equity"}
    # Always labelled an estimate, wherever it appears.
    assert money["marginIsEstimate"] is True


async def test_the_stop_watcher_counters_say_they_are_process_wide(auth_client):
    """A process-wide number must never sit silently under a strategy heading.

    `SwingStopMonitor` keeps one set of counters across every automated
    strategy. With one module running they are the same figures; with two they
    would be the sum, and nothing about the number itself would say so.
    """
    response = await auth_client.get(f"/api/swing/health?strategyKey={STRATEGY}")
    watcher = response.json()["holdings"]["watcher"]

    assert watcher["countersAreProcessWide"] is True
    assert "ONCE PER PROCESS" in watcher["countersNote"]


async def test_the_rules_block_reports_the_default_beside_what_is_in_force(auth_client):
    """Both, always -- and who last moved each switch.

    A block showing only the YAML would teach a rule nothing obeys; one
    showing only the effective value would hide that a switch had been moved.
    The third column is the audit line, and its absence on an untouched switch
    is a real answer rather than a blank.
    """
    response = await auth_client.get(f"/api/swing/health?strategyKey={STRATEGY}")
    rules = response.json()["rules"]

    assert rules["policies"], "the strategy declares three rule switches"
    for row in rules["policies"]:
        assert "default" in row and "enforced" in row
        assert "updatedAt" in row and "updatedBy" in row
        # Nobody has touched any switch in a fresh database.
        assert row["everChanged"] is False
        assert row["updatedBy"] is None

    for row in rules["settings"]:
        assert "default" in row and "value" in row
        assert row["everChanged"] is False

    assert "not a history" in rules["auditNote"]


async def test_moving_a_switch_shows_who_moved_it(auth_client):
    """The one audit fact this schema can answer, answered.

    There is no history table, so the question is only ever "who set it to
    what it is now". That is what the payload claims and it has to be true.
    """
    changed = await auth_client.put(
        f"/api/strategies/{STRATEGY}/policies/regime.enforce",
        json={"enforced": False},
    )
    assert changed.status_code == 200, changed.text

    response = await auth_client.get(f"/api/swing/health?strategyKey={STRATEGY}")
    rows = {row["key"]: row for row in response.json()["rules"]["policies"]}
    moved = rows["regime.enforce"]

    assert moved["everChanged"] is True
    assert moved["enforced"] is False
    assert moved["default"] is True
    assert moved["updatedAt"]
    assert "trader@abc.com" in moved["updatedBy"]


async def test_every_timestamp_carries_its_offset(auth_client):
    """Naive UTC must not leave this endpoint, on any field.

    Every timestamp in this database is stored naive UTC and the conversion to
    IST happens at the edges. An endpoint that emitted the naive value would
    send `2026-09-18T11:46:38`, which a browser parses as eleven forty-six
    LOCAL -- so the page would report a switch as moved five and a half hours
    before it was, right beside an IST clock reading the correct time. That is
    exactly the plausible-looking wrong number this project cares most about,
    and it shipped once before being caught on the rendered page.
    """
    changed = await auth_client.put(
        f"/api/strategies/{STRATEGY}/policies/regime.enforce",
        json={"enforced": False},
    )
    assert changed.status_code == 200, changed.text

    response = await auth_client.get(f"/api/swing/health?strategyKey={STRATEGY}")
    rows = {row["key"]: row for row in response.json()["rules"]["policies"]}
    stamp = rows["regime.enforce"]["updatedAt"]

    assert stamp.endswith("+05:30"), stamp

    # And it agrees with the clock on the same payload, to the minute. A
    # correctly formatted timestamp five and a half hours out would pass the
    # assertion above on its own.
    from datetime import datetime

    now = datetime.fromisoformat(response.json()["nowIst"])
    assert abs((now - datetime.fromisoformat(stamp)).total_seconds()) < 120


async def test_the_journal_says_when_a_run_HAPPENED_not_only_what_it_decided(
    auth_client, db_session
):
    """`sessionDate` and the run's own clock are different facts.

    The session is the newest stored bar date -- the data a decision was
    computed FROM. WHEN the run happened is not derivable from it and is
    routinely a different day: Dhan publishes a daily bar after the nightly's
    18:15 slot, so a run on Monday evening decides Friday's session and says
    so. Read without a timestamp the journal looks stale when it is being
    accurate, which is what prompted this.

    Carries the offset for the same reason
    `test_every_timestamp_carries_its_offset` does one endpoint over: stored
    naive UTC rendered by a browser as local time reads five and a half hours
    early. That guarantee had never covered this endpoint.
    """
    from datetime import date, datetime

    from src.core.time_utils import to_ist, utc_now
    from src.swing.database.db_operations.swing_session_repository import (
        SwingSessionRepository,
    )

    ran_at = utc_now()
    await SwingSessionRepository(db_session).create(
        strategy_key=STRATEGY,
        portfolio_id=1,
        # A session THREE DAYS before the run, which is the real shape.
        session_date=date(2026, 9, 18),
        run_kind="NIGHTLY",
        status="COMPLETED",
        started_at=ran_at,
        completed_at=ran_at,
        created_at=ran_at,
        updated_at=ran_at,
    )
    await db_session.commit()

    body = (await auth_client.get(f"/api/swing/history?strategyKey={STRATEGY}")).json()
    row = next(one for one in body["sessions"] if one["sessionDate"] == "2026-09-18")

    assert row["startedAtIst"].endswith("+05:30"), row["startedAtIst"]
    assert row["completedAtIst"].endswith("+05:30"), row["completedAtIst"]

    # The instant, not merely the format: an offset bolted onto a naive UTC
    # value would pass the assertion above and still be wrong by 5h30.
    reported = datetime.fromisoformat(row["startedAtIst"])
    assert abs((reported - to_ist(ran_at)).total_seconds()) < 5

    # And it is NOT the session date, which is the whole point of the column.
    assert reported.date() != date(2026, 9, 18)

    # Naive UTC must not leave the endpoint under the old key either.
    assert "startedAt" not in row
    assert "completedAt" not in row


async def test_the_problems_list_is_filtered_to_this_strategys_loggers(auth_client):
    """A component's own warnings, picked by LOGGER NAME rather than by text.

    Every buffered record carries the logger it came from, so `dcpt.swing.*`
    and `dcpt.daily_bars.*` are an exact filter. Matching on the message would
    be a guess.
    """
    from src import log_buffer
    from src.logging_config import get_logger

    handler = log_buffer.get_handler()
    assert handler is not None, "the buffer is installed by configure_logging()"
    handler.clear()

    get_logger("swing.stops").warning("a stop could not be ratcheted")
    get_logger("daily_bars.refresh").warning("one symbol failed to refresh")
    get_logger("orders.service").warning("nothing to do with the rotation")

    response = await auth_client.get(f"/api/swing/health?strategyKey={STRATEGY}")
    problems = response.json()["problems"]

    messages = [record["message"] for record in problems["records"]]
    assert "a stop could not be ratcheted" in messages
    assert "one symbol failed to refresh" in messages
    assert "nothing to do with the rotation" not in messages

    # The counts are of everything the buffer has seen, including the record
    # this strategy does not own -- so they are not the length of the list.
    assert problems["totalSeen"] == 3
    assert "process-scoped" in problems["bufferNote"]
    assert "app.log" in problems["bufferNote"]


async def test_the_health_payload_places_nothing_and_changes_nothing(auth_client):
    """This tab READS. Asserted directly, because it is the point.

    The endpoint touches the scheduler, the stop monitor, the feed manager,
    the balance service and six repositories. Any one of them acquiring a side
    effect would show up here.
    """
    before_orders = (await auth_client.get("/api/orders")).json()
    before_history = (await auth_client.get("/api/swing/history")).json()

    for _ in range(3):
        assert (await auth_client.get("/api/swing/health")).status_code == 200

    assert (await auth_client.get("/api/orders")).json() == before_orders
    assert (await auth_client.get("/api/swing/history")).json() == before_history


async def test_a_discretionary_module_has_no_health_tab(auth_client):
    """MCX crude is unaffected: it decides nothing, so it has no machinery.

    The same refusal `status` gives, for the same reason -- a module with no
    `automation` block has no scheduler, no stop watcher and no journal, and a
    health page about it would be a page of absences.
    """
    response = await auth_client.get("/api/swing/health?strategyKey=mcx-crude-options")
    assert response.status_code == 404, response.text
    assert "discretionary" in response.json()["detail"].lower()


@pytest.mark.parametrize("block", ["working", "schedule", "data", "holdings", "rules"])
async def test_every_block_is_present_even_on_an_empty_installation(
    auth_client, block
):
    """The tab is a fixed set of panels; a missing block would be a blank page."""
    response = await auth_client.get(f"/api/swing/health?strategyKey={STRATEGY}")
    assert response.json()[block] is not None
