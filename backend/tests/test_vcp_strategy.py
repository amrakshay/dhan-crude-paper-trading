"""NSE VCP Swing: the rules as the backtest measured them, and what the jobs do.

Three groups:

  1. THE SCAN reproduces the backtest's daily-step signals for four names over
     their whole history, remembers bases it will not trade, and applies the
     two FINAL_LOGIC gates.
  2. THE EXITS: breakout inside its window, the trail only AFTER the breakout
     bar, the stop, the cap, and the first due exit winning.
  3. THE JOBS on a real database: the nightly journals a candidate once; the
     morning buys it only on the next trading day, only when armed, never
     under its stop and never without the cash; the live stop sells.
"""
import csv
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from src.core.time_utils import IST, utc_now
from src.daily_bars.database.db_operations.daily_bar_repository import DailyBarRepository
from src.instruments.database.db_operations.instrument_repository import InstrumentRepository
from src.market.services.feed_manager import get_feed_manager
from src.portfolios.services.portfolio_service import PortfolioService
from src.strategies.services.strategy_registry import get_strategy_registry
from src.vcp.database.db_models.vcp_models import (
    ACTION_BOUGHT,
    ACTION_SKIPPED,
    EXIT_CAP,
    EXIT_STOP,
    EXIT_TRAIL,
    POSITION_EXITED,
    POSITION_OPEN,
    RUN_NIGHTLY,
    SIGNAL_CANDIDATE,
    SIGNAL_IGNORED,
    SIGNAL_REJECTED,
)
from src.vcp.database.db_operations.vcp_repository import (
    VcpDecisionRepository,
    VcpPositionRepository,
    VcpScanStateRepository,
    VcpSessionRepository,
    VcpSignalRepository,
)
from src.vcp.services import signal_service as ss
from src.vcp.services.detector.bars import Bars, from_rows
from src.vcp.services.execution_service import VcpExecutionService, next_trading_day
from src.vcp.services.module_hooks import nightly_due
from src.vcp.services.vcp_parameters import VcpConfigError, VcpParameters

STRATEGY = "nse-vcp-swing"
SEGMENT = "NSE_EQ"
FIXTURES = Path(__file__).parent / "fixtures" / "vcp"

# TITAN's 10W 12/5 3T base, first visible on 2026-06-25 -- a real candidate in
# the fixture bars: pivot 4554.00, stop 4225.00, close 4291.30.
SYMBOL, SECURITY_ID = "TITAN", "3506"
SIGNAL_DAY = date(2026, 6, 25)
NEXT_DAY = date(2026, 6, 26)            # a Friday


@pytest.fixture
def definition():
    return get_strategy_registry().require(STRATEGY)


@pytest.fixture
def params(definition):
    return VcpParameters.from_definition(definition)


