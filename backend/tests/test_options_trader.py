"""NSE Options Trader, Phase 1 (OPTIONS_TRADER_PLAN.md section 7).

What is pinned here:

* the instrument master can hold NSE option rows ALONGSIDE the rotation's
  equity rows for the same names, within an expiry window;
* the shared engine's chain view resolves an underlying, reads greeks from the
  option chain endpoint, and pins only the ATM window to the feed;
* Dhan's product type is stored on orders and positions, and an Intraday order
  never silently merges into a Carry-forward position (or the reverse);
* the module's orders are charged on the NSE equity options card;
* two portfolios trading the same option keep separate books.
"""
from datetime import timedelta
from decimal import Decimal

import pytest

from src.core.time_utils import ist_today, utc_now
from src.database.session import get_session_factory, session_scope
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.instruments.services.instrument_master_service import InstrumentMasterService
from src.market.services.dhan_option_chain_client import OptionChainSnapshot, OptionLeg
from src.market.services.feed_manager import get_feed_manager
from src.options.services import chain_view_service
from src.options.services.chain_view_service import ChainViewError, ChainViewService
from src.positions.database.db_operations.position_repository import PositionRepository
from src.strategies.services.strategy_registry import get_strategy_registry

KEY = "nse-options-trader"
SWING = "nse-swing-momentum"
EXPIRY = ist_today() + timedelta(days=7)
FAR_EXPIRY = ist_today() + timedelta(days=200)
STRIKES = [24900, 24950, 25000, 25050, 25100]

DEPTH = [
    (5000, 5000, 9, 9, 99.5, 100.5),
    (5000, 5000, 9, 9, 99.0, 101.0),
    (0, 0, 0, 0, 0.0, 0.0),
    (0, 0, 0, 0, 0.0, 0.0),
    (0, 0, 0, 0, 0.0, 0.0),
]


def _id(strike: int, option_type: str) -> str:
    return f"{9000 + (strike - 24900) // 50}{1 if option_type == 'CE' else 2}"


def _row(strike: int, option_type: str, expiry=EXPIRY, active=True):
    security_id = _id(strike, option_type)
    return {
        "security_id": security_id,
        "exchange_id": "NSE", "exchange_segment": "NSE_FNO", "segment_code": 2,
        "instrument_type": "OPTIDX", "underlying_symbol": "NIFTY",
        "underlying_scrip": 26000,
        "trading_symbol": f"NIFTY {strike} {'CALL' if option_type == 'CE' else 'PUT'}",
        "display_name": f"NIFTY {strike} {option_type}",
        "expiry_date": expiry, "strike_price": Decimal(strike),
        "option_type": option_type, "lot_size": 65, "tick_size": Decimal("0.05"),
        "is_active": active, "refreshed_at": utc_now(),
    }


async def _seed_nifty_chain():
    session = get_session_factory()()
    try:
        await InstrumentRepository(session).upsert_many(
            [_row(strike, side) for strike in STRIKES for side in ("CE", "PE")]
        )
        await session.commit()
    finally:
        await session.close()


def _definition():
    return get_strategy_registry().require(KEY)


# --- ingestion ----------------------------------------------------------------
MASTER_HEADER = (
    "EXCH_ID,SEGMENT,SECURITY_ID,ISIN,INSTRUMENT,UNDERLYING_SECURITY_ID,"
    "UNDERLYING_SYMBOL,SYMBOL_NAME,DISPLAY_NAME,INSTRUMENT_TYPE,SERIES,"
    "LOT_SIZE,SM_EXPIRY_DATE,STRIKE_PRICE,OPTION_TYPE,TICK_SIZE"
)


