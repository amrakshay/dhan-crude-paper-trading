"""The swing rotation is unaffected by any of this.

`tests/test_swing_does_not_disturb_crude.py` has the pattern and the reason:
the rotation is LIVE and ARMED on a real book, and every change BTST needed in
shared code is one that could have moved a rotation number without anything
failing. This file is the one that fails with a name saying WHY.

Four shared things changed on 2026-09-19, and each gets an assertion here:

  1. **The instrument master lets two strategies claim one symbol** when they
     ingest it identically. The rotation's rows must be unchanged.
  2. **`submit_paper_order` takes a `strategy_key`**, because the Nifty 500 no
     longer determines a strategy. A rotation order must still land under the
     rotation.
  3. **The scheduler dispatches per module.** The rotation's own jobs, times
     and run kinds must be exactly what they were.
  4. **`for_instrument` returns None when several strategies claim a name.**
     The rotation must not silently start trading under another key.

And one thing that must NOT have changed: the rotation still has a stop, still
has a breadth ramp and still has its own three policy switches. BTST has none
of those, and the two modules share no parameters class.
"""
from decimal import Decimal

import pytest

from src.core.time_utils import utc_now
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.market.services.feed_manager import get_feed_manager
from src.orders.database.db_operations.order_repository import OrderRepository
from src.orders.services.order_service import OrderService, OrderValidationError
from src.portfolios.services.portfolio_service import PortfolioService
from src.positions.database.db_operations.position_repository import PositionRepository
from src.strategies.services.strategy_definition import (
    POLICY_OFF_GATE_ENABLED,
    POLICY_REGIME_ENFORCE,
    POLICY_REGIME_ENFORCE_ENTRY_RETURN,
    SETTING_NIGHTLY_AT,
    SETTING_REBALANCE_AT,
)
from src.strategies.services.strategy_registry import get_strategy_registry

SWING = "nse-swing-momentum"
BTST = "nse-btst-overnight"
SEGMENT = "NSE_EQ"
SYMBOL = "WELCORP"
SECURITY_ID = "9101"


@pytest.fixture(autouse=True)
def _clean_book():
    book = get_feed_manager().book
    book.clear()
    yield
    book.clear()


async def _seed_instrument(session):
    await InstrumentRepository(session).upsert_many(
        [
            {
                "security_id": SECURITY_ID, "exchange_id": "NSE",
                "exchange_segment": SEGMENT, "segment_code": 1,
                "instrument_type": "EQUITY", "underlying_symbol": SYMBOL,
                "underlying_scrip": None, "trading_symbol": SYMBOL,
                "display_name": SYMBOL, "expiry_date": None,
                "strike_price": None, "option_type": None, "lot_size": 1,
                "tick_size": Decimal("0.05"), "is_active": True,
                "fno_eligible": False, "refreshed_at": utc_now(),
            }
        ]
    )
    await session.commit()


def _seed_book():
    get_feed_manager().book.apply_packet(
        {
            "security_id": int(SECURITY_ID), "segment": 1,
            "ltp": 100.0, "volume": 500_000,
            "high": 101.0, "low": 99.0, "open": 99.5, "close": 99.5,
            "depth": [(50_000, 50_000, 1, 1, 100.0, 100.5) for _ in range(5)],
        }
    )


def _orders(session):
    return OrderService(
        OrderRepository(session),
        InstrumentRepository(session),
        PositionRepository(session),
        book=get_feed_manager().book,
    )


# --- 1. the instrument master ----------------------------------------------


