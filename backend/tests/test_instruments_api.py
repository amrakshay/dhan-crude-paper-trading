"""Instrument API endpoints."""
from datetime import date
from decimal import Decimal

import pytest

from src.database.session import get_session_factory
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.core.time_utils import utc_now
from tests.conftest import (
    FAR_FUTURE_EXPIRY,
    NEAR_FUTURE_EXPIRY,
    NEAR_OPTION_EXPIRY,
)


async def _seed(rows):
    session = get_session_factory()()
    try:
        await InstrumentRepository(session).upsert_many(rows)
        await session.commit()
    finally:
        await session.close()


def _contract(security_id, instrument_type, expiry, strike=None, option_type=None):
    return {
        "security_id": security_id,
        "exchange_id": "MCX",
        "exchange_segment": "MCX_COMM",
        "segment_code": 5,
        "instrument_type": instrument_type,
        "underlying_symbol": "CRUDEOIL",
        "underlying_scrip": 294,
        "trading_symbol": f"CRUDEOIL {security_id}",
        "display_name": f"CRUDEOIL {security_id}",
        "expiry_date": expiry,
        "strike_price": strike,
        "option_type": option_type,
        "lot_size": 100,
        "tick_size": Decimal("0.1"),
        "is_active": True,
        "refreshed_at": utc_now(),
    }


SEED = [
    _contract("565899", "FUTCOM", NEAR_FUTURE_EXPIRY),
    _contract("569900", "FUTCOM", FAR_FUTURE_EXPIRY),
    _contract("C1", "OPTFUT", NEAR_OPTION_EXPIRY, Decimal("7000"), "CE"),
    _contract("P1", "OPTFUT", NEAR_OPTION_EXPIRY, Decimal("7000"), "PE"),
    _contract("C2", "OPTFUT", NEAR_OPTION_EXPIRY, Decimal("7050"), "CE"),
    _contract("P2", "OPTFUT", NEAR_OPTION_EXPIRY, Decimal("7050"), "PE"),
]

NEAR_OPTION = NEAR_OPTION_EXPIRY.isoformat()


@pytest.mark.parametrize(
    "path",
    [
        "/api/instruments/status",
        "/api/instruments/expiries",
        "/api/instruments/chain",
        "/api/instruments/futures/near",
    ],
)
async def test_endpoints_require_authentication(api_client, path):
    response = await api_client.get(path)
    assert response.status_code == 401


async def test_refresh_requires_authentication(api_client):
    response = await api_client.post("/api/instruments/refresh")
    assert response.status_code == 401


async def test_expiries_endpoint_returns_the_underlying_mapping(auth_client):
    await _seed(SEED)

    response = await auth_client.get("/api/instruments/expiries")

    assert response.status_code == 200
    body = response.json()
    assert body["optionExpiries"] == [NEAR_OPTION]
    assert body["futuresExpiries"] == [
        NEAR_FUTURE_EXPIRY.isoformat(), FAR_FUTURE_EXPIRY.isoformat()
    ]
    assert body["underlyingFutureByExpiry"] == {NEAR_OPTION: "565899"}


async def test_chain_endpoint_returns_paired_strikes(auth_client):
    await _seed(SEED)

    response = await auth_client.get(f"/api/instruments/chain?expiry={NEAR_OPTION}")

    assert response.status_code == 200
    body = response.json()
    assert body["expiry"] == NEAR_OPTION
    assert body["strikeStep"] == "50.0000"
    assert body["underlyingFuture"]["securityId"] == "565899"
    assert len(body["rows"]) == 2
    assert body["rows"][0]["call"]["securityId"] == "C1"
    assert body["rows"][0]["put"]["securityId"] == "P1"


async def test_chain_defaults_to_the_nearest_expiry(auth_client):
    await _seed(SEED)

    response = await auth_client.get("/api/instruments/chain")

    assert response.status_code == 200
    assert response.json()["expiry"] == NEAR_OPTION


async def test_chain_for_an_unknown_expiry_is_404(auth_client):
    await _seed(SEED)

    response = await auth_client.get("/api/instruments/chain?expiry=2030-01-01")

    assert response.status_code == 404


async def test_near_future_endpoint(auth_client):
    await _seed(SEED)

    response = await auth_client.get("/api/instruments/futures/near")

    assert response.status_code == 200
    body = response.json()
    assert body["securityId"] == "565899"
    assert body["lotSize"] == 100
    assert body["expiryDate"] == NEAR_FUTURE_EXPIRY.isoformat()


async def test_status_reports_an_empty_universe_before_any_refresh(auth_client):
    response = await auth_client.get("/api/instruments/status")

    assert response.status_code == 200
    body = response.json()
    assert body["instrumentCount"] == 0
    assert body["nearFuture"] is None
    assert body["optionExpiries"] == []


async def test_status_after_seeding(auth_client):
    await _seed(SEED)

    response = await auth_client.get("/api/instruments/status")

    body = response.json()
    assert body["instrumentCount"] == 6
    assert body["nearFuture"]["securityId"] == "565899"
