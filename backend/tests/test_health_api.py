"""The system health page's backend.

The point of this feature is to say something true about the process, so these
tests mostly assert that it reports a *problem* when there is one: a task that
is not running, a token that has expired, a browser tab that is dropping data.
A health page that reports "healthy" unconditionally is worse than no health
page, because it is trusted.

The secrecy assertions live in tests/test_no_secrets_in_logs.py, alongside the
rest of the no-secrets suite.
"""
import asyncio
import logging
import time

import jwt
import pytest

from src import log_buffer
from src.health.services import health_service, process_stats, task_inspector
from src.logging_config import get_logger


# --- the task table ---------------------------------------------------------
def test_the_expected_task_set_uses_the_real_feed_and_never_both():
    """The feed is dhan-feed (+ watchdog) or synthetic-feed. Never both."""
    live = task_inspector.expected_task_names(is_synthetic=False, feed_running=True)
    synthetic = task_inspector.expected_task_names(is_synthetic=True, feed_running=True)

    assert "dhan-feed" in live and "dhan-feed-watchdog" in live
    assert "synthetic-feed" not in live

    assert "synthetic-feed" in synthetic
    assert "dhan-feed" not in synthetic
    assert "dhan-feed-watchdog" not in synthetic


def test_a_feed_that_never_started_expects_only_the_broadcaster_and_the_workers():
    """No credentials and synthetic off: start() bails deliberately.

    It must not then be reported as eight missing tasks -- nothing is wrong
    with a feed that was never meant to run.
    """
    expected = task_inspector.expected_task_names(is_synthetic=False, feed_running=False)

    assert "broadcaster" in expected
    assert "dhan-feed" not in expected
    assert "greeks-poller" not in expected
    assert "feed-resync" not in expected


async def test_a_task_that_died_is_reported_as_missing_not_merely_absent():
    """The failure this table exists to catch: a task that stopped silently."""
    stop = asyncio.Event()

    async def forever():
        await stop.wait()

    running = asyncio.create_task(forever(), name="order-matcher")
    await asyncio.sleep(0)
    try:
        table = task_inspector.inspect(is_synthetic=True, feed_running=False)
        names = {row["name"]: row for row in table["tasks"]}
        assert names["order-matcher"]["state"] == "running"
        assert "order-matcher" not in table["missing"]
    finally:
        stop.set()
        await running

    table = task_inspector.inspect(is_synthetic=True, feed_running=False)
    names = {row["name"]: row for row in table["tasks"]}
    assert names["order-matcher"]["state"] == "missing"
    assert "order-matcher" in table["missing"]
    assert table["healthy"] is False


async def test_two_copies_of_one_task_are_reported_as_duplicated():
    """Two broadcasters would mean two flushes per interval, silently."""
    stop = asyncio.Event()

    async def forever():
        await stop.wait()

    first = asyncio.create_task(forever(), name="broadcaster")
    second = asyncio.create_task(forever(), name="broadcaster")
    await asyncio.sleep(0)
    try:
        table = task_inspector.inspect(is_synthetic=True, feed_running=False)
        assert "broadcaster" in table["duplicated"]
        assert table["healthy"] is False
    finally:
        stop.set()
        await asyncio.gather(first, second)


async def test_framework_tasks_are_counted_not_listed_as_application_tasks():
    """Starlette names its per-request tasks after the coroutine.

    A shape-based filter ("does not look like Task-N") let those into the
    table as though an unexpected application task were running.
    """
    stop = asyncio.Event()

    async def forever():
        await stop.wait()

    noise = asyncio.create_task(
        forever(),
        name="starlette.middleware.base.BaseHTTPMiddleware.__call__.<locals>.coro",
    )
    await asyncio.sleep(0)
    try:
        table = task_inspector.inspect(is_synthetic=True, feed_running=False)
        assert table["unexpected"] == []
        assert all("starlette" not in row["name"] for row in table["tasks"])
        assert table["transientCount"] >= 1
    finally:
        stop.set()
        await noise


# --- the in-memory problem buffer -------------------------------------------
@pytest.fixture
def clean_buffer():
    handler = log_buffer.get_handler()
    assert handler is not None, "the log buffer should be installed by configure_logging"
    handler.clear()
    yield handler
    handler.clear()


def test_the_buffer_keeps_warnings_and_errors_and_ignores_the_narrative(clean_buffer):
    logger = get_logger("tests.health.levels")

    logger.debug("debug line")
    logger.info("info line")
    logger.warning("warning line")
    logger.error("error line")

    messages = [entry["message"] for entry in clean_buffer.recent()]
    assert any("warning line" in message for message in messages)
    assert any("error line" in message for message in messages)
    assert not any("info line" in message for message in messages)
    assert not any("debug line" in message for message in messages)


