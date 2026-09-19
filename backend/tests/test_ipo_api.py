"""The IPO dashboard's endpoints, and the split between reading and acting.

**HIDING A BUTTON IS NOT ACCESS CONTROL.** `conf/role-pages.json` grants /ipo
to both roles and the page hides the three action buttons for a ROLE_USER;
this file asserts the thing that actually refuses -- `require_admin` on the
action endpoints, called directly, with no UI in the way. Same rule
`tests/test_users_api.py` follows for Settings.
"""
import json
from datetime import date
from pathlib import Path

import pytest

from src.ipo.services.ipo_service import IpoService
from src.users.services.user_service import UserRole
from tests.conftest import SEED_ADMIN_EMAIL, SEED_ADMIN_PASSWORD, login_as

FIXTURE = Path(__file__).parent / "fixtures" / "ipo_gmp_report.json"

PLAIN_USER = {
    "email": "ipo-reader@abc.com",
    "firstName": "Ipo",
    "lastName": "Reader",
    "password": "reader-password-1",
    "role": UserRole.USER.value,
}


def fixture_rows():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["reportTableData"]


class FakeSource:
    def __init__(self, rows=None):
        self._rows = rows if rows is not None else fixture_rows()

    async def fetch_rows(self):
        return self._rows


@pytest.fixture
async def seeded_board(auth_client, monkeypatch):
    """A board loaded from the saved response, through the real service."""
    monkeypatch.setattr(
        "src.ipo.services.ipo_service.IpoSourceClient", lambda *a, **k: FakeSource()
    )
    from src.database.session import session_scope

    async with session_scope() as session:
        await IpoService(session).refresh(today=date(2026, 9, 19))
        await session.commit()
    return auth_client


@pytest.fixture
async def user_client(auth_client):
    """A signed-in ROLE_USER, alongside the admin's own client."""
    import httpx

    import main

    created = await auth_client.post("/api/users", json=PLAIN_USER)
    assert created.status_code == 201, created.text

    transport = httpx.ASGITransport(app=main.app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    await login_as(client, PLAIN_USER["email"], PLAIN_USER["password"])
    yield client
    await client.aclose()
    await login_as(auth_client, SEED_ADMIN_EMAIL, SEED_ADMIN_PASSWORD)


# --- reading ---------------------------------------------------------------
async def test_the_three_tabs_answer(seeded_board):
    for tab in ("closing-today", "closing-next", "listed"):
        response = await seeded_board.get(f"/api/ipo/{tab}")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["tab"] == tab
        assert isinstance(payload["ipos"], list)
        assert all(ipo["board"] == "MAINBOARD" for ipo in payload["ipos"])


async def test_the_listed_tab_says_why_it_is_empty(seeded_board):
    """An empty forward-only tab must not read as a bug."""
    payload = (await seeded_board.get("/api/ipo/listed")).json()

    assert payload["ipos"] == []
    assert "Forward-only" in payload["caveat"]


async def test_the_closing_next_tab_admits_it_has_no_holiday_calendar(seeded_board):
    payload = (await seeded_board.get("/api/ipo/closing-next")).json()

    assert "holiday" in payload["caveat"]
    assert payload["day"] is not None


async def test_every_row_carries_its_gmp_and_the_age_of_it(seeded_board):
    payload = (await seeded_board.get("/api/ipo/closing-next")).json()
    assert payload["ipos"], "the captured response has IPOs closing next"

    for ipo in payload["ipos"]:
        assert "gmp" in ipo
        assert "isStale" in ipo["gmp"]
        assert "capturedAt" in ipo["gmp"]
        assert ipo["companyName"]


async def test_a_plain_user_can_read_every_tab(seeded_board, user_client):
    for tab in ("closing-today", "closing-next", "listed", "status"):
        response = await user_client.get(f"/api/ipo/{tab}")
        assert response.status_code == 200, f"{tab}: {response.text}"


# --- acting ----------------------------------------------------------------
async def _first_ipo_id(client) -> int:
    payload = (await client.get("/api/ipo/closing-next")).json()
    return payload["ipos"][0]["id"]


async def test_an_admin_can_mark_applied_and_accepted(seeded_board):
    ipo_id = await _first_ipo_id(seeded_board)

    applied = await seeded_board.post(
        f"/api/ipo/{ipo_id}/action", json={"action": "APPLIED", "value": True}
    )
    accepted = await seeded_board.post(
        f"/api/ipo/{ipo_id}/action",
        json={"action": "MANDATE_ACCEPTED", "value": True},
    )

    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] is True
    assert applied.json()["isOutstanding"] is True, "applied alone is not done"
    assert accepted.json()["mandateAccepted"] is True
    assert accepted.json()["isOutstanding"] is False
    assert accepted.json()["appliedAt"] is not None


async def test_an_action_survives_a_reload(seeded_board):
    """The state is stored, not held in the page."""
    ipo_id = await _first_ipo_id(seeded_board)
    await seeded_board.post(
        f"/api/ipo/{ipo_id}/action", json={"action": "REJECTED", "value": True}
    )

    payload = (await seeded_board.get("/api/ipo/closing-next")).json()
    row = next(ipo for ipo in payload["ipos"] if ipo["id"] == ipo_id)

    assert row["rejected"] is True
    assert row["rejectedAt"] is not None
    assert row["isOutstanding"] is False