def _master(tmp_path):
    near = EXPIRY.isoformat()
    far = FAR_EXPIRY.isoformat()
    lines = [
        MASTER_HEADER,
        f"NSE,D,70001,NA,OPTIDX,26000,NIFTY,NIFTY,NIFTY {near} 25000 CALL,OP,NA,65.0,{near},25000.00000,CE,5.0000",
        f"NSE,D,70002,NA,OPTIDX,26000,NIFTY,NIFTY,NIFTY {far} 25000 CALL,OP,NA,65.0,{far},25000.00000,CE,5.0000",
        f"NSE,D,70003,NA,OPTSTK,2885,RELIANCE,RELIANCE,RELIANCE {near} 1400 PUT,OP,NA,500.0,{near},1400.00000,PE,5.0000",
        "NSE,E,2885,INE002A01018,EQUITY,2885,RELIANCE,RELIANCE,Reliance Industries,ES,EQ,1.0,,,,10.0000",
    ]
    path = tmp_path / "master.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def test_option_rows_coexist_with_the_rotation_owning_the_same_stock(tmp_path):
    """RELIANCE's OPTSTK rows and its EQUITY row are claimed by two modules.

    Until 2026-11-24 ownership was keyed on (exchange, symbol), so this was
    refused as a disagreement about how to ingest RELIANCE -- for two families
    of rows that can never produce the same `instruments` row.
    """
    registry = get_strategy_registry()
    service = InstrumentMasterService(
        repository=None, strategies=[registry.require(SWING), registry.require(KEY)]
    )

    rows, _, _ = service.parse(_master(tmp_path))
    by_id = {row["security_id"]: row for row in rows}

    assert "70003" in by_id and by_id["70003"]["lot_size"] == 500
    assert "2885" in by_id and by_id["2885"]["instrument_type"] == "EQUITY"
    assert by_id["70001"]["lot_size"] == 65, "NSE lot size comes from the master"
    assert by_id["70001"]["tick_size"] == Decimal("0.05"), "paise to rupees"


def test_contracts_beyond_the_expiry_window_are_not_ingested(tmp_path):
    service = InstrumentMasterService(repository=None, strategies=[_definition()])

    rows, _, _ = service.parse(_master(tmp_path))

    ids = {row["security_id"] for row in rows}
    assert "70001" in ids
    assert "70002" not in ids, "200 days out is beyond the 70-day window"


def test_a_universe_divergence_is_judged_on_cash_rows_only(tmp_path):
    """The first RELIANCE row is now an option; it is not RELIANCE's id."""
    service = InstrumentMasterService(repository=None, strategies=[_definition()])

    _, _, warnings = service.parse(_master(tmp_path))

    assert not any("RELIANCE (file" in warning for warning in warnings)


# --- the chain view ----------------------------------------------------------
class _FakeBook:
    def __init__(self):
        self.rows = {}
        self.merged = {}

    def get(self, security_id):
        return self.rows.get(str(security_id))

    def merge_greeks(self, security_id, greeks):
        self.merged[str(security_id)] = greeks


class _FakeFeed:
    is_synthetic = False

    def __init__(self):
        self.book = _FakeBook()
        self.pins = []
        self.resyncs = 0

    def pin_instruments(self, key, ids):
        self.pins.append((key, set(ids)))

    async def resync(self):
        self.resyncs += 1
        return {}


class _FakeClient:
    def __init__(self, spot=25010.0):
        self.calls = []
        self.spot = spot

    def has_credentials(self):
        return True

    async def fetch_option_chain(self, scrip, segment, expiry):
        self.calls.append((scrip, segment, expiry))
        snapshot = OptionChainSnapshot(underlying_last_price=self.spot, expiry=expiry)
        for strike in STRIKES:
            snapshot.strikes[float(strike)] = {
                "CE": OptionLeg(security_id=_id(strike, "CE"), last_price=100.0,
                                implied_volatility=14.2, delta=0.5, oi=1000, previous_oi=900),
                "PE": OptionLeg(security_id=_id(strike, "PE"), last_price=90.0,
                                implied_volatility=15.1, delta=-0.5, oi=800, previous_oi=800),
            }
        return snapshot


@pytest.fixture
def fresh_engine_state():
    chain_view_service.reset_state_for_tests()
    yield
    chain_view_service.reset_state_for_tests()


async def test_an_index_chain_asks_dhan_for_the_IDX_I_id_not_the_master_one(
    db_session, fresh_engine_state
):
    await _seed_nifty_chain()
    client = _FakeClient()
    service = ChainViewService(db_session, _definition(), feed_manager=_FakeFeed(), client=client)

    await service.chain("NIFTY", EXPIRY)

    # 26000 (the option rows' UNDERLYING_SECURITY_ID) is refused by Dhan with
    # "813 Invalid SecurityId"; 13 is the IDX_I id. Verified live 2026-11-24.
    assert client.calls == [(13, "IDX_I", EXPIRY.isoformat())]


async def test_the_chain_pins_only_the_atm_window_and_merges_its_greeks(
    db_session, fresh_engine_state
):
    await _seed_nifty_chain()
    feed = _FakeFeed()
    service = ChainViewService(db_session, _definition(), feed_manager=feed, client=_FakeClient())
    service.strike_window = 1

    view = await service.chain("NIFTY", EXPIRY)

    assert view["atmStrike"] == 25000.0
    assert view["spot"] == 25010.0 and view["spotSource"] == "option-chain"
    pinned = feed.pins[-1][1]
    assert pinned == {_id(s, t) for s in (24950, 25000, 25050) for t in ("CE", "PE")}
    assert set(feed.book.merged) == pinned, "greeks only for what is on the feed"
    row = next(one for one in view["rows"] if one["strike"] == 25000.0)
    assert row["call"]["iv"] == 14.2 and row["put"]["delta"] == -0.5