def test_the_buffer_returns_newest_first(clean_buffer):
    logger = get_logger("tests.health.order")

    logger.warning("oldest")
    logger.warning("newest")

    messages = [entry["message"] for entry in clean_buffer.recent()]
    assert messages[0] == "newest"
    assert messages[1] == "oldest"


def test_the_buffer_is_bounded_but_the_totals_survive_the_wrap(clean_buffer):
    """A wrapped buffer must not under-report how much went wrong."""
    handler = log_buffer.RecentProblemsHandler(capacity=3)
    logger = logging.getLogger("dcpt.tests.health.bounded")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        for index in range(10):
            logger.warning("line %s", index)

        assert len(handler.recent()) == 3
        # The count is of everything seen, not of what is still held.
        assert handler.counts()["WARNING"] == 10
        assert handler.recent()[0]["message"] == "line 9"
    finally:
        logger.removeHandler(handler)


def test_the_buffer_carries_the_traceback_of_an_exception(clean_buffer):
    logger = get_logger("tests.health.traceback")

    try:
        raise ValueError("something specific went wrong")
    except ValueError:
        logger.exception("a pass failed")

    message = clean_buffer.recent()[0]["message"]
    assert "a pass failed" in message
    assert "ValueError: something specific went wrong" in message


# --- the payload ------------------------------------------------------------
async def test_the_health_payload_states_one_process_one_worker(auth_client):
    response = await auth_client.get("/api/healthcheck/system")

    assert response.status_code == 200, response.text
    process = response.json()["process"]
    assert process["workers"] == 1
    assert "one asyncio event loop" in process["concurrencyModel"]
    assert "no thread pool" in process["concurrencyModel"].lower()


async def test_the_health_payload_reports_uptime_once_the_lifespan_has_run(auth_client):
    """The fixtures drive the ASGI app without its lifespan, so mark it here."""
    process_stats.mark_started()

    response = await auth_client.get("/api/healthcheck/system")

    assert response.status_code == 200
    process = response.json()["process"]
    assert process["startedAtMs"] is not None
    assert process["uptimeSeconds"] >= 0


async def test_the_synthetic_feed_is_not_shown_as_a_healthy_upstream_socket(auth_client):
    """In synthetic mode there is no socket at all, and the card must say so.

    Showing "connected" here would be the same lie the price chart refuses to
    tell about synthetic bars.
    """
    from src.market.services.feed_manager import get_feed_manager

    manager = get_feed_manager()
    was_synthetic = manager.is_synthetic
    manager.is_synthetic = True
    try:
        response = await auth_client.get("/api/healthcheck/system")

        assert response.status_code == 200
        feed = response.json()["upstreamFeed"]
        assert feed["hasUpstreamConnection"] is False
        assert feed["connectionsUsedByThisProcess"] == 0
        assert "No upstream connection" in feed["upstreamNote"]
        # No fake headroom against a cliff that does not apply.
        assert feed["inactivityHeadroomMs"] is None
        assert feed["inactivityTimeoutSeconds"] is None
    finally:
        manager.is_synthetic = was_synthetic


async def test_the_upstream_card_reads_the_message_age_against_the_drop_cliff(auth_client):
    """The raw age is an integer; the headroom is the number to act on.

    The stub is SUBSCRIBED, which is the only state in which the cliff exists:
    Dhan sends data for instruments this process asked for and nothing else, so
    with an empty subscription set the countdown drains on a healthy socket and
    is deliberately withheld (see test_market_feed_watchdog.py).
    """
    from src.market.services.feed_manager import get_feed_manager

    manager = get_feed_manager()
    was_synthetic = manager.is_synthetic
    manager.is_synthetic = False
    original_status = manager.status

    def status_with_a_quiet_feed():
        payload = original_status()
        payload["feed"] = {
            **payload["feed"],
            "state": "CONNECTED",
            "lastMessageAgeMs": 35_000,
            "subscribed": 165,
        }
        return payload

    manager.status = status_with_a_quiet_feed
    try:
        response = await auth_client.get("/api/healthcheck/system")

        assert response.status_code == 200
        body = response.json()
        feed = body["upstreamFeed"]
        assert feed["inactivityTimeoutSeconds"] == 40.0
        # 40s cliff, 35s of silence: 5s left.
        assert feed["inactivityHeadroomMs"] == pytest.approx(5000)
        assert feed["connectionBudget"] == 5
        assert feed["connectionsUsedByThisProcess"] == 1
        assert any("inactivity drop" in problem for problem in body["summary"]["problems"])
    finally:
        manager.status = original_status
        manager.is_synthetic = was_synthetic


