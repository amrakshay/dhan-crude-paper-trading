"""A token can be dead long before `exp`, and the app must say so.

**What happened.** On 2026-09-19 a token issued at 15:31 with a declared
24-hour life was refused by every Dhan endpoint from about 19:18 -- 4.2 hours
in -- with `401 808 "Authentication Failed"` on the option chain and
`400 DH-906 "Invalid Token"` on the charts. Meanwhile the Connections card and
the system health page both showed a comfortable twenty-hour countdown in
green, because both read the `exp` claim out of the JWT locally and a JWT has
no way to say "revoked".

So the application was confidently wrong about the one thing that had stopped
everything working.

**The fix is a RECORD, not a probe.** `dhan_auth_state` remembers how Dhan
answered the last real call. Nothing new is sent: the clients were making those
requests anyway and already classified their failures. That shape matters here
more than usual -- the incident involved 329 connection attempts in an
afternoon, and a health check that generated traffic to diagnose a traffic
problem would have been exactly the wrong answer.
"""
import pytest

from src.market.services import dhan_auth_state
from src.market.services.dhan_auth_state import (
    VERDICT_ACCEPTED,
    VERDICT_REFUSED,
    VERDICT_UNKNOWN,
)

TOKEN = "header.payload.signature-one"
OTHER_TOKEN = "header.payload.signature-two"


@pytest.fixture(autouse=True)
def _clean():
    dhan_auth_state.reset_for_tests()
    yield
    dhan_auth_state.reset_for_tests()


# --- what counts as a refusal ----------------------------------------------


@pytest.mark.parametrize(
    "status,body",
    [
        # The two Dhan actually sent on 2026-09-19, verbatim.
        (401, '{"data":{"808":"Authentication Failed - Client ID or Token invalid"},"status":"failed"}'),
        (400, '{"errorType":"Order_Error","errorCode":"DH-906","errorMessage":"Invalid Token"}'),
        (403, "forbidden"),
    ],
)
def test_dhan_says_the_token_is_bad_in_more_than_one_way(status, body):
    """Which is why the status code alone is not enough.

    A 400 is normally a business error rather than an auth failure, so DH-906
    has to be recognised by name. Classifying a refusal as transient is a
    mistake this codebase has already made once, on the renewal path, and it
    cost nineteen retries across the six hours somebody could have acted in.
    """
    assert dhan_auth_state.is_authentication_failure(status, body) is True


@pytest.mark.parametrize(
    "status,body",
    [
        (400, '{"errorCode":"DH-905","errorMessage":"Bad date range"}'),
        (429, "rate limited"),
        (500, "server error"),
        (200, "ok"),
    ],
)
def test_an_ordinary_failure_is_not_read_as_a_token_refusal(status, body):
    """The other direction matters just as much.

    Calling a bad date range "your token is revoked" would send somebody to
    regenerate a perfectly good token, and the real fault would still be there
    afterwards.
    """
    assert dhan_auth_state.is_authentication_failure(status, body) is False


# --- the record -------------------------------------------------------------


def test_nothing_recorded_is_UNKNOWN_and_not_healthy():
    """Three states, because two would lie.

    A restart lands here. "Nobody has called Dhan yet" is not the same answer
    as "Dhan accepts this", and a page that rendered the first as the second
    would be green about something it had not checked.
    """
    state = dhan_auth_state.state_for(TOKEN)
    assert state.verdict == VERDICT_UNKNOWN
    assert state.refused is False
    assert "not the same as healthy" in state.as_dict()["note"]


def test_a_refusal_is_remembered_with_what_dhan_said():
    dhan_auth_state.record_refused(
        TOKEN, "/v2/optionchain/expirylist", '{"data":{"808":"Authentication Failed"}}'
    )
    state = dhan_auth_state.state_for(TOKEN)

    assert state.verdict == VERDICT_REFUSED
    assert state.refused is True
    assert state.endpoint == "/v2/optionchain/expirylist"
    assert "808" in state.detail
    assert "DHAN IS CURRENTLY REFUSING THIS TOKEN" in state.as_dict()["note"]


