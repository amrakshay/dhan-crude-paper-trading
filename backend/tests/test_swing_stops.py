"""The chandelier trailing stop.

P14 sets it at `entry - 3.5 x ATR14`; P15 raises it on every daily close to
`max(stop, highest_close_since_entry - 3.5 x ATR14_today)` and **never lowers
it**. The ratchet test is the load-bearing one: ATR rises after a violent day,
so the naive formula can produce a LOWER stop on exactly the session the
position became more dangerous.

The firing tests are all about restraint -- no mark, no fire; no session, no
fire; inside the closing auction, record the trigger and wait. An exit this
simulator cannot honestly fill must not be filled.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from src.core.time_utils import IST, utc_now
from src.daily_bars.database.db_operations.daily_bar_repository import (
    DailyBarRepository,
)
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.market.services.feed_manager import get_feed_manager
from src.portfolios.database.db_models.portfolio_model import Portfolio
from src.portfolios.database.db_operations.portfolio_repository import (
    CashLedgerRepository,
    PortfolioRepository,
)
from src.strategies.services import market_clock
from src.strategies.services.strategy_registry import get_strategy_registry
from src.swing.database.db_models.swing_stop_model import (
    EXIT_ROTATION,
    EXIT_TRAIL,
    STOP_ACTIVE,
    STOP_CLOSED,
    STOP_TRIGGERED,
)
from src.swing.database.db_operations.swing_stop_repository import (
    SwingStopError,
    SwingStopRepository,
)
from src.swing.services.ranking_service import RankingService
from src.swing.services.stop_monitor import SwingStopMonitor
from src.swing.services.stop_service import StopService
from src.swing.services.swing_parameters import SwingParameters

STRATEGY = "nse-swing-momentum"
SEGMENT = "NSE_EQ"
START = date(2024, 1, 1)
SYMBOL = "WELCORP"
SECURITY_ID = "9000"

# Inside NSE's continuous session, and inside the closing auction. The CAS went
# live on 3 August 2026: for F&O-eligible names continuous trading ends at
# 15:15 and an auction runs to 15:35.
MIDSESSION = datetime(2026, 9, 17, 11, 30, tzinfo=IST)     # a Thursday
IN_AUCTION = datetime(2026, 9, 17, 15, 20, tzinfo=IST)
AFTER_CLOSE = datetime(2026, 9, 17, 19, 0, tzinfo=IST)


@pytest.fixture
def definition():
    return get_strategy_registry().require(STRATEGY)


@pytest.fixture
def parameters(definition):
    return SwingParameters.from_definition(definition)


@pytest.fixture(autouse=True)
def _clean_book():
    get_feed_manager().book.clear()
    yield
    get_feed_manager().book.clear()


def _bars(symbol, rows, start=START):
    """rows is [(open, high, low, close)] -- the stop needs real ranges."""
    return [
        {
            "symbol": symbol,
            "exchange_segment": SEGMENT,
            "security_id": SECURITY_ID,
            "bar_date": start + timedelta(days=offset),
            "open": Decimal(str(row[0])),
            "high": Decimal(str(row[1])),
            "low": Decimal(str(row[2])),
            "close": Decimal(str(row[3])),
            "volume": 5_000_000,
            "source": "import",
        }
        for offset, row in enumerate(rows)
    ]


def _calm(count, start=100.0, step=1.0):
    """A quiet uptrend: a narrow range every day, so ATR stays small."""
    rows = []
    for index in range(count):
        close = start + step * index
        rows.append((close, close + 0.5, close - 0.5, close))
    return rows


async def _seed_portfolio(db_session) -> int:
    portfolio = Portfolio(name="Swing Momentum", status="ACTIVE")
    db_session.add(portfolio)
    await db_session.flush()
    await PortfolioRepository(db_session).set_strategies(portfolio.id, [STRATEGY])
    await CashLedgerRepository(db_session).add_entry(
        portfolio_id=portfolio.id, entry_type="DEPOSIT",
        amount=Decimal("1000000"), entry_at=utc_now(), note="Opening balance",
    )
    await db_session.commit()
    return portfolio.id


async def _seed_instrument(db_session, fno_eligible=False):
    await InstrumentRepository(db_session).upsert_many(
        [
            {
                "security_id": SECURITY_ID, "exchange_id": "NSE",
                "exchange_segment": SEGMENT, "segment_code": 1,
                "instrument_type": "EQUITY", "underlying_symbol": SYMBOL,
                "underlying_scrip": None, "trading_symbol": SYMBOL,
                "display_name": SYMBOL, "expiry_date": None,
                "strike_price": None, "option_type": None, "lot_size": 1,
                "tick_size": Decimal("0.05"), "fno_eligible": fno_eligible,
                "is_active": True, "refreshed_at": utc_now(),
            }
        ]
    )
    await db_session.commit()


def _seed_book(ltp, size=100000):
    book = get_feed_manager().book
    book.register_instrument(
        SECURITY_ID, {"tradingSymbol": SYMBOL, "lotSize": 1, "tickSize": 0.05}
    )
    book.apply_packet(
        {
            "security_id": SECURITY_ID, "segment": 1, "ltp": ltp,
            "depth": [
                (size, size, 5, 5, ltp - 0.05, ltp + 0.05),
                (0, 0, 0, 0, 0.0, 0.0),
                (0, 0, 0, 0, 0.0, 0.0),
                (0, 0, 0, 0, 0.0, 0.0),
                (0, 0, 0, 0, 0.0, 0.0),
            ],
        }
    )
    return book


def _stop_service(db_session, definition):
    parameters = SwingParameters.from_definition(definition)
    ranking = RankingService(DailyBarRepository(db_session), definition, parameters)
    return StopService(
        SwingStopRepository(db_session), ranking, parameters, definition.key
    )


# --- P14: the stop at entry --------------------------------------------------


async def test_the_entry_stop_is_the_entry_price_less_the_atr_multiple(
    db_session, definition, parameters
):
    portfolio_id = await _seed_portfolio(db_session)
    service = _stop_service(db_session, definition)

    stop = await service.open_for_entry(
        portfolio_id=portfolio_id, symbol=SYMBOL, security_id=SECURITY_ID,
        quantity=100, entry_price=Decimal("500"), entry_session=date(2026, 9, 17),
        entry_atr=10.0,
    )

    assert parameters.trail_atr_multiple == 3.5
    assert Decimal(str(stop.stop_price)) == Decimal("465.0000")
    assert stop.status == STOP_ACTIVE
    # The entry price seeds the high-water mark: there is no close for the
    # entry session yet at 09:16.
    assert Decimal(str(stop.highest_close)) == Decimal("500.0000")


async def test_no_atr_means_no_stop_rather_than_an_invented_one(
    db_session, definition
):
    """A position with no stop is not the same as one with a distant stop.

    Changed shape on 2026-09-18 and NOT changed meaning. A row is now written
    for every position -- the table is the rotation's per-position record and
    carries the policy the position was opened under, which `plan_sells` reads
    back to decide whether a regime exit reaches it -- but `stop_price` is
    NULL, because there is still no level and nothing is invented. A table that
    skipped these entries would have failed open for exactly them.
    """
    portfolio_id = await _seed_portfolio(db_session)
    service = _stop_service(db_session, definition)

    stop = await service.open_for_entry(
        portfolio_id=portfolio_id, symbol=SYMBOL, security_id=SECURITY_ID,
        quantity=100, entry_price=Decimal("500"), entry_session=date(2026, 9, 17),
        entry_atr=None,
    )
    # No level was invented: not a percentage of the entry price, not zero.
    assert stop.stop_price is None
    assert stop.entry_atr is None
    assert stop.status == STOP_ACTIVE

    stored = await SwingStopRepository(db_session).get_active(
        portfolio_id, SECURITY_ID
    )
    assert stored is not None
    assert stored.stop_price is None
    # And it can never fire while it has no level.
    assert StopService.is_hit(stored, Decimal("0.01")) is False


async def test_one_holding_cannot_have_two_active_stops(db_session, definition):
    portfolio_id = await _seed_portfolio(db_session)
    service = _stop_service(db_session, definition)
    await service.open_for_entry(
        portfolio_id=portfolio_id, symbol=SYMBOL, security_id=SECURITY_ID,
        quantity=100, entry_price=Decimal("500"), entry_session=date(2026, 9, 17),
        entry_atr=10.0,
    )
    with pytest.raises(SwingStopError):
        await service.open_for_entry(
            portfolio_id=portfolio_id, symbol=SYMBOL, security_id=SECURITY_ID,
            quantity=100, entry_price=Decimal("510"),
            entry_session=date(2026, 9, 18), entry_atr=10.0,
        )


# --- P15: the ratchet --------------------------------------------------------


async def test_a_trailing_stop_never_ratchets_down(db_session, definition):
    """The load-bearing test.

    A violent session widens ATR14, so `highest_close - 3.5 x ATR_today` can be
    LOWER than yesterday's stop. Taking the maximum is what stops the exit
    sliding away from a position that has just become more dangerous. Remove
    the `max()` in `ratchet_one` and this fails.
    """
    portfolio_id = await _seed_portfolio(db_session)
    repository = DailyBarRepository(db_session)

    # 250 calm sessions, then one enormous range at the same close.
    rows = _calm(250, 100.0, 1.0)
    calm_close = rows[-1][3]
    rows.append((calm_close, calm_close + 60.0, calm_close - 60.0, calm_close))
    await repository.upsert_many(_bars(SYMBOL, rows))
    await db_session.commit()

    service = _stop_service(db_session, definition)
    calm_session = START + timedelta(days=249)
    wild_session = START + timedelta(days=250)

    stop = await service.open_for_entry(
        portfolio_id=portfolio_id, symbol=SYMBOL, security_id=SECURITY_ID,
        quantity=10, entry_price=Decimal(str(calm_close)),
        entry_session=calm_session, entry_atr=1.5,
    )
    await service.ratchet_one(stop, calm_session, SEGMENT)
    after_calm = Decimal(str(stop.stop_price))

    decision = await service.ratchet_one(stop, wild_session, SEGMENT)
    after_wild = Decimal(str(stop.stop_price))

    # The ATR really did widen -- otherwise this test proves nothing.
    assert Decimal(str(stop.last_atr)) > Decimal("1.5")
    naive = (
        Decimal(str(stop.highest_close))
        - Decimal(str(stop.atr_multiple)) * Decimal(str(stop.last_atr))
    )
    assert naive < after_calm, "the naive formula must want to lower the stop here"
    assert after_wild == after_calm
    assert decision is not None
    assert "never ratchets down" in decision.reason


async def test_the_stop_rises_with_the_highest_close_since_entry(
    db_session, definition
):
    portfolio_id = await _seed_portfolio(db_session)
    repository = DailyBarRepository(db_session)
    await repository.upsert_many(_bars(SYMBOL, _calm(260, 100.0, 1.0)))
    await db_session.commit()

    service = _stop_service(db_session, definition)
    entry_session = START + timedelta(days=250)
    stop = await service.open_for_entry(
        portfolio_id=portfolio_id, symbol=SYMBOL, security_id=SECURITY_ID,
        quantity=10, entry_price=Decimal("350"), entry_session=entry_session,
        entry_atr=1.0,
    )
    first = Decimal(str(stop.stop_price))

    await service.ratchet_one(stop, START + timedelta(days=255), SEGMENT)
    mid = Decimal(str(stop.stop_price))
    await service.ratchet_one(stop, START + timedelta(days=259), SEGMENT)
    last = Decimal(str(stop.stop_price))

    assert first < mid < last
    assert Decimal(str(stop.highest_close)) == Decimal("359.0000")


async def test_the_nightly_ratchet_is_idempotent_for_one_session(
    db_session, definition
):
    """A restart at 18:20 must not re-apply 18:15's work."""
    portfolio_id = await _seed_portfolio(db_session)
    repository = DailyBarRepository(db_session)
    await repository.upsert_many(_bars(SYMBOL, _calm(260, 100.0, 1.0)))
    await db_session.commit()

    service = _stop_service(db_session, definition)
    session_date = START + timedelta(days=259)
    await service.open_for_entry(
        portfolio_id=portfolio_id, symbol=SYMBOL, security_id=SECURITY_ID,
        quantity=10, entry_price=Decimal("350"),
        entry_session=START + timedelta(days=250), entry_atr=1.0,
    )

    first = await service.ratchet_all(session_date, SEGMENT)
    assert first.moved_count == 1

    second = await service.ratchet_all(session_date, SEGMENT)
    assert second.moved_count == 0
    assert second.unchanged == 1


