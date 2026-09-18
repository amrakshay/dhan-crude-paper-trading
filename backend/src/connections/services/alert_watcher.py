"""The system-health conditions worth waking somebody up for.

The owner asked for "errors, system health, and the rotation's trades" and left
the health list to me. These are the ones where NOT knowing costs something
real, in the order I would want them:

  1. **A background task that should be running is not.** `task_inspector`
     already computes `missing` against `expected_task_names`. A dead
     `swing-scheduler` on an ARMED strategy means nothing decides and nothing
     trades, silently, and it is the failure the whole Health tab was built to
     surface. This is the single most valuable alert here.
  2. **The Dhan token could not be renewed.** Renewal is automatic now, so its
     FAILURE is the event. "Will retry" and "cannot renew, a human must paste a
     new token" are kept apart -- only the second needs waking somebody up.
  3. **The feed is stale or disconnected while a market is open.** Stale prices
     that look live are described in `frontend/CLAUDE.md` section 3 as the worst
     failure this tool can have.
  4. **A scheduled session was missed.** The detector already exists and
     already reports; this forwards it.
  5. **Daily bars too stale to trade on.** `staleness_reason()` is the exact
     sentence the rebalance would refuse with. Sent when the condition appears,
     not at 09:16 when it is too late to fix.
  6. **A stop triggered but its exit is deferred to the next open.** A decision
     the strategy took that a human would want to know about the same evening.
  7. **The application started.** Low volume, high signal: a restart you did not
     perform is worth a message. Raised from the lifespan, not from here.

**Every one of these hangs off a place that ALREADY KNOWS the fact.** Nothing
here is new measurement, nothing is on the tick path, and every figure is an
existing `status()` dict, a repository read or an `asyncio.all_tasks()` walk --
the same rule `src/health/` and the swing Health tab follow.

**De-duplication is keyed on the EVENT.** A health condition persists: a dead
task stays dead. Keying on the message would send the same sentence every
minute until somebody fixed it, which is the flood this whole design exists to
avoid.

Deliberately NOT alerted on: anything WARNING-level and routine, the synthetic
feed being on (the UI already says so permanently), or a strategy being
switched off by somebody who is looking at the screen at the time.
"""
import asyncio
from typing import Any, Dict, List, Optional

from src import config_utils
from src.connections.database.db_models.alert_model import (
    SEVERITY_CRITICAL,
    SEVERITY_INFO,
    SEVERITY_WARNING,
)
from src.core.time_utils import utc_now
from src.logging_config import get_logger

logger = get_logger("connections.watcher")

# Slower than the dispatcher: none of these conditions appears and disappears
# inside a minute, and a watcher that is itself a load source is reporting on a
# system it distorted.
DEFAULT_INTERVAL_SECONDS = 60

# How long the feed may be quiet, while a market is open, before it is stale.
# Dhan drops a connection at 40 s, so this is deliberately past that cliff --
# the watchdog gets its chance to reconnect first.
FEED_STALE_SECONDS = 90


