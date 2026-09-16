"""Shared pytest configuration and fixtures.

Environment is set before anything imports the application, because both the
YAML config and main.py read it at import time.
"""
import os
import sys
import tempfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

_TEST_DB_PATH = os.path.join(tempfile.gettempdir(), "dcpt_test.db")
# Log files go to a temp directory, not backend/logs, so a test run does not
# leave artefacts in the working tree. tests/test_no_secrets_in_logs.py reads
# app.log back out of here.
TEST_LOG_DIR = os.path.join(tempfile.gettempdir(), "dcpt_test_logs")

os.environ.setdefault("CONFIG_PATH", str(BACKEND_ROOT / "conf"))
os.environ.setdefault("LOG_DIR", TEST_LOG_DIR)
os.environ.setdefault("APP_USERNAME", "trader")
os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("APP_JWT_SECRET", "test-secret-key-for-unit-tests-only")
os.environ.setdefault("DHAN_SYNTHETIC_FEED", "true")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB_PATH}"

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from src.app_utils import load_config_properties  # noqa: E402

load_config_properties()


@pytest_asyncio.fixture
async def db_session():
    """A clean database per test, torn down afterwards."""
    from src.database.connection import close_database_connection
    from src.database.session import DatabaseManager, get_session_factory

    await DatabaseManager.drop_tables()
    await DatabaseManager.create_tables()

    session = get_session_factory()()
    try:
        yield session
    finally:
        await session.close()
        await DatabaseManager.drop_tables()
        await close_database_connection()


@pytest_asyncio.fixture
async def api_client():
    """An httpx client bound to the ASGI app, with a clean database."""
    import httpx

    from src.database.connection import close_database_connection
    from src.database.session import DatabaseManager
    import main

    await DatabaseManager.drop_tables()
    await DatabaseManager.create_tables()

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    await DatabaseManager.drop_tables()
    await close_database_connection()


@pytest_asyncio.fixture
async def auth_client(api_client):
    """An api_client that has already logged in."""
    response = await api_client.post(
        "/api/auth/login", json={"username": "trader", "password": "test-password"}
    )
    assert response.status_code == 200, response.text
    return api_client


@pytest.fixture
def sample_master_csv(tmp_path):
    """A miniature instrument master with the real column layout.

    Column names and value conventions match the live file as verified on
    2026-09-16, including the MCX quirks: LOT_SIZE always 1.0, TICK_SIZE in
    paise, futures carrying OPTION_TYPE=XX and a placeholder strike.
    """
    header = (
        "EXCH_ID,SEGMENT,SECURITY_ID,ISIN,INSTRUMENT,UNDERLYING_SECURITY_ID,"
        "UNDERLYING_SYMBOL,SYMBOL_NAME,DISPLAY_NAME,INSTRUMENT_TYPE,SERIES,"
        "LOT_SIZE,SM_EXPIRY_DATE,STRIKE_PRICE,OPTION_TYPE,TICK_SIZE"
    )
    rows = [
        # CRUDEOIL near future
        "MCX,M,565899,NA,FUTCOM,294,CRUDEOIL,CRUDEOIL,CRUDEOIL SEP FUT,FUTCOM,NA,"
        "1.0,2026-09-21,0.00000,XX,100.0000",
        # CRUDEOIL next future
        "MCX,M,569900,NA,FUTCOM,294,CRUDEOIL,CRUDEOIL,CRUDEOIL OCT FUT,FUTCOM,NA,"
        "1.0,2026-10-19,0.00000,XX,100.0000",
        # Options, near expiry
        "MCX,M,576266,NA,OPTFUT,294,CRUDEOIL,CRUDEOIL,CRUDEOIL 17 SEP 7000 CALL,"
        "OPTFUT,NA,1.0,2026-09-17,7000.00000,CE,10.0000",
        "MCX,M,576267,NA,OPTFUT,294,CRUDEOIL,CRUDEOIL,CRUDEOIL 17 SEP 7000 PUT,"
        "OPTFUT,NA,1.0,2026-09-17,7000.00000,PE,10.0000",
        "MCX,M,576273,NA,OPTFUT,294,CRUDEOIL,CRUDEOIL,CRUDEOIL 17 SEP 7050 CALL,"
        "OPTFUT,NA,1.0,2026-09-17,7050.00000,CE,10.0000",
        "MCX,M,576274,NA,OPTFUT,294,CRUDEOIL,CRUDEOIL,CRUDEOIL 17 SEP 7050 PUT,"
        "OPTFUT,NA,1.0,2026-09-17,7050.00000,PE,10.0000",
        "MCX,M,576275,NA,OPTFUT,294,CRUDEOIL,CRUDEOIL,CRUDEOIL 17 SEP 7100 CALL,"
        "OPTFUT,NA,1.0,2026-09-17,7100.00000,CE,10.0000",
        "MCX,M,576276,NA,OPTFUT,294,CRUDEOIL,CRUDEOIL,CRUDEOIL 17 SEP 7100 PUT,"
        "OPTFUT,NA,1.0,2026-09-17,7100.00000,PE,10.0000",
        # Options, far expiry
        "MCX,M,580001,NA,OPTFUT,294,CRUDEOIL,CRUDEOIL,CRUDEOIL 15 OCT 7000 CALL,"
        "OPTFUT,NA,1.0,2026-10-15,7000.00000,CE,10.0000",
        # Noise that must be filtered out: another commodity, and another exchange
        "MCX,M,999001,NA,OPTFUT,432,GOLD,GOLD,GOLD 17 SEP 70000 CALL,OPTFUT,NA,"
        "1.0,2026-09-17,70000.00000,CE,100.0000",
        "NSE,D,111111,NA,OPTIDX,13,NIFTY,NIFTY,NIFTY 25 SEP 24000 CALL,OPTIDX,NA,"
        "65.0,2026-09-25,24000.00000,CE,5.0000",
    ]
    path = tmp_path / "mini-master.csv"
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return str(path)
