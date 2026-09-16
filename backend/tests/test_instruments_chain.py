"""Chain resolution: strike step, ATM, and the option/futures expiry mapping."""
from datetime import date
from decimal import Decimal

import pytest

from src.instruments.database.db_models.instrument_model import Instrument
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.instruments.services.chain_service import ChainService

STRIKES_50 = [Decimal(value) for value in range(7000, 7500, 50)]


def test_infer_strike_step_from_uniform_ladder():
    assert ChainService.infer_strike_step(STRIKES_50) == Decimal(50)


def test_infer_strike_step_picks_the_dominant_gap():
    """A single odd gap must not change the inferred step."""
    strikes = [Decimal(7000), Decimal(7050), Decimal(7100), Decimal(7250), Decimal(7300)]
    assert ChainService.infer_strike_step(strikes) == Decimal(50)


def test_infer_strike_step_needs_at_least_two_strikes():
    assert ChainService.infer_strike_step([Decimal(7000)]) is None
    assert ChainService.infer_strike_step([]) is None


@pytest.mark.parametrize(
    "spot,expected",
    [
        (Decimal("7000"), Decimal("7000")),
        (Decimal("7010"), Decimal("7000")),
        (Decimal("7040"), Decimal("7050")),
        (Decimal("7025"), Decimal("7000")),   # exact tie resolves to the lower strike
        (Decimal("6000"), Decimal("7000")),   # below the ladder
        (Decimal("9999"), Decimal("7450")),   # above the ladder
    ],
)
def test_resolve_atm_strike(spot, expected):
    assert ChainService.resolve_atm_strike(STRIKES_50, spot) == expected


def test_resolve_atm_strike_without_a_spot():
    assert ChainService.resolve_atm_strike(STRIKES_50, None) is None
    assert ChainService.resolve_atm_strike([], Decimal("7000")) is None


def _instrument(security_id, instrument_type, expiry, strike=None, option_type=None):
    return Instrument(
        security_id=security_id,
        exchange_id="MCX",
        exchange_segment="MCX_COMM",
        segment_code=5,
        instrument_type=instrument_type,
        underlying_symbol="CRUDEOIL",
        underlying_scrip=294,
        trading_symbol=f"CRUDEOIL {security_id}",
        expiry_date=expiry,
        strike_price=strike,
        option_type=option_type,
        lot_size=100,
        tick_size=Decimal("0.1"),
        is_active=True,
    )


async def _seed_chain(session):
    """Three option expiries and three futures expiries, as MCX really lists them."""
    session.add(_instrument("565899", "FUTCOM", date(2026, 9, 21)))
    session.add(_instrument("569900", "FUTCOM", date(2026, 10, 19)))
    session.add(_instrument("573422", "FUTCOM", date(2026, 11, 19)))

    for index, strike in enumerate(STRIKES_50):
        session.add(
            _instrument(f"CE{index}", "OPTFUT", date(2026, 9, 17), strike, "CE")
        )
        session.add(
            _instrument(f"PE{index}", "OPTFUT", date(2026, 9, 17), strike, "PE")
        )
    session.add(_instrument("OCTCE", "OPTFUT", date(2026, 10, 15), Decimal(7000), "CE"))
    session.add(_instrument("NOVCE", "OPTFUT", date(2026, 11, 17), Decimal(7000), "CE"))
    await session.flush()


async def test_option_expiry_maps_to_the_right_underlying_future(db_session):
    """Options expire days BEFORE the future they settle against."""
    await _seed_chain(db_session)
    service = ChainService(InstrumentRepository(db_session))

    september = await service.resolve_underlying_future(date(2026, 9, 17))
    october = await service.resolve_underlying_future(date(2026, 10, 15))
    november = await service.resolve_underlying_future(date(2026, 11, 17))

    assert september.security_id == "565899"
    assert september.expiry_date == date(2026, 9, 21)
    assert october.security_id == "569900"
    assert november.security_id == "573422"


async def test_underlying_future_is_not_matched_by_calendar_month(db_session):
    """An option expiring after its own month's future rolls to the next one."""
    await _seed_chain(db_session)
    service = ChainService(InstrumentRepository(db_session))

    # A hypothetical option expiring 2026-09-25, after the SEP future's 09-21.
    resolved = await service.resolve_underlying_future(date(2026, 9, 25))
    assert resolved.security_id == "569900", "must roll to OCT, not stay on SEP"


async def test_get_chain_pairs_calls_and_puts_by_strike(db_session):
    await _seed_chain(db_session)
    service = ChainService(InstrumentRepository(db_session))

    rows = await service.get_chain(date(2026, 9, 17))

    assert len(rows) == len(STRIKES_50)
    assert [row.strike_price for row in rows] == sorted(STRIKES_50)
    assert all(row.call is not None and row.put is not None for row in rows)
    assert rows[0].call.option_type == "CE"
    assert rows[0].put.option_type == "PE"


async def test_strike_window_is_centred_on_atm(db_session):
    await _seed_chain(db_session)
    service = ChainService(InstrumentRepository(db_session))

    selected = await service.get_strike_window(
        date(2026, 9, 17), spot=Decimal("7200"), window=2
    )

    strikes = sorted({contract.strike_price for contract in selected})
    assert strikes == [Decimal(7100), Decimal(7150), Decimal(7200), Decimal(7250), Decimal(7300)]
    assert len(selected) == 10, "both a call and a put per strike"


async def test_strike_window_clamps_at_the_edge_of_the_ladder(db_session):
    await _seed_chain(db_session)
    service = ChainService(InstrumentRepository(db_session))

    selected = await service.get_strike_window(
        date(2026, 9, 17), spot=Decimal("7000"), window=5
    )

    strikes = sorted({contract.strike_price for contract in selected})
    assert strikes[0] == Decimal(7000), "cannot extend below the lowest listed strike"
    assert len(strikes) == 6


async def test_strike_window_without_a_spot_falls_back_to_the_middle(db_session):
    """Before the first tick there is no spot; the window must still be usable."""
    await _seed_chain(db_session)
    service = ChainService(InstrumentRepository(db_session))

    selected = await service.get_strike_window(date(2026, 9, 17), spot=None, window=1)

    assert selected, "an empty first subscription would leave the chain blank"
    assert len({contract.strike_price for contract in selected}) == 3


async def test_near_future_is_the_front_month(db_session):
    await _seed_chain(db_session)
    service = ChainService(InstrumentRepository(db_session))

    near = await service.get_near_future()
    assert near.security_id == "565899"
