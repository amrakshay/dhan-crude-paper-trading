"""Everything this process already knows about itself, in one payload.

Almost nothing here is new measurement. Nine components already expose a
`status()` or `stats()` dict and only one of them was reachable from the API;
this service is mostly the aggregation that was missing, plus the thresholds
that turn a raw counter into a judgement (a last-message age means nothing
until it is read against the 40-second cliff that Dhan drops us at).

Three rules govern what may appear here:

- **No secret, ever.** This page exists to display internals, which makes it
  the single most likely place to leak one. The Dhan access token is reported
  as a mask plus decoded JWT metadata (`inspect_token`), never in full; the
  database URL goes through `redact_database_url`; log lines come out of a
  buffer that has already been through `RedactingFormatter`. There are
  assertions for all of this in `tests/test_no_secrets_in_logs.py`.
- **Nothing on the tick path.** Every number here is either already-existing
  state or an `asyncio.all_tasks()` walk. `feed_protocol` and
  `MarketBook.apply_packet` are untouched.
- **Counters are labelled with their epoch.** `FeedManager.reconfigure()`
  replaces the feed client outright, so `framesReceived`, `packetsApplied` and
  `reconnects` reset on a settings save. Verified against a live reconfigure:
  the greeks poller and the broadcaster are *not* replaced -- the poller is
  constructed once in `FeedManager.__init__` and only stopped and restarted, and
  the broadcaster is deliberately kept alive so browser tabs are not stranded --
  so their counters, and those of the option chain client the poller owns, run
  from process start. A page reporting "requests: 12" without saying since when
  is lying quietly, and saying the wrong "since when" is worse.

Cross-package singletons are imported inside the functions that need them, per
backend/CLAUDE.md section 1, because importing them at module scope would close
an import cycle.
"""
import os
from typing import Any, Dict, List, Optional

from src import config_utils, log_buffer
from src.constants import ConnectionState
from src.database.connection import get_database_url, redact_database_url
from src.health.services import process_stats, task_inspector
from src.logging_config import get_logger
from src.market.services.market_book import now_ms

logger = get_logger("health.service")

# Dhan allows this many concurrent feed connections per user. Documented in
# dhan_feed_client's module docstring, and the reason browser tabs are fanned
# out from one shared connection rather than each opening their own.
DEFAULT_MAX_FEED_CONNECTIONS = 5


def _config_path() -> str:
    return config_utils.get_config_path()


def _log_dir() -> str:
    return os.path.abspath(config_utils.get_property_value("logging.log_dir", "./logs"))


# --- process ---------------------------------------------------------------
def _database_health(schema_revision: Optional[str]) -> Dict[str, Any]:
    url = get_database_url()
    is_sqlite = url.startswith("sqlite")
    return {
        "backend": "SQLite" if is_sqlite else url.partition(":")[0],
        "urlRedacted": redact_database_url(url),
        "schemaRevision": schema_revision,
        # SQLite under aiosqlite uses a NullPool -- pool sizing options do not
        # apply and passing them raises -- so there is no pool to report. The
        # MySQL branch does configure one, but MySQL has never been executed
        # against a live server here, so its numbers are stated as configured
        # rather than observed.
        "pooling": (
            "NullPool — SQLite has no connection pool"
            if is_sqlite
            else "configured pool_size=10, max_overflow=20 (never run against a live MySQL server)"
        ),
        "poolStatsAvailable": False,
    }


def process_health(schema_revision: Optional[str] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "startedAtMs": process_stats.started_at_ms(),
        "uptimeSeconds": process_stats.uptime_seconds(),
        # Stated rather than counted. server.py forces workers=1 because more
        # uvicorn workers would mean more upstream Dhan connections and a
        # desynchronised book (root CLAUDE.md section 4).
        "workers": config_utils.get_property_value_int("server.uvicorn_workers", 1),
        "concurrencyModel": (
            "One process, one uvicorn worker, one asyncio event loop. "
            "No thread pool and no worker pool — the concurrency here is the "
            "named asyncio tasks below."
        ),
        "configPath": _config_path(),
        "logDir": _log_dir(),
        "logLevel": config_utils.get_property_value("logging.level", "INFO"),
        "staticDir": config_utils.get_property_value("server.static_dir", "") or None,
        "database": _database_health(schema_revision),
        "resources": process_stats.resources(),
    }
    payload.update(process_stats.interpreter())
    return payload