def test_a_later_success_clears_an_earlier_refusal():
    """It reports the LAST answer, not the worst one.

    A transient refusal that has since started working again must not leave the
    page permanently red -- that is how a warning stops being read.
    """
    dhan_auth_state.record_refused(TOKEN, "/v2/charts/historical", "DH-906")
    assert dhan_auth_state.state_for(TOKEN).refused is True

    dhan_auth_state.record_accepted(TOKEN, "/v2/charts/historical")
    assert dhan_auth_state.state_for(TOKEN).verdict == VERDICT_ACCEPTED


def test_a_verdict_dies_with_the_token_it_was_about():
    """PASTING A FRESH TOKEN MUST CLEAR THE REFUSAL IMMEDIATELY.

    Otherwise the page goes on accusing a brand-new token of something the old
    one did, and the operator has no way to tell whether their fix worked. The
    same property `token_refresh_service` has: it remembers WHICH token was
    refused and resumes the moment a different one is stored.
    """
    dhan_auth_state.record_refused(TOKEN, "/v2/optionchain/expirylist", "808")
    assert dhan_auth_state.state_for(TOKEN).refused is True

    # A different token has its own, unknown, verdict.
    assert dhan_auth_state.state_for(OTHER_TOKEN).verdict == VERDICT_UNKNOWN
    assert dhan_auth_state.state_for(OTHER_TOKEN).refused is False


def test_the_fingerprint_is_one_way_and_is_not_the_token():
    """It exists to tie a verdict to a token, not to store one."""
    digest = dhan_auth_state.fingerprint(TOKEN)
    assert digest is not None
    assert TOKEN not in digest
    assert len(digest) == 16
    assert dhan_auth_state.fingerprint(None) is None
    assert dhan_auth_state.fingerprint(TOKEN) == dhan_auth_state.fingerprint(TOKEN)
    assert dhan_auth_state.fingerprint(TOKEN) != dhan_auth_state.fingerprint(OTHER_TOKEN)


# --- the surfaces -----------------------------------------------------------


def test_the_health_page_reports_a_refusal_as_a_problem_and_says_expiry_is_beside_the_point():
    """And reports it INSTEAD of the expiry warning, not after it.

    When Dhan is refusing the token, how long it had left is not the useful
    fact, and leading with the countdown is what made this invisible for four
    hours.
    """
    from src.health.services.health_service import summarise

    refused = summarise(
        {
            "credentials": {
                "token": {
                    "present": True,
                    "expired": False,
                    "secondsRemaining": 20 * 3600,
                    "dhanVerdict": {"verdict": VERDICT_REFUSED},
                }
            },
            "tasks": {},
        }
    )["problems"]

    assert any("REFUSING" in one for one in refused)
    # NOT also reported as merely expiring soon: one problem, the real one.
    assert not any("expires in" in one.lower() for one in refused)


def test_a_healthy_token_is_still_reported_as_healthy():
    """The guard must not make every token look refused."""
    from src.health.services.health_service import summarise

    problems = summarise(
        {
            "credentials": {
                "token": {
                    "present": True,
                    "expired": False,
                    "secondsRemaining": 20 * 3600,
                    "dhanVerdict": {"verdict": VERDICT_ACCEPTED},
                }
            },
            "tasks": {},
        }
    )["problems"]
    assert not any("REFUSING" in one for one in problems)


def test_the_alert_catalogue_and_the_watcher_agree_about_the_new_rule():
    """The catalogue is documentation an operator reads, so it cannot drift.

    `tests/test_alerts.py` asserts the general property; this one asserts that
    the rule added for THIS failure is in it, because a rule that fires and is
    not listed is a message nobody can look up.
    """
    from src.connections.services.alert_catalogue import (
        EVENT_TOKEN_REJECTED,
        RULES,
    )

    rule = next((one for one in RULES if one.key == EVENT_TOKEN_REJECTED), None)
    assert rule is not None
    assert "revoked" in rule.why.lower()
    assert "DH-906" in rule.trigger
    # It must NOT be strategy-scoped: a dead token stops everything, and it is
    # nobody's strategy.
    assert rule.strategy_scoped is False
