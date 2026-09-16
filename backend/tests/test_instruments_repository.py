"""Instrument repository: upsert semantics and deactivation."""
from datetime import date
from decimal import Decimal

from src.core.time_utils import utc_now
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)


def _row(security_id, **overrides):
    row = {
        "security_id": security_id,
        "exchange_id": "MCX",
        "exchange_segment": "MCX_COMM",
        "segment_code": 5,
        "instrument_type": "OPTFUT",
        "underlying_symbol": "CRUDEOIL",
        "underlying_scrip": 294,
        "trading_symbol": f"CRUDEOIL {security_id}",
        "display_name": f"CRUDEOIL {security_id}",
        "expiry_date": date(2026, 9, 17),
        "strike_price": Decimal("7000"),
        "option_type": "CE",
        "lot_size": 100,
        "tick_size": Decimal("0.1"),
        "is_active": True,
        "refreshed_at": utc_now(),
    }
    row.update(overrides)
    return row


async def test_upsert_inserts_then_reports_unchanged(db_session):
    """A refresh against an unchanged master must not churn rows."""
    repository = InstrumentRepository(db_session)

    first = await repository.upsert_many([_row("1"), _row("2")])
    assert first == {"inserted": 2, "updated": 0, "unchanged": 0}

    second = await repository.upsert_many([_row("1"), _row("2")])
    assert second == {"inserted": 0, "updated": 0, "unchanged": 2}


async def test_upsert_detects_a_changed_field(db_session):
    repository = InstrumentRepository(db_session)
    await repository.upsert_many([_row("1")])

    result = await repository.upsert_many([_row("1", lot_size=10)])

    assert result == {"inserted": 0, "updated": 1, "unchanged": 0}
    stored = await repository.get_by_security_id("1")
    assert stored.lot_size == 10


async def test_money_column_round_trips_as_decimal(db_session):
    """Prices must come back as exact Decimals, not floats."""
    repository = InstrumentRepository(db_session)
    await repository.upsert_many([_row("1", strike_price=Decimal("7350.50"))])

    stored = await repository.get_by_security_id("1")
    assert isinstance(stored.strike_price, Decimal)
    assert stored.strike_price == Decimal("7350.5000")


async def test_deactivate_missing_retires_expired_series(db_session):
    """Expired contracts vanish from the master but their rows must survive,
    because order history references them."""
    repository = InstrumentRepository(db_session)
    await repository.upsert_many([_row("old"), _row("current")])

    deactivated = await repository.deactivate_missing("CRUDEOIL", ["current"])

    assert deactivated == 1
    assert (await repository.get_by_security_id("old")).is_active is False
    assert (await repository.get_by_security_id("current")).is_active is True
    assert await repository.count_all() == 2, "rows are retired, never deleted"


async def test_list_expiries_is_sorted_and_deduplicated(db_session):
    repository = InstrumentRepository(db_session)
    await repository.upsert_many(
        [
            _row("a", expiry_date=date(2026, 11, 17)),
            _row("b", expiry_date=date(2026, 9, 17)),
            _row("c", expiry_date=date(2026, 9, 17)),
            _row("d", expiry_date=date(2026, 10, 15)),
        ]
    )

    expiries = await repository.list_expiries("CRUDEOIL", "OPTFUT")

    assert expiries == [date(2026, 9, 17), date(2026, 10, 15), date(2026, 11, 17)]


async def test_list_expiries_can_exclude_past_dates(db_session):
    repository = InstrumentRepository(db_session)
    await repository.upsert_many(
        [
            _row("past", expiry_date=date(2020, 1, 1)),
            _row("future", expiry_date=date(2026, 9, 17)),
        ]
    )

    expiries = await repository.list_expiries(
        "CRUDEOIL", "OPTFUT", on_or_after=date(2026, 1, 1)
    )

    assert expiries == [date(2026, 9, 17)]