# --- upstream feed ---------------------------------------------------------
def _upstream_feed_health(manager: Any, feed_status: Dict[str, Any]) -> Dict[str, Any]:
    """The one scarce connection, with the thresholds that make it readable."""
    inactivity_seconds = config_utils.get_property_value_float(
        "market_feed.server_inactivity_timeout_seconds", 40.0
    )
    last_message_age_ms = feed_status.get("lastMessageAgeMs")
    timeout_ms = inactivity_seconds * 1000
    headroom_ms = (
        None if last_message_age_ms is None else max(0.0, timeout_ms - last_message_age_ms)
    )

    common: Dict[str, Any] = {
        "synthetic": manager.is_synthetic,
        "state": feed_status.get("state"),
        "detail": feed_status.get("detail"),
        "subscribed": feed_status.get("subscribed"),
        "maxInstrumentsPerConnection": config_utils.get_property_value_int(
            "market_feed.max_instruments_per_connection", 5000
        ),
        "maxInstrumentsPerSubscribe": config_utils.get_property_value_int(
            "market_feed.max_instruments_per_subscribe", 100
        ),
        "framesReceived": feed_status.get("framesReceived"),
        "packetsApplied": feed_status.get("packetsApplied"),
        "reconnects": feed_status.get("reconnects"),
        "connectedAtMs": feed_status.get("connectedAtMs"),
        "error": manager.last_error,
    }

    if manager.is_synthetic:
        # There is no socket at all in synthetic mode. Showing a healthy-looking
        # connection card here would be the same lie the chart refuses to tell.
        common.update(
            {
                "hasUpstreamConnection": False,
                "upstreamNote": (
                    "No upstream connection — prices are generated locally by the "
                    "synthetic feed. Nothing is being read from Dhan."
                ),
                "mode": None,
                "requestCode": None,
                "lastMessageAgeMs": last_message_age_ms,
                "inactivityTimeoutSeconds": None,
                "inactivityHeadroomMs": None,
                "connectionsUsedByThisProcess": 0,
                "connectionBudget": None,
                "reconnectBackoffInitialSeconds": None,
                "reconnectBackoffMaxSeconds": None,
            }
        )
        return common

    state = feed_status.get("state")
    connected = state == ConnectionState.CONNECTED.value

    # Headroom is only meaningful when something is subscribed. Dhan sends data
    # for instruments this process asked for and nothing else -- its protocol
    # pings never surface as messages -- so with an empty subscription set the
    # figure drains to zero on a perfectly healthy socket. Reporting it anyway
    # showed a bar sliding to zero and back every 45 seconds, which is the same
    # lie the synthetic card refuses to tell in the other direction.
    subscribed = feed_status.get("subscribed") or 0
    if not subscribed:
        headroom_ms = None

    common.update(
        {
            "hasUpstreamConnection": True,
            "upstreamNote": (
                None
                if subscribed
                else (
                    "Connected, but nothing is subscribed, so no data is expected "
                    "and the inactivity countdown does not apply. Enable a "
                    "strategy with instruments, or refresh the instrument master."
                )
            ),
            "mode": feed_status.get("mode"),
            "requestCode": feed_status.get("requestCode"),
            "lastMessageAgeMs": last_message_age_ms,
            # Dhan drops a silent connection after this long; the watchdog task
            # exists for exactly this. An age creeping towards it is the most
            # actionable number on this page.
            "inactivityTimeoutSeconds": inactivity_seconds if subscribed else None,
            "inactivityHeadroomMs": headroom_ms,
            # This process uses at most one slot. It cannot see what other
            # processes on the same credentials are using, so the claim is
            # deliberately scoped to "by this process".
            "connectionsUsedByThisProcess": 1 if connected else 0,
            "connectionBudget": config_utils.get_property_value_int(
                "market_feed.max_connections_per_user", DEFAULT_MAX_FEED_CONNECTIONS
            ),
            "reconnectBackoffInitialSeconds": config_utils.get_property_value_float(
                "market_feed.reconnect_backoff_initial_seconds", 1.0
            ),
            "reconnectBackoffMaxSeconds": config_utils.get_property_value_float(
                "market_feed.reconnect_backoff_max_seconds", 30.0
            ),
        }
    )
    return common


