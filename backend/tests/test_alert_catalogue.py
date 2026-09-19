"""The Alerts tab: the catalogue, and what it says about what actually fired."""
import httpx
import pytest

from src.connections.database.db_models.alert_model import SEVERITY_INFO
from src.connections.services import alert_catalogue
from src.connections.services.alert_service import AlertService

STRATEGY = "nse-swing-momentum"


# --- the catalogue itself ---------------------------------------------------
def test_every_rule_declares_what_it_is_and_why():
    """A rule that cannot say why it is worth a message should not be a rule.

    The tab is documentation an operator reads, so the prose is the feature,
    not decoration around it.
    """
    for rule in alert_catalogue.all_rules():
        assert rule.title, rule.key
        assert len(rule.trigger) > 30, f"{rule.key} does not say what fires it"
        assert len(rule.why) > 30, f"{rule.key} does not say why it matters"
        assert rule.severity in ("INFO", "WARNING", "ERROR", "CRITICAL"), rule.key
        assert rule.collapsing, f"{rule.key} does not say how repeats behave"


def test_rule_keys_are_unique():
    keys = [rule.key for rule in alert_catalogue.all_rules()]
    assert len(keys) == len(set(keys))


def test_the_watcher_and_the_catalogue_cannot_drift():
    """Every health event the watcher raises is a rule in the catalogue.

    They share the event NAMES rather than each spelling their own, which is
    what makes this assertable at all.
    """
    from src.connections.services import alert_watcher  # noqa: F401

    watcher_events = {
        alert_catalogue.EVENT_MISSING_TASKS,
        alert_catalogue.EVENT_TOKEN_LAPSED,
        alert_catalogue.EVENT_TOKEN_RENEWAL_FAILED,
        alert_catalogue.EVENT_FEED_DOWN,
        alert_catalogue.EVENT_FEED_STALE,
        alert_catalogue.EVENT_MISSED_SESSIONS,
        alert_catalogue.EVENT_STALE_BARS,
        alert_catalogue.EVENT_DEFERRED_STOPS,
        alert_catalogue.EVENT_APP_STARTED,
    }
    catalogued = {rule.key for rule in alert_catalogue.all_rules()}
    missing = watcher_events - catalogued
    assert not missing, f"health events with no catalogue entry: {sorted(missing)}"


def test_a_strategy_sees_only_the_rules_about_it():
    """A dead feed and an expiring token are not a strategy's business.

    Repeating a process-wide rule onto a strategy's page would make two pages
    that disagree the moment one of them changes.
    """
    everything = alert_catalogue.rules_for(None)
    for_strategy = alert_catalogue.rules_for(STRATEGY)

    assert len(for_strategy) < len(everything)
    assert all(rule.strategy_scoped for rule in for_strategy)

    strategy_keys = {rule.key for rule in for_strategy}
    assert alert_catalogue.EVENT_STALE_BARS in strategy_keys
    assert alert_catalogue.EVENT_DEFERRED_STOPS in strategy_keys
    assert "trade-sold" in strategy_keys
    # Process-wide, and deliberately absent.
    assert alert_catalogue.EVENT_FEED_DOWN not in strategy_keys
    assert alert_catalogue.EVENT_TOKEN_LAPSED not in strategy_keys
    assert "error-records" not in strategy_keys


def test_a_rule_about_machinery_a_strategy_DOES_NOT_HAVE_stays_off_its_page():
    """"About a strategy" and "about THIS strategy" stopped being the same thing.

    Until a second automated module arrived every `strategy_scoped` rule was
    the rotation's, so the unqualified list was right by accident. It is not
    any more: BTST's specification says B15 is none and none is possible, so a
    deferred stop cannot happen to it, and the rotation holds for weeks and has
    no overnight book with a due exit to be stuck in.

    A rule on the wrong page is worse than a missing one -- it reads as cover
    somebody has and has not.
    """
    swing = {rule.key for rule in alert_catalogue.rules_for(STRATEGY)}
    btst = {
        rule.key
        for rule in alert_catalogue.rules_for(alert_catalogue.STRATEGY_BTST)
    }

    assert alert_catalogue.EVENT_DEFERRED_STOPS in swing
    assert alert_catalogue.EVENT_DEFERRED_STOPS not in btst

    assert alert_catalogue.EVENT_BTST_EXIT_INCOMPLETE in btst
    assert alert_catalogue.EVENT_BTST_EXIT_INCOMPLETE not in swing

    # What BOTH have: a journal that can miss a session, bars that can go
    # stale, and trades. Those stay unqualified rather than being listed per
    # module, so a third strategy inherits them without an edit here.
    for shared in (alert_catalogue.EVENT_MISSED_SESSIONS,
                   alert_catalogue.EVENT_STALE_BARS,
                   "trade-bought", "trade-sold"):
        assert shared in swing, shared
        assert shared in btst, shared


# --- what it reports --------------------------------------------------------
async def test_a_rule_that_never_fired_says_never_rather_than_zero(auth_client):
    """Never fired is a third state, not a timestamp and not a count."""
    response = await auth_client.get("/api/connections/alerts/catalogue")

    assert response.status_code == 200, response.text
    rules = {rule["key"]: rule for rule in response.json()["rules"]}
    quiet = rules[alert_catalogue.EVENT_FEED_STALE]
    assert quiet["lastFiredAt"] is None
    assert quiet["lastStatus"] is None
    assert quiet["timesFired"] == 0