async def test_an_unknown_action_is_refused(seeded_board):
    ipo_id = await _first_ipo_id(seeded_board)

    response = await seeded_board.post(
        f"/api/ipo/{ipo_id}/action", json={"action": "SOLD", "value": True}
    )

    assert response.status_code == 400


async def test_an_unknown_ipo_is_a_404(seeded_board):
    response = await seeded_board.post(
        "/api/ipo/999999/action", json={"action": "APPLIED", "value": True}
    )

    assert response.status_code == 404


async def test_a_plain_user_is_refused_by_the_action_endpoints(seeded_board, user_client):
    """Not merely hidden from the page -- refused by the API."""
    ipo_id = await _first_ipo_id(seeded_board)

    action = await user_client.post(
        f"/api/ipo/{ipo_id}/action", json={"action": "APPLIED", "value": True}
    )
    refresh = await user_client.post("/api/ipo/refresh", json={})

    assert action.status_code == 403, action.text
    assert refresh.status_code == 403, refresh.text


async def test_the_refused_action_changed_nothing(seeded_board, user_client):
    ipo_id = await _first_ipo_id(seeded_board)
    await user_client.post(
        f"/api/ipo/{ipo_id}/action", json={"action": "APPLIED", "value": True}
    )

    payload = (await seeded_board.get("/api/ipo/closing-next")).json()
    row = next(ipo for ipo in payload["ipos"] if ipo["id"] == ipo_id)

    assert row["applied"] is False


# --- the page's own footer -------------------------------------------------
async def test_status_reports_the_clock(seeded_board):
    payload = (await seeded_board.get("/api/ipo/status")).json()

    assert payload["dailyRefreshAt"] == "13:00"
    assert payload["reminderFrom"] == "10:00"
    assert payload["reminderTo"] == "17:00"
    assert payload["storedIpos"] > 0


async def test_role_pages_grant_ipo_to_both_roles(seeded_board, user_client):
    user_pages = (await user_client.get("/api/users/role-pages")).json()["pages"]
    admin_pages = (await seeded_board.get("/api/users/role-pages")).json()["pages"]

    assert "/ipo" in user_pages
    assert "/ipo" in admin_pages


# --- the Status tab ---------------------------------------------------------
async def test_the_status_payload_reports_the_clock_and_the_job_log(seeded_board):
    payload = (await seeded_board.get("/api/ipo/health")).json()

    assert payload["clock"]["taskName"] == "ipo-scheduler"
    assert payload["clock"]["dailyRefreshAt"] == "13:00"
    assert payload["clock"]["nextDailyRefreshAt"] is not None
    assert payload["source"]["host"] == "webnodejs.investorgain.com"
    assert payload["freshness"]["storedIpos"] > 0
    assert isinstance(payload["jobs"], list)
    assert payload["notes"]


async def test_no_sweep_is_promised_on_a_day_nothing_closes(seeded_board):
    """None is a real answer, not a missing one.

    Printing "next sweep 14:00" on a day with no closing IPO would promise a
    message that is never coming.
    """
    payload = (await seeded_board.get("/api/ipo/health")).json()

    assert payload["sweeps"]["closingToday"] == 0
    assert payload["sweeps"]["expectedSlots"] == []
    assert payload["sweeps"]["missedSlots"] == []
    assert payload["clock"]["nextSweepAt"] is None


async def test_a_plain_user_is_refused_the_status_payload(seeded_board, user_client):
    """Admin-only, like the other two health surfaces: it serves machinery
    state and job detail lines."""
    response = await user_client.get("/api/ipo/health")

    assert response.status_code == 403


async def test_the_alert_catalogue_can_be_narrowed_to_this_feature(seeded_board):
    response = await seeded_board.get(
        "/api/connections/alerts/catalogue?category=IPO"
    )

    assert response.status_code == 200, response.text
    keys = {rule["key"] for rule in response.json()["rules"]}
    assert keys == {"ipo-closing-reminder", "ipo-source-unreachable"}
    assert all(rule["categoryLabel"] == "IPO dashboard" for rule in response.json()["rules"])


async def test_narrowing_the_catalogue_does_not_withdraw_the_rules_elsewhere(
    seeded_board,
):
    """A narrower view of one list, not a second list."""
    everything = (await seeded_board.get("/api/connections/alerts/catalogue")).json()

    keys = {rule["key"] for rule in everything["rules"]}
    assert "ipo-closing-reminder" in keys
    assert "ipo-source-unreachable" in keys


async def test_asking_the_catalogue_by_strategy_and_category_at_once_is_refused(
    seeded_board,
):
    """Two different questions. Answering both at once returns nothing, which
    would look like a feature with no rules rather than a bad request."""
    response = await seeded_board.get(
        "/api/connections/alerts/catalogue"
        "?category=IPO&strategyKey=nse-swing-momentum"
    )

    assert response.status_code == 400


async def test_an_unknown_category_is_refused_rather_than_empty(seeded_board):
    """A typo must not look like a feature with no rules."""
    response = await seeded_board.get(
        "/api/connections/alerts/catalogue?category=IPOS"
    )

    assert response.status_code == 400