def test_the_two_modules_ingest_the_nifty_500_identically(definition=None):
    """Which is WHY they are allowed to share it.

    The exclusivity rule was relaxed from "one strategy per symbol" to "one
    ingestion rule per symbol". That is only safe while the two rules agree, so
    this asserts the agreement rather than trusting it: same segment, same
    series filter, same lot-size source, same trading-symbol source.
    """
    registry = get_strategy_registry()
    swing = registry.require(SWING)
    btst = registry.require(BTST)

    swing_equity = [
        one for one in swing.instrument_sets if one.instrument_type == "EQUITY"
    ]
    btst_equity = [
        one for one in btst.instrument_sets if one.instrument_type == "EQUITY"
    ]
    assert len(swing_equity) == len(btst_equity) == 1
    assert (
        swing_equity[0].ingestion_signature()
        == btst_equity[0].ingestion_signature()
    )
    # And the same universe file, so there is one list to keep current.
    assert swing.universe.file == btst.universe.file
    assert swing.universe.symbols == btst.universe.symbols


def test_two_strategies_that_DISAGREED_would_still_be_refused():
    """The relaxation has a floor, and it is the thing that matters.

    A lot size from the master against one from config produces a genuinely
    different row, and "whichever strategy loaded first" is not an answer.
    """
    from src.strategies.services.strategy_definition import InstrumentSet

    master = InstrumentSet(
        instrument_type="EQUITY", exchange_segment=SEGMENT,
        exchange_segment_code=1, lot_size_source="master",
    )
    config = InstrumentSet(
        instrument_type="EQUITY", exchange_segment=SEGMENT,
        exchange_segment_code=1, lot_size_source="config",
    )
    assert master.ingestion_signature() != config.ingestion_signature()


# --- 2. the order path ------------------------------------------------------


async def test_a_rotation_order_still_lands_under_the_rotation(db_session):
    """The Nifty 500 belongs to two strategies now, so the order says which."""
    await _seed_instrument(db_session)
    _seed_book()
    portfolio = await PortfolioService(db_session).create(
        name="Both", description="", strategy_keys=[SWING, BTST],
        opening_balance=Decimal("1000000"),
    )
    await db_session.commit()

    order = await _orders(db_session).submit_paper_order(
        security_id=SECURITY_ID, side="BUY", order_type="MARKET", lots=10,
        portfolio_id=portfolio.id, strategy_key=SWING,
    )
    assert order.strategy_key == SWING

    other = await _orders(db_session).submit_paper_order(
        security_id=SECURITY_ID, side="BUY", order_type="MARKET", lots=10,
        portfolio_id=portfolio.id, strategy_key=BTST,
    )
    assert other.strategy_key == BTST


async def test_an_ambiguous_order_with_no_strategy_key_is_REFUSED_not_guessed(
    db_session,
):
    """Returning the alphabetically first would put the trade under the wrong
    rate card, the wrong arming switch and the wrong journal, silently.

    The same reasoning `frontend/CLAUDE.md` section 2a gives for the portfolio
    picker: a trade landing in the wrong book is exactly what saying it
    explicitly prevents.
    """
    await _seed_instrument(db_session)
    _seed_book()
    portfolio = await PortfolioService(db_session).create(
        name="Both", description="", strategy_keys=[SWING, BTST],
        opening_balance=Decimal("1000000"),
    )
    await db_session.commit()

    with pytest.raises(OrderValidationError, match="more than one strategy"):
        await _orders(db_session).submit_paper_order(
            security_id=SECURITY_ID, side="BUY", order_type="MARKET", lots=10,
            portfolio_id=portfolio.id,
        )


async def test_an_order_naming_a_strategy_that_does_not_trade_it_is_refused(
    db_session,
):
    """A caller naming an unrelated strategy is a bug, not a preference."""
    await _seed_instrument(db_session)
    _seed_book()
    portfolio = await PortfolioService(db_session).create(
        name="Both", description="", strategy_keys=[SWING, BTST],
        opening_balance=Decimal("1000000"),
    )
    await db_session.commit()

    with pytest.raises(OrderValidationError, match="does not trade"):
        await _orders(db_session).submit_paper_order(
            security_id=SECURITY_ID, side="BUY", order_type="MARKET", lots=10,
            portfolio_id=portfolio.id, strategy_key="mcx-crude-options",
        )


