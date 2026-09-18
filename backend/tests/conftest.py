"""Shared pytest configuration and fixtures.

Environment is set before anything imports the application, because both the
YAML config and main.py read it at import time.
"""
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

# Per PROCESS, not a fixed name. Two pytest runs at once used to share one
# SQLite file: the second run's schema setup tears down tables the first is
# mid-query on, which does not merely produce wrong results -- it has twice
# taken the interpreter down with a fatal error whose traceback points into
# SQLAlchemy and says nothing about the real cause. Diagnosing that as "the
# baseline is broken" cost an hour on 2026-09-18.
_TEST_DB_PATH = os.path.join(tempfile.gettempdir(), f"dcpt_test_{os.getpid()}.db")

# The seeded administrator, as every test knows it.
SEED_ADMIN_EMAIL = "trader@abc.com"
SEED_ADMIN_PASSWORD = "seed-admin-password"
# Log files go to a temp directory, not backend/logs, so a test run does not
# leave artefacts in the working tree. tests/test_no_secrets_in_logs.py reads
# app.log back out of here.
# Also per process, for the same reason: tests/test_no_secrets_in_logs.py reads
# app.log back out of here, and a concurrent run writing the same file would
# make that assertion read another run's output.
TEST_LOG_DIR = os.path.join(tempfile.gettempdir(), f"dcpt_test_logs_{os.getpid()}")

os.environ.setdefault("CONFIG_PATH", str(BACKEND_ROOT / "conf"))
os.environ.setdefault("LOG_DIR", TEST_LOG_DIR)
# APP_USERNAME / APP_PASSWORD are gone: the users table is the only identity
# source. The seeded administrator is created from APP_ADMIN_PASSWORD.
os.environ.setdefault("APP_ADMIN_PASSWORD", SEED_ADMIN_PASSWORD)
os.environ.setdefault("APP_JWT_SECRET", "test-secret-key-for-unit-tests-only")
os.environ.setdefault("DHAN_SYNTHETIC_FEED", "true")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB_PATH}"

# ---------------------------------------------------------------------------
# Expiry dates used across the suite.
#
# These are RELATIVE TO TODAY on purpose. The expiries endpoint, the chain's
# default expiry and the feed's subscription policy all hide a series that has
# already expired (`ChainService` filters on `ist_today()`), so a hardcoded
# date turns the suite red on the morning it passes -- which is exactly what
# happened on 2026-09-18, when the fixtures' 2026-09-17 option expiry became
# yesterday and four tests started failing with no code change.
#
# The SHAPE is the real CRUDEOIL quirk documented in the root CLAUDE.md §5: an
# option expires a few days BEFORE the future it is written on, so an option
# expiry may never be mapped to a future by month name.
# ---------------------------------------------------------------------------
# The portfolio every test trades into. The opening balance is deliberately
# large: most tests are not about funds, and one that is deposits or withdraws
# to set up the case it cares about.
TEST_PORTFOLIO_NAME = "Test"
TEST_PORTFOLIO_OPENING_BALANCE = "10000000"

NEAR_OPTION_EXPIRY = date.today() + timedelta(days=7)
NEAR_FUTURE_EXPIRY = NEAR_OPTION_EXPIRY + timedelta(days=4)
FAR_OPTION_EXPIRY = NEAR_OPTION_EXPIRY + timedelta(days=28)
FAR_FUTURE_EXPIRY = FAR_OPTION_EXPIRY + timedelta(days=4)


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
    await seed_admin()
    await seed_portfolio()

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
    await seed_admin()
    await seed_portfolio()

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    await DatabaseManager.drop_tables()
    await close_database_connection()


async def seed_admin():
    """Create the default administrator.

    The app does this in its lifespan, but these tests drive the ASGI app
    through httpx without running it, and build the schema with
    `create_tables()` rather than by migrating. Seeding explicitly keeps the
    two paths equivalent.
    """
    from src.database.session import session_scope
    from src.users.database.db_operations.user_repository import UserRepository
    from src.users.services.user_service import UserService

    async with session_scope() as session:
        await UserService(UserRepository(session)).ensure_seed_user(
            SEED_ADMIN_PASSWORD
        )
        await session.commit()