async def test_the_catalogue_reports_when_a_rule_last_fired(auth_client, db_session):
    from src.database.session import get_session_factory

    session = get_session_factory()()
    try:
        await AlertService(session).record_health(
            event=alert_catalogue.EVENT_FEED_STALE,
            title="The market feed has gone quiet",
            body="no message for 90s",
        )
        await session.commit()
    finally:
        await session.close()

    response = await auth_client.get("/api/connections/alerts/catalogue")

    rules = {rule["key"]: rule for rule in response.json()["rules"]}
    fired = rules[alert_catalogue.EVENT_FEED_STALE]
    assert fired["lastFiredAt"] is not None
    assert fired["timesFired"] == 1
    assert fired["lastStatus"] in ("PENDING", "SUPPRESSED")
    # IST with its offset: a naive string is parsed by the browser as LOCAL.
    assert "+05:30" in fired["lastFiredAt"]


async def test_a_suffixed_health_event_still_matches_its_rule(auth_client, db_session):
    """`missing-tasks|swing-scheduler` belongs to the `missing-tasks` rule.

    The dedupe key carries what made this occurrence distinct, so matching is
    on the PREFIX -- otherwise every task combination would look like a rule
    that has never fired.
    """
    from src.database.session import get_session_factory

    session = get_session_factory()()
    try:
        service = AlertService(session)
        await service.record_health(
            event=f"{alert_catalogue.EVENT_MISSING_TASKS}|swing-scheduler",
            title="1 background task is not running",
            body="swing-scheduler",
        )
        await service.record_health(
            event=f"{alert_catalogue.EVENT_MISSING_TASKS}|order-matcher",
            title="1 background task is not running",
            body="order-matcher",
        )
        await session.commit()
    finally:
        await session.close()

    response = await auth_client.get("/api/connections/alerts/catalogue")

    rules = {rule["key"]: rule for rule in response.json()["rules"]}
    assert rules[alert_catalogue.EVENT_MISSING_TASKS]["timesFired"] == 2


async def test_a_strategys_tab_shows_only_its_own_alerts(auth_client, db_session):
    """`strategy_key` is a COLUMN, so this is a query rather than a text match."""
    from decimal import Decimal

    from src.database.session import get_session_factory

    session = get_session_factory()()
    try:
        service = AlertService(session)
        await service.record_health(
            event=f"{alert_catalogue.EVENT_STALE_BARS}|{STRATEGY}",
            title="bars are stale",
            body="refusing",
            strategy_key=STRATEGY,
        )
        # A process-wide one, which must NOT appear on the strategy's tab.
        await service.record_health(
            event=alert_catalogue.EVENT_FEED_DOWN,
            title="the feed is down",
            body="no prices",
        )
        await session.commit()
    finally:
        await session.close()

    mine = await auth_client.get(
        f"/api/connections/alerts/catalogue?strategyKey={STRATEGY}"
    )
    everything = await auth_client.get("/api/connections/alerts/catalogue")

    assert mine.status_code == 200, mine.text
    assert mine.json()["strategyKey"] == STRATEGY
    titles = [row["title"] for row in mine.json()["recent"]]
    assert "bars are stale" in titles
    assert "the feed is down" not in titles, (
        "a process-wide alert is not a strategy's business"
    )
    # And the system view still has both.
    assert "the feed is down" in [row["title"] for row in everything.json()["recent"]]


async def test_recording_and_delivering_are_reported_separately(auth_client):
    """A page that conflated them would show a healthy list while every
    message was being dropped on the floor."""
    response = await auth_client.get("/api/connections/alerts/catalogue")

    delivery = response.json()["delivery"]
    assert delivery["recording"] is True
    # Nothing is configured in the test database.
    assert delivery["delivering"] is False
    assert delivery["telegramConfigured"] is False


async def test_the_catalogue_says_what_it_cannot_tell_you(auth_client):
    notes = (await auth_client.get("/api/connections/alerts/catalogue")).json()["notes"]

    assert any("read-only" in note for note in notes)
    assert any("delivered" in note for note in notes)

    scoped = (
        await auth_client.get(
            f"/api/connections/alerts/catalogue?strategyKey={STRATEGY}"
        )
    ).json()["notes"]
    assert any("System Health" in note for note in scoped)


async def test_the_catalogue_is_admin_only(auth_client):
    """It serves alert bodies, which can carry whatever a developer
    interpolated into a log line."""
    created = await auth_client.post(
        "/api/users",
        json={
            "email": "alert-reader@abc.com",
            "firstName": "Alert",
            "lastName": "Reader",
            "password": "reader-password-123",
            "role": "ROLE_USER",
            "mustChangePassword": False,
        },
    )
    assert created.status_code in (200, 201), created.text

    import main

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/api/auth/login",
            json={"email": "alert-reader@abc.com", "password": "reader-password-123"},
        )
        assert login.status_code == 200, login.text
        refused = await client.get("/api/connections/alerts/catalogue")
        assert refused.status_code == 403, refused.text


async def test_the_catalogue_is_read_only(auth_client):
    """No edit, no delete. What gets alerted is a property of the build."""
    for call in (
        auth_client.put("/api/connections/alerts/catalogue", json={}),
        auth_client.post("/api/connections/alerts/catalogue", json={}),
        auth_client.delete("/api/connections/alerts/catalogue"),
    ):
        response = await call
        assert response.status_code == 405, response.text
