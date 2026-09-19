"""Keeps the Dhan access token alive, so nobody has to paste one every morning.

A Dhan access token lasts 24 hours. When it lapses the live feed stops, the
chart stops, the option chain stops and the rotation's overnight bar refresh
fails -- and every one of those failures is quiet until something asks for a
price. For a strategy that trades unattended at 09:16 that is the difference
between working and not.

HOW IT DECIDES WHEN. Not on a clock: off the token's OWN expiry. Dhan issues
JWTs and `inspect_token()` already reads the `exp` claim locally, so this asks
"how long is left" rather than "what time is it". That is self-correcting --
a token pasted in at an odd hour is renewed relative to itself, a restart picks
up wherever the token actually is, and there is no schedule to get wrong.

WHAT IT CANNOT DO. It renews; it cannot authenticate. See
`src/market/services/dhan_token_client.py` -- the only credential involved is
the token the application already holds, and `auth.dhan.co` is deliberately
unreachable, so a token that has fully lapsed needs a human. That is a
consequence of a decision taken on 2026-09-18, not an oversight: the
alternative was storing the operator's PIN.

THREE STATES, KEPT APART. Renewed, could-not-renew-yet (transient: retry), and
will-never-renew (Dhan refused: stop trying and say so). The third is why
`TokenRenewalRefused` is a distinct type, and getting that distinction wrong is
not hypothetical -- on 2026-09-19 a 400 was classified as transient, retried
nineteen times over the six hours before the token expired, and the only alert
arrived after the token was dead and could no longer be renewed by anyone. A
refusal must reach a human WHILE THERE IS STILL TIME TO ACT.
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from src import config_utils
from src.core.time_utils import utc_now
from src.logging_config import get_logger

logger = get_logger("settings.token_refresh")

# How often to look at the clock. Cheap: it decodes a JWT locally and usually
# decides to do nothing. Nothing here touches the tick path or the network.
CHECK_INTERVAL_SECONDS = 900  # 15 minutes

# Renew when less than this is left. Six hours out of twenty-four leaves room
# for a machine that was asleep, a Dhan outage, and several retries, while
# staying far from the cliff.
DEFAULT_RENEW_BEFORE_HOURS = 6


@dataclass
class RefreshOutcome:
    renewed: bool
    reason: str
    expires_at: Optional[datetime] = None
    # Dhan will not renew this token and repeating the call will not change
    # that. The monitor stops asking and the operator is told WHILE THE TOKEN
    # IS STILL ALIVE -- which is the difference between six hours of warning
    # and an alert that arrives after the session is already dead.
    needs_human: bool = False


class TokenRefreshService:
    """Decides whether to renew, and renews. Owns no schedule of its own."""

    def __init__(self, session):
        self.session = session

    @staticmethod
    def _enabled() -> bool:
        return config_utils.get_property_value_boolean("dhan.auto_renew_token", True)

    @staticmethod
    def _renew_before_hours() -> int:
        return config_utils.get_property_value_int(
            "dhan.renew_token_before_hours", DEFAULT_RENEW_BEFORE_HOURS
        )

    async def refresh_if_due(self, force: bool = False) -> RefreshOutcome:
        """Renew the stored token if it is close enough to expiry.

        Never raises. Every failure here is reportable rather than fatal: the
        application runs perfectly well on a token that has hours left, and
        taking the process down over a failed renewal would turn a recoverable
        problem into an outage.
        """
        from src.market.services.dhan_token_client import (
            TokenRenewalError,
            TokenRenewalRefused,
            get_token_client,
        )
        from src.settings.database.db_operations.app_setting_repository import (
            AppSettingRepository,
        )
        from src.settings.services.settings_service import (
            KEY_ACCESS_TOKEN,
            SettingsService,
            inspect_token,
        )

        if not self._enabled() and not force:
            return RefreshOutcome(False, "Automatic renewal is switched off.")

        if config_utils.get_property_value_boolean("market_feed.synthetic_feed", False):
            return RefreshOutcome(
                False,
                "The synthetic feed is on, so there is no live token to renew.",
            )

        settings = SettingsService(AppSettingRepository(self.session))
        stored = await settings.load_stored()
        token = stored.get(KEY_ACCESS_TOKEN) or settings.env_fallbacks().get(
            KEY_ACCESS_TOKEN
        )
        info = inspect_token(token)

        if not info.present:
            return RefreshOutcome(False, "No Dhan access token is configured.")
        if not info.is_jwt or info.seconds_remaining is None:
            # An opaque token has no readable expiry, so "is it due" cannot be
            # answered. Guessing a renewal cadence for it would be inventing
            # the one number this whole module exists to avoid inventing.
            return RefreshOutcome(
                False,
                "The stored token carries no readable expiry, so renewal "
                "cannot be timed. It will not be renewed automatically.",
            )
        if info.expired:
            return RefreshOutcome(
                False,
                "The stored token has already expired. Dhan renews only an "
                "active token, so a new one has to be generated on Dhan Web "
                "and saved on the Connections page.",
                expires_at=info.expires_at,
                needs_human=True,
            )

        hours_left = info.seconds_remaining / 3600
        if not force and hours_left > self._renew_before_hours():
            return RefreshOutcome(
                False,
                f"Not due yet: {hours_left:.1f} hour(s) left, renewing under "
                f"{self._renew_before_hours()}.",
                expires_at=info.expires_at,
            )

        try:
            payload = await get_token_client().renew()
        except TokenRenewalRefused as error:
            # Caught BEFORE TokenRenewalError, which it subclasses. A refusal
            # is final for this token: say so once, loudly, with how long is
            # left to act.
            logger.error(
                "Dhan will not renew the stored token (%.1f hour(s) of validity "
                "left). This needs a person: %s",
                hours_left, error,
            )
            return RefreshOutcome(
                False, str(error), expires_at=info.expires_at, needs_human=True
            )
        except TokenRenewalError as error:
            logger.warning("Dhan token renewal failed, will try again: %s", error)
            return RefreshOutcome(False, str(error), expires_at=info.expires_at)

        # Saved through SettingsService so it lands ENCRYPTED, is registered
        # with the log redactor, and is overlaid onto the running config -- the
        # same path a token typed into the Settings page takes. Writing the row
        # directly would skip all three.
        await settings.save(
            client_id=stored.get("dhan.client_id")
            or settings.env_fallbacks().get("dhan.client_id"),
            access_token=payload["accessToken"],
            synthetic_feed=False,
        )

        renewed = inspect_token(payload["accessToken"])
        logger.info(
            "Dhan access token renewed automatically; the new one is valid for "
            "%.1f more hour(s)",
            (renewed.seconds_remaining or 0) / 3600,
        )
        return RefreshOutcome(
            True,
            "Renewed for another 24 hours.",
            expires_at=renewed.expires_at,
        )


class TokenRefreshMonitor:
    """The task that asks `refresh_if_due` on a timer.

    Shaped like `OrderMatcher` and the swing monitors: its own task, its own
    session per pass, off the tick path, a `status()` the health page reads,
    and a name registered in `task_inspector`.
    """

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self.passes = 0
        self.renewals = 0
        self.last_pass_at: Optional[datetime] = None
        self.last_outcome: Optional[str] = None
        self.last_error: Optional[str] = None
        self.needs_human = False
        # The token Dhan last REFUSED, as a fingerprint. While the stored token
        # is still that one there is nothing to gain by asking again -- but the
        # moment a new one is pasted in, the refusal no longer applies and the
        # monitor picks straight back up. A fingerprint rather than the token
        # itself: this is held in memory and reported nowhere, and a secret
        # that need not exist should not.
        self._refused_fingerprint: Optional[str] = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        if not TokenRefreshService._enabled():  # noqa: SLF001
            logger.info("Dhan token auto-renewal is switched off; not starting the task")
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="dhan-token-refresh")
        logger.info(
            "Dhan token refresh started (checking every %s s, renewing under %s h)",
            CHECK_INTERVAL_SECONDS, TokenRefreshService._renew_before_hours(),  # noqa: SLF001
        )

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _run(self) -> None:
        from src.database.session import session_scope

        # One pass on start-up, before the first sleep: a machine that was
        # asleep overnight should not wait fifteen minutes to find out its
        # token is nearly dead.
        while not self._stopping:
            try:
                async with session_scope() as session:
                    service = TokenRefreshService(session)
                    fingerprint = await self._token_fingerprint(session)
                    if (
                        self._refused_fingerprint is not None
                        and fingerprint == self._refused_fingerprint
                    ):
                        # Already refused, and the token has not changed. Say
                        # nothing further until somebody pastes a new one.
                        outcome = RefreshOutcome(
                            False,
                            "Dhan refused to renew this token and it has not "
                            "changed since. Waiting for a new one.",
                            needs_human=True,
                        )
                    else:
                        self._refused_fingerprint = None
                        outcome = await service.refresh_if_due()
                        if outcome.needs_human:
                            self._refused_fingerprint = fingerprint

                self.passes += 1
                self.last_pass_at = utc_now()
                self.last_outcome = outcome.reason
                self.needs_human = outcome.needs_human
                if outcome.renewed:
                    self.renewals += 1
                    self._refused_fingerprint = None
                    await self._reconfigure_feed()
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - a bad pass must not end the task
                self.last_error = f"{type(error).__name__}: {error}"
                logger.exception("Dhan token refresh pass failed")

            try:
                await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                raise

    @staticmethod
    async def _token_fingerprint(session) -> Optional[str]:
        """A short, non-reversible stand-in for the stored token.

        Used only to notice that the token CHANGED. A hash rather than the
        token so that nothing here holds a credential it has no use for.
        """
        import hashlib

        from src.settings.database.db_operations.app_setting_repository import (
            AppSettingRepository,
        )
        from src.settings.services.settings_service import (
            KEY_ACCESS_TOKEN,
            SettingsService,
        )

        settings = SettingsService(AppSettingRepository(session))
        stored = await settings.load_stored()
        token = stored.get(KEY_ACCESS_TOKEN) or settings.env_fallbacks().get(
            KEY_ACCESS_TOKEN
        )
        if not token:
            return None
        return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    async def _reconfigure_feed() -> None:
        """Point the live feed at the new token.

        `reconfigure()` rebuilds the feed client and the book but deliberately
        KEEPS the broadcaster, so connected browser tabs are not stranded on a
        dead one (backend/CLAUDE.md section 7). Without this the process would
        hold a fresh token and go on using the old one until something
        restarted it.
        """
        from src.market.services.feed_manager import get_feed_manager

        try:
            await get_feed_manager().reconfigure()
            logger.info("Market feed reconnected with the renewed token")
        except Exception:  # noqa: BLE001
            logger.exception(
                "The token was renewed but the feed could not be reconnected; "
                "it will pick the new token up on the next restart"
            )

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": TokenRefreshService._enabled(),  # noqa: SLF001
            "running": self._task is not None and not self._task.done(),
            "intervalSeconds": CHECK_INTERVAL_SECONDS,
            "renewBeforeHours": TokenRefreshService._renew_before_hours(),  # noqa: SLF001
            "passes": self.passes,
            "renewals": self.renewals,
            "lastPassAt": self.last_pass_at.isoformat() if self.last_pass_at else None,
            "lastOutcome": self.last_outcome,
            "lastError": self.last_error,
            # True when renewal cannot succeed without somebody pasting a new
            # token. The health page and the alerts read this.
            "needsHuman": self.needs_human,
        }


_monitor: Optional[TokenRefreshMonitor] = None


def get_token_refresh_monitor() -> TokenRefreshMonitor:
    global _monitor
    if _monitor is None:
        _monitor = TokenRefreshMonitor()
    return _monitor


async def shutdown_token_refresh_monitor() -> None:
    global _monitor
    if _monitor is not None:
        await _monitor.stop()
        _monitor = None
