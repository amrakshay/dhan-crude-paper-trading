"""How Dhan last responded to the token this process is holding.

**The gap this closes.** `inspect_token()` reads the `exp` claim out of the
JWT, locally, without asking anybody -- which is exactly right for "when does
this expire" and says nothing at all about "does Dhan still accept it". Those
are different questions, and on 2026-09-19 they gave opposite answers: a token
issued at 15:31 with a declared 24-hour life was refused by every Dhan endpoint
from about 19:18, 4.2 hours in, while the Connections card and the health page
both went on showing a comfortable twenty-hour countdown. A JWT has no way to
say "revoked", so nothing local ever could.

**It is a RECORD, not a probe.** Nothing here calls Dhan. The clients already
make requests and already classify their failures; this only remembers how the
last one went, so the answer costs nothing and cannot itself become load. That
matters more than usual here: the incident this module exists for involved 329
connection attempts in an afternoon, and a health check that added traffic to
diagnose a traffic problem would be the wrong shape entirely.

**It is keyed on the TOKEN, not on the account.** A verdict has to die with the
token it was about, or pasting a fresh one would leave the page still reporting
the old one's refusal -- the same property `token_refresh_service` has, where a
refusal is remembered against the token that was refused and the monitor
resumes the moment a different one is stored. The key is a one-way digest, so
nothing here holds a secret that could be read back out.

**In memory, and it says so.** Empty after a restart, which is honestly
"nobody has called Dhan yet in this process" and not "the token is fine". Three
states, because two would lie: ACCEPTED, REFUSED and UNKNOWN.
"""
import hashlib
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.market.services.market_book import now_ms

# What the last Dhan response said about the token.
VERDICT_ACCEPTED = "ACCEPTED"
VERDICT_REFUSED = "REFUSED"
# Nothing has called Dhan with this token in this process yet. NOT a synonym
# for healthy: a restart lands here, and so does a token nobody has used.
VERDICT_UNKNOWN = "UNKNOWN"


def fingerprint(token: Optional[str]) -> Optional[str]:
    """A short one-way digest, so a verdict can be tied to one token.

    Never logged and never returned to a browser. It exists only so that
    replacing the token replaces the verdict; a mask would be shorter but two
    different tokens can share one, and the whole point is that a stale verdict
    must not survive onto a new token.
    """
    if not token:
        return None
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class DhanAuthState:
    """What Dhan last said, for one token."""

    verdict: str
    at_ms: Optional[int] = None
    endpoint: Optional[str] = None
    detail: Optional[str] = None

    @property
    def refused(self) -> bool:
        return self.verdict == VERDICT_REFUSED

    def as_dict(self) -> Dict[str, Any]:
        age_ms = now_ms() - self.at_ms if self.at_ms else None
        return {
            "verdict": self.verdict,
            "endpoint": self.endpoint,
            "detail": self.detail,
            "ageMs": age_ms,
            # Said on the payload rather than composed by each page, so the two
            # surfaces cannot describe the same state differently.
            "note": _NOTES[self.verdict],
        }


_NOTES = {
    VERDICT_ACCEPTED: (
        "Dhan accepted this token on its last request. The expiry beside this "
        "is the token's own declared `exp`, read locally."
    ),
    VERDICT_REFUSED: (
        "DHAN IS CURRENTLY REFUSING THIS TOKEN, whatever its declared expiry "
        "says. A token can be revoked server-side long before it expires -- "
        "generating a new one for the same client id invalidates the previous "
        "one, and a lapsed Data APIs subscription has the same effect. The "
        "countdown beside this is the `exp` claim read locally and cannot see "
        "any of that. Generate a fresh token on Dhan Web and save it on the "
        "Connections page."
    ),
    VERDICT_UNKNOWN: (
        "Nothing has called Dhan with this token since this process started, "
        "so whether Dhan still accepts it is genuinely unknown. This is not "
        "the same as healthy."
    ),
}


class _Recorder:
    """Process-wide, and deliberately tiny.

    A lock rather than bare assignment because the feed task, the chart client
    and the option chain client all write to it from different tasks, and a
    verdict read half-way through an update would report one call's endpoint
    against another's outcome.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token: Optional[str] = None
        self._state: DhanAuthState = DhanAuthState(verdict=VERDICT_UNKNOWN)

    def record(
        self, token: Optional[str], verdict: str, endpoint: str, detail: Optional[str] = None
    ) -> None:
        key = fingerprint(token)
        if key is None:
            return
        with self._lock:
            self._token = key
            self._state = DhanAuthState(
                verdict=verdict,
                at_ms=now_ms(),
                endpoint=endpoint,
                # Truncated: a Dhan error body is short, but this reaches an
                # HTTP response and there is no reason to carry more of it than
                # a person needs to read.
                detail=(detail or "")[:300] or None,
            )

    def state_for(self, token: Optional[str]) -> DhanAuthState:
        """The verdict for THIS token, or UNKNOWN for any other.

        A different token gets UNKNOWN rather than the last one's answer, which
        is what makes pasting a fresh token clear a refusal immediately instead
        of leaving the page accusing it of something the old one did.
        """
        key = fingerprint(token)
        with self._lock:
            if key is None or key != self._token:
                return DhanAuthState(verdict=VERDICT_UNKNOWN)
            return self._state

    def reset(self) -> None:
        with self._lock:
            self._token = None
            self._state = DhanAuthState(verdict=VERDICT_UNKNOWN)


_recorder = _Recorder()


def record_accepted(token: Optional[str], endpoint: str) -> None:
    _recorder.record(token, VERDICT_ACCEPTED, endpoint)


def record_refused(token: Optional[str], endpoint: str, detail: str) -> None:
    _recorder.record(token, VERDICT_REFUSED, endpoint, detail)


def state_for(token: Optional[str]) -> DhanAuthState:
    return _recorder.state_for(token)


def reset_for_tests() -> None:
    _recorder.reset()


def is_authentication_failure(status_code: int, body: str) -> bool:
    """Does this response mean "Dhan will not accept this token"?

    Kept HERE rather than in each client so the three of them cannot drift into
    disagreeing about what a refusal looks like -- and because Dhan says it in
    more than one way. On 2026-09-19 the same revoked token produced:

        401 {"data":{"808":"Authentication Failed - Client ID or Token invalid"}}
        400 {"errorCode":"DH-906","errorMessage":"Invalid Token"}

    A 400 is normally a business error rather than an auth failure, which is
    why the status code alone is not enough and `DH-906` has to be recognised
    by name. Getting that wrong in the other direction is what made a token
    refusal look transient once already (root `CLAUDE.md`, the renewal path).
    """
    lowered = (body or "").lower()
    if status_code in (401, 403):
        return True
    return "dh-906" in lowered or "invalid token" in lowered