# --- Dhan REST -------------------------------------------------------------
def _dhan_api_health(manager: Any) -> Dict[str, Any]:
    """The two plain-HTTPS clients, kept apart from the WebSocket card.

    "Feed connected" must never be read as "greeks are arriving": these fail
    independently of the socket, which is exactly why `MarketBook.merge_greeks`
    is a separate entry point from `apply_packet`.
    """
    from src.market.services.candle_service import get_candle_service

    chain_stats = manager.greeks_poller.client.stats()
    charts_stats = get_candle_service().client.stats()

    return {
        "optionChain": {
            **chain_stats,
            "purpose": "Greeks, IV and OI for the subscribed expiries",
            "limit": (
                "Dhan documents one request per 3 s per underlying+expiry. "
                "Expiries are polled concurrently; the client self-throttles."
            ),
            "limitIsOurs": False,
            # The poller that owns this client is built once in
            # FeedManager.__init__ and only stopped/restarted by reconfigure(),
            # so unlike the feed client's counters these do NOT reset on a save.
            "countersSince": "process start — the greeks poller survives a settings save",
        },
        "charts": {
            **charts_stats,
            "purpose": "Candle history for the price chart",
            "limit": (
                "One request per second per series. This is our own floor, not "
                "Dhan's — Dhan publishes no per-endpoint limit for charts."
            ),
            "limitIsOurs": True,
            "countersSince": "process start — the charts client survives a settings save",
        },
    }


# --- credentials -----------------------------------------------------------
def _stored_settings_health(stored: Optional[Dict[str, Optional[str]]]) -> Dict[str, Any]:
    """Whether what is saved in the database is what this process is using.

    Settings saved in the UI beat `.env`: `apply_to_config()` overlays them onto
    the in-memory config, in the lifespan before the feed starts and again after
    every save. When that holds, this reports nothing.

    It is checked anyway because the failure is silent. Until 2026-09-17 the
    startup call did not exist, and every restart quietly reverted the running
    configuration to `.env` while the Settings page went on showing the stored
    values -- the feed used the wrong credentials and nothing said so. This
    check is what found it. It still earns its place: a token that cannot be
    decrypted (a rotated `APP_ENCRYPTION_KEY`) falls back to `.env` by design,
    and a future refactor that drops the startup call would otherwise be
    invisible again.

    Values are compared but never reported -- only key names leave this
    function, because the decrypted access token is among them.
    """
    if stored is None:
        return {
            "known": False,
            "storedKeys": [],
            "appliedToThisProcess": None,
            "keysDiffering": [],
            "note": "The stored settings could not be read.",
        }

    differing = []
    for key, stored_value in stored.items():
        effective = config_utils.get_property_value(key, "")
        # Normalise: the synthetic flag is stored as a string and read as one.
        if str(stored_value or "") != str(effective or ""):
            differing.append(key)

    return {
        "known": True,
        "storedKeys": sorted(stored.keys()),
        "appliedToThisProcess": not differing,
        "keysDiffering": sorted(differing),
        "note": (
            None
            if not differing
            else (
                "Saved settings are NOT in effect in this process, which should "
                "not happen -- they are applied at startup and on every save. "
                "The usual cause is a stored value that could not be read, such "
                "as a token encrypted under a rotated APP_ENCRYPTION_KEY, which "
                "falls back to .env by design. Re-save on the Settings page, and "
                "check app.log for a decryption warning."
            )
        ),
    }