async def test_an_expired_dhan_token_is_called_out_in_the_summary(auth_client):
    expired = jwt.encode(
        {"dhanClientId": "1100999888", "exp": int(time.time()) - 60},
        "irrelevant-signing-key",
        algorithm="HS256",
    )
    saved = await auth_client.put(
        "/api/settings",
        json={"syntheticFeed": True, "clientId": "1100999888", "accessToken": expired},
    )
    assert saved.status_code == 200, saved.text

    response = await auth_client.get("/api/healthcheck/system")

    assert response.status_code == 200
    body = response.json()
    assert body["credentials"]["token"]["expired"] is True
    assert body["summary"]["healthy"] is False
    assert any("expired" in problem for problem in body["summary"]["problems"])


async def test_a_browser_tab_dropping_data_is_visible_per_client(auth_client):
    """Today a slow tab's resyncs only reach a human in the disconnect log line."""
    from src.market.services.feed_manager import get_feed_manager

    broadcaster = get_feed_manager().broadcaster
    client = broadcaster.register(None, client_id="deadbeef", user_email="tab@abc.com")
    client.dropped = 4
    client.configure(["565899"], include_depth=False)
    try:
        response = await auth_client.get("/api/healthcheck/system")

        assert response.status_code == 200
        body = response.json()
        rows = {row["clientId"]: row for row in body["browserSockets"]["clients"]}
        assert "deadbeef" in rows
        row = rows["deadbeef"]
        assert row["dropped"] == 4
        assert row["user"] == "tab@abc.com"
        assert row["securityIds"] == ["565899"]
        assert row["includeDepth"] is False
        assert row["connectedForMs"] >= 0
        assert body["browserSockets"]["droppedTotal"] >= 4
        assert any("resync" in problem for problem in body["summary"]["problems"])
    finally:
        broadcaster.unregister(client)


async def test_rejected_websocket_handshakes_are_counted(auth_client):
    """Logged but never counted before, so an auth problem looked like a dead page."""
    from src.market.services.feed_manager import get_feed_manager

    broadcaster = get_feed_manager().broadcaster
    before = broadcaster.rejected_handshakes
    broadcaster.record_rejected_handshake()

    response = await auth_client.get("/api/healthcheck/system")

    assert response.status_code == 200
    assert response.json()["browserSockets"]["rejectedHandshakes"] == before + 1


async def test_both_dhan_rest_clients_report_their_limits_and_whose_they_are(auth_client):
    """Dhan documents the option chain limit; the charts floor is our own guess."""
    response = await auth_client.get("/api/healthcheck/system")

    assert response.status_code == 200
    dhan = response.json()["dhanApi"]
    assert dhan["optionChain"]["limitIsOurs"] is False
    assert "3 s" in dhan["optionChain"]["limit"]
    assert dhan["charts"]["limitIsOurs"] is True
    assert "our own floor" in dhan["charts"]["limit"]
    for client in (dhan["optionChain"], dhan["charts"]):
        assert "requests" in client and "errors" in client and "rateLimited" in client
        # The epoch matters: reconfigure() resets some of these and not others.
        assert client["countersSince"]


async def test_the_counter_epochs_match_what_reconfigure_actually_rebuilds():
    """The epoch labels are a claim about code, so pin the code.

    Verified against a live reconfigure: only the feed client is *replaced*
    (its counters reset), while the greeks poller is constructed once in
    FeedManager.__init__ and merely stopped and restarted, so its counters --
    and its option chain client's -- run from process start. Getting this
    backwards would make the page confidently misattribute a counter reset.
    """
    import inspect

    from src.market.services import feed_manager

    source = inspect.getsource(feed_manager.FeedManager)
    # Constructed exactly once, in __init__.
    assert source.count("GreeksPoller(") == 1
    assert "self.greeks_poller = GreeksPoller(" in inspect.getsource(
        feed_manager.FeedManager.__init__
    )
    # reconfigure() stops the poller but does not rebuild it, and it keeps the
    # broadcaster on purpose so open tabs are not stranded.
    reconfigure = inspect.getsource(feed_manager.FeedManager.reconfigure)
    assert "self.greeks_poller.stop()" in reconfigure
    assert "GreeksPoller(" not in reconfigure
    assert "Broadcaster(" not in reconfigure
    # The feed client, by contrast, is dropped and rebuilt by start().
    assert "self.feed = None" in reconfigure


async def test_the_option_chain_counters_are_not_claimed_to_reset_on_a_save(auth_client):
    response = await auth_client.get("/api/healthcheck/system")

    assert response.status_code == 200
    dhan = response.json()["dhanApi"]
    for client in (dhan["optionChain"], dhan["charts"]):
        assert "process start" in client["countersSince"]
        assert "resets" not in client["countersSince"]