def _rows(symbol):
    with (FIXTURES / f"{symbol}.csv").open() as fh:
        return [(r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"])
                for r in csv.DictReader(fh)]


# =============================================================================
# 1. THE SCAN
# =============================================================================


@pytest.fixture(scope="module")
def titan_scan():
    """The last 900 bars of TITAN, scanned once for the tests that read it."""
    from src.strategies.services.strategy_registry import get_strategy_registry as reg

    p = VcpParameters.from_definition(reg().require(STRATEGY))
    bars = from_rows("TITAN", _rows("TITAN"))
    return bars, p, ss.scan(bars, range(len(bars) - 900, len(bars)), [], p)


def test_a_base_already_through_its_pivot_is_remembered_not_dropped(titan_scan):
    """Forgetting it let a later reading of the same base pass as new."""
    _, _, found = titan_scan
    assert any(b.status == SIGNAL_IGNORED for b in found)


def test_the_two_gates_reject_2T_bases_and_coin_flip_stops(params):
    """APARINDS's last 900 bars hold both: a 2T base with a wide stop (2.14
    ATR, rejected for its count) and 3T bases with stops under 0.5 ATR."""
    bars = from_rows("APARINDS", _rows("APARINDS"))
    found = ss.scan(bars, range(len(bars) - 900, len(bars)), [], params)
    rejected = [b for b in found if b.status == SIGNAL_REJECTED]
    assert any(b.contractions == 2 and b.stop_atr >= params.min_stop_atr for b in rejected)
    assert any(b.contractions >= 3 and b.stop_atr < params.min_stop_atr for b in rejected)
    for b in found:
        if b.status == SIGNAL_CANDIDATE:
            assert b.contractions >= 3 and b.stop_atr >= params.min_stop_atr


def test_memory_blocks_a_second_reading_of_the_same_base(params):
    bars = from_rows("TITAN", _rows("TITAN"))
    window = range(len(bars) - 300, len(bars))
    memory = []
    first = ss.scan(bars, window, memory, params)
    again = ss.scan(bars, window, memory, params)
    assert first and again == []


def test_every_parameter_is_required(definition):
    section = dict(definition.module_section("parameters"))
    section.pop("min_stop_atr")

    class Fake:
        key = definition.key

        def module_section(self, name):
            return section if name == "parameters" else definition.module_section(name)

    with pytest.raises(VcpConfigError, match="min_stop_atr"):
        VcpParameters.from_definition(Fake())


# =============================================================================
# 2. THE EXITS
# =============================================================================


def _series(closes, lows=None, start=date(2026, 1, 1)):
    n = len(closes)
    lows = lows or [c - 1 for c in closes]
    days = [start + timedelta(days=i) for i in range(n)]
    return from_rows("X", [(d, c, c + 1, l, c, 1000) for d, c, l in zip(days, closes, lows)]), days


def test_the_trail_arms_only_after_the_breakout_bar(params):
    # signal day 0 at 100, pivot 105; breakout on day 3; then a close under
    # the EMA. The EMA is seeded high so the breakout bar itself is under it.
    closes = [200] * 60 + [100, 101, 102, 110, 111, 50]
    bars, days = _series(closes)
    sig = days[60]
    v = ss.evaluate_position(bars, signal_date=sig, entry_date=days[61], stop=40.0,
                             pivot=105.0, checked_through=None, params=params)
    assert v.breakout_date == days[63]
    # Days 63 and 64 are under the 200-seeded EMA too; the FIRST bar after the
    # breakout bar that closes under it is day 64.
    assert v.exit_due == EXIT_TRAIL and str(days[64]) in v.exit_reason


def test_a_breakout_outside_its_window_never_arms_the_trail(params):
    closes = [100] * (params.breakout_window_sessions + 2) + [200, 50]
    bars, days = _series(closes)
    v = ss.evaluate_position(bars, signal_date=days[0], entry_date=days[1], stop=10.0,
                             pivot=150.0, checked_through=None, params=params)
    assert v.breakout_date is None and v.exit_due is None


def test_a_low_through_the_stop_is_due_at_the_next_open(params):
    bars, days = _series([100, 101, 102, 103], lows=[99, 100, 89, 100])
    v = ss.evaluate_position(bars, signal_date=days[0], entry_date=days[1], stop=90.0,
                             pivot=150.0, checked_through=None, params=params)
    assert v.exit_due == EXIT_STOP


def test_the_holding_cap(params):
    bars, days = _series([100.0] * (params.max_hold_sessions + 5))
    v = ss.evaluate_position(bars, signal_date=days[0], entry_date=days[1], stop=10.0,
                             pivot=150.0, checked_through=None, params=params)
    assert v.exit_due == EXIT_CAP


def test_bars_already_checked_are_not_re_read(params):
    bars, days = _series([100, 101, 102, 103], lows=[99, 89, 100, 100])
    v = ss.evaluate_position(bars, signal_date=days[0], entry_date=days[1], stop=90.0,
                             pivot=150.0, checked_through=days[1], params=params)
    assert v.exit_due is None


def test_the_nightly_never_runs_inside_the_session(definition):
    assert nightly_due(definition, time(8, 40), time(9, 0))
    assert not nightly_due(definition, time(8, 40), time(9, 15))
    assert not nightly_due(definition, time(8, 40), time(12, 0))
    assert not nightly_due(definition, time(8, 40), time(8, 39))
    # An evening analysis time runs through midnight to the open.
    assert nightly_due(definition, time(18, 15), time(23, 0))
    assert nightly_due(definition, time(18, 15), time(7, 0))
    assert not nightly_due(definition, time(18, 15), time(16, 0))


def test_a_signal_is_good_for_the_next_trading_day_only(definition):
    assert next_trading_day(date(2026, 6, 25), definition) == date(2026, 6, 26)
    assert next_trading_day(date(2026, 6, 26), definition) == date(2026, 6, 29)


# =============================================================================
# 3. THE JOBS
# =============================================================================


@pytest.fixture(autouse=True)
def _clean_book():
    book = get_feed_manager().book
    book.clear()
    yield
    book.clear()


@pytest.fixture
def armed():
    registry = get_strategy_registry()
    registry.set_armed(STRATEGY, True)
    yield
    registry.set_armed(STRATEGY, False)


async def _seed(session, through=SIGNAL_DAY, cash=Decimal("1000000")):
    """TITAN's bars to `through`, NIFTY on the same dates, the instrument, a
    portfolio, and a scan cursor one bar back (as the bootstrap leaves it)."""
    rows = [r for r in _rows(SYMBOL) if r[0] <= through.isoformat()]
    bars = []
    for d, o, h, l, c, v in rows:
        for sym, seg, sid in ((SYMBOL, SEGMENT, SECURITY_ID), ("NIFTY", "IDX_I", "13")):
            bars.append(dict(symbol=sym, exchange_segment=seg, security_id=sid,
                             bar_date=date.fromisoformat(d), open=Decimal(o), high=Decimal(h),
                             low=Decimal(l), close=Decimal(c), volume=int(float(v))))
    await DailyBarRepository(session).upsert_many(bars)
    await InstrumentRepository(session).upsert_many([{
        "security_id": SECURITY_ID, "exchange_id": "NSE", "exchange_segment": SEGMENT,
        "segment_code": 1, "instrument_type": "EQUITY", "underlying_symbol": SYMBOL,
        "underlying_scrip": None, "trading_symbol": SYMBOL, "display_name": SYMBOL,
        "expiry_date": None, "strike_price": None, "option_type": None, "lot_size": 1,
        "tick_size": Decimal("0.05"), "is_active": True, "fno_eligible": False,
        "refreshed_at": utc_now()}])
    await VcpScanStateRepository(session).advance(STRATEGY, SYMBOL,
                                                  date.fromisoformat(rows[-2][0]))
    portfolio = await PortfolioService(session).create(
        name="VCP test", description="", strategy_keys=[STRATEGY], opening_balance=cash)
    await session.commit()
    return portfolio.id


def _book(price, quantity=100_000):
    get_feed_manager().book.apply_packet({
        "security_id": int(SECURITY_ID), "segment": 1, "ltp": price, "volume": 500_000,
        "high": price + 10, "low": price - 10, "open": price, "close": price,
        "depth": [(quantity, quantity, 1, 1, price - 0.5, price + 0.5) for _ in range(5)],
    })


def _service(session, definition, at=None):
    clock = (lambda: at) if at is not None else None
    return VcpExecutionService(session, definition, book=get_feed_manager().book, clock=clock)


AT_OPEN = datetime.combine(NEXT_DAY, time(9, 16)).replace(tzinfo=IST)


async def test_the_nightly_journals_the_candidate_once(db_session, definition):
    await _seed(db_session)
    out = await _service(db_session, definition).run_nightly()
    await db_session.commit()
    assert out.ok and out.candidates == 1
    signals = await VcpSignalRepository(db_session).with_status(STRATEGY, [SIGNAL_CANDIDATE])
    assert [(s.symbol, s.seen_date, s.pivot_price, s.stop_price) for s in signals] == [
        (SYMBOL, SIGNAL_DAY, Decimal("4554.0000"), Decimal("4225.0000"))]
    again = await _service(db_session, definition).run_nightly()
    assert "already decided" in again.detail


async def test_unarmed_it_decides_the_same_and_places_nothing(db_session, definition):
    pid = await _seed(db_session)
    await _service(db_session, definition).run_nightly()
    await db_session.commit()
    _book(4300.0)
    out = await _service(db_session, definition, AT_OPEN).run_morning(pid)
    assert out.bought == 0
    decisions = await VcpDecisionRepository(db_session).list_recent(pid)
    assert decisions[0].action == ACTION_SKIPPED and "NOT ARMED" in decisions[0].reason
    assert await VcpPositionRepository(db_session).open_for(STRATEGY, pid) == []


async def test_armed_it_buys_a_fixed_rupee_position_with_its_stop(db_session, definition, armed):
    pid = await _seed(db_session)
    await _service(db_session, definition).run_nightly()
    await db_session.commit()
    _book(4300.0)
    out = await _service(db_session, definition, AT_OPEN).run_morning(pid)
    assert out.bought == 1
    [pos] = await VcpPositionRepository(db_session).open_for(STRATEGY, pid)
    assert pos.quantity == int(150000 // 4300)
    assert pos.stop_price == Decimal("4225.0000") and pos.entry_date == NEXT_DAY
    decisions = await VcpDecisionRepository(db_session).list_recent(pid)
    assert decisions[0].action == ACTION_BOUGHT and decisions[0].order_id


async def test_a_stale_signal_is_not_bought_a_day_late(db_session, definition, armed):
    pid = await _seed(db_session)
    await _service(db_session, definition).run_nightly()
    await db_session.commit()
    _book(4300.0)
    late = datetime.combine(date(2026, 6, 29), time(9, 16)).replace(tzinfo=IST)
    out = await _service(db_session, definition, late).run_morning(pid)
    assert out.bought == 0 and "Nothing to buy" in out.detail


async def test_an_open_under_the_stop_is_not_bought(db_session, definition, armed):
    pid = await _seed(db_session)
    await _service(db_session, definition).run_nightly()
    await db_session.commit()
    _book(4200.0)
    out = await _service(db_session, definition, AT_OPEN).run_morning(pid)
    assert out.bought == 0
    decisions = await VcpDecisionRepository(db_session).list_recent(pid)
    assert "under the stop" in decisions[0].reason


async def test_it_never_buys_without_the_cash(db_session, definition, armed):
    pid = await _seed(db_session, cash=Decimal("100000"))
    await _service(db_session, definition).run_nightly()
    await db_session.commit()
    _book(4300.0)
    out = await _service(db_session, definition, AT_OPEN).run_morning(pid)
    assert out.bought == 0
    decisions = await VcpDecisionRepository(db_session).list_recent(pid)
    assert "available" in decisions[0].reason


async def test_the_live_stop_sells_in_session_and_not_outside_it(db_session, definition, armed):
    pid = await _seed(db_session)
    await _service(db_session, definition).run_nightly()
    await db_session.commit()
    _book(4300.0)
    await _service(db_session, definition, AT_OPEN).run_morning(pid)
    await db_session.commit()

    _book(4220.0)
    after_close = datetime.combine(NEXT_DAY, time(16, 0)).replace(tzinfo=IST)
    assert await _service(db_session, definition, after_close).check_stops() == 0
    midday = datetime.combine(NEXT_DAY, time(11, 0)).replace(tzinfo=IST)
    assert await _service(db_session, definition, midday).check_stops() == 1
    positions = await VcpPositionRepository(db_session).list_recent(STRATEGY, pid)
    assert positions[0].status == POSITION_EXITED and positions[0].exit_kind == EXIT_STOP


async def test_a_flagged_trail_exit_is_sold_at_the_next_open(db_session, definition, armed):
    pid = await _seed(db_session)
    await _service(db_session, definition).run_nightly()
    await db_session.commit()
    _book(4300.0)
    await _service(db_session, definition, AT_OPEN).run_morning(pid)
    await db_session.commit()
    [pos] = await VcpPositionRepository(db_session).open_for(STRATEGY, pid)
    await VcpPositionRepository(db_session).save(pos, exit_due=EXIT_TRAIL,
                                                 exit_reason="test: closed under the EMA")
    await db_session.commit()

    monday = datetime.combine(date(2026, 6, 29), time(9, 16)).replace(tzinfo=IST)
    out = await _service(db_session, definition, monday).run_morning(pid)
    assert out.sold == 1
    [pos] = await VcpPositionRepository(db_session).list_recent(STRATEGY, pid)
    assert pos.status == POSITION_EXITED and pos.exit_kind == EXIT_TRAIL


async def test_a_name_already_held_is_not_bought_again(db_session, definition, armed):
    pid = await _seed(db_session)
    await _service(db_session, definition).run_nightly()
    await db_session.commit()
    _book(4300.0)
    await _service(db_session, definition, AT_OPEN).run_morning(pid)
    await db_session.commit()
    # Force a second morning for the same session (as if the journal row were lost).
    service = _service(db_session, definition, AT_OPEN)
    record = await VcpSessionRepository(db_session).latest(STRATEGY)
    [signal] = await VcpSignalRepository(db_session).with_status(STRATEGY, ["TAKEN"])
    assert await service._enter(record, signal, pid, True) is False  # noqa: SLF001
