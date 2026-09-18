"""The seven watched health conditions.

Every one hangs off a place that ALREADY knows the fact -- these assert that
the watcher reads the existing state rather than measuring anything new, and
that a condition that persists does not send the same message every minute.
"""
from src.connections.database.db_models.alert_model import KIND_HEALTH
from src.connections.database.db_operations.alert_repository import AlertRepository
from src.connections.services import providers
from src.connections.services.alert_service import AlertService
from src.connections.services.alert_watcher import AlertWatcher
from src.connections.services.connection_store import ConnectionStore

BOT_TOKEN = "111222333:AAH-watcher-bot-token-DO-NOT-LOG-77ab12"
CHAT_ID = "-1001112223334"


async def _configure(session) -> None:
    store = ConnectionStore(session)
    await store.put(providers.PROVIDER_TELEGRAM, providers.TELEGRAM_BOT_TOKEN, BOT_TOKEN)
    await store.put(providers.PROVIDER_TELEGRAM, providers.TELEGRAM_CHAT_ID, CHAT_ID)
    await session.commit()


async def test_a_dead_background_task_is_the_first_thing_reported(
    db_session, monkeypatch
):
    """A dead `swing-scheduler` on an ARMED strategy means nothing decides and
    nothing trades, silently. It is the single most valuable alert here."""
    from src.connections.services import alert_watcher as module
    from src.health.services import task_inspector

    await _configure(db_session)

    monkeypatch.setattr(
        task_inspector, "feed_flags", lambda: {"is_synthetic": True, "feed_running": True}
    )
    monkeypatch.setattr(
        task_inspector,
        "inspect",
        lambda **kwargs: {"missing": ["swing-scheduler"], "unexpected": []},
    )
    monkeypatch.setattr(AlertWatcher, "_armed_strategies", staticmethod(lambda: ["nse-swing-momentum"]))

    service = AlertService(db_session)
    fired = await AlertWatcher()._check_missing_tasks(service)
    await db_session.commit()

    assert fired == "missing-tasks"
    rows = await AlertRepository(db_session).recent()
    health = [row for row in rows if row.kind == KIND_HEALTH]
    assert health
    assert "swing-scheduler" in health[0].body
    assert health[0].severity == "CRITICAL", (
        "an armed strategy with a dead scheduler is not a warning"
    )
    assert "ARMED" in health[0].body


async def test_a_persisting_condition_does_not_send_the_same_message_every_minute(
    db_session, monkeypatch
):
    """A dead task stays dead. Keying on the message rather than the EVENT
    would send it once a minute until somebody fixed it."""
    from src.health.services import task_inspector

    await _configure(db_session)
    monkeypatch.setattr(
        task_inspector, "feed_flags", lambda: {"is_synthetic": True, "feed_running": True}
    )
    monkeypatch.setattr(
        task_inspector,
        "inspect",
        lambda **kwargs: {"missing": ["swing-scheduler"], "unexpected": []},
    )

    watcher = AlertWatcher()
    service = AlertService(db_session)
    for _ in range(5):
        await watcher._check_missing_tasks(service)
    await db_session.commit()

    rows = await AlertRepository(db_session).recent()
    health = [row for row in rows if row.kind == KIND_HEALTH]
    assert len(health) == 1
    assert health[0].suppressed_count == 4


async def test_a_lapsed_token_and_a_failing_renewal_read_differently(
    db_session, monkeypatch
):
    """Only one of them needs waking somebody up.

    "Will retry" and "cannot renew, a human must paste a new token" are
    different events with different remedies.
    """
    import time

    import jwt

    from src import config_utils
    from src.connections.services import alert_watcher as module

    await _configure(db_session)
    monkeypatch.setitem(config_utils.load_config()["market_feed"], "synthetic_feed", "false")

    expired = jwt.encode(
        {"exp": int(time.time()) - 3600, "dhanClientId": "1100003626"},
        "not-the-real-key",
        algorithm="HS256",
    )
    await ConnectionStore(db_session).put(
        providers.PROVIDER_DHAN, providers.DHAN_CLIENT_ID, "1100003626"
    )
    await ConnectionStore(db_session).put(
        providers.PROVIDER_DHAN, providers.DHAN_ACCESS_TOKEN, expired
    )
    await db_session.commit()

    service = AlertService(db_session)
    fired = await AlertWatcher()._check_token_renewal(service)
    await db_session.commit()

    assert fired == "token-lapsed"
    rows = await AlertRepository(db_session).recent()
    assert rows[0].severity == "CRITICAL"
    assert "cannot recover" in rows[0].body
    assert "Connections page" in rows[0].body


async def test_the_synthetic_feed_being_on_is_not_an_alert(db_session, monkeypatch):
    """The UI already says so permanently on every screen."""
    from src import config_utils

    await _configure(db_session)
    monkeypatch.setitem(config_utils.load_config()["market_feed"], "synthetic_feed", "true")

    service = AlertService(db_session)
    assert await AlertWatcher()._check_token_renewal(service) is None
    # The feed check bails on `manager.is_synthetic` before it looks at
    # anything else, which is the point: a locally generated price is not a
    # broken feed.
    from src.market.services.feed_manager import get_feed_manager

    manager = get_feed_manager()
    monkeypatch.setattr(manager, "is_synthetic", True)
    assert await AlertWatcher()._check_feed_staleness(service) is None


async def test_a_deferred_stop_is_reported_the_same_evening(db_session):
    """A decision the strategy took that a human would want to know about."""
    from datetime import date
    from decimal import Decimal

    from src.swing.database.db_models.swing_stop_model import STOP_TRIGGERED
    from src.swing.database.db_operations.swing_stop_repository import (
        SwingStopRepository,
    )

    await _configure(db_session)
    await SwingStopRepository(db_session).create(
        strategy_key="nse-swing-momentum",
        portfolio_id=1,
        symbol="TCS",
        security_id="11536",
        status=STOP_TRIGGERED,
        stop_price=Decimal("3400"),
        entry_session=date.today(),
        entry_price=Decimal("3500"),
        quantity=10,
        atr_multiple=Decimal("3.5"),
        highest_close=Decimal("3600"),
    )
    await db_session.commit()

    service = AlertService(db_session)
    fired = await AlertWatcher()._check_deferred_stops(service)
    await db_session.commit()

    assert fired == "deferred-stops"
    rows = await AlertRepository(db_session).recent()
    assert "TCS" in rows[0].body
    assert "Closing Auction Session" in rows[0].body


async def test_one_failing_check_does_not_stop_the_rest(db_session, monkeypatch):
    """A watcher that stops at the first exception reports nothing after it."""
    await _configure(db_session)

    watcher = AlertWatcher()

    async def _explode(_service):
        raise RuntimeError("this check is broken")

    monkeypatch.setattr(watcher, "_check_missing_tasks", _explode)
    called = []

    async def _fine(_service):
        called.append(True)
        return None

    for name in (
        "_check_token_renewal",
        "_check_feed_staleness",
        "_check_missed_sessions",
        "_check_bar_staleness",
        "_check_deferred_stops",
    ):
        monkeypatch.setattr(watcher, name, _fine)

    fired = await watcher.run_once()

    assert fired == []
    assert len(called) == 5, "every remaining check must still run"


def test_the_watcher_reports_what_it_is_doing():
    status = AlertWatcher().status()

    assert status["running"] is False
    assert "intervalSeconds" in status
    assert status["lastConditions"] == []