async def test_a_symbol_with_no_bar_on_the_session_keeps_its_stop(
    db_session, definition
):
    """Never forward-fill. A stop recomputed off a stale close is a stop moved
    on information that does not exist."""
    portfolio_id = await _seed_portfolio(db_session)
    repository = DailyBarRepository(db_session)
    await repository.upsert_many(_bars(SYMBOL, _calm(260, 100.0, 1.0)))
    await db_session.commit()

    service = _stop_service(db_session, definition)
    stop = await service.open_for_entry(
        portfolio_id=portfolio_id, symbol=SYMBOL, security_id=SECURITY_ID,
        quantity=10, entry_price=Decimal("350"),
        entry_session=START + timedelta(days=250), entry_atr=1.0,
    )
    before = Decimal(str(stop.stop_price))

    # A session this name did not trade on.
    decision = await service.ratchet_one(
        stop, START + timedelta(days=400), SEGMENT
    )
    assert decision is None
    assert Decimal(str(stop.stop_price)) == before


# --- firing -------------------------------------------------------------------


def _monitor():
    monitor = SwingStopMonitor(book=get_feed_manager().book)
    return monitor


async def _open_stop(db_session, definition, portfolio_id, stop_price="90"):
    """A position and an active stop just under the market."""
    from src.instruments.database.db_operations.instrument_repository import (
        InstrumentRepository,
    )
    from src.orders.database.db_operations.order_repository import OrderRepository
    from src.orders.services.order_service import OrderService
    from src.positions.database.db_operations.position_repository import (
        PositionRepository,
    )

    orders = OrderService(
        OrderRepository(db_session),
        InstrumentRepository(db_session),
        PositionRepository(db_session),
        book=get_feed_manager().book,
    )
    order = await orders.submit_paper_order(
        security_id=SECURITY_ID, side="BUY", order_type="MARKET",
        lots=100, portfolio_id=portfolio_id,
    )
    service = _stop_service(db_session, definition)
    stop = await service.open_for_entry(
        portfolio_id=portfolio_id, symbol=SYMBOL, security_id=SECURITY_ID,
        quantity=100, entry_price=Decimal("100"),
        entry_session=date(2026, 9, 17), entry_atr=10.0, entry_order_id=order.id,
    )
    stop.stop_price = Decimal(stop_price)
    await db_session.commit()
    return stop