def _credentials_health(stored: Optional[Dict[str, Optional[str]]] = None) -> Dict[str, Any]:
    """Token presence, mask and expiry. Never the token itself.

    `inspect_token` is reused rather than re-implemented: it already decodes
    the JWT's `exp` without verifying the signature (Dhan signed it with a key
    we do not hold, and this is metadata, not trust), and the Settings page
    renders its countdown off the same fields.

    Everything reported here is the EFFECTIVE configuration -- what the feed
    would actually use -- not what is saved in the database. Where the two
    differ, `storedSettings` says so.
    """
    from src.market.services import dhan_auth_state
    from src.market.services.dhan_feed_client import DhanFeedClient
    from src.settings.services.settings_service import inspect_token

    client_id, token = DhanFeedClient.credentials()
    info = inspect_token(token)
    # TWO DIFFERENT QUESTIONS, reported as two. `inspect_token` answers "when
    # does this expire", read locally out of the JWT; `dhan_auth_state` answers
    # "does Dhan still accept it", remembered from the last real call. On
    # 2026-09-19 they disagreed for four hours -- a revoked token showing a
    # comfortable twenty-hour countdown -- and a page that reported only the
    # first was confidently wrong.
    accepted = dhan_auth_state.state_for(token)

    return {
        "storedSettings": _stored_settings_health(stored),
        # The client id is an account identifier, not a secret: the Settings
        # API already returns it in plain and it is safe to log (SECRET_KEYS
        # holds the access token alone).
        "clientId": client_id or None,
        "clientIdPresent": bool(client_id),
        "token": {
            "present": info.present,
            "masked": info.masked,
            "isJwt": info.is_jwt,
            "expiresAt": info.expires_at.isoformat() if info.expires_at else None,
            "secondsRemaining": info.seconds_remaining,
            "expired": info.expired,
            # The countdown above is DECLARED validity. This is observed.
            "dhanVerdict": accepted.as_dict(),
        },
        "syntheticFeed": config_utils.get_property_value_boolean(
            "market_feed.synthetic_feed", True
        ),
        "encryptionConfigured": bool(
            config_utils.get_property_value("security.encryption_key", "")
            or config_utils.get_property_value("auth.jwt_secret", "")
        ),
    }


# --- problems --------------------------------------------------------------
def problems_health(limit: int) -> Dict[str, Any]:
    handler = log_buffer.get_handler()
    if handler is None:  # pragma: no cover - logging is always configured
        return {
            "available": False,
            "entries": [],
            "counts": {},
            "capacity": 0,
            "note": "The in-memory log buffer is not installed.",
        }
    return {
        "available": True,
        "entries": handler.recent(limit),
        "counts": handler.counts(),
        "capacity": handler.capacity,
        "note": (
            "The last WARNING and ERROR records held in memory. This buffer is "
            "process-scoped: it is empty after a restart, and app.log remains "
            "the durable record."
        ),
    }