async def test_closing_a_position_resolves_the_strategy_from_the_POSITION(
    db_session,
):
    """A close must land under the same strategy as its open.

    Otherwise the two halves of one round trip end up in different books, and
    the realised P&L of both is wrong. This is what lets a person close a
    position from the UI without knowing which strategy opened it.
    """
    await _seed_instrument(db_session)
    _seed_book()
    portfolio = await PortfolioService(db_session).create(
        name="Both", description="", strategy_keys=[SWING, BTST],
        opening_balance=Decimal("1000000"),
    )
    await db_session.commit()

    await _orders(db_session).submit_paper_order(
        security_id=SECURITY_ID, side="BUY", order_type="MARKET", lots=10,
        portfolio_id=portfolio.id, strategy_key=BTST,
    )
    await db_session.commit()

    closing = await _orders(db_session).submit_paper_order(
        security_id=SECURITY_ID, side="SELL", order_type="MARKET", lots=10,
        is_close_order=True, portfolio_id=portfolio.id,
    )
    assert closing.strategy_key == BTST


def test_for_instrument_returns_None_rather_than_a_guess_when_two_claim_it():
    """`None` now also means "more than one", and every caller handles it."""
    registry = get_strategy_registry()
    # An MCX contract is claimed by one strategy, so it still resolves.
    assert registry.for_instrument("MCX_COMM", "CRUDEOIL").key == "mcx-crude-options"
    # A Nifty 500 name is claimed by two.
    assert registry.for_instrument(SEGMENT, SYMBOL) is None
    assert {
        one.key for one in registry.strategies_for_instrument(SEGMENT, SYMBOL)
    } == {SWING, BTST}


# --- 3. the scheduler -------------------------------------------------------


def test_the_rotations_own_times_run_kinds_and_jobs_are_unchanged():
    """The clock dispatches per module now; what it dispatches must not move."""
    from src.swing.database.db_models.swing_session_model import (
        RUN_NIGHTLY,
        RUN_REBALANCE,
        RUN_KINDS,
    )
    from src.swing.services.schedule_settings import resolve_schedule
    from src.swing.services.swing_parameters import SwingParameters

    definition = get_strategy_registry().require(SWING)
    parameters = SwingParameters.from_definition(definition)
    schedule = resolve_schedule(definition, parameters)

    assert schedule.nightly_at == "18:15"
    assert schedule.rebalance_at == "09:16"
    assert RUN_NIGHTLY == "NIGHTLY" and RUN_REBALANCE == "REBALANCE"
    assert set(RUN_KINDS) == {"NIGHTLY", "REBALANCE", "MANUAL"}


def test_the_two_modules_run_kinds_do_not_overlap():
    """Stored strings, and two strategies whose jobs do different things must
    not share a vocabulary that makes their journals look comparable."""
    from src.btst.database.db_models.btst_session_model import (
        RUN_KINDS as BTST_KINDS,
    )
    from src.swing.database.db_models.swing_session_model import (
        RUN_KINDS as SWING_KINDS,
    )

    assert set(BTST_KINDS) == {"SCAN", "EXIT", "MANUAL"}
    # MANUAL is shared in NAME only; the journals are separate tables.
    assert set(BTST_KINDS) & set(SWING_KINDS) == {"MANUAL"}


def test_the_scheduler_dispatches_to_each_modules_own_hooks():
    """One clock, two sets of jobs, and neither imports the other."""
    from src.strategies.services.strategy_modules import hooks_for

    registry = get_strategy_registry()
    swing_hooks = hooks_for(registry.require(SWING))
    btst_hooks = hooks_for(registry.require(BTST))

    assert swing_hooks is not btst_hooks
    assert swing_hooks.__name__ == "src.swing.services.module_hooks"
    assert btst_hooks.__name__ == "src.btst.services.module_hooks"
    # A discretionary module has no hooks at all, which is a real answer.
    assert hooks_for(registry.require("mcx-crude-options")) is None


# --- 4. the policies and settings stay per module ---------------------------


