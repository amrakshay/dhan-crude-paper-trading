"""The alert outbox: what is raised, what is collapsed, and what is delivered.

Several of these deliberately assert that something does NOT get sent. They are
load-bearing: this codebase has produced the exact flood an unthrottled version
of this would have forwarded, twice in one evening.
"""
import logging

import pytest

from src import alert_sink
from src.connections.database.db_models.alert_model import (
    ALERT_PENDING,
    ALERT_SENT,
    ALERT_SUPPRESSED,
    KIND_ERROR,
    KIND_TRADE_BOUGHT,
    KIND_TRADE_SOLD,
    SEVERITY_INFO,
)
from src.connections.database.db_operations.alert_repository import AlertRepository
from src.connections.services import providers
from src.connections.services.alert_service import AlertService
from src.connections.services.connection_store import ConnectionStore
from src.logging_config import get_logger

BOT_TOKEN = "987654321:AAH-alert-bot-token-DO-NOT-LOG-3c81de"
CHAT_ID = "-1009876543210"


async def _configure_telegram(session) -> None:
    store = ConnectionStore(session)
    await store.put(providers.PROVIDER_TELEGRAM, providers.TELEGRAM_BOT_TOKEN, BOT_TOKEN)
    await store.put(providers.PROVIDER_TELEGRAM, providers.TELEGRAM_CHAT_ID, CHAT_ID)
    await session.commit()


# --- de-duplication ---------------------------------------------------------
async def test_a_repeated_error_is_collapsed_onto_one_row(db_session):
    """The swing stop monitor once logged the same line once a second for
    twelve minutes. Unthrottled that is 720 messages and flood control."""
    await _configure_telegram(db_session)
    service = AlertService(db_session)

    first = await service.record_error(
        "dcpt.swing.stops", "ERROR", "database is locked"
    )
    assert first is not None

    for _ in range(46):
        assert (
            await service.record_error(
                "dcpt.swing.stops", "ERROR", "database is locked"
            )
            is None
        ), "a repeat inside the window must not become its own row"

    await db_session.commit()
    rows = await AlertRepository(db_session).recent()
    error_rows = [row for row in rows if row.kind == KIND_ERROR]
    assert len(error_rows) == 1
    assert error_rows[0].suppressed_count == 46


async def test_the_dedupe_key_survives_a_counter_inside_the_message(db_session):
    """Keying on the RAW text would give every repeat a new key.

    The alert body carries its own occurrence count, so a raw key would defeat
    exactly the de-duplication it is reporting.
    """
    from src.alert_sink import normalise_for_dedupe

    a = normalise_for_dedupe("dcpt.swing", "this has now happened 47 times")
    b = normalise_for_dedupe("dcpt.swing", "this has now happened 1024 times")
    c = normalise_for_dedupe("dcpt.swing", "something else entirely")

    assert a == b
    assert a != c


async def test_the_delivered_message_says_how_many_it_stands_for(db_session):
    """A collapsed flood arriving as one line looks like a single event."""
    from src.connections.services.alert_dispatcher import AlertDispatcher

    await _configure_telegram(db_session)
    service = AlertService(db_session)
    row = await service.record_error("dcpt.swing.stops", "ERROR", "database is locked")
    for _ in range(3):
        await service.record_error("dcpt.swing.stops", "ERROR", "database is locked")
    await db_session.commit()

    rendered = AlertDispatcher.render(row)
    assert "this has now happened 4 times" in rendered


# --- the sink ---------------------------------------------------------------
def test_the_alerting_logger_is_excluded_from_its_own_sink():
    """A delivery error that alerts about itself is a loop ending in flood control."""
    assert alert_sink.is_excluded("dcpt.connections.dispatcher")
    assert alert_sink.is_excluded("dcpt.connections")
    assert not alert_sink.is_excluded("dcpt.swing.stops")
    assert not alert_sink.is_excluded("dcpt.connectionsomething")


def test_the_sink_ignores_a_record_from_the_alerting_machinery():
    handler = alert_sink.AlertSinkHandler()
    try:
        handler.emit(
            logging.LogRecord(
                "dcpt.connections.dispatcher", logging.ERROR, __file__, 1,
                "Telegram refused the send", None, None,
            )
        )
        handler.emit(
            logging.LogRecord(
                "dcpt.swing.stops", logging.ERROR, __file__, 1,
                "something real broke", None, None,
            )
        )
    finally:
        pending = handler.drain()

    assert [entry["logger"] for entry in pending] == ["dcpt.swing.stops"]
    assert handler.excluded == 1


def test_the_sink_floor_is_error_not_warning():
    """WARNING is the right floor for a page somebody is reading, and far too
    chatty for a phone."""
    assert alert_sink.MINIMUM_LEVEL == logging.ERROR
    from src import log_buffer

    assert log_buffer.MINIMUM_LEVEL == logging.WARNING