# --- the whole payload -----------------------------------------------------
def build_health(
    *,
    schema_revision: Optional[str] = None,
    instrument_status: Optional[Dict[str, Any]] = None,
    stored_settings: Optional[Dict[str, Optional[str]]] = None,
    problem_limit: int = 50,
) -> Dict[str, Any]:
    """One snapshot of the process. Nothing here blocks and nothing here writes."""
    from src.chart_trading.services.bracket_monitor import get_bracket_monitor
    from src.charges.services.charges_engine import ChargesEngine
    from src.market.services.feed_manager import get_feed_manager
    from src.orders.services.order_matcher import get_order_matcher
    from src.swing.services.scheduler import get_swing_scheduler
    from src.swing.services.stop_monitor import get_swing_stop_monitor

    manager = get_feed_manager()
    status = manager.status()
    feed_status = status.get("feed") or {}
    greeks_status = status.get("greeks") or {}
    matcher_status = get_order_matcher().status()
    bracket_status = get_bracket_monitor().status()
    swing_stop_status = get_swing_stop_monitor().status()
    swing_scheduler_status = get_swing_scheduler().status()

    # Derived in `task_inspector` so the strategy health tab and this page
    # cannot disagree about which tasks should be alive.
    feed_running = task_inspector.feed_flags()["feed_running"]

    # Counters that belong to a component, joined onto its task's row.
    feed_task_counters = {
        "intervalMs": None,
        "runs": feed_status.get("framesReceived"),
        "runsLabel": "frames received",
        "lastError": feed_status.get("detail") if feed_status.get("detail") else None,
    }
    task_counters: Dict[str, Dict[str, Any]] = {
        "dhan-feed": feed_task_counters,
        "synthetic-feed": {
            "intervalMs": config_utils.get_property_value_int(
                "market_feed.synthetic_tick_interval_ms", 250
            ),
            "runs": feed_status.get("packetsApplied"),
            "runsLabel": "packets applied",
            "lastError": None,
        },
        "broadcaster": {
            "intervalMs": config_utils.get_property_value_int(
                "fanout.broadcast_interval_ms", 100
            ),
            "runs": (status.get("fanout") or {}).get("broadcasts"),
            "runsLabel": "flushes",
            "lastError": None,
        },
        "greeks-poller": {
            "intervalMs": int(
                config_utils.get_property_value_float("greeks_poller.interval_seconds", 3.0)
                * 1000
            ),
            "runs": greeks_status.get("polls"),
            "runsLabel": "polls",
            "lastError": greeks_status.get("error"),
        },
        "order-matcher": {
            "intervalMs": matcher_status.get("intervalMs"),
            "runs": matcher_status.get("runs"),
            "runsLabel": "passes",
            "lastError": matcher_status.get("error"),
        },
        "bracket-monitor": {
            "intervalMs": bracket_status.get("intervalMs"),
            "runs": bracket_status.get("runs"),
            "runsLabel": "passes",
            "lastError": bracket_status.get("error"),
        },
        "swing-stop-monitor": {
            "intervalMs": swing_stop_status.get("intervalMs"),
            "runs": swing_stop_status.get("runs"),
            "runsLabel": "passes",
            "lastError": swing_stop_status.get("error"),
        },
        "swing-scheduler": {
            "intervalMs": int(swing_scheduler_status.get("intervalSeconds") or 0) * 1000,
            "runs": swing_scheduler_status.get("runs"),
            "runsLabel": "clock checks",
            "lastError": swing_scheduler_status.get("error"),
        },
    }

    try:
        rates_version = ChargesEngine().version
    except Exception as exc:
        logger.warning("Could not read the charge rates version: %s", exc)
        rates_version = None

    return {
        "generatedAtMs": now_ms(),
        "process": process_health(schema_revision),
        "tasks": task_inspector.inspect(
            is_synthetic=manager.is_synthetic,
            feed_running=feed_running,
            counters=task_counters,
        ),
        "upstreamFeed": _upstream_feed_health(manager, feed_status),
        "browserSockets": {
            **(status.get("fanout") or {}),
            "clients": manager.broadcaster.clients(),
            "clientCount": manager.broadcaster.client_count,
        },
        "dhanApi": _dhan_api_health(manager),
        "credentials": _credentials_health(stored_settings),
        "book": status.get("book") or {},
        "market": status.get("market") or {},
        "workers": {
            "orderMatcher": matcher_status,
            "bracketMonitor": bracket_status,
            # The rotation's own two. Both report counters they already keep;
            # nothing here is measured for this page.
            "swingStopMonitor": swing_stop_status,
            "swingScheduler": swing_scheduler_status,
            "greeksPoller": {
                "enabled": greeks_status.get("enabled"),
                "synthetic": greeks_status.get("synthetic"),
                "intervalSeconds": greeks_status.get("intervalSeconds"),
                "polls": greeks_status.get("polls"),
                "legsMerged": greeks_status.get("legsMerged"),
                "lastPollAgeMs": greeks_status.get("lastPollAgeMs"),
                "error": greeks_status.get("error"),
            },
        },
        "dataFreshness": {
            "instruments": instrument_status,
            "chargeRatesVersion": rates_version,
            "chargeRatesNote": (
                "The per-rate as-of dates live in comments in the rate card, "
                "not as structured fields, so staleness per rate cannot be "
                "computed without promoting them to real keys."
            ),
            "lastResyncMs": status.get("lastResyncMs"),
            "subscribedExpiries": status.get("subscribedExpiries") or [],
            "nearFutureSecurityId": status.get("nearFutureSecurityId"),
            "strikeWindow": status.get("strikeWindow"),
            "windowCentre": status.get("windowCentre"),
        },
        # What is switched on, and the ongoing consequences of what is not.
        # A strategy switched off with open positions is a CONDITION, not an
        # event: those positions stop being marked for as long as it is off,
        # and this page is where that belongs.
        "features": _features_health(),
        "problems": problems_health(problem_limit),
    }