async def test_a_stop_fires_intraday_and_exits_through_the_ordinary_order_path(
    db_session, definition, monkeypatch
):
    portfolio_id = await _seed_portfolio(db_session)
    await _seed_instrument(db_session)
    _seed_book(ltp=100.0)
    get_strategy_registry().set_armed(STRATEGY, True)
    await _open_stop(db_session, definition, portfolio_id, stop_price="95")

    monkeypatch.setattr(market_clock, "ist_now", lambda: MIDSESSION)
    _seed_book(ltp=94.0)

    placed = await _monitor().run_once()
    assert placed == 1

    repository = SwingStopRepository(db_session)
    db_session.expire_all()
    rows = await repository.history(STRATEGY)
    stop = rows[0]
    assert stop.status == STOP_TRIGGERED
    assert stop.exit_kind == EXIT_TRAIL
    assert stop.exit_order_id is not None
    assert Decimal(str(stop.trigger_price)) == Decimal("94.0000")

    from src.orders.database.db_operations.order_repository import OrderRepository

    order = await OrderRepository(db_session).get_by_id(stop.exit_order_id)
    assert order.side == "SELL"
    assert order.is_close_order is True
    placed_event = [one for one in order.events if one.event_type == "PLACED"][0]
    assert "Trailing stop hit" in placed_event.message