async def test_an_unchanged_window_does_not_resync_the_feed(db_session, fresh_engine_state):
    await _seed_nifty_chain()
    feed = _FakeFeed()
    service = ChainViewService(db_session, _definition(), feed_manager=feed, client=_FakeClient())

    await service.chain("NIFTY", EXPIRY)
    await service.chain("NIFTY", EXPIRY)

    assert feed.resyncs == 1, "a page polling every 3 s must not resync every 3 s"


async def test_a_snapshot_is_reused_within_dhans_three_second_limit(
    db_session, fresh_engine_state
):
    await _seed_nifty_chain()
    client = _FakeClient()
    service = ChainViewService(db_session, _definition(), feed_manager=_FakeFeed(), client=client)

    await service.chain("NIFTY", EXPIRY)
    await service.chain("NIFTY", EXPIRY)

    assert len(client.calls) == 1


async def test_releasing_the_view_unpins_and_resyncs(db_session, fresh_engine_state):
    await _seed_nifty_chain()
    feed = _FakeFeed()
    service = ChainViewService(db_session, _definition(), feed_manager=feed, client=_FakeClient())

    await service.chain("NIFTY", EXPIRY)
    await service.release()

    assert feed.pins[-1] == (KEY, set())
    assert feed.resyncs == 2


async def test_a_chain_without_credentials_still_lists_contracts(db_session, fresh_engine_state):
    await _seed_nifty_chain()

    class _NoCredentials(_FakeClient):
        def has_credentials(self):
            return False

    service = ChainViewService(
        db_session, _definition(), feed_manager=_FakeFeed(), client=_NoCredentials()
    )

    view = await service.chain("NIFTY", EXPIRY)

    assert len(view["rows"]) == len(STRIKES)
    assert "credentials" in view["snapshotNote"]
    assert view["atmStrike"] is None, "no spot: the ATM is not guessed"


async def test_an_underlying_the_module_does_not_trade_is_refused(db_session, fresh_engine_state):
    service = ChainViewService(db_session, _definition(), feed_manager=_FakeFeed(), client=_FakeClient())

    with pytest.raises(ChainViewError):
        await service.chain("CRUDEOIL")


async def test_underlyings_list_only_names_with_active_options(db_session, fresh_engine_state):
    await _seed_nifty_chain()
    service = ChainViewService(db_session, _definition(), feed_manager=_FakeFeed(), client=_FakeClient())

    found = await service.underlyings()

    assert [(one.symbol, one.kind, one.lot_size) for one in found] == [("NIFTY", "INDEX", 65)]