async def test_settings_saved_but_not_applied_to_this_process_are_called_out(auth_client):
    """The divergence this page was built to notice.

    `apply_to_config()` runs when settings are SAVED, not at startup, so a
    restart leaves the process on its `.env` values while the Settings page
    still shows what was saved. Nothing else in the application says so: the
    feed just quietly uses different credentials.
    """
    from src import config_utils
    from src.health.services import health_service

    # A saved client id that differs from whatever this process is running
    # with. Derived from the effective value rather than assumed: another test
    # in this file saves settings, and save() calls apply_to_config(), which
    # mutates the in-memory config process-wide -- the very mechanism this
    # test is about.
    effective = config_utils.get_property_value("dhan.client_id", "")
    report = health_service._stored_settings_health(
        {"dhan.client_id": f"{effective}-different"}
    )

    assert report["known"] is True
    assert report["appliedToThisProcess"] is False
    assert report["keysDiffering"] == ["dhan.client_id"]
    assert "NOT in effect" in report["note"]

    summary = health_service.summarise(
        {
            "tasks": {"missing": [], "duplicated": []},
            "upstreamFeed": {"hasUpstreamConnection": False},
            "credentials": {"token": {}, "storedSettings": report},
            "workers": {},
            "browserSockets": {},
        }
    )
    assert summary["healthy"] is False
    assert any("not in effect" in problem for problem in summary["problems"])


def test_settings_that_match_the_running_config_raise_no_alarm():
    from src import config_utils
    from src.health.services import health_service

    effective = config_utils.get_property_value("market_feed.synthetic_feed", "")
    report = health_service._stored_settings_health(
        {"market_feed.synthetic_feed": str(effective)}
    )

    assert report["appliedToThisProcess"] is True
    assert report["keysDiffering"] == []
    assert report["note"] is None


async def test_the_stored_settings_comparison_never_leaks_the_token(auth_client):
    """The comparison reads the DECRYPTED token, so only key names may escape."""
    from src.health.services import health_service

    secret = "stored-token-DO-NOT-SHOW-4ab19c2f"
    report = health_service._stored_settings_health({"dhan.access_token": secret})

    assert secret not in str(report)
    assert report["storedKeys"] == ["dhan.access_token"]


async def test_the_health_payload_says_sqlite_has_no_pool(auth_client):
    """Nothing may imply the SQLite path has pool statistics to report."""
    response = await auth_client.get("/api/healthcheck/system")

    assert response.status_code == 200
    database = response.json()["process"]["database"]
    assert database["poolStatsAvailable"] is False
    assert "NullPool" in database["pooling"]


async def test_the_problems_endpoint_returns_recent_records_newest_first(
    auth_client, clean_buffer
):
    logger = get_logger("tests.health.endpoint")
    logger.warning("first problem")
    logger.error("second problem")

    response = await auth_client.get("/api/healthcheck/problems?limit=10")

    assert response.status_code == 200, response.text
    entries = response.json()["entries"]
    messages = [entry["message"] for entry in entries]
    assert messages.index("second problem") < messages.index("first problem")
    assert entries[messages.index("second problem")]["level"] == "ERROR"


async def test_the_health_endpoint_is_not_in_the_access_log(auth_client):
    """A page that polls must not fill the log it reports on."""
    import main

    assert "/api/healthcheck/system" in main.LogRequestsMiddleware.IGNORED_PATHS
    assert "/api/healthcheck/problems" in main.LogRequestsMiddleware.IGNORED_PATHS


def test_the_summary_is_conservative_about_calling_things_healthy():
    """Anything it cannot confirm is healthy must not be reported as healthy."""
    unhealthy = health_service.summarise(
        {
            "tasks": {"missing": ["greeks-poller"], "duplicated": []},
            "upstreamFeed": {"hasUpstreamConnection": True, "state": "CONNECTED"},
            "credentials": {"token": {}},
            "workers": {},
            "browserSockets": {},
        }
    )
    assert unhealthy["healthy"] is False
    assert any("greeks-poller" in problem for problem in unhealthy["problems"])

    healthy = health_service.summarise(
        {
            "tasks": {"missing": [], "duplicated": []},
            "upstreamFeed": {"hasUpstreamConnection": True, "state": "CONNECTED"},
            "credentials": {"token": {"secondsRemaining": 20 * 3600}},
            "workers": {"orderMatcher": {"error": None}},
            "browserSockets": {"droppedTotal": 0},
        }
    )
    assert healthy["healthy"] is True
    assert healthy["problems"] == []


def test_a_component_reporting_an_error_reaches_the_summary():
    summary = health_service.summarise(
        {
            "tasks": {"missing": [], "duplicated": []},
            "upstreamFeed": {"hasUpstreamConnection": False},
            "credentials": {"token": {}},
            "workers": {"bracketMonitor": {"error": "no price for the underlying"}},
            "browserSockets": {},
        }
    )
    assert summary["healthy"] is False
    assert any("bracketMonitor" in problem for problem in summary["problems"])