async def seed_portfolio():
    """The default portfolio every test trades into.

    The app creates one in its lifespan and the migration creates one for an
    existing database; these tests build the schema with `create_tables()` and
    drive the ASGI app without running it, so they seed it explicitly. Trading
    with no portfolio is refused, which is correct behaviour and not what most
    of these tests are about.
    """
    from decimal import Decimal

    from src.database.session import session_scope
    from src.portfolios.services.portfolio_service import PortfolioService

    async with session_scope() as session:
        await PortfolioService(session).create(
            name=TEST_PORTFOLIO_NAME,
            strategy_keys=["mcx-crude-options"],
            opening_balance=Decimal(TEST_PORTFOLIO_OPENING_BALANCE),
        )
        await session.commit()


async def default_portfolio_id() -> int:
    """The id of the portfolio seeded for this test.

    Looked up rather than assumed to be 1: a test that creates portfolios of
    its own should not have to care what order the ids came out in.
    """
    from src.database.session import session_scope
    from src.portfolios.database.db_operations.portfolio_repository import (
        PortfolioRepository,
    )

    async with session_scope() as session:
        portfolio = await PortfolioRepository(session).get_by_name(TEST_PORTFOLIO_NAME)
        return portfolio.id


async def login_as(client, email: str, password: str):
    """Log `client` in, replacing whatever session it held."""
    response = await client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest_asyncio.fixture
async def auth_client(api_client):
    """An api_client signed in as the seeded administrator."""
    await login_as(api_client, SEED_ADMIN_EMAIL, SEED_ADMIN_PASSWORD)
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


@pytest.fixture(autouse=True)
def strategy_state_baseline():
    """Every strategy ON for the duration of a test, restored afterwards.

    Two problems, one fixture.

    **Leakage.** The registry is a process-wide singleton, so a test that flips
    a toggle leaks it into every test that runs after. That used to be handled
    by a `try/finally` per test restoring the value it *believed* was the
    default -- which stopped being true the moment the defaults changed on
    2026-09-18, and broke tests that never touched a toggle. Snapshotting
    cannot go stale.

    **Dependence on the shipped defaults.** `submit_paper_order` refuses a new
    order for a switched-off strategy, so every test that trades a contract
    used to depend on that contract's module happening to ship enabled. When
    crude was switched off by default, 71 tests failed for a reason that had
    nothing to do with what they were testing. Enabling everything makes the
    starting state explicit and independent of what a fresh install does.

    A test whose subject IS the shipped default reads `enabled_by_default` off
    the definition; a test whose subject is what switching a strategy off does
    sets the state it wants (see `only_crude_is_running` in
    test_strategy_toggles.py).
    """
    from src.strategies.services.strategy_registry import get_strategy_registry

    registry = get_strategy_registry()
    strategies = registry.strategy_states()
    capabilities = registry.capability_states()
    armed = registry.armed_states()
    for definition in registry.all():
        registry.set_enabled(definition.key, True)
        # DISARMED, explicitly, for the same reason everything is enabled
        # explicitly: the starting state should not depend on what a fresh
        # install happens to ship. Unarmed is also the state that makes a test
        # placing no orders mean something. A test about execution arms what it
        # needs; a test whose subject IS the shipped default reads
        # `automation.armed_by_default` off the definition.
        if definition.automation.automated:
            registry.set_armed(definition.key, False)
    yield
    registry.apply_state(strategies, capabilities, armed)


def pytest_sessionfinish(session, exitstatus):
    """Remove this run's own database and log directory.

    Both are named after the process id so that two concurrent runs cannot
    share them (see the comments at the top of this file). That makes cleanup
    this hook's job -- without it every run would leave a file behind in the
    temp directory for ever.

    Never raises: a failure to tidy up must not change a run's exit status.
    """
    import glob
    import shutil

    for path in glob.glob(f"{_TEST_DB_PATH}*"):
        try:
            os.remove(path)
        except OSError:
            pass
    shutil.rmtree(TEST_LOG_DIR, ignore_errors=True)