def test_each_module_offers_only_its_own_switches():
    """The framework knows the NAMES; what each MEANS belongs to its module.

    The rotation keeps its three; BTST has two, one of which is the same
    `regime.enforce` -- deliberately the same name, because both mean the index
    against its own 200-session SMA.
    """
    from src.btst.services.btst_policy import describe_policies as btst_policies
    from src.swing.services.gate_policy import describe_policies as swing_policies

    registry = get_strategy_registry()
    swing_keys = {
        row["key"] for row in swing_policies(registry.require(SWING))["policies"]
    }
    btst_keys = {
        row["key"] for row in btst_policies(registry.require(BTST))["policies"]
    }

    assert swing_keys == {
        POLICY_REGIME_ENFORCE,
        POLICY_REGIME_ENFORCE_ENTRY_RETURN,
        POLICY_OFF_GATE_ENABLED,
    }
    assert POLICY_OFF_GATE_ENABLED not in btst_keys
    assert POLICY_REGIME_ENFORCE_ENTRY_RETURN not in btst_keys
    assert POLICY_REGIME_ENFORCE in swing_keys & btst_keys


def test_each_module_offers_only_its_own_clock_times():
    from src.btst.services.btst_schedule import describe_settings as btst_settings
    from src.swing.services.schedule_settings import (
        describe_settings as swing_settings,
    )

    registry = get_strategy_registry()
    swing_keys = {
        row["key"] for row in swing_settings(registry.require(SWING))["settings"]
    }
    btst_keys = {
        row["key"] for row in btst_settings(registry.require(BTST))["settings"]
    }
    assert swing_keys == {SETTING_NIGHTLY_AT, SETTING_REBALANCE_AT}
    assert not (swing_keys & btst_keys)


def test_btst_wires_up_no_stop_service_and_no_stop_monitor():
    """B15 is "none", and the absence has to be structural rather than a
    decision somebody remembers.

    A stop cannot apply to the risk this strategy carries: every position is
    opened near the close and sold at the next open, so its only risk window is
    one in which no order can execute at any price.
    """
    import src.btst.services.execution_service as execution
    import src.btst.services.module_hooks as hooks

    for module in (execution, hooks):
        source = module.__doc__ or ""
        assert "stop_monitor" not in dir(module)
    # And nothing in the package imports the rotation's stop machinery.
    import pathlib

    package = pathlib.Path(execution.__file__).parent
    for path in package.glob("*.py"):
        text = path.read_text()
        assert "from src.swing.services.stop_service" not in text, path
        assert "from src.swing.services.stop_monitor" not in text, path


def test_btst_has_no_breadth_ramp_and_no_rotation_rank():
    """The two parameters classes are separate, and this is why.

    They share three numbers by coincidence -- a 20-session liquidity window, a
    Rs 50 price floor, a 200-session trend SMA -- and disagree about everything
    else. A shared class would have to make every field optional, which is the
    defaulting both files exist to forbid.
    """
    from src.btst.services.btst_parameters import BtstParameters
    from src.swing.services.swing_parameters import SwingParameters

    registry = get_strategy_registry()
    btst = BtstParameters.from_definition(registry.require(BTST))
    swing = SwingParameters.from_definition(registry.require(SWING))

    for field in ("breadth_lower", "breadth_span", "rotation_exit_rank",
                  "trail_atr_multiple", "atr_sessions", "max_positions"):
        assert hasattr(swing, field)
        assert not hasattr(btst, field), f"BTST must not have {field}"

    for field in ("breakout_lookback_sessions", "volume_multiple",
                  "close_location_minimum", "slots", "ranking"):
        assert hasattr(btst, field)
        assert not hasattr(swing, field), f"the rotation must not have {field}"

    # The three they genuinely share, and they agree about them.
    assert btst.liquidity_window_sessions == swing.liquidity_window_sessions
    assert btst.price_floor == swing.price_floor
    assert btst.trend_sma_sessions == swing.trend_sma_sessions