async def test_a_stop_does_not_fire_without_a_mark(
    db_session, definition, monkeypatch
):
    portfolio_id = await _seed_portfolio(db_session)
    await _seed_instrument(db_session)
    _seed_book(ltp=100.0)
    get_strategy_registry().set_armed(STRATEGY, True)
    await _open_stop(db_session, definition, portfolio_id, stop_price="95")

    monkeypatch.setattr(market_clock, "ist_now", lambda: MIDSESSION)
    get_feed_manager().book.forget([SECURITY_ID])

    assert await _monitor().run_once() == 0
    db_session.expire_all()
    stop = (await SwingStopRepository(db_session).history(STRATEGY))[0]
    assert stop.status == STOP_ACTIVE


async def test_a_stop_does_not_fire_outside_the_session(
    db_session, definition, monkeypatch
):
    """After the close the book holds the last prices of the day. A stop
    evaluated against one of those would fire every evening on a price nobody
    can trade at."""
    portfolio_id = await _seed_portfolio(db_session)
    await _seed_instrument(db_session)
    _seed_book(ltp=100.0)
    get_strategy_registry().set_armed(STRATEGY, True)
    await _open_stop(db_session, definition, portfolio_id, stop_price="95")

    monkeypatch.setattr(market_clock, "ist_now", lambda: AFTER_CLOSE)
    _seed_book(ltp=94.0)

    assert await _monitor().run_once() == 0
    db_session.expire_all()
    stop = (await SwingStopRepository(db_session).history(STRATEGY))[0]
    assert stop.status == STOP_ACTIVE