class AlertWatcher:
    """Evaluates the watched conditions and raises an alert for each."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self.passes = 0
        self.raised = 0
        self.last_pass_at = None
        self.last_error: Optional[str] = None
        self.last_conditions: List[str] = []

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @staticmethod
    def _interval() -> float:
        return float(
            config_utils.get_property_value_int(
                "connections.alert_watch_interval_seconds", DEFAULT_INTERVAL_SECONDS
            )
        )

    @staticmethod
    def _enabled() -> bool:
        return config_utils.get_property_value_boolean("connections.alerts_enabled", True)

    async def start(self) -> None:
        if self.running:
            return
        if not self._enabled():
            logger.info("Alerts are switched off; not starting the health watcher")
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="alert-watcher")
        logger.info("Alert health watcher started (every %.0f s)", self._interval())

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
        while not self._stopping:
            try:
                await self.run_once()
                self.passes += 1
                self.last_pass_at = utc_now()
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                self.last_error = f"{type(error).__name__}: {error}"
                logger.warning(
                    "Alert health pass failed: %s", type(error).__name__, exc_info=True
                )
            try:
                await asyncio.sleep(self._interval())
            except asyncio.CancelledError:
                raise

    async def run_once(self) -> List[str]:
        """Evaluate every condition. Returns the names of the ones that fired."""
        from src.connections.services.alert_service import AlertService
        from src.database.session import session_scope

        fired: List[str] = []
        async with session_scope() as session:
            service = AlertService(session)
            for check in (
                self._check_missing_tasks,
                self._check_token_renewal,
                self._check_feed_staleness,
                self._check_missed_sessions,
                self._check_bar_staleness,
                self._check_deferred_stops,
            ):
                try:
                    name = await check(service)
                except Exception:  # noqa: BLE001 - one bad check must not stop the rest
                    logger.warning(
                        "An alert health check failed: %s", check.__name__, exc_info=True
                    )
                    continue
                if name:
                    fired.append(name)
            await session.commit()

        self.raised += len(fired)
        self.last_conditions = fired
        return fired

    # --- 1. a background task that should be running is not ----------------
    async def _check_missing_tasks(self, service) -> Optional[str]:
        from src.health.services import task_inspector

        flags = task_inspector.feed_flags()
        table = task_inspector.inspect(
            is_synthetic=flags["is_synthetic"], feed_running=flags["feed_running"]
        )
        missing = table.get("missing") or []
        if not missing:
            return None

        # An ARMED strategy with a dead scheduler is the case this alert
        # exists for, so it is named rather than left inside a list.
        armed = self._armed_strategies()
        severity = (
            SEVERITY_CRITICAL
            if armed and ("swing-scheduler" in missing or "swing-stop-monitor" in missing)
            else SEVERITY_WARNING
        )
        body = [
            "These background tasks should be running and are not:",
            *(f"  {name} -- {task_inspector.TASK_DESCRIPTIONS.get(name, '')}" for name in missing),
        ]
        if armed:
            body += [
                "",
                "ARMED: " + ", ".join(armed) + ". Nothing decides and nothing "
                "trades while a scheduler is dead.",
            ]
        await service.record_health(
            event=f"missing-tasks|{','.join(sorted(missing))}",
            title=f"{len(missing)} background task(s) are not running",
            body="\n".join(body),
            severity=severity,
        )
        return "missing-tasks"

    @staticmethod
    def _armed_strategies() -> List[str]:
        from src.strategies.services.strategy_registry import get_strategy_registry

        registry = get_strategy_registry()
        return [
            definition.key
            for definition in registry.automated()
            if registry.is_armed(definition.key)
        ]

    # --- 2. the Dhan token ------------------------------------------------
    async def _check_token_renewal(self, service) -> Optional[str]:
        from src.settings.services.settings_service import (
            KEY_ACCESS_TOKEN,
            SettingsService,
            inspect_token,
        )
        from src.settings.database.db_operations.app_setting_repository import (
            AppSettingRepository,
        )

        if config_utils.get_property_value_boolean("market_feed.synthetic_feed", False):
            return None

        settings = SettingsService(AppSettingRepository(service.session))
        _client_id, token = await settings.resolve_credentials()
        info = inspect_token(token)
        if not info.present:
            return None

        if info.expired:
            # The one that needs a human. Dhan renews only an ACTIVE token, so
            # nothing this process can do will recover from here.
            await service.record_health(
                event="token-lapsed",
                title="The Dhan access token has expired",
                body=(
                    "The stored Dhan access token expired at "
                    f"{info.expires_at.isoformat() if info.expires_at else 'an unknown time'}. "
                    "Automatic renewal cannot recover from this: Dhan renews only "
                    "a token that is still active, and this application "
                    "deliberately cannot mint one. Generate a new token on Dhan "
                    "Web and save it on the Connections page. Until then the live "
                    "feed, the chart, the option chain and the overnight bar "
                    "refresh are all stopped."
                ),
                severity=SEVERITY_CRITICAL,
            )
            return "token-lapsed"

        from src.settings.services.token_refresh_service import (
            get_token_refresh_monitor,
        )

        status = get_token_refresh_monitor().status()
        if status.get("lastError"):
            await service.record_health(
                event="token-renewal-failed",
                title="Dhan token renewal is failing",
                body=(
                    f"The renewal task reported: {status['lastError']}\n"
                    f"It will try again. The token still has "
                    f"{(info.seconds_remaining or 0) / 3600:.1f} hour(s) on it."
                ),
                severity=SEVERITY_WARNING,
            )
            return "token-renewal-failed"
        return None

    # --- 3. a stale feed while a market is open ----------------------------
    async def _check_feed_staleness(self, service) -> Optional[str]:
        from src.market.services.feed_manager import get_feed_manager
        from src.strategies.services.market_clock import enabled_market_open

        manager = get_feed_manager()
        if manager.is_synthetic:
            # The synthetic feed being on is not an alert -- the UI says so
            # permanently on every screen.
            return None
        if not enabled_market_open():
            return None

        status = manager.status() or {}
        feed = status.get("feed") or {}
        state = feed.get("state")
        age_ms = feed.get("lastMessageAgeMs")
        # None is NOT zero. A feed that has never delivered a message has no
        # age, and treating that as "0 seconds ago" would report the most
        # broken case as the healthiest one.
        age = (float(age_ms) / 1000) if age_ms is not None else None

        if state in (None, "DISCONNECTED", "ERROR", "DISABLED"):
            await service.record_health(
                event="feed-down",
                title="The market feed is not connected while a market is open",
                body=(
                    f"Feed state: {state}. Prices are not arriving, and anything "
                    f"reading a mark right now is reading the last one it saw."
                ),
                severity=SEVERITY_CRITICAL,
            )
            return "feed-down"

        if age is not None and age > FEED_STALE_SECONDS:
            await service.record_health(
                event="feed-stale",
                title="The market feed has gone quiet while a market is open",
                body=(
                    f"No message for {age:.0f} seconds (Dhan drops a "
                    f"connection at 40 s, and the watchdog should have "
                    f"reconnected by now). A stale book that looks live is the "
                    f"worst failure this tool can have."
                ),
                severity=SEVERITY_WARNING,
            )
            return "feed-stale"
        return None

    # --- 4. a missed scheduled session -------------------------------------
    async def _check_missed_sessions(self, service) -> Optional[str]:
        from src.swing.services.scheduler import get_swing_scheduler

        missed = get_swing_scheduler().missed or []
        if not missed:
            return None
        lines = [
            "The decision journal has no record for these sessions:",
            *(
                f"  {one.strategy_key} {one.kind}: "
                f"{', '.join(session.isoformat() for session in one.sessions)}"
                for one in missed
            ),
            "",
            "Nothing is re-decided days later on bars that may since have been "
            "restated -- the next scheduled run decides from fresh bars.",
        ]
        await service.record_health(
            event="missed-sessions|"
            + ",".join(sorted(one.strategy_key + one.kind for one in missed)),
            title=f"{len(missed)} scheduled session(s) were missed",
            body="\n".join(lines),
            severity=SEVERITY_WARNING,
        )
        return "missed-sessions"

    # --- 5. daily bars too stale to trade on -------------------------------
    async def _check_bar_staleness(self, service) -> Optional[str]:
        from src.daily_bars.database.db_operations.daily_bar_repository import (
            DailyBarRepository,
        )
        from src.strategies.services.strategy_registry import get_strategy_registry
        from src.swing.services.execution_service import staleness_reason
        from src.swing.services.swing_parameters import SwingParameters

        registry = get_strategy_registry()
        for definition in registry.automated():
            if not registry.is_enabled(definition.key):
                continue
            try:
                parameters = SwingParameters.from_definition(definition)
                symbol = definition.reference_instrument(
                    parameters.regime.index_role
                )
            except Exception:  # noqa: BLE001
                continue
            if symbol is None:
                continue
            latest = await DailyBarRepository(service.session).latest_date(
                symbol.symbol, definition.exchange_segment
            )
            if latest is None:
                continue
            # The SAME sentence the rebalance would refuse with, lifted to
            # module scope for exactly this reason -- a second description of
            # the rule would drift from it.
            reason = staleness_reason(latest)
            if reason is None:
                continue
            await service.record_health(
                event=f"stale-bars|{definition.key}",
                title=f"{definition.key}: the stored daily bars are too old to trade on",
                body=reason
                + "\n\nSent now rather than at the next order window, so there "
                "is still time to fix it.",
                severity=SEVERITY_WARNING,
            )
            return "stale-bars"
        return None

    # --- 6. a stop that triggered but deferred -----------------------------
    async def _check_deferred_stops(self, service) -> Optional[str]:
        from src.swing.database.db_operations.swing_stop_repository import (
            SwingStopRepository,
        )

        triggered = await SwingStopRepository(service.session).list_triggered()
        if not triggered:
            return None
        lines = [
            "These trailing stops have fired and their exit is waiting for the "
            "next session's open:",
            *(
                f"  {stop.symbol} -- stop {stop.stop_price}"
                for stop in triggered
            ),
            "",
            "NSE's Closing Auction Session ends continuous trading at 15:15 for "
            "F&O-eligible names, and this simulator has no model of a call "
            "auction, so a stop that fires after it is recorded as triggered "
            "and fills at the next open.",
        ]
        await service.record_health(
            event="deferred-stops|" + ",".join(sorted(stop.symbol for stop in triggered)),
            title=f"{len(triggered)} triggered stop(s) are waiting for the next open",
            body="\n".join(lines),
            severity=SEVERITY_INFO,
        )
        return "deferred-stops"

    def status(self) -> Dict[str, Any]:
        return {
            "running": self.running,
            "enabled": self._enabled(),
            "intervalSeconds": self._interval(),
            "passes": self.passes,
            "raised": self.raised,
            "lastPassAt": self.last_pass_at.isoformat() if self.last_pass_at else None,
            "lastConditions": list(self.last_conditions),
            "lastError": self.last_error,
        }


_watcher: Optional[AlertWatcher] = None


def get_alert_watcher() -> AlertWatcher:
    global _watcher
    if _watcher is None:
        _watcher = AlertWatcher()
    return _watcher


async def shutdown_alert_watcher() -> None:
    global _watcher
    if _watcher is not None:
        await _watcher.stop()
        _watcher = None