def _features_health() -> Dict[str, Any]:
    """Which strategies and capabilities are running, and what is not.

    No secrets here and none possible: a strategy's key, label, underlying and
    exchange segment are the same public facts the Option Chain page shows.
    """
    from src.market.services.feed_manager import get_feed_manager
    from src.strategies.services.strategy_registry import get_strategy_registry

    registry = get_strategy_registry()
    manager = get_feed_manager()

    strategies = []
    for definition in registry.all():
        enabled = registry.is_enabled(definition.key)
        state = manager.strategy_state.get(definition.key)
        strategies.append(
            {
                "key": definition.key,
                "label": definition.label,
                "enabled": enabled,
                "symbol": definition.symbol,
                "exchangeSegment": definition.exchange_segment,
                "instrumentsSubscribed": state.instrument_count if state else 0,
                "subscribedExpiries": (
                    [expiry.isoformat() for expiry in state.subscribed_expiries]
                    if state
                    else []
                ),
                "activeCapabilities": sorted(
                    capability
                    for capability in definition.capabilities
                    if registry.capability_active(capability, definition.key)
                ),
            }
        )

    return {
        "strategies": strategies,
        "capabilities": registry.capability_states(),
        "pages": sorted(registry.feature_pages()),
        "disabledStrategies": [
            definition.key for definition in registry.disabled()
        ],
    }


def summarise(health: Dict[str, Any]) -> Dict[str, Any]:
    """A one-line verdict, derived from the payload rather than measured again.

    Deliberately conservative: anything it cannot confirm is healthy is not
    reported as healthy. The headline exists so the page answers "is this thing
    OK?" before the operator reads a single card.
    """
    problems: List[str] = []

    tasks = health.get("tasks") or {}
    for name in tasks.get("missing") or []:
        problems.append(f"Background task '{name}' is not running")
    for name in tasks.get("duplicated") or []:
        problems.append(f"Background task '{name}' is running more than once")

    feed = health.get("upstreamFeed") or {}
    # IDLE is not a problem. It means no strategy has an instrument to
    # subscribe right now -- the ordinary overnight state -- so there is no
    # socket and nothing is wrong. Reporting it here would put a permanent
    # entry on the problems list for a system working exactly as configured,
    # which is the same "off is not broken" rule the strategy health tabs
    # follow.
    if (
        feed.get("hasUpstreamConnection")
        and feed.get("state")
        not in (ConnectionState.CONNECTED.value, ConnectionState.IDLE.value)
    ):
        problems.append(f"Upstream feed is {feed.get('state')}")
    if feed.get("error"):
        problems.append("The feed reported an error")

    headroom = feed.get("inactivityHeadroomMs")
    if headroom is not None and headroom < 10_000:
        problems.append("The upstream feed is close to Dhan's inactivity drop")

    stored = (health.get("credentials") or {}).get("storedSettings") or {}
    if stored.get("appliedToThisProcess") is False:
        problems.append(
            "Settings saved in the UI are not in effect in this process "
            f"({', '.join(stored.get('keysDiffering') or [])})"
        )

    token = (health.get("credentials") or {}).get("token") or {}
    # Refusal first: a revoked token is a live outage, and its declared
    # expiry is irrelevant to it. Reporting "expires in 20h" while every Dhan
    # call is being refused is the failure this ordering exists to prevent.
    if (token.get("dhanVerdict") or {}).get("verdict") == "REFUSED":
        problems.append(
            "Dhan is REFUSING the access token, whatever its declared expiry "
            "says. Generate a new one on Dhan Web and save it on Connections."
        )
    elif token.get("expired"):
        problems.append("The Dhan access token has expired")
    elif token.get("secondsRemaining") is not None and token["secondsRemaining"] < 2 * 3600:
        problems.append("The Dhan access token expires in under two hours")

    for label, worker in (health.get("workers") or {}).items():
        if (worker or {}).get("error"):
            problems.append(f"{label} reported an error")

    dropped = (health.get("browserSockets") or {}).get("droppedTotal") or 0
    if dropped:
        problems.append(f"{dropped} browser-socket resync(s) from clients falling behind")

    return {
        "healthy": not problems,
        "problems": problems,
        "synthetic": bool(feed.get("synthetic")),
    }