async def test_an_unarmed_strategy_records_the_trigger_and_places_no_exit(
    db_session, definition, monkeypatch
):
    portfolio_id = await _seed_portfolio(db_session)
    await _seed_instrument(db_session)
    _seed_book(ltp=100.0)
    get_strategy_registry().set_armed(STRATEGY, True)
    await _open_stop(db_session, definition, portfolio_id, stop_price="95")

    get_strategy_registry().set_armed(STRATEGY, False)
    monkeypatch.setattr(market_clock, "ist_now", lambda: MIDSESSION)
    _seed_book(ltp=94.0)

    assert await _monitor().run_once() == 0
    db_session.expire_all()
    stop = (await SwingStopRepository(db_session).history(STRATEGY))[0]
    assert stop.status == STOP_TRIGGERED
    assert stop.exit_order_id is None
    assert "NOT ARMED" in stop.note


# --- the Closing Auction Session ---------------------------------------------


async def test_the_closing_auction_defers_an_fno_names_exit_to_the_next_open(
    db_session, definition, monkeypatch
):
    """Continuous cash trading for an F&O-eligible name ends at 15:15.

    An exit sent between then and the close lands in a call auction this
    simulator has no model of. Filling it against the last continuous book
    would flatter the fill silently, which is exactly what the backtest --
    written before the CAS existed -- assumes.
    """
    portfolio_id = await _seed_portfolio(db_session)
    await _seed_instrument(db_session, fno_eligible=True)
    _seed_book(ltp=100.0)
    get_strategy_registry().set_armed(STRATEGY, True)
    await _open_stop(db_session, definition, portfolio_id, stop_price="95")

    monkeypatch.setattr(market_clock, "ist_now", lambda: IN_AUCTION)
    _seed_book(ltp=94.0)

    monitor = _monitor()
    assert await monitor.run_once() == 0

    db_session.expire_all()
    stop = (await SwingStopRepository(db_session).history(STRATEGY))[0]
    assert stop.status == STOP_TRIGGERED
    assert stop.exit_order_id is None
    assert "Closing Auction Session" in stop.note
    assert "next session's open" in stop.note

    # And it is picked up at the next open, without re-triggering.
    monkeypatch.setattr(market_clock, "ist_now", lambda: MIDSESSION)
    assert await monitor.run_once() == 1
    db_session.expire_all()
    stop = (await SwingStopRepository(db_session).history(STRATEGY))[0]
    assert stop.exit_order_id is not None