def test_the_sink_counts_what_it_dropped_rather_than_blocking():
    """The situation in which it overflows IS a flood; the count is the honest
    report of one."""
    handler = alert_sink.AlertSinkHandler(capacity=3)
    for index in range(10):
        handler.emit(
            logging.LogRecord(
                "dcpt.swing.stops", logging.ERROR, __file__, 1,
                f"failure {index}", None, None,
            )
        )

    assert handler.stats()["queued"] == 3
    assert handler.stats()["dropped"] == 7
    assert handler.stats()["seen"] == 10


def test_the_sink_never_raises_out_of_emit():
    """A handler that raises breaks the call site it is observing."""
    handler = alert_sink.AlertSinkHandler()
    record = logging.LogRecord(
        "dcpt.swing.stops", logging.ERROR, __file__, 1, "%d %d", (1,), None
    )
    handler.emit(record)  # args do not interpolate; must not raise
    assert handler.drain()


def test_the_sink_is_installed_by_configure_logging():
    """Installed from configure_logging and nowhere else, like log_buffer."""
    get_logger("tests.sink.install")
    assert alert_sink.get_handler() is not None


# --- what happens with nothing configured ----------------------------------
async def test_a_fact_is_recorded_even_when_nothing_can_carry_it(db_session):
    """A row written SUPPRESSED with its reason beats the fact being dropped."""
    service = AlertService(db_session)

    row = await service.record_health(
        event="feed-down", title="The feed is down", body="no prices"
    )
    await db_session.commit()

    assert row is not None
    assert row.status == ALERT_SUPPRESSED
    assert "No Telegram connection" in (row.last_error or "")


async def test_a_switched_off_connection_suppresses_rather_than_queues(db_session):
    await _configure_telegram(db_session)
    connection = await ConnectionStore(db_session).get(providers.PROVIDER_TELEGRAM)
    connection.enabled = False
    await db_session.commit()

    row = await AlertService(db_session).record_health(
        event="feed-stale", title="quiet feed", body="nothing for 90s"
    )
    await db_session.commit()

    assert row.status == ALERT_SUPPRESSED
    assert "switched off" in (row.last_error or "")


# --- trades -----------------------------------------------------------------
async def test_a_buy_raises_an_alert_carrying_the_reason_it_was_placed(
    db_session, monkeypatch
):
    """The reason is already on the PLACED event; it is read, never recomputed."""
    from decimal import Decimal

    from src.positions.database.db_operations.position_repository import (
        PositionRepository,
    )
    from src.positions.services.position_service import PositionService

    await _configure_telegram(db_session)

    service = PositionService(PositionRepository(db_session))
    await service.apply_fill(
        strategy_key="nse-swing-momentum",
        portfolio_id=1,
        security_id="11536",
        trading_symbol="TCS",
        side="BUY",
        quantity=10,
        price=Decimal("3500"),
        lot_size=1,
        charges=Decimal("12.34"),
        alert_context={"reason": "rank 3 of 10, score 0.82"},
    )
    await db_session.commit()

    rows = await AlertRepository(db_session).recent()
    bought = [row for row in rows if row.kind == KIND_TRADE_BOUGHT]
    assert len(bought) == 1
    assert "TCS" in bought[0].title
    assert "rank 3 of 10" in bought[0].body
    assert "Cash remaining" in bought[0].body


async def test_a_sale_reports_realised_pnl_and_why_the_position_left(db_session):
    """`swing_stops.exit_kind` is a STORED fact; a sale with no row says so
    rather than being assigned a category it was never given."""
    from decimal import Decimal

    from src.positions.database.db_operations.position_repository import (
        PositionRepository,
    )
    from src.positions.services.position_service import PositionService

    await _configure_telegram(db_session)
    service = PositionService(PositionRepository(db_session))

    await service.apply_fill(
        strategy_key="nse-swing-momentum", portfolio_id=1, security_id="11536",
        trading_symbol="TCS", side="BUY", quantity=10, price=Decimal("3500"),
        lot_size=1,
    )
    await service.apply_fill(
        strategy_key="nse-swing-momentum", portfolio_id=1, security_id="11536",
        trading_symbol="TCS", side="SELL", quantity=10, price=Decimal("3600"),
        lot_size=1, charges=Decimal("20"),
    )
    await db_session.commit()

    rows = await AlertRepository(db_session).recent()
    sold = [row for row in rows if row.kind == KIND_TRADE_SOLD]
    assert len(sold) == 1
    body = sold[0].body
    assert "Realised: ₹1,000.00" in body
    assert "%" in body, "rupees AND percent"
    assert "Held:" in body
    assert "Why it left: closed by hand (no exit-kind recorded)" in body