# --- orders: product, charges, portfolios ------------------------------------
async def _options_portfolio(client, name="Options Book A") -> int:
    response = await client.post(
        "/api/portfolios",
        json={"name": name, "strategies": [KEY], "openingBalance": "1000000"},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


def _seed_book(security_id):
    book = get_feed_manager().book
    book.register_instrument(
        security_id,
        {"tradingSymbol": "NIFTY", "optionType": "CE", "strikePrice": 25000.0,
         "lotSize": 65, "tickSize": 0.05},
    )
    book.apply_packet({"security_id": security_id, "segment": 2, "ltp": 100.0, "depth": DEPTH})


@pytest.fixture
async def trading(auth_client):
    await _seed_nifty_chain()
    security_id = _id(25000, "CE")
    _seed_book(security_id)
    portfolio_id = await _options_portfolio(auth_client)
    yield auth_client, portfolio_id, security_id
    get_feed_manager().book.clear()


async def _order(client, portfolio_id, security_id, side="BUY", product="INTRADAY", **extra):
    return await client.post(
        "/api/orders",
        json={"securityId": security_id, "side": side, "orderType": "MARKET", "lots": 1,
              "portfolioId": portfolio_id, "product": product, **extra},
    )


async def test_the_product_is_stored_on_the_order_and_the_position(trading):
    client, portfolio_id, security_id = trading

    response = await _order(client, portfolio_id, security_id, product="intraday")

    assert response.status_code == 201, response.text
    assert response.json()["product"] == "INTRADAY"
    async with session_scope() as session:
        position = await PositionRepository(session).get_open_for_security(portfolio_id, security_id)
    assert position.product == "INTRADAY" and position.strategy_key == KEY


async def test_a_different_product_does_not_merge_into_an_open_position(trading):
    client, portfolio_id, security_id = trading
    assert (await _order(client, portfolio_id, security_id, product="INTRADAY")).status_code == 201

    response = await _order(client, portfolio_id, security_id, product="CARRYFORWARD")

    assert response.status_code == 400
    assert "Intraday" in response.json()["detail"]


async def test_a_closing_order_is_not_refused_for_its_product(trading):
    client, portfolio_id, security_id = trading
    assert (await _order(client, portfolio_id, security_id, product="INTRADAY")).status_code == 201

    response = await _order(
        client, portfolio_id, security_id, side="SELL", product="CARRYFORWARD", isCloseOrder=True
    )

    assert response.status_code == 201, response.text


async def test_an_unknown_product_is_refused(trading):
    client, portfolio_id, security_id = trading

    response = await _order(client, portfolio_id, security_id, product="MIS")

    assert response.status_code == 400
    assert "INTRADAY" in response.json()["detail"]


async def test_a_sell_is_charged_stt_on_the_nse_equity_options_card(trading):
    client, portfolio_id, security_id = trading

    response = await _order(client, portfolio_id, security_id, side="SELL", product="CARRYFORWARD")

    assert response.status_code == 201, response.text
    charges = response.json()["charges"]
    names = {component["name"] for component in charges["components"]}
    assert "stt" in names and "ctt" not in names
    stt = next(c for c in charges["components"] if c["name"] == "stt")
    turnover = Decimal(charges["turnover"])
    # 0.15% of premium on the sell side (NSE/FATAX/73524, from 2026-04-01).
    assert Decimal(stt["amount"]) == (turnover * Decimal("0.0015")).quantize(Decimal("0.01"))


async def test_two_portfolios_trading_the_same_option_keep_separate_books(trading):
    client, first, security_id = trading
    second = await _options_portfolio(client, name="Hedged Writing")

    assert (await _order(client, first, security_id, product="CARRYFORWARD")).status_code == 201
    assert (await _order(client, second, security_id, side="SELL", product="CARRYFORWARD")).status_code == 201

    async with session_scope() as session:
        repository = PositionRepository(session)
        long_book = await repository.get_open_for_security(first, security_id)
        short_book = await repository.get_open_for_security(second, security_id)
    assert long_book.net_quantity == 65
    assert short_book.net_quantity == -65


# --- the page's API ----------------------------------------------------------
async def test_the_chain_endpoint_serves_a_chain(auth_client, fresh_engine_state):
    await _seed_nifty_chain()

    response = await auth_client.get(
        "/api/options-trader/chain", params={"underlying": "NIFTY", "expiry": EXPIRY.isoformat()}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["strategyKey"] == KEY
    assert body["underlying"] == {"symbol": "NIFTY", "kind": "INDEX", "lotSize": 65}
    assert len(body["rows"]) == len(STRIKES)
    assert "ESTIMATE" in body["marginNote"]


async def test_the_page_says_why_when_the_module_is_off(auth_client):
    get_strategy_registry().set_enabled(KEY, False)  # restored by the baseline fixture

    response = await auth_client.get("/api/options-trader/underlyings")

    assert response.status_code == 409
    assert "switched off" in response.json()["detail"]


def test_the_options_page_is_granted_by_its_capability_and_leaves_with_it():
    registry = get_strategy_registry()
    assert "/options" in registry.feature_pages()

    registry.set_enabled(KEY, False)  # restored by the baseline fixture

    assert "/options" not in registry.feature_pages()


async def test_a_resting_order_keeps_its_contract_on_the_feed_after_the_pin_lapses(trading):
    """Found live on 2026-11-24: the chain's pin lapsed and took a resting
    limit order's contract off the feed with it, so the order could never
    fill. A `positions` strategy now subscribes what it HOLDS and what it has
    RESTING, pinned or not."""
    client, portfolio_id, security_id = trading
    response = await _order(
        client, portfolio_id, security_id, side="SELL", product="CARRYFORWARD",
        orderType="LIMIT", limitPrice="500",
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "OPEN", "far above the book: it rests"

    manager = get_feed_manager()
    manager.pin_instruments(KEY, set())
    async with session_scope() as session:
        targets, _ = await manager._resolve_strategy_targets(
            _definition(), InstrumentRepository(session)
        )

    assert ("NSE_FNO", security_id) in targets