async def test_a_non_fno_name_still_exits_during_the_auction_window(
    db_session, definition, monkeypatch
):
    """Only F&O-eligible names move to 15:15; the rest trade continuously to
    15:30, so their stops must keep working."""
    portfolio_id = await _seed_portfolio(db_session)
    await _seed_instrument(db_session, fno_eligible=False)
    _seed_book(ltp=100.0)
    get_strategy_registry().set_armed(STRATEGY, True)
    await _open_stop(db_session, definition, portfolio_id, stop_price="95")

    monkeypatch.setattr(market_clock, "ist_now", lambda: IN_AUCTION)
    _seed_book(ltp=94.0)

    assert await _monitor().run_once() == 1


async def test_the_fno_set_is_derived_from_the_masters_own_futstk_rows(
    db_session, tmp_path
):
    """No list to maintain: a name with a single-stock future is F&O-eligible.

    228 distinct NSE underlyings as of 2026-09-18, and the exchange changes it.
    """
    from src.instruments.services.instrument_master_service import (
        InstrumentMasterService,
    )

    header = (
        "EXCH_ID,SEGMENT,SECURITY_ID,ISIN,INSTRUMENT,UNDERLYING_SECURITY_ID,"
        "UNDERLYING_SYMBOL,SYMBOL_NAME,DISPLAY_NAME,INSTRUMENT_TYPE,SERIES,"
        "LOT_SIZE,SM_EXPIRY_DATE,STRIKE_PRICE,OPTION_TYPE,TICK_SIZE"
    )
    rows = [
        # The cash rows come FIRST, before the futures row that decides
        # eligibility -- which is why the parser resolves this in a second pass.
        "NSE,E,9000,NA,EQUITY,9000,WELCORP,WELCORP,Welspun Corp,EQUITY,EQ,"
        "1.0,,0.00000,,5.0000",
        "NSE,E,9001,NA,EQUITY,9001,RADICO,RADICO,Radico Khaitan,EQUITY,EQ,"
        "1.0,,0.00000,,5.0000",
        # WELCORP has a stock future; RADICO does not.
        "NSE,D,50001,NA,FUTSTK,9000,WELCORP,WELCORP,WELCORP SEP FUT,FUTSTK,NA,"
        "600.0,2026-09-25,0.00000,XX,5.0000",
    ]
    path = tmp_path / "master.csv"
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")

    service = InstrumentMasterService(InstrumentRepository(db_session))
    parsed, _scanned, _warnings = service.parse(str(path))
    by_symbol = {row["underlying_symbol"]: row for row in parsed}

    assert by_symbol["WELCORP"]["fno_eligible"] is True
    assert by_symbol["RADICO"]["fno_eligible"] is False
    # The futures row itself is never ingested. Nothing here trades one.
    assert all(row["instrument_type"] != "FUTSTK" for row in parsed)


# --- exits that are not the stop ----------------------------------------------


async def test_a_rotation_exit_closes_the_stop_without_firing_it(
    db_session, definition
):
    """The row stays as the record that a stop existed and did NOT fire.

    That distinction is the exit mix the performance report counts: 348 trail
    stops against 324 rotations over the backtest's 672 trades.
    """
    portfolio_id = await _seed_portfolio(db_session)
    service = _stop_service(db_session, definition)
    stop = await service.open_for_entry(
        portfolio_id=portfolio_id, symbol=SYMBOL, security_id=SECURITY_ID,
        quantity=100, entry_price=Decimal("500"),
        entry_session=date(2026, 9, 17), entry_atr=10.0,
    )
    await service.close_for_exit(stop, EXIT_ROTATION, order_id=None,
                                 note="Rotation exit: rank 19 > 15.")

    assert stop.status == STOP_CLOSED
    assert stop.exit_kind == EXIT_ROTATION
    assert stop.triggered_at is None
    assert await SwingStopRepository(db_session).get_active(
        portfolio_id, SECURITY_ID
    ) is None