async def test_an_alert_failure_never_breaks_the_fill(db_session, monkeypatch):
    """A fill must never be lost to an alert. This is the whole reason the
    emit is wrapped."""
    from decimal import Decimal

    from src.connections.services import alert_service as module
    from src.positions.database.db_operations.position_repository import (
        PositionRepository,
    )
    from src.positions.services.position_service import PositionService

    class _Exploding:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("the alerting machinery is broken")

    monkeypatch.setattr(module, "AlertService", _Exploding)

    position, realised = await PositionService(
        PositionRepository(db_session)
    ).apply_fill(
        strategy_key="nse-swing-momentum", portfolio_id=1, security_id="11536",
        trading_symbol="TCS", side="BUY", quantity=5, price=Decimal("3500"),
        lot_size=1,
    )

    assert position.net_quantity == 5
    assert realised == Decimal("0")


# --- delivery ---------------------------------------------------------------
async def test_the_dispatcher_marks_a_delivered_alert_sent(db_session, monkeypatch):
    from src.connections.services import telegram_service as module
    from src.connections.services.alert_dispatcher import AlertDispatcher

    await _configure_telegram(db_session)
    await AlertService(db_session).raise_alert(
        kind="TEST", severity=SEVERITY_INFO, title="hello", body="world"
    )
    await db_session.commit()

    sent = []

    async def _send(self, text):
        sent.append(text)

    monkeypatch.setattr(module.TelegramService, "send", _send)

    result = await AlertDispatcher().run_once()

    assert result["delivered"] == 1
    assert sent and "hello" in sent[0]

    rows = await AlertRepository(db_session).recent()
    assert rows[0].status == ALERT_SENT
    assert rows[0].sent_at is not None


async def test_a_permanent_failure_is_given_up_on_rather_than_retried_for_ever(
    db_session, monkeypatch
):
    """Retrying a bad token for ever blocks every message behind it."""
    from src.connections.services import telegram_service as module
    from src.connections.services.alert_dispatcher import AlertDispatcher
    from src.connections.services.telegram_client import FAIL_BAD_TOKEN, TelegramError

    await _configure_telegram(db_session)
    await AlertService(db_session).raise_alert(
        kind="TEST", severity=SEVERITY_INFO, title="hello", body="world"
    )
    await db_session.commit()

    async def _refuse(self, text):
        raise TelegramError(FAIL_BAD_TOKEN, "Telegram rejected the bot token.")

    monkeypatch.setattr(module.TelegramService, "send", _refuse)

    await AlertDispatcher().run_once()

    rows = await AlertRepository(db_session).recent()
    assert rows[0].status == "FAILED"
    assert rows[0].attempts == 1
    assert "rejected the bot token" in rows[0].last_error


async def test_flood_control_pauses_the_batch_rather_than_burning_through_it(
    db_session, monkeypatch
):
    """Telegram asked for a pause. Honour it rather than meeting flood control
    again on the next row."""
    from src.connections.services import telegram_service as module
    from src.connections.services.alert_dispatcher import AlertDispatcher
    from src.connections.services.telegram_client import (
        FAIL_FLOOD_CONTROL,
        TelegramError,
    )

    await _configure_telegram(db_session)
    service = AlertService(db_session)
    for index in range(3):
        await service.raise_alert(
            kind="TEST", severity=SEVERITY_INFO, title=f"alert {index}", body="x"
        )
    await db_session.commit()

    attempts = []

    async def _flooded(self, text):
        attempts.append(text)
        raise TelegramError(FAIL_FLOOD_CONTROL, "rate limited", retry_after=17)

    monkeypatch.setattr(module.TelegramService, "send", _flooded)

    await AlertDispatcher().run_once()

    assert len(attempts) == 1, "it must stop at the first flood-control refusal"
    rows = sorted(await AlertRepository(db_session).recent(), key=lambda row: row.id)
    # Transient, so it is NOT given up on: it stays pending for the next pass.
    assert rows[0].status == ALERT_PENDING
    assert rows[0].attempts == 1


async def test_the_error_sink_reaches_the_outbox_through_the_dispatcher(
    db_session, monkeypatch
):
    from src.connections.services.alert_dispatcher import AlertDispatcher

    await _configure_telegram(db_session)
    handler = alert_sink.get_handler()
    assert handler is not None
    handler.clear()
    get_logger("tests.alerts.ingest").error("a real failure in a real component")

    dispatcher = AlertDispatcher()
    monkeypatch.setattr(
        dispatcher, "_deliver", lambda session: _no_delivery()
    )
    result = await dispatcher.run_once()

    assert result["ingested"] == 1
    rows = await AlertRepository(db_session).recent()
    assert any("a real failure" in row.body for row in rows)
    handler.clear()


async def _no_delivery():
    return 0, 0, 0
